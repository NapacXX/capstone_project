"""Real-fixture nine-chart release contracts with a synthetic offline encoder.

These tests read, but never change, the recorded human approvals. They test
release integrity, provenance and retrieval plumbing, not model quality,
clinical correctness, or a Windows installation.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np


PIPELINE_DIR = Path(__file__).resolve().parents[1]
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import kb_v1_build as builder
import kb_v1_runtime as runtime
import test_kb_v1_chart_integration as previous


SUPPLEMENT = PIPELINE_DIR / "outputs/kb_v1_work/20260929_tables_9_1_9_4"
SUPPLEMENT_VERSIONS = ("table_9_1_draft_001_en", "table_9_4_draft_001_en")
FIXTURES = previous.LOCAL_FIXTURES and all(
    (SUPPLEMENT / "decisions" / f"{name}_approved_001.json").is_file()
    for name in SUPPLEMENT_VERSIONS
)


@unittest.skipUnless(FIXTURES, "Private ADA source and nine recorded chart approvals are unavailable")
class NineChartIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="ada-nine-chart-integration-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.selected = [(previous.WORKSPACE, name) for name in previous.VERSIONS]
        cls.selected += [(SUPPLEMENT, name) for name in SUPPLEMENT_VERSIONS]
        cls.documents = {
            name: json.loads((workspace / "reviews" / name / "document.json").read_text(encoding="utf-8"))
            for workspace, name in cls.selected
        }
        cls.protected = {
            path: builder.sha256_file(path)
            for workspace, name in cls.selected
            for path in (
                workspace / "reviews" / name / "document.json",
                workspace / "reviews" / name / "review_manifest.json",
                workspace / "decisions" / f"{name}_approved_001.json",
            )
        }
        cls.model = cls.root / "snapshots" / builder.MODEL_REVISION
        cls.model.mkdir(parents=True)
        for filename, content in {
            "config.json": '{"model_type": "synthetic-test-only"}',
            "README.md": "Synthetic contract fixture, not an embedding evaluation.",
            "LICENSE": "Synthetic fixture; no real weights redistributed.",
            "model.safetensors": "Synthetic bytes used only with a patched encoder.",
        }.items():
            (cls.model / filename).write_text(content, encoding="utf-8")
        pins = {p.name: builder.sha256_file(p) for p in cls.model.iterdir() if p.name != "LICENSE"}
        cls.bundle = cls.root / "baseline-nine-charts"
        with mock.patch.object(builder, "MODEL_FILE_SHA256", pins), \
                mock.patch.object(builder, "_load_embedding_runtime", return_value=previous.ContractEncoder()):
            cls.manifest = builder.build_bundle(
                previous.BASE_KB, previous.SOURCE_PDF, cls.model, cls.bundle,
                chart_workspace=previous.WORKSPACE, supplement_workspace=SUPPLEMENT,
            )
        cls.records = runtime.read_jsonl(cls.bundle / "records.jsonl")
        cls.by_chunk = {r["chunk_id"]: r for r in cls.records if r.get("chunk_id")}
        cls.charts = {r["figure_id"]: r for r in cls.records if r["content_type"] == "visual_whole_chart"}
        cls.units = runtime.read_jsonl(cls.bundle / "units.jsonl")
        cls.indexed = {unit["record_id"] for unit in cls.units}

    def copy_bundle(self):
        temporary = tempfile.TemporaryDirectory(dir=self.root, prefix="mutated-bundle-")
        self.addCleanup(temporary.cleanup)
        destination = Path(temporary.name) / "bundle"
        shutil.copytree(self.bundle, destination)
        return destination

    def copy_supplement(self):
        temporary = tempfile.TemporaryDirectory(dir=self.root, prefix="mutated-supplement-")
        self.addCleanup(temporary.cleanup)
        destination = Path(temporary.name) / "supplement"
        destination.mkdir()
        for name in ("scope_manifest.json", "parent_scope_manifest.json"):
            shutil.copyfile(SUPPLEMENT / name, destination / name)
        for name in ("reviews", "decisions"):
            shutil.copytree(SUPPLEMENT / name, destination / name)
        return destination

    @staticmethod
    def rehash(bundle, relative):
        manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
        manifest["files"][relative] = builder.sha256_file(bundle / relative)
        (bundle / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def write_records(bundle, records):
        (bundle / "records.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records), encoding="utf-8",
        )
        NineChartIntegrationTests.rehash(bundle, "records.jsonl")

    def test_nine_complete_approved_parents_and_originals_unchanged(self):
        result = runtime.verify_bundle(self.bundle)
        self.assertEqual(result["status"], "released")
        self.assertEqual(result["visual_record_count"], 9)
        self.assertEqual(len(self.charts), 9)
        self.assertEqual(len({r["record_id"] for r in self.records}), len(self.records))
        self.assertEqual(set(self.charts), {d["figure_id"] for d in self.documents.values()})
        for path, expected in self.protected.items():
            self.assertEqual(builder.sha256_file(path), expected, str(path))

    def test_new_tables_preserve_full_approved_documents_and_limitations(self):
        for name, pages, blocks, limitations in (
            ("table_9_1_draft_001_en", [3, 4], 42, 2),
            ("table_9_4_draft_001_en", [22], 52, 3),
        ):
            with self.subTest(table=name):
                document = self.documents[name]
                chart = self.charts[document["figure_id"]]
                self.assertEqual(chart["approved_document"], document)
                self.assertEqual(chart["source_blocks"], document["source_blocks"])
                self.assertEqual(chart["source_text"], "\n\n".join(b["text"] for b in document["source_blocks"]))
                self.assertEqual(chart["logic_paths"], document["paths"])
                self.assertEqual(chart["symbols"], document["symbols"])
                self.assertEqual(chart["page_numbers"], pages)
                self.assertEqual(len(chart["source_blocks"]), blocks)
                self.assertEqual(chart["open_questions"], document["open_questions"])
                self.assertEqual(len(chart["open_questions"]), limitations)
                self.assertIs(chart["use_policy"]["automatic_dose_calculation_allowed"], False)
                self.assertIs(chart["use_policy"]["clinical_execution_allowed"], False)
                self.assertGreater(sum(u["record_id"] == chart["record_id"] for u in self.units), 1)

    def test_new_table_source_navigation_contains_every_page(self):
        sources = json.loads((self.bundle / "sources.json").read_text(encoding="utf-8"))
        for figure, expected in (("ada2026-ch9-table-9-1", {3, 4}), ("ada2026-ch9-table-9-4", {22})):
            chart = self.charts[figure]
            images = [x for x in sources[chart["source_id"]]["images"] if x.get("figure_id") == figure]
            self.assertEqual({x["page_number"] for x in images}, expected)
            self.assertEqual(len(images), len(expected))
            for image in images:
                self.assertEqual(builder.sha256_file(self.bundle / image["relative_path"]), image["sha256"])

    def test_old_table_carriers_and_chart_only_text_are_audit_only(self):
        for chunk in ("table_tbl_0001", "table_tbl_0002", "table_tbl_0008"):
            row = self.by_chunk[chunk]
            self.assertIs(row["indexable"], False, chunk)
            self.assertEqual(row["retrieval_text"], "")
            self.assertTrue(row["source_text"])
            self.assertNotIn(row["record_id"], self.indexed)
        flat = [r for r in self.records if r["content_type"] == "text" and r["page_number"] in {3, 4}]
        self.assertEqual(len(flat), 6)
        for row in flat:
            self.assertIs(row["indexable"], False)
            self.assertEqual(row["retrieval_text"], "")
            self.assertTrue(row["source_text"])
            self.assertNotIn(row["record_id"], self.indexed)

    def test_mixed_page_keeps_clinical_prose_not_duplicate_table(self):
        row = self.by_chunk["text_p022_001_01"]
        expected = row["source_text"][:584].strip()
        self.assertEqual(row["source_text"].index("Table 9.4—Median cost"), 584)
        self.assertEqual(row["retrieval_text"], expected)
        self.assertEqual(row["evidence_text"], expected)
        self.assertEqual(row["source_excerpt_char_range"], [0, 584])
        self.assertIs(row["indexable"], True)
        self.assertIn(row["record_id"], self.indexed)
        for needle in ("9.31a", "9.31b", "is unavailable (e.g., in shortage), it is"):
            self.assertIn(needle, row["retrieval_text"])
        for chunk in ("text_p022_001_02", "text_p022_001_03"):
            covered = self.by_chunk[chunk]
            self.assertIs(covered["indexable"], False)
            self.assertEqual(covered["retrieval_text"], "")
            self.assertTrue(covered["source_text"])
            self.assertNotIn(covered["record_id"], self.indexed)

    def test_public_mixed_page_evidence_excludes_removed_table_span(self):
        original = self.by_chunk["text_p022_001_01"]
        public = runtime._public_evidence(original)
        self.assertEqual(public["source_text"], original["evidence_text"])
        self.assertEqual(public["original_source_text_retained_in"], "records.jsonl")
        self.assertIn("source_text_scope", public)
        self.assertNotIn("Table 9.4—Median cost", public["source_text"])
        self.assertNotIn("$87", public["source_text"])
        self.assertIn("Table 9.4—Median cost", original["source_text"])

    def test_each_new_table_uses_one_rank_slot_and_returns_complete_parent(self):
        for figure in ("ada2026-ch9-table-9-1", "ada2026-ch9-table-9-4"):
            row = self.charts[figure]
            units = [u for u in self.units if u["record_id"] == row["record_id"]]
            vectors = np.zeros((len(units), 2), dtype="float32")
            vectors[:, 0] = 1
            hits = runtime.rank_evidence([row], units, vectors, np.asarray([[1, 0]], dtype="float32"), top_k=8)
            self.assertEqual(len(hits), 1)
            for field in ("source_text", "source_blocks", "logic_paths", "open_questions", "page_numbers"):
                self.assertEqual(hits[0][field], row[field])

    def test_rehashed_removed_or_mislabeled_table_9_1_page_four_rejected(self):
        for operation in ("remove", "mislabel"):
            with self.subTest(operation=operation):
                mutated = self.copy_bundle()
                registry = json.loads((mutated / "sources.json").read_text(encoding="utf-8"))
                chart = self.charts["ada2026-ch9-table-9-1"]
                images = registry[chart["source_id"]]["images"]
                page = next(x for x in images if x.get("figure_id") == chart["figure_id"] and x["page_number"] == 4)
                if operation == "remove":
                    images.remove(page)
                else:
                    page["page_number"] = 3
                (mutated / "sources.json").write_text(json.dumps(registry), encoding="utf-8")
                self.rehash(mutated, "sources.json")
                with self.assertRaises(ValueError):
                    runtime.verify_bundle(mutated)

    def test_rehashed_new_table_approval_projection_mutations_rejected(self):
        for figure, field, replacement in (
            ("ada2026-ch9-table-9-1", "open_questions", []),
            ("ada2026-ch9-table-9-1", "logic_paths", []),
            ("ada2026-ch9-table-9-4", "source_text", "Injected unapproved prices."),
            ("ada2026-ch9-table-9-4", "use_policy", {}),
        ):
            with self.subTest(figure=figure, field=field):
                mutated = self.copy_bundle()
                records = runtime.read_jsonl(mutated / "records.jsonl")
                next(r for r in records if r.get("figure_id") == figure)[field] = replacement
                self.write_records(mutated, records)
                with self.assertRaises(ValueError):
                    runtime.verify_bundle(mutated)

    def test_missing_changed_or_nonapproved_supplement_decision_cannot_build(self):
        for operation in ("missing", "changed_fingerprint", "pending"):
            with self.subTest(operation=operation):
                supplement = self.copy_supplement()
                decision_path = supplement / "decisions/table_9_1_draft_001_en_approved_001.json"
                if operation == "missing":
                    decision_path.unlink()
                else:
                    decision = json.loads(decision_path.read_text(encoding="utf-8"))
                    if operation == "changed_fingerprint":
                        decision["reviewed_content_sha256"] = "0" * 64
                    else:
                        decision["status"] = "pending"
                    decision_path.write_text(json.dumps(decision), encoding="utf-8")
                output = supplement.parent / "failed-build"
                with mock.patch.object(builder, "_load_embedding_runtime") as encode:
                    with self.assertRaises((ValueError, FileNotFoundError)):
                        builder.build_bundle(previous.BASE_KB, previous.SOURCE_PDF, self.model, output,
                                             chart_workspace=previous.WORKSPACE, supplement_workspace=supplement)
                    encode.assert_not_called()
                self.assertFalse((output / "manifest.json").exists())

    def test_supplement_cannot_be_used_without_original_approved_scope(self):
        output = self.root / "supplement-alone-must-not-build"
        with mock.patch.object(builder, "_load_embedding_runtime") as encode:
            with self.assertRaises(ValueError):
                builder.build_bundle(previous.BASE_KB, previous.SOURCE_PDF, self.model, output,
                                     supplement_workspace=SUPPLEMENT)
            encode.assert_not_called()
        self.assertFalse(output.exists())

    def test_rehashed_mixed_page_excerpt_tampering_rejected(self):
        for field, replacement in (("evidence_text", "Injected text outside the approved source span."),
                                   ("source_excerpt_char_range", [0, 585])):
            with self.subTest(field=field):
                mutated = self.copy_bundle()
                records = runtime.read_jsonl(mutated / "records.jsonl")
                row = next(r for r in records if r.get("chunk_id") == "text_p022_001_01")
                row[field] = replacement
                self.write_records(mutated, records)
                with self.assertRaises(ValueError):
                    runtime.verify_bundle(mutated)

    def test_public_response_distinguishes_current_from_historical_scope(self):
        cases = self.root / "scope_context_case.csv"
        cases.write_text("case_id,vignette_text\nsynthetic,Insulin product costs and source limitations\n", encoding="utf-8")
        output = self.root / "scope_context_evidence"
        with mock.patch.object(runtime, "load_embedding_model", return_value=previous.ContractEncoder()):
            runtime.retrieve_cases(self.bundle, cases, output)
        result = runtime.read_jsonl(output / "evidence.jsonl")[0]
        self.assertEqual(result["release_context"]["approved_chart_groups"], 9)
        note = result["release_context"]["historical_scope_note"]
        self.assertIn("historical seven-group", note)
        self.assertIn("Table 9.4 is now separately approved", note)
        self.assertIn(note, (output / "evidence.md").read_text())

    def test_nine_chart_bundle_relocates_without_original_absolute_paths(self):
        relocated = self.root / "\u4e2d\u6587 path with spaces" / "bundle"
        shutil.copytree(self.bundle, relocated)
        program = (
            "import json,sys; sys.path.insert(0,sys.argv[1]); "
            "from kb_v1_runtime import verify_bundle; "
            "print(json.dumps(verify_bundle(sys.argv[2])))"
        )
        result = subprocess.run([sys.executable, "-c", program, str(PIPELINE_DIR), str(relocated)],
                                cwd=self.root, text=True, capture_output=True, check=True)
        verified = json.loads(result.stdout)
        self.assertEqual(verified["kb_version"], self.manifest["kb_version"])
        self.assertEqual(verified["visual_record_count"], 9)
        sources = json.loads((relocated / "sources.json").read_text(encoding="utf-8"))
        for source in sources.values():
            self.assertTrue(runtime.safe_bundle_path(relocated, source["relative_path"]).is_file())
            for image in source["images"]:
                self.assertTrue(runtime.safe_bundle_path(relocated, image["relative_path"]).is_file())


if __name__ == "__main__":
    unittest.main()
