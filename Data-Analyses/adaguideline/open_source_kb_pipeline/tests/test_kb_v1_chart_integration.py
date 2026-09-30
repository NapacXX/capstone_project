"""Integration contracts for whole-chart release, using real approved fixtures.

The private local ADA fixtures are read-only. Builds use a synthetic CPU encoder
and a temporary model snapshot: these tests verify data/release contracts, not
embedding quality, a clinical interpretation, or a Windows installation.
"""

from __future__ import annotations

import hashlib
import json
import re
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

import kb_v1_build as builder
import kb_v1_runtime as runtime


WORKSPACE = PIPELINE_DIR / "outputs/kb_v1_work/20260928/figure_reconstruction_v1"
BASE_KB = PIPELINE_DIR / "outputs/full_guideline_kb"
SOURCE_PDF = PIPELINE_DIR / "ADA principles for pharmacologic therapy.pdf"
VERSIONS = (
    "figure_9_1_draft_001_en", "figure_9_2_draft_001_en",
    "figure_9_3_draft_001_en", "figure_9_4_draft_002_en",
    "figure_9_5_draft_001_en", "table_9_2_draft_001_en",
    "table_9_3_draft_001_en",
)
LOCAL_FIXTURES = (SOURCE_PDF.is_file() and (BASE_KB / builder.BASE_FILES[0][0]).is_file()
                  and all((WORKSPACE / "decisions" / f"{name}_approved_001.json").is_file()
                          for name in VERSIONS))


class WordTokenizer:
    def num_special_tokens_to_add(self, pair=False):
        return 2

    def __call__(self, text, add_special_tokens=True, truncation=False,
                 return_offsets_mapping=False):
        offsets = [match.span() for match in re.finditer(r"\S+", text)]
        result = {"input_ids": list(range(len(offsets) + (2 if add_special_tokens else 0)))}
        if return_offsets_mapping:
            result["offset_mapping"] = offsets
        return result


class ContractEncoder:
    max_seq_length = 256
    tokenizer = WordTokenizer()

    def encode(self, texts, **kwargs):
        vectors = []
        for text in texts:
            if len(self.tokenizer(text)["input_ids"]) > self.max_seq_length:
                raise AssertionError("Synthetic encoder received an over-limit window")
            seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "little")
            vector = np.random.default_rng(seed).normal(size=builder.MODEL_DIMENSION).astype("float32")
            vectors.append(vector / np.linalg.norm(vector))
        return np.asarray(vectors, dtype="float32")


@unittest.skipUnless(LOCAL_FIXTURES, "Private local ADA PDF and seven approved chart fixtures are unavailable")
class ApprovedChartIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="ada-chart-integration-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.documents = {
            name: json.loads((WORKSPACE / "reviews" / name / "document.json").read_text(encoding="utf-8"))
            for name in VERSIONS
        }
        cls.protected = {
            path: builder.sha256_file(path)
            for name in VERSIONS
            for path in (
                WORKSPACE / "reviews" / name / "document.json",
                WORKSPACE / "reviews" / name / "review_manifest.json",
                WORKSPACE / "decisions" / f"{name}_approved_001.json",
            )
        }
        cls.model = cls.root / "snapshots" / builder.MODEL_REVISION
        cls.model.mkdir(parents=True)
        for filename, content in {
            "config.json": '{"model_type": "synthetic-test-only"}',
            "README.md": "Synthetic encoder fixture; not a model-quality evaluation.",
            "LICENSE": "Synthetic fixture; no model weights redistributed.",
            "model.safetensors": "Synthetic bytes used only with a patched encoder.",
        }.items():
            (cls.model / filename).write_text(content, encoding="utf-8")
        pins = {path.name: builder.sha256_file(path) for path in cls.model.iterdir() if path.name != "LICENSE"}
        cls.bundle = cls.root / "baseline"
        with mock.patch.object(builder, "MODEL_FILE_SHA256", pins), \
                mock.patch.object(builder, "_load_embedding_runtime", return_value=ContractEncoder()):
            cls.manifest = builder.build_bundle(
                BASE_KB, SOURCE_PDF, cls.model, cls.bundle, chart_workspace=WORKSPACE,
            )
        cls.records = runtime.read_jsonl(cls.bundle / "records.jsonl")
        cls.charts = {row["evidence_group_id"]: row for row in cls.records
                      if row["content_type"] == "visual_whole_chart"}
        cls.units = runtime.read_jsonl(cls.bundle / "units.jsonl")

    def copy_bundle(self):
        temporary = tempfile.TemporaryDirectory(dir=self.root, prefix="mutation-")
        self.addCleanup(temporary.cleanup)
        destination = Path(temporary.name) / "bundle"
        shutil.copytree(self.bundle, destination)
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
        ApprovedChartIntegrationTests.rehash(bundle, "records.jsonl")

    def test_actual_seven_chart_release_and_read_only_approvals(self):
        result = runtime.verify_bundle(self.bundle)
        self.assertEqual(result["status"], "released")
        self.assertEqual(result["visual_record_count"], 7)
        self.assertEqual(len(self.charts), 7)
        for path, original_hash in self.protected.items():
            self.assertEqual(builder.sha256_file(path), original_hash, str(path))

    def test_exact_source_blocks_and_ai_are_distinct_full_parents(self):
        for document in self.documents.values():
            row = self.charts[document["figure_id"]]
            with self.subTest(figure=document["figure_id"]):
                self.assertEqual(row["source_blocks"], document["source_blocks"])
                self.assertEqual(row["source_text"], "\n\n".join(block["text"] for block in document["source_blocks"]))
                self.assertEqual(row["logic_paths"], document["paths"])
                self.assertEqual(row["symbols"], document["symbols"])
                self.assertEqual(row["open_questions"], document["open_questions"])
                self.assertEqual(row["ai_description"], document["description_en"])
                self.assertEqual(row["dependency_ids"], [])
                self.assertEqual(row["page_numbers"], document["page_numbers"])

    def test_table_continuation_footnotes_and_page_mapping_survive(self):
        document = self.documents["table_9_2_draft_001_en"]
        row = self.charts[document["figure_id"]]
        self.assertEqual(row["page_numbers"], [11, 12, 13, 14])
        self.assertEqual(len(row["source_blocks"]), 104)
        footnotes = {block["id"]: block["text"] for block in document["source_blocks"]
                     if block["kind"] == "footnote"}
        self.assertEqual(set(footnotes), {"F01", "F02", "FSTAR"})
        for text in footnotes.values():
            self.assertIn(text, row["source_text"])
        self.assertGreater(sum(unit["record_id"] == row["record_id"] for unit in self.units), 1)
        self.assertIn("single", " ".join(question["text"] for question in row["open_questions"]))

    def test_old_table_page_rows_not_indexed_and_mixed_prose_retained(self):
        by_chunk = {row.get("chunk_id"): row for row in self.records if row.get("chunk_id")}
        indexed = {unit["record_id"] for unit in self.units}
        for chunk in ["table_tbl_0003", "table_tbl_0004", "table_tbl_0005", "table_tbl_0006", "table_tbl_0007"]:
            with self.subTest(chunk=chunk):
                self.assertIs(by_chunk[chunk]["indexable"], False)
                self.assertNotIn(by_chunk[chunk]["record_id"], indexed)
                self.assertTrue(by_chunk[chunk]["source_text"])
        for chunk in ["text_p014_001_02", "text_p014_001_03", "text_p014_001_04",
                      "text_p021_001_01", "text_p021_001_03", "text_p021_001_04"]:
            with self.subTest(mixed_or_prose=chunk):
                self.assertIs(by_chunk[chunk]["indexable"], True)
                self.assertIn(by_chunk[chunk]["record_id"], indexed)
                self.assertEqual(by_chunk[chunk]["retrieval_text"], by_chunk[chunk]["source_text"])

    def test_verified_chart_only_pages_do_not_reenter_as_raw_text(self):
        indexed = {unit["record_id"] for unit in self.units}
        suppressed = [row for row in self.records if row["content_type"] == "text"
                      and row["page_number"] in {9, 11, 12, 13, 16}]
        self.assertTrue(suppressed)
        for row in suppressed:
            with self.subTest(record=row["record_id"]):
                self.assertIs(row["indexable"], False)
                self.assertEqual(row["retrieval_text"], "")
                self.assertTrue(row["source_text"])
                self.assertNotIn(row["record_id"], indexed)

    def test_chart_windows_consume_one_slot_and_return_complete_parent(self):
        row = self.charts["ada2026-ch9-table-9-2"]
        units = [unit for unit in self.units if unit["record_id"] == row["record_id"]]
        vectors = np.zeros((len(units), 2), dtype="float32")
        vectors[:, 0] = 1
        hits = runtime.rank_evidence([row], units, vectors, np.asarray([[1, 0]], dtype="float32"), top_k=8)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["source_text"], row["source_text"])
        self.assertEqual(hits[0]["logic_paths"], row["logic_paths"])
        self.assertEqual(hits[0]["page_numbers"], [11, 12, 13, 14])

    def test_public_evidence_keeps_restrictions_not_machine_specific_document_paths(self):
        original = self.charts["ada2026-ch9-figure-9-5"]
        row = runtime._public_evidence(original)
        self.assertNotIn("approved_document", row)
        self.assertNotIn("source_pdf", row)
        self.assertNotIn("image_path", row)
        self.assertEqual(row["source_blocks"], original["source_blocks"])
        self.assertEqual(row["logic_paths"], original["logic_paths"])
        self.assertEqual(row["open_questions"], original["open_questions"])
        self.assertEqual(row["use_policy"], original["use_policy"])
        self.assertEqual(len(row["open_questions"]), 3)
        self.assertNotIn("/Users/changchangpan/", json.dumps(row))

    def test_retrieve_cases_exports_complete_source_ai_and_use_restrictions(self):
        chart = self.charts["ada2026-ch9-figure-9-5"]
        index = next(i for i, unit in enumerate(self.units) if unit["record_id"] == chart["record_id"])
        query_vector = np.load(self.bundle / "embeddings.npy", allow_pickle=False)[index]

        class MatchingQueryEncoder:
            tokenizer = WordTokenizer()

            def encode(self, texts, **kwargs):
                return np.asarray([query_vector for _ in texts], dtype="float32")

        cases = self.root / "synthetic_contract_case.csv"
        cases.write_text("case_id,vignette_text\ncontract-only,An artificial query for data contract validation.\n", encoding="utf-8")
        output = self.root / "synthetic-contract-retrieval"
        with mock.patch.object(runtime, "load_embedding_model", return_value=MatchingQueryEncoder()):
            summary = runtime.retrieve_cases(self.bundle, cases, output, top_k=1)
        self.assertEqual(summary["case_count"], 1)
        result = runtime.read_jsonl(output / "evidence.jsonl")[0]
        self.assertEqual(len(result["evidence"]), 1)
        evidence = result["evidence"][0]
        self.assertEqual(evidence["record_id"], chart["record_id"])
        for field in ("source_text", "source_blocks", "logic_paths", "ai_description", "open_questions", "use_policy"):
            self.assertEqual(evidence[field], chart[field])
        self.assertEqual(evidence["dependency_evidence"], [])
        self.assertIs(evidence["use_policy"]["clinical_execution_allowed"], False)
        self.assertIs(evidence["use_policy"]["automatic_dose_calculation_allowed"], False)
        self.assertIs(evidence["use_policy"]["external_model_enforcement"], False)
        self.assertEqual(len(evidence["use_policy"]["figure_9_5_restrictions"]), 3)
        self.assertNotIn("/Users/changchangpan/", json.dumps(result))
        markdown = (output / "evidence.md").read_text(encoding="utf-8")
        self.assertIn("#### Source transcription", markdown)
        self.assertIn("#### AI reconstruction reviewed with the chart", markdown)
        self.assertIn("not verbatim ADA statements", markdown)
        for question in chart["open_questions"]:
            self.assertIn(question["text"], markdown)
        for restriction in chart["use_policy"]["figure_9_5_restrictions"]:
            self.assertIn(restriction, markdown)

    def test_table_page_sources_include_all_four_images(self):
        chart = self.charts["ada2026-ch9-table-9-2"]
        sources = json.loads((self.bundle / "sources.json").read_text(encoding="utf-8"))
        images = [image for image in sources[chart["source_id"]]["images"]
                  if image.get("figure_id") == chart["figure_id"]]
        self.assertEqual({image["page_number"] for image in images}, {11, 12, 13, 14})
        for image in images:
            self.assertEqual(builder.sha256_file(self.bundle / image["relative_path"]), image["sha256"])

    def test_rehashed_source_or_ai_or_policy_mutation_is_not_approved(self):
        for field, replacement in (
            ("source_text", "Injected, unapproved clinical source wording."),
            ("ai_description", "Injected, unapproved interpretation."),
            ("use_policy", {}),
            ("open_questions", []),
            ("logic_paths", []),
            ("source_blocks", []),
        ):
            with self.subTest(field=field):
                mutated = self.copy_bundle()
                rows = runtime.read_jsonl(mutated / "records.jsonl")
                chart = next(row for row in rows if row.get("evidence_group_id") == "ada2026-ch9-figure-9-5")
                chart[field] = replacement
                self.write_records(mutated, rows)
                with self.assertRaises(ValueError):
                    runtime.verify_bundle(mutated)

    def test_visual_count_mismatch_rejected(self):
        mutated = self.copy_bundle()
        manifest = json.loads((mutated / "manifest.json").read_text(encoding="utf-8"))
        manifest["visual_record_count"] = 0
        (mutated / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(ValueError):
            runtime.verify_bundle(mutated)

    def test_rehashed_wrong_image_page_mapping_is_rejected(self):
        mutated = self.copy_bundle()
        sources = json.loads((mutated / "sources.json").read_text(encoding="utf-8"))
        chart = self.charts["ada2026-ch9-figure-9-5"]
        image = next(image for image in sources[chart["source_id"]]["images"]
                     if image["sha256"] == chart["image_sha256"])
        image["page_number"] = 9
        (mutated / "sources.json").write_text(json.dumps(sources), encoding="utf-8")
        self.rehash(mutated, "sources.json")
        with self.assertRaises(ValueError):
            runtime.verify_bundle(mutated)

    def test_rehashed_false_citation_metadata_is_rejected(self):
        for field, replacement in (("title", "An unrelated unapproved publication"), ("year", 2035)):
            with self.subTest(field=field):
                mutated = self.copy_bundle()
                sources = json.loads((mutated / "sources.json").read_text(encoding="utf-8"))
                chart = self.charts["ada2026-ch9-figure-9-5"]
                sources[chart["source_id"]][field] = replacement
                (mutated / "sources.json").write_text(json.dumps(sources), encoding="utf-8")
                self.rehash(mutated, "sources.json")
                with self.assertRaises(ValueError):
                    runtime.verify_bundle(mutated)

    def test_release_cannot_mix_chart_and_candidate_or_legacy_inputs(self):
        for options in ({"candidate": True}, {"review_dir": self.root / "legacy"},
                        {"ledger_path": self.root / "ledger.jsonl"}):
            with self.subTest(options=options), mock.patch.object(builder, "_load_embedding_runtime") as encode:
                with self.assertRaises(ValueError):
                    builder.build_bundle(BASE_KB, SOURCE_PDF, self.model, self.root / "must-not-build",
                                         chart_workspace=WORKSPACE, **options)
                encode.assert_not_called()
                self.assertFalse((self.root / "must-not-build").exists())

    def test_table_supersession_without_text_fallback_is_rejected(self):
        chart = self.charts["ada2026-ch9-table-9-3"]
        row = {"record_id": "synthetic-old-table", "source_id": chart["source_id"],
               "content_type": "table", "table_or_figure_id": "Table 9.3",
               "page_number": 21, "source_text": "Mixed table and prose.",
               "retrieval_text": "Mixed table and prose.", "indexable": True}
        with self.assertRaisesRegex(ValueError, "fallback"):
            builder.supersede_chart_base_records([row], [chart])
        self.assertIs(row["indexable"], True)

    def test_chart_cannot_bypass_review_as_ordinary_text(self):
        mutated = self.copy_bundle()
        rows = runtime.read_jsonl(mutated / "records.jsonl")
        for row in rows:
            if row["content_type"] == "visual_whole_chart":
                row["content_type"] = "text"
                row.pop("is_visual", None)
        self.write_records(mutated, rows)
        with self.assertRaises(ValueError):
            runtime.verify_bundle(mutated)

    def test_relocated_chart_bundle_verifies_from_different_working_directory(self):
        relocated = self.root / "\u4e2d\u6587 folder with spaces" / "release"
        if not relocated.exists():
            shutil.copytree(self.bundle, relocated)
        program = (
            "import json,sys; sys.path.insert(0,sys.argv[1]); "
            "from kb_v1_runtime import verify_bundle; "
            "print(json.dumps(verify_bundle(sys.argv[2])))"
        )
        result = subprocess.run([sys.executable, "-c", program, str(PIPELINE_DIR), str(relocated)],
                                cwd=self.root, text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout)["kb_version"], self.manifest["kb_version"])
        sources = json.loads((relocated / "sources.json").read_text(encoding="utf-8"))
        for source in sources.values():
            self.assertTrue(runtime.safe_bundle_path(relocated, source["relative_path"]).is_file())
            for image in source["images"]:
                self.assertTrue(runtime.safe_bundle_path(relocated, image["relative_path"]).is_file())


if __name__ == "__main__":
    unittest.main()
