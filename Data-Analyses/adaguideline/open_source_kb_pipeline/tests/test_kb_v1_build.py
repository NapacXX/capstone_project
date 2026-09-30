"""Offline build-contract tests: synthetic source, fake tokenizer and encoder."""

from __future__ import annotations

import csv
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import numpy as np


PIPELINE_DIR = Path(__file__).resolve().parents[1]
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

import kb_v1_build as builder
import kb_v1_runtime as runtime
import kb_v1_review as review_helpers


class FakeTokenizer:
    def num_special_tokens_to_add(self, pair=False):
        return 2

    def __call__(self, text, add_special_tokens=True, truncation=False, return_offsets_mapping=False):
        result = {"input_ids": list(range(len(text) + (2 if add_special_tokens else 0)))}
        if return_offsets_mapping:
            result["offset_mapping"] = [(i, i + 1) for i in range(len(text))]
        return result


class FakeEncoder:
    max_seq_length = 256
    tokenizer = FakeTokenizer()

    def encode(self, texts, **kwargs):
        vectors = np.zeros((len(texts), builder.MODEL_DIMENSION), dtype=np.float32)
        for index, text in enumerate(texts):
            self_test_count = len(self.tokenizer(text)["input_ids"])
            if self_test_count > self.max_seq_length:
                raise AssertionError("Builder passed a silently truncated window")
            vectors[index, 0] = 3.0
            vectors[index, 1] = 4.0
        return vectors


def write_csv(path, rows, fields=None):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields or list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


class BuildBundleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.pdf = self.root / "source.pdf"
        self.pdf.write_bytes(b"synthetic PDF bytes for offline contract tests, not a clinical source")
        self.source_hash = builder.sha256_file(self.pdf)
        self.kb = self.root / "kb"
        self.kb.mkdir()
        self.original = "Use treatment only when condition X is present; avoid it when condition Y is present. " * 8
        self.base = {
            "chunk_id": "text_p001_001_01", "content_type": "text",
            "source_pdf": "/publisher/private/location/source.pdf",
            "source_pdf_sha256": self.source_hash, "page_number": "1",
            "retrieval_text": self.original,
        }
        write_csv(self.kb / "full_guideline_text_chunks.csv", [self.base])
        write_csv(self.kb / "full_guideline_structured_tables.csv", [], list(self.base))
        write_csv(self.kb / "full_guideline_figure_pages.csv", [{**self.base, "chunk_id": "page_001", "content_type": "figure_or_page"}])
        self.model = self.root / "snapshots" / builder.MODEL_REVISION
        self.model.mkdir(parents=True)
        (self.model / "config.json").write_text('{"model_type":"bert"}')
        (self.model / "README.md").write_text("Synthetic model card for fake encoder tests")
        (self.model / "LICENSE").write_text("Synthetic license fixture, not a redistributed model")
        blobs = self.root / "blobs"
        blobs.mkdir()
        weight = blobs / "weight"
        weight.write_bytes(b"fake test weights")
        (self.model / "model.safetensors").symlink_to(weight)
        # All model I/O is synthetic in these tests. Keep the real integrity
        # checking code active, but replace its expected pins with fixture pins.
        self.fixture_model_hashes = {path.relative_to(self.model).as_posix(): builder.sha256_file(path) for path in self.model.rglob("*") if path.is_file() and path.name != "LICENSE"}
        self.hash_patch = mock.patch.object(builder, "MODEL_FILE_SHA256", self.fixture_model_hashes)
        self.hash_patch.start()
        self.addCleanup(self.hash_patch.stop)
        self.output = self.root / "bundle"
        self.encoder_patch = mock.patch.object(builder, "_load_embedding_runtime", return_value=FakeEncoder())
        self.encoder_patch.start()
        self.addCleanup(self.encoder_patch.stop)

    def build_candidate(self, **kwargs):
        return builder.build_bundle(self.kb, self.pdf, self.model, self.output, candidate=True, **kwargs)

    def test_candidate_is_portable_hash_bound_and_preserves_original_text(self):
        manifest = self.build_candidate()
        self.assertEqual(manifest["status"], "review_candidate")
        self.assertEqual(manifest["record_count"], 1)
        self.assertGreater(manifest["unit_count"], 1)
        self.assertEqual(manifest["visual_record_count"], 0)
        self.assertFalse((self.output / ".incomplete").exists())
        self.assertNotIn("manifest.json", manifest["files"])
        files = {path.relative_to(self.output).as_posix() for path in self.output.rglob("*") if path.is_file() and path.name != "manifest.json"}
        self.assertEqual(files, set(manifest["files"]))
        records = builder._read_jsonl(self.output / "records.jsonl")
        self.assertEqual(records[0]["source_text"], self.original)
        self.assertEqual(records[0]["source_pdf"], self.base["source_pdf"])
        units = builder._read_jsonl(self.output / "units.jsonl")
        self.assertTrue(all(row["token_count"] <= 256 for row in units))
        self.assertFalse(any(path.is_symlink() for path in self.output.rglob("*")))
        self.assertEqual((self.output / "model/model.safetensors").read_bytes(), b"fake test weights")
        embeddings = np.load(self.output / "embeddings.npy", allow_pickle=False)
        self.assertEqual(embeddings.dtype, np.float32)
        np.testing.assert_allclose(np.linalg.norm(embeddings, axis=1), 1.0)
        self.assertEqual(len(builder._read_jsonl(self.output / "audit/page_previews_not_indexed.jsonl")), 1)
        with self.assertRaisesRegex(ValueError, "NOT a formal"):
            runtime.verify_bundle(self.output)
        moved = self.root / "different-machine-location"
        self.output.rename(moved)
        runtime.verify_bundle(moved, allow_candidate=True)

    def test_candidate_never_reads_visual_review_directory(self):
        with mock.patch.object(builder, "_load_approved_visuals", side_effect=AssertionError("must not read review")):
            self.build_candidate(review_dir=self.root / "does-not-exist")

    def test_figure_page_text_warning_is_page_scoped_without_changing_content(self):
        pages = (1, 2, 6, 7, 8, 9, 10, 16)
        rows = [{**self.base, "chunk_id": f"text_p{page:03d}_001_01", "page_number": str(page)} for page in pages]
        write_csv(self.kb / "full_guideline_text_chunks.csv", rows)
        records, _ = builder._load_base_records(self.kb, self.source_hash)
        for record in records:
            with self.subTest(page=record["page_number"]):
                self.assertEqual(record["source_text"], self.original)
                self.assertEqual(record["retrieval_text"], self.original)
                self.assertEqual(record["review_status"], "not_individually_reviewed")
                self.assertEqual(record["release_status"], "review_candidate")
                self.assertFalse(record.get("human_approved", False))
                if record["page_number"] in {2, 6, 8, 9, 16}:
                    self.assertEqual(record["source_layout_status"], "unverified_pdf_figure_page_text")
                    self.assertIn("may also contain ordinary prose", record["source_layout_note"])
                    self.assertIn("not human-approved visual evidence", record["source_layout_note"])
                else:
                    self.assertNotIn("source_layout_status", record)
                    self.assertNotIn("source_layout_note", record)

    def test_bibliography_records_preserved_without_index_units(self):
        rows = [
            {**self.base, "retrieval_text": "Preserve the clinical qualification. References 1. Author A. 2. Author B."},
            {**self.base, "chunk_id": "text_p002_001_01", "page_number": "2", "retrieval_text": "3. Bibliography continuation."},
        ]
        write_csv(self.kb / "full_guideline_text_chunks.csv", rows)
        self.build_candidate()
        records = builder._read_jsonl(self.output / "records.jsonl")
        units = builder._read_jsonl(self.output / "units.jsonl")
        self.assertEqual(records[1]["source_text"], rows[1]["retrieval_text"])
        self.assertEqual(records[1]["retrieval_text"], "")
        self.assertIs(records[1]["indexable"], False)
        self.assertNotIn(records[1]["record_id"], {unit["record_id"] for unit in units})
        runtime.verify_bundle(self.output, allow_candidate=True)

    def test_table_continuations_bind_all_pages_and_footnotes(self):
        rows = [{**self.base, "chunk_id": f"table_{page}", "content_type": "table", "page_number": str(page), "table_or_figure_id": "Table 9.2", "retrieval_text": f"Table 9.2 continued page {page}; source property and qualifying footnotes."} for page in range(11, 15)]
        write_csv(self.kb / "full_guideline_structured_tables.csv", rows)
        self.build_candidate()
        records = builder._read_jsonl(self.output / "records.jsonl")
        tables = [row for row in records if row["content_type"] == "table"]
        self.assertEqual(len({row["evidence_group_id"] for row in tables}), 1)
        identifiers = {row["record_id"] for row in tables}
        for row in tables:
            self.assertEqual(set(row["dependency_ids"]), identifiers - {row["record_id"]})
            self.assertEqual(row["structure_status"], "unverified_pdf_text_table")
            self.assertTrue(row["source_text"].endswith("qualifying footnotes."))

    def test_partial_table_9_2_group_fails_closed(self):
        write_csv(self.kb / "full_guideline_structured_tables.csv", [{**self.base, "chunk_id": "table_11", "content_type": "table", "page_number": "11", "table_or_figure_id": "Table 9.2"}])
        with self.assertRaisesRegex(ValueError, "all continuation pages"):
            self.build_candidate()
        self.assertFalse((self.output / "manifest.json").exists())

    def test_formal_release_fails_without_human_review_before_loading_model(self):
        with mock.patch.object(builder, "_load_embedding_runtime") as load:
            with self.assertRaisesRegex(ValueError, "Formal release requires"):
                builder.build_bundle(self.kb, self.pdf, self.model, self.output)
            load.assert_not_called()
        self.assertTrue((self.output / ".incomplete").is_file())
        self.assertFalse((self.output / "manifest.json").exists())

    def test_existing_output_is_never_overwritten(self):
        self.output.mkdir()
        original = self.output / "manifest.json"
        original.write_text("pre-existing user data")
        with self.assertRaises(FileExistsError):
            self.build_candidate()
        self.assertEqual(original.read_text(), "pre-existing user data")
        self.assertFalse((self.output / ".incomplete").exists())

    def test_output_inside_any_input_directory_rejected_before_writing(self):
        review = self.root / "review"
        for source in (self.kb, self.model, review / "frozen_inputs"):
            destination = source / "unsafe-bundle"
            with self.subTest(destination=destination), self.assertRaisesRegex(ValueError, "overlaps an input"):
                builder.build_bundle(self.kb, self.pdf, self.model, destination, review_dir=review, candidate=True)
            self.assertFalse(destination.exists())

    def test_output_containing_source_asset_rejected_before_writing(self):
        destination = self.root / "new-output"
        for pdf, ledger in ((destination / "source.pdf", None), (self.pdf, destination / "human.jsonl")):
            with self.subTest(pdf=pdf, ledger=ledger), self.assertRaisesRegex(ValueError, "contains an input asset"):
                builder.build_bundle(self.kb, pdf, self.model, destination, candidate=True, ledger_path=ledger)
            self.assertFalse(destination.exists())

    def test_output_via_symlink_into_model_rejected_before_writing(self):
        alias = self.root / "model-alias"
        alias.symlink_to(self.model, target_is_directory=True)
        destination = alias / "unsafe-bundle"
        with self.assertRaisesRegex(ValueError, "overlaps an input"):
            builder.build_bundle(self.kb, self.pdf, self.model, destination, candidate=True)
        self.assertFalse(destination.exists())

    def test_complete_text_duplicate_preserved_but_not_indexed(self):
        table = {**self.base, "chunk_id": "table_1", "content_type": "table", "table_or_figure_id": "Synthetic table", "retrieval_text": "Table introduction. " + self.original + " Essential table footnotes."}
        write_csv(self.kb / "full_guideline_structured_tables.csv", [table])
        self.build_candidate()
        records = builder._read_jsonl(self.output / "records.jsonl")
        units = builder._read_jsonl(self.output / "units.jsonl")
        self.assertEqual(records[0]["source_text"], self.original)
        self.assertEqual(records[0]["source_pdf_sha256"], self.source_hash)
        self.assertIs(records[0]["indexable"], False)
        self.assertEqual(records[0]["retrieval_text"], "")
        self.assertEqual(records[0]["retained_table_id"], records[1]["record_id"])
        self.assertEqual({unit["record_id"] for unit in units}, {records[1]["record_id"]})
        log = builder._read_jsonl(self.output / "audit/cleaning_log.jsonl")
        self.assertEqual(log[0]["reason"], "duplicate_text_fully_covered_by_table")
        self.assertEqual(log[0]["retained_table_id"], records[1]["record_id"])
        runtime.verify_bundle(self.output, allow_candidate=True)

    def test_pdf_fingerprint_mismatch_fails_closed(self):
        self.pdf.write_bytes(b"changed PDF")
        with self.assertRaisesRegex(ValueError, "fingerprint mismatch"):
            self.build_candidate()
        self.assertTrue((self.output / ".incomplete").is_file())
        self.assertFalse((self.output / "manifest.json").exists())

    def test_wrong_model_revision_rejected(self):
        different = self.model.with_name("not-the-pinned-revision")
        self.model.rename(different)
        with self.assertRaisesRegex(ValueError, "snapshot directory"):
            builder.build_bundle(self.kb, self.pdf, different, self.output, candidate=True)

    def test_right_directory_name_with_wrong_weights_rejected(self):
        (self.root / "blobs/weight").write_bytes(b"changed model weights under correct revision name")
        with self.assertRaisesRegex(ValueError, "model content mismatch: model.safetensors"):
            self.build_candidate()
        self.assertFalse((self.output / "manifest.json").exists())

    def test_right_weights_with_changed_tokenizer_config_rejected(self):
        (self.model / "config.json").write_text('{"model_type":"changed"}')
        with self.assertRaisesRegex(ValueError, "model content mismatch: config.json"):
            self.build_candidate()

    def test_candidate_cannot_claim_imported_human_ledger(self):
        with self.assertRaisesRegex(ValueError, "only to a formal build"):
            self.build_candidate(ledger_path=self.root / "human.jsonl")

    def test_supplemental_license_copied_without_editing_snapshot(self):
        (self.model / "LICENSE").unlink()
        license_file = self.root / "official-license-fixture.txt"
        license_file.write_text("supplemental license fixture")
        with mock.patch.object(builder, "SUPPLEMENTAL_LICENSE", license_file):
            self.build_candidate()
        self.assertEqual((self.output / "model/LICENSE").read_text(), license_file.read_text())
        self.assertFalse((self.model / "LICENSE").exists())

    def test_invalid_embeddings_leave_only_incomplete_output(self):
        encoder = FakeEncoder()
        encoder.encode = lambda texts, **kwargs: np.full((len(texts), 384), np.nan, dtype=np.float32)
        with mock.patch.object(builder, "_load_embedding_runtime", return_value=encoder):
            with self.assertRaisesRegex(ValueError, "finite-value"):
                self.build_candidate()
        self.assertTrue((self.output / ".incomplete").is_file())
        self.assertFalse((self.output / "manifest.json").exists())

    def test_formal_build_invokes_review_gate_and_preserves_visual_provenance(self):
        review = self.root / "review"
        review.mkdir()
        image = self.root / "source.png"
        image.write_bytes(b"synthetic source image bytes")
        visual = {
            "record_id": "visual-record-1", "_record_type": "action",
            "action": "Synthetic conditional action.",
            "source_pdf": self.base["source_pdf"], "source_pdf_sha256": self.source_hash,
            "page_number": 1, "evidence_text": "Synthetic source evidence with condition X.",
            "image_path": str(image), "image_sha256": builder.sha256_file(image),
            "v1_dependency_ids": [], "reviewer": "human-test-reviewer",
            "reviewed_at": "2026-09-28T12:00:00+00:00",
            "effective_content_sha256": "a" * 64,
        }
        builder._write_jsonl(review / "canonical_records.jsonl", [visual])
        ledger = [{
            "record_id": visual["record_id"], "final_disposition": "approved",
            "human_reviewer": "synthetic-test-signer", "human_reviewed_at": visual["reviewed_at"],
            "human_notes": "Synthetic unit-test approval fixture, not real clinical review.",
            "reviewed_content_sha256": review_helpers.record_fingerprint(visual),
            "group_id": "synthetic-group", "dependency_ids": [], "dependency_reviewed": True,
        }]
        group = {
            "group_id": "synthetic-group", "record_ids": [visual["record_id"]],
            "group_status": "complete", "human_confirmed": True,
            "human_reviewer": "synthetic-test-signer", "human_reviewed_at": visual["reviewed_at"],
        }
        group["reviewed_group_sha256"] = review_helpers.group_fingerprint(group, [visual])
        builder._write_jsonl(review / "review_ledger.jsonl", ledger)
        builder._write_json(review / "group_reviews.json", {"groups": [group], "expected_record_ids": [visual["record_id"]]})
        builder._write_json(review / "review_manifest.json", {"expected_record_ids": [visual["record_id"]], "n_records": 1, "n_visual_assets": 1})
        builder._write_json(review / "source_inventory.json", {"files": []})
        # The actual stdlib audit runs; only the raw Step 07 reconstruction is
        # outside this synthetic builder test and covered by review-module tests.
        gate = mock.Mock(side_effect=review_helpers.assert_review_audit)
        adapter = lambda row: {
                "content_type": "visual_action", "retrieval_text": runtime.expected_visual_retrieval_text(row),
                "human_approved": True, "approved_by": row["reviewer"],
                "approved_at": row["reviewed_at"],
            }
        with mock.patch.object(review_helpers, "verify_review_inputs", return_value={}) as inputs, mock.patch.object(review_helpers, "assert_release_ready", gate), mock.patch.object(review_helpers, "to_retrieval_record", adapter):
            manifest = builder.build_bundle(self.kb, self.pdf, self.model, self.output, review_dir=review)
        inputs.assert_called_once_with(review)
        gate.assert_called_once()
        self.assertEqual(manifest["status"], "released")
        self.assertEqual(manifest["visual_record_count"], 1)
        records = builder._read_jsonl(self.output / "records.jsonl")
        self.assertEqual(records[1]["source_text"], visual["evidence_text"])
        self.assertEqual(records[1]["image_path"], str(image))
        sources = json.loads((self.output / "sources.json").read_text())
        self.assertEqual(sources[self.source_hash]["images"][0]["sha256"], visual["image_sha256"])
        runtime.verify_bundle(self.output)

    def test_formal_gate_rejection_propagates_without_manifest(self):
        review = self.root / "review"
        review.mkdir()
        builder._write_jsonl(review / "canonical_records.jsonl", [{"record_id": "synthetic-record"}])
        builder._write_jsonl(review / "review_ledger.jsonl", [])
        builder._write_json(review / "group_reviews.json", {"expected_record_ids": ["synthetic-record"], "groups": []})
        builder._write_json(review / "review_manifest.json", {"expected_record_ids": ["synthetic-record"]})
        builder._write_json(review / "source_inventory.json", {"files": []})
        adapter = types.SimpleNamespace(verify_review_inputs=mock.Mock(return_value={}), assert_release_ready=mock.Mock(side_effect=ValueError("pending human group review")))
        with mock.patch.dict(sys.modules, {"kb_v1_review": adapter}):
            with self.assertRaisesRegex(ValueError, "pending human"):
                builder.build_bundle(self.kb, self.pdf, self.model, self.output, review_dir=review)
        self.assertFalse((self.output / "manifest.json").exists())
        self.assertTrue((self.output / ".incomplete").is_file())

    def test_group_inventory_cannot_drop_an_entire_original_candidate(self):
        review = self.root / "review"
        review.mkdir()
        builder._write_jsonl(review / "canonical_records.jsonl", [{"record_id": "kept"}])
        builder._write_jsonl(review / "review_ledger.jsonl", [])
        builder._write_json(review / "group_reviews.json", {"expected_record_ids": ["kept"], "groups": []})
        builder._write_json(review / "review_manifest.json", {"expected_record_ids": ["kept", "dropped-whole-asset"]})
        gate = mock.Mock()
        with mock.patch.object(review_helpers, "verify_review_inputs", return_value={}), mock.patch.object(review_helpers, "assert_release_ready", gate):
            with self.assertRaisesRegex(ValueError, "Group inventory differs"):
                builder._load_approved_visuals(review)
        gate.assert_not_called()

    def test_frozen_input_verification_failure_precedes_human_gate(self):
        gate = mock.Mock()
        with mock.patch.object(review_helpers, "verify_review_inputs", side_effect=ValueError("frozen input fingerprint mismatch")), mock.patch.object(review_helpers, "assert_release_ready", gate):
            with self.assertRaisesRegex(ValueError, "frozen input fingerprint"):
                builder._load_approved_visuals(self.root / "review")
        gate.assert_not_called()

    def test_explicit_imported_ledger_is_used_and_copied_to_audit(self):
        # Reuse the meaningful positive fixture, then replace only the maintainer
        # input selection in a second build. No real human approval is fabricated.
        self.test_formal_build_invokes_review_gate_and_preserves_visual_provenance()
        review = self.root / "review"
        imported = self.root / "human-imported.jsonl"
        imported.write_bytes((review / "review_ledger.jsonl").read_bytes())
        pending = builder._read_jsonl(review / "review_ledger.jsonl")
        pending[0]["final_disposition"] = "pending"
        builder._write_jsonl(review / "review_ledger.jsonl", pending)
        destination = self.root / "bundle-with-imported-ledger"
        def to_retrieval(row):
            return {"content_type": "visual_action", "retrieval_text": runtime.expected_visual_retrieval_text(row), "human_approved": True, "approved_by": row["reviewer"], "approved_at": row["reviewed_at"]}
        with mock.patch.object(review_helpers, "verify_review_inputs", return_value={}), mock.patch.object(review_helpers, "assert_release_ready", side_effect=review_helpers.assert_review_audit), mock.patch.object(review_helpers, "to_retrieval_record", to_retrieval):
            builder.build_bundle(self.kb, self.pdf, self.model, destination, review_dir=review, ledger_path=imported)
        self.assertEqual((destination / "audit/review/review_ledger.jsonl").read_bytes(), imported.read_bytes())
        self.assertEqual(builder._read_jsonl(review / "review_ledger.jsonl")[0]["final_disposition"], "pending")
        runtime.verify_bundle(destination)


class CleaningTests(unittest.TestCase):
    def record(self, identifier, text, page=1):
        return {"record_id": identifier, "content_type": "text", "source_id": "source", "page_number": page, "source_text": text, "retrieval_text": text}

    def test_mixed_clinical_block_is_not_deleted(self):
        clinical = "Use therapy only if eGFR exceeds 30; avoid in condition Y."
        text = "Readers may use this work for educational, noncommercial purposes if properly cited and unaltered. " + clinical
        rows = [self.record("text_01", text)]
        log = builder.clean_retrieval_text(rows)
        self.assertEqual(rows[0]["source_text"], text)
        self.assertEqual(rows[0]["retrieval_text"], clinical)
        self.assertEqual(len(log), 1)

    def test_reference_boundary_preserves_clinical_prefix_and_original_records(self):
        clinical = "Do not use in condition Y. "
        text = clinical + "References 1. Author A. Title. 2. Author B. Title."
        rows = [self.record("text_p026_001_03", text, 26), self.record("text_p026_001_04", "Author continuation", 26), self.record("text_p027_001_01", "3. Author C.", 27)]
        builder.clean_retrieval_text(rows)
        self.assertEqual(rows[0]["retrieval_text"], clinical.strip())
        self.assertEqual(rows[0]["source_text"], text)
        self.assertEqual(rows[1]["retrieval_text"], "")
        self.assertEqual(rows[2]["retrieval_text"], "")
        self.assertEqual(len(rows), 3)

    def test_ordinary_reference_mention_is_not_a_boundary(self):
        text = "References to treatment evidence should preserve all conditions."
        rows = [self.record("text_01", text), self.record("text_02", "Later clinical content.", 2)]
        self.assertEqual(builder.clean_retrieval_text(rows), [])
        self.assertTrue(all(row["indexable"] for row in rows))

    def test_ambiguous_bibliography_requires_manual_review(self):
        rows = [self.record("text_01", "References 1. A. 2. B.", 1), self.record("text_02", "References 1. C. 2. D.", 2)]
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            builder.clean_retrieval_text(rows)

    def test_exact_administrative_opening_and_split_prefix_preserve_mixed_clinical_text(self):
        first = "9. PHARMACOLOGIC APPROACHES TO GLYCEMIC TREATMENT *A complete list of members. Suggested citation: ADA. © 2025 by the American Diabetes Association. similar technologies without prior written permission. More information is available at https:// di"
        clinical = "The American Diabetes Association (ADA) introduction. PHARMACOLOGIC THERAPY FOR ADULTS WITH TYPE 1 DIABETES Recommendations 9.1 Preserve this clinical recommendation."
        second = "hnologies without prior written permission. More information is available at https:// diabetesjournals.org/journals/pages/license . " + clinical
        rows = [self.record("source:text_p001_001_01", first), self.record("source:text_p001_001_02", second)]
        builder.clean_retrieval_text(rows)
        self.assertEqual(rows[0]["retrieval_text"], "")
        self.assertEqual(rows[0]["source_text"], first)
        self.assertEqual(rows[1]["retrieval_text"], clinical)
        self.assertEqual(rows[1]["source_text"], second)

    def test_split_license_fragment_not_removed_without_confirmed_previous_chunk(self):
        text = "hnologies without prior written permission. https:// diabetesjournals.org/journals/pages/license . The American Diabetes Association (ADA) clinical text."
        rows = [self.record("source:text_p001_001_02", text)]
        self.assertEqual(builder.clean_retrieval_text(rows), [])
        self.assertEqual(rows[0]["retrieval_text"], text)


class TableContainmentTests(unittest.TestCase):
    clinical = "Use treatment only when all eligibility conditions hold; retain renal limitations and footnotes before applying any medication recommendation. " * 2

    def pair(self):
        text = {"record_id": "text", "content_type": "text", "source_id": "source", "page_number": 11, "source_text": self.clinical, "retrieval_text": self.clinical, "indexable": True}
        table_text = "Table heading. " + self.clinical + " Additional table footnotes."
        table = {**text, "record_id": "table", "content_type": "table", "source_text": table_text, "retrieval_text": table_text, "dependency_ids": ["table-page-14"]}
        return [text, table]

    def test_whitespace_only_variations_match_and_preserve_sources_and_dependencies(self):
        rows = self.pair()
        rows[0]["source_text"] = rows[0]["source_text"].replace(" ", "\n\t")
        original = rows[0]["source_text"]
        log = builder.deduplicate_text_covered_by_tables(rows)
        self.assertEqual(len(log), 1)
        self.assertEqual(rows[0]["source_text"], original)
        self.assertEqual(rows[1]["dependency_ids"], ["table-page-14"])
        self.assertTrue(rows[1]["indexable"])

    def test_partial_overlap_or_distinct_condition_is_never_removed(self):
        for suffix in (" Additional clinical qualification not present in the table.", " Do not use in condition Y."):
            rows = self.pair()
            rows[0]["source_text"] += suffix
            rows[0]["retrieval_text"] = rows[0]["source_text"]
            self.assertEqual(builder.deduplicate_text_covered_by_tables(rows), [])
            self.assertTrue(rows[0]["indexable"])

    def test_same_page_and_same_pdf_are_both_required(self):
        for field, value in (("source_id", "other-source"), ("page_number", 12)):
            rows = self.pair()
            rows[1][field] = value
            self.assertEqual(builder.deduplicate_text_covered_by_tables(rows), [])

    def test_short_case_changed_or_nonretained_table_never_triggers_removal(self):
        for mutation in ("short", "case", "audit_only", "cleaned_table"):
            rows = self.pair()
            if mutation == "short":
                rows[0]["source_text"] = "Use treatment only when"
            elif mutation == "case":
                rows[0]["source_text"] = self.clinical.upper()
            elif mutation == "audit_only":
                rows[1]["indexable"] = False
            else:
                rows[1]["retrieval_text"] = "Original table content no longer indexed."
            with self.subTest(mutation=mutation):
                self.assertEqual(builder.deduplicate_text_covered_by_tables(rows), [])
                self.assertTrue(rows[0]["indexable"])

    def test_real_baseline_deduplicates_table_overlap_without_losing_records(self):
        kb = PIPELINE_DIR / "outputs/full_guideline_kb"
        if not (kb / "full_guideline_text_chunks.csv").is_file():
            self.skipTest("Optional real repository fixture not present")
        source_hash = builder._read_csv(kb / "full_guideline_text_chunks.csv")[0]["source_pdf_sha256"]
        records, _ = builder._load_base_records(kb, source_hash)
        original_sources = {row["record_id"]: row["source_text"] for row in records}
        builder.clean_retrieval_text(records)
        log = builder.deduplicate_text_covered_by_tables(records)
        self.assertEqual(original_sources, {row["record_id"]: row["source_text"] for row in records})
        by_id = {row["record_id"]: row for row in records}
        self.assertTrue(any(event["record_id"].endswith(":text_p011_001_01") and event["retained_table_id"].endswith(":table_tbl_0003") for event in log))
        for event in log:
            text, table = by_id[event["record_id"]], by_id[event["retained_table_id"]]
            self.assertEqual((text["source_id"], text["page_number"]), (table["source_id"], table["page_number"]))
            self.assertIn("".join(text["source_text"].split()), "".join(table["source_text"].split()))
            self.assertTrue(table["indexable"])
            self.assertFalse(text["indexable"])


if __name__ == "__main__":
    unittest.main()
