"""Offline unit and portable integration tests; no clinical correctness claims."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np


PIPELINE_DIR = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("kb_v1_runtime", PIPELINE_DIR / "kb_v1_runtime.py")
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)


class FakeTokenizer:
    def num_special_tokens_to_add(self, pair=False):
        return 2

    def __call__(self, text, add_special_tokens=True, truncation=False, return_offsets_mapping=False):
        assert truncation is False
        matches = list(re.finditer(r"\S+", text))
        result = {"input_ids": list(range(len(matches) + (2 if add_special_tokens else 0)))}
        if return_offsets_mapping:
            result["offset_mapping"] = [(match.start(), match.end()) for match in matches]
        return result


class FakeModel:
    tokenizer = FakeTokenizer()

    def encode(self, sentences, **kwargs):
        return np.array([[1.0, 0.0] if "kidney" in sentence else [0.0, 1.0] for sentence in sentences], dtype="float32")


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def make_bundle(path, released=False):
    path.mkdir()
    (path / "model").mkdir()
    (path / "model" / "config.json").write_text("{}", encoding="utf-8")
    (path / "sources").mkdir()
    (path / "sources" / "test.pdf").write_bytes(b"synthetic PDF fixture, not clinical evidence")
    source_hash = runtime.sha256_file(path / "sources" / "test.pdf")
    sources = {"ada-test": {"title": "Synthetic test guideline", "year": 2026, "pdf_sha256": source_hash, "relative_path": "sources/test.pdf", "images": []}}
    (path / "sources.json").write_text(json.dumps(sources), encoding="utf-8")
    records = [
        {"record_id": "a", "content_type": "text", "source_id": "ada-test", "page_number": 1, "source_text": "kidney source context", "retrieval_text": "kidney source context", "review_status": "not_applicable", "release_status": "text_candidate", "dependency_ids": []},
        {"record_id": "b", "content_type": "text", "source_id": "ada-test", "page_number": 2, "source_text": "glucose source context", "retrieval_text": "glucose source context", "review_status": "not_applicable", "release_status": "text_candidate", "dependency_ids": []},
    ]
    if released:
        records[1].update(content_type="visual_node", review_status="approved", release_status="released", human_approved=True, approved_by="Synthetic test reviewer", approved_at="2026-09-28T12:00:00+00:00", effective_content_sha256="0" * 64)
    units = [{"unit_id": "a-1", "record_id": "a", "text": "kidney source context"}, {"unit_id": "b-1", "record_id": "b", "text": "glucose source context"}]
    write_jsonl(path / "records.jsonl", records)
    write_jsonl(path / "units.jsonl", units)
    np.save(path / "embeddings.npy", np.eye(2, dtype="float32"), allow_pickle=False)
    manifest = {"schema_version": "ada-kb-v1", "status": "released" if released else "review_candidate", "kb_version": "synthetic-test-only", "record_count": 2, "unit_count": 2, "embedding": {"model_id": "synthetic-fixture", "revision": "fixed-test-revision", "dimension": 2, "normalize": True, "max_seq_length": 6, "model_dir": "model"}, "vector_order": "units.jsonl", "files": {}}
    (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    rehash(path)
    return records, units


def rehash(path):
    manifest = json.loads((path / "manifest.json").read_text())
    manifest["files"] = {entry.relative_to(path).as_posix(): runtime.sha256_file(entry) for entry in path.rglob("*") if entry.is_file() and entry.name != "manifest.json"}
    (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def attach_synthetic_review_audit(path):
    specification = importlib.util.spec_from_file_location("runtime_audit_fixtures", PIPELINE_DIR / "kb_v1_review.py")
    review = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(review)
    records = runtime.read_jsonl(path / "records.jsonl")
    sources = json.loads((path / "sources.json").read_text())
    source = sources["ada-test"]
    source_id = source["pdf_sha256"]
    for row in records:
        row["source_id"] = source_id
    (path / "sources.json").write_text(json.dumps({source_id: source}))
    canonical = [{"record_id": "b", "_record_type": "node", "node_id": "b", "condition": "Synthetic condition only", "source_pdf_sha256": source_id, "page_number": 2, "evidence_text": records[1]["source_text"]}]
    fingerprint = review.record_fingerprint(canonical[0])
    records[1].update(canonical[0])
    records[1]["v1_reviewed_content_sha256"] = fingerprint
    records[1]["retrieval_text"] = runtime.expected_visual_retrieval_text(canonical[0])
    units = runtime.read_jsonl(path / "units.jsonl")
    units[1]["text"] = records[1]["retrieval_text"]
    write_jsonl(path / "units.jsonl", units)
    ledger = [{"record_id": "b", "group_id": "synthetic-page", "final_disposition": "approved", "human_reviewer": "SYNTHETIC TEST", "human_reviewed_at": "2026-09-28T12:00:00+00:00", "human_notes": "Synthetic unit-test approval; not real clinical evidence", "reviewed_content_sha256": fingerprint, "dependency_reviewed": True, "dependency_ids": []}]
    group = {"group_id": "synthetic-page", "record_ids": ["b"], "group_status": "complete", "human_confirmed": True, "human_reviewer": "SYNTHETIC TEST", "human_reviewed_at": "2026-09-28T12:00:00+00:00"}
    group["reviewed_group_sha256"] = review.group_fingerprint(group, canonical)
    review_dir = path / "audit" / "review"
    review_dir.mkdir(parents=True)
    write_jsonl(review_dir / "canonical_records.jsonl", canonical)
    write_jsonl(review_dir / "review_ledger.jsonl", ledger)
    (review_dir / "group_reviews.json").write_text(json.dumps({"expected_record_ids": ["b"], "groups": [group]}))
    (review_dir / "review_manifest.json").write_text(json.dumps({"expected_record_ids": ["b"]}))
    write_jsonl(path / "records.jsonl", records)
    rehash(path)


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.bundle = self.root / "\u4e2d\u6587 \u77e5\u8bc6\u5e93"
        self.records, self.units = make_bundle(self.bundle)

    def tearDown(self):
        self.temporary.cleanup()

    def verify(self):
        return runtime.verify_bundle(self.bundle, allow_candidate=True)

    def test_candidate_requires_explicit_opt_in(self):
        with self.assertRaisesRegex(ValueError, "NOT a formal"):
            runtime.verify_bundle(self.bundle)
        self.assertEqual(self.verify()["status"], "review_candidate")

    def test_hash_tampering_rejected(self):
        with (self.bundle / "records.jsonl").open("a") as handle:
            handle.write("\n")
        with self.assertRaisesRegex(ValueError, "Checksum mismatch"):
            self.verify()

    def test_missing_model_hash_rejected(self):
        (self.bundle / "model" / "tokenizer.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "not covered"):
            self.verify()

    def test_model_symlinks_rejected(self):
        if not hasattr(os, "symlink"):
            self.skipTest("Symlink creation not available")
        target = self.root / "external.json"
        target.write_text("{}")
        try:
            (self.bundle / "model" / "link.json").symlink_to(target)
        except OSError:
            self.skipTest("Symlink creation unavailable to current user")
        with self.assertRaisesRegex(ValueError, "symlinks"):
            self.verify()

    def test_paths_reject_absolute_traversal_and_windows_forms(self):
        for value in ("../outside", "/tmp/a", "C:/a", "a\\b", "./manifest.json", "model//config.json"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                runtime.safe_bundle_path(self.bundle, value)

    def test_duplicate_record_and_unit_ids_rejected(self):
        for name, rows, field in (("records.jsonl", self.records, "record_id"), ("units.jsonl", self.units, "unit_id")):
            original = (self.bundle / name).read_text()
            duplicate = [dict(row) for row in rows]
            duplicate[1][field] = duplicate[0][field]
            write_jsonl(self.bundle / name, duplicate)
            rehash(self.bundle)
            with self.assertRaisesRegex(ValueError, f"Duplicate {field}"):
                self.verify()
            (self.bundle / name).write_text(original)
            rehash(self.bundle)

    def test_missing_dependency_rejected(self):
        self.records[0]["dependency_ids"] = ["missing"]
        write_jsonl(self.bundle / "records.jsonl", self.records)
        rehash(self.bundle)
        with self.assertRaisesRegex(ValueError, "Unresolved evidence dependency"):
            self.verify()

    def test_visual_in_candidate_still_requires_human_approval(self):
        self.records[1]["content_type"] = "visual_node"
        write_jsonl(self.bundle / "records.jsonl", self.records)
        rehash(self.bundle)
        with self.assertRaisesRegex(ValueError, "Unreleased visual"):
            self.verify()

    def test_released_text_only_bundle_rejected(self):
        manifest = json.loads((self.bundle / "manifest.json").read_text())
        manifest["status"] = "released"
        (self.bundle / "manifest.json").write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "requires at least one released visual"):
            self.verify()

    def test_release_metadata_required(self):
        another = self.root / "synthetic released"
        records, _ = make_bundle(another, released=True)
        with self.assertRaisesRegex(ValueError, "complete hash-bound review ledger"):
            runtime.verify_bundle(another)
        records[1]["approved_at"] = "2026-09-28T12:00:00"
        write_jsonl(another / "records.jsonl", records)
        rehash(another)
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            runtime.verify_bundle(another)

    def test_formal_bundle_rechecks_human_audit_and_content_fingerprints(self):
        another = self.root / "synthetic release audit"
        make_bundle(another, released=True)
        attach_synthetic_review_audit(another)
        self.assertEqual(runtime.verify_bundle(another)["status"], "released")
        canonical_path = another / "audit" / "review" / "canonical_records.jsonl"
        rows = runtime.read_jsonl(canonical_path)
        rows[0]["condition"] = "Changed after synthetic review"
        write_jsonl(canonical_path, rows)
        rehash(another)
        with self.assertRaisesRegex(ValueError, "stale or missing"):
            runtime.verify_bundle(another)

    def test_published_visual_source_cannot_diverge_from_human_review(self):
        another = self.root / "synthetic source mismatch"
        make_bundle(another, released=True)
        attach_synthetic_review_audit(another)
        rows = runtime.read_jsonl(another / "records.jsonl")
        rows[1]["source_text"] = "Different, unreviewed evidence"
        write_jsonl(another / "records.jsonl", rows)
        rehash(another)
        with self.assertRaisesRegex(ValueError, "source text differs"):
            runtime.verify_bundle(another)

    def test_published_visual_retrieval_and_clinical_fields_are_bound(self):
        for field in ("retrieval_text", "condition"):
            with self.subTest(field=field):
                another = self.root / ("synthetic mutation " + field)
                make_bundle(another, released=True)
                attach_synthetic_review_audit(another)
                rows = runtime.read_jsonl(another / "records.jsonl")
                rows[1][field] = "Different, unreviewed clinical material"
                write_jsonl(another / "records.jsonl", rows)
                rehash(another)
                with self.assertRaisesRegex(ValueError, "differs from reviewed evidence"):
                    runtime.verify_bundle(another)

    def test_group_inventory_must_match_frozen_review_manifest(self):
        another = self.root / "synthetic missing original ID"
        make_bundle(another, released=True)
        attach_synthetic_review_audit(another)
        review_manifest = another / "audit" / "review" / "review_manifest.json"
        review_manifest.write_text(json.dumps({"expected_record_ids": ["b", "missing-original-record"]}))
        rehash(another)
        with self.assertRaisesRegex(ValueError, "Group expected IDs differ"):
            runtime.verify_bundle(another)

    def test_unit_text_cannot_escape_its_parent(self):
        self.units[0]["text"] = "Material that was not reviewed or encoded"
        write_jsonl(self.bundle / "units.jsonl", self.units)
        rehash(self.bundle)
        with self.assertRaisesRegex(ValueError, "not a span"):
            self.verify()

    def test_declared_unit_offsets_must_match_parent(self):
        self.units[0].update(start_char=1, end_char=3)
        write_jsonl(self.bundle / "units.jsonl", self.units)
        rehash(self.bundle)
        with self.assertRaisesRegex(ValueError, "offsets do not match"):
            self.verify()

    def test_audit_only_records_are_preserved_but_cannot_be_indexed(self):
        audit = {**self.records[0], "record_id": "audit-only", "indexable": False, "retrieval_text": "", "source_text": "Synthetic bibliography text preserved for audit"}
        write_jsonl(self.bundle / "records.jsonl", self.records + [audit])
        manifest_path = self.bundle / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["record_count"] = 3
        manifest_path.write_text(json.dumps(manifest))
        rehash(self.bundle)
        self.assertEqual(self.verify()["record_count"], 3)
        modified_units = [self.units[0], {**self.units[1], "record_id": "audit-only"}]
        write_jsonl(self.bundle / "units.jsonl", modified_units)
        rehash(self.bundle)
        with self.assertRaisesRegex(ValueError, "audit-only records must not be indexed"):
            self.verify()

    def test_shape_dtype_finiteness_and_norms_rejected(self):
        for vectors, message in ((np.eye(3, dtype="float32"), "shape"), (np.eye(2, dtype="float64"), "float32"), (np.array([[np.nan, 0], [0, 1]], dtype="float32"), "non-finite"), (np.array([[2, 0], [0, 1]], dtype="float32"), "unit norm")):
            with self.subTest(message=message):
                np.save(self.bundle / "embeddings.npy", vectors, allow_pickle=False)
                rehash(self.bundle)
                with self.assertRaisesRegex(ValueError, message):
                    self.verify()

    def test_source_pdf_hash_binding_required(self):
        sources = json.loads((self.bundle / "sources.json").read_text())
        sources["ada-test"]["pdf_sha256"] = "1" * 64
        (self.bundle / "sources.json").write_text(json.dumps(sources))
        rehash(self.bundle)
        with self.assertRaisesRegex(ValueError, "hash-bound"):
            self.verify()

    def test_candidate_retrieval_and_relative_source_link_outside_cwd(self):
        cases_path = self.root / "cases.csv"
        cases_path.write_text("case_id,vignette_text\nC1,kidney disease\nC1,kidney disease\n", encoding="utf-8-sig")
        output = self.root / "\u4e2d\u6587 \u8f93\u51fa" / "run 1"
        original = Path.cwd()
        try:
            os.chdir(self.root)
            with patch.object(runtime, "load_embedding_model", return_value=FakeModel()):
                stats = runtime.retrieve_cases(self.bundle, cases_path, output, allow_candidate=True)
        finally:
            os.chdir(original)
        self.assertEqual(stats["case_count"], 1)
        evidence = json.loads((output / "evidence.jsonl").read_text())
        self.assertEqual(evidence["evidence"][0]["record_id"], "a")
        self.assertEqual(evidence["evidence"][0]["source"]["relative_path"], "sources/test.pdf")
        markdown = (output / "evidence.md").read_text()
        self.assertIn("REVIEW CANDIDATE", markdown)
        self.assertIn("../../\u4e2d\u6587 \u77e5\u8bc6\u5e93/sources/test.pdf#page=1", markdown)
        with self.assertRaises(FileExistsError):
            runtime.retrieve_cases(self.bundle, cases_path, output, allow_candidate=True)

    def test_public_evidence_does_not_expose_historical_machine_paths(self):
        record = {**self.records[0], "source_pdf": "/Users/private-user/source.pdf", "image_path": "/Users/private-user/tile.png", "source_json": "/Users/private-user/raw.json"}
        exported = runtime._public_evidence(record)
        self.assertNotIn("source_pdf", exported)
        self.assertNotIn("image_path", exported)
        self.assertNotIn("source_json", exported)
        self.assertEqual(exported["source_text"], record["source_text"])

    def test_unverified_table_warning_survives_json_and_markdown(self):
        self.records[0].update(content_type="table", structure_status="unverified_pdf_text_table", source_text_scope="Source table text; row/column relationships are not verified")
        write_jsonl(self.bundle / "records.jsonl", self.records)
        rehash(self.bundle)
        cases = self.root / "table-cases.csv"
        cases.write_text("case_id,vignette_text\nC1,kidney\n")
        output = self.root / "table-output"
        with patch.object(runtime, "load_embedding_model", return_value=FakeModel()):
            runtime.retrieve_cases(self.bundle, cases, output, top_k=1, allow_candidate=True)
        evidence = json.loads((output / "evidence.jsonl").read_text())["evidence"][0]
        self.assertEqual(evidence["structure_status"], "unverified_pdf_text_table")
        self.assertIn("row/column relationships are not verified", evidence["source_text_scope"])
        self.assertIn("unverified_pdf_text_table", (output / "evidence.md").read_text())

    def test_figure_page_warning_survives_without_altering_retrieval(self):
        cases = self.root / "figure-cases.csv"
        cases.write_text("case_id,vignette_text\nC1,kidney\n")
        baseline_output = self.root / "without-layout-metadata"
        with patch.object(runtime, "load_embedding_model", return_value=FakeModel()):
            runtime.retrieve_cases(self.bundle, cases, baseline_output, allow_candidate=True)
        baseline = json.loads((baseline_output / "evidence.jsonl").read_text())["evidence"]
        self.records[0].update(source_layout_status="unverified_pdf_figure_page_text", source_layout_note="This page may also contain ordinary prose; PDF text is not human-approved visual evidence.")
        write_jsonl(self.bundle / "records.jsonl", self.records)
        rehash(self.bundle)
        output = self.root / "with-layout-metadata"
        with patch.object(runtime, "load_embedding_model", return_value=FakeModel()):
            runtime.retrieve_cases(self.bundle, cases, output, allow_candidate=True)
        evidence = json.loads((output / "evidence.jsonl").read_text())["evidence"]
        self.assertEqual(evidence[0]["source_layout_status"], "unverified_pdf_figure_page_text")
        self.assertIn("not human-approved", evidence[0]["source_layout_note"])
        self.assertNotIn("source_layout_status", evidence[1])
        for before, after in zip(baseline, evidence):
            for field in ("record_id", "rank", "similarity_score", "source_text", "retrieval_text", "review_status", "release_status"):
                self.assertEqual(before[field], after[field])
        markdown = (output / "evidence.md").read_text()
        self.assertIn("Source-layout warning: unverified_pdf_figure_page_text", markdown)
        self.assertIn("may also contain ordinary prose", markdown)
        self.assertEqual(markdown.count("Source-layout warning:"), 1)

    def test_dependency_context_not_lost_outside_top_k(self):
        self.records[0]["dependency_ids"] = ["b"]
        self.records[1]["dependency_ids"] = ["a"]
        write_jsonl(self.bundle / "records.jsonl", self.records)
        rehash(self.bundle)
        cases = self.root / "cases.csv"
        cases.write_text("case_id,vignette_text\nC1,kidney\n")
        output = self.root / "dependency-output"
        with patch.object(runtime, "load_embedding_model", return_value=FakeModel()):
            runtime.retrieve_cases(self.bundle, cases, output, top_k=1, allow_candidate=True)
        evidence = json.loads((output / "evidence.jsonl").read_text())["evidence"]
        self.assertEqual(len(evidence), 1)
        self.assertEqual([row["record_id"] for row in evidence[0]["dependency_evidence"]], ["b"])


class CaseInputTests(unittest.TestCase):
    def test_blank_text_and_identifiers_rejected(self):
        for row in ({"case_id": "", "vignette_text": "diabetes"}, {"case_id": "a", "vignette_text": " "}):
            with self.assertRaises(ValueError):
                runtime.prepare_cases([row])

    def test_duplicate_same_case_collapses_but_conflicts_fail(self):
        rows = [{"case_id": "a", "vignette_text": "kidney   disease"}, {"case_id": "a", "vignette_text": "kidney disease"}]
        self.assertEqual(len(runtime.prepare_cases(rows)), 1)
        rows[1]["vignette_text"] = "no kidney disease"
        with self.assertRaisesRegex(ValueError, "Conflicting content"):
            runtime.prepare_cases(rows)

    def test_structured_query_omits_missing_and_never_uses_generated_plan(self):
        row = {"case_id": "a", "hba1c_percent": "9.2", "egfr_ml_min_1_73m2": "", "current_dm_drugs": "metformin", "heart_failure_present": "No", "treatment_plan": "SECRET GENERATED PLAN"}
        prepared = runtime.prepare_cases([row], "structured")[0]
        self.assertIn("HbA1c 9.2", prepared["retrieval_query"])
        self.assertIn("heart failure No", prepared["retrieval_query"])
        self.assertNotIn("eGFR", prepared["retrieval_query"])
        self.assertNotIn("SECRET", json.dumps(prepared))
        with self.assertRaisesRegex(ValueError, "no recognized"):
            runtime.prepare_cases([{"case_id": "a", "treatment_plan": "metformin"}], "structured")


class TokenAndRankingTests(unittest.TestCase):
    def test_visual_projection_matches_maintainer_for_all_record_types(self):
        # Optional maintainer parity check; the consumer itself needs no pandas.
        specification = importlib.util.spec_from_file_location("runtime_projection_parity", PIPELINE_DIR / "07_validate_visual_logic_outputs.py")
        validator = importlib.util.module_from_spec(specification)
        try:
            specification.loader.exec_module(validator)
        except ModuleNotFoundError as exc:
            self.skipTest(f"Maintainer-only projection dependency unavailable: {exc.name}")
        for kind in ("node", "edge", "action", "footnote", "symbol"):
            row = {field: f" {field}  value\n" for field in runtime.VISUAL_CLINICAL_FIELDS}
            row.update(record_id="synthetic", _record_type=kind, title=" Synthetic  title\n", patient_variables=None, strength=float("nan"), ordinal_level=0, symbol="+ $", drug_class=" Synthetic title ")
            with self.subTest(kind=kind):
                self.assertEqual(runtime.expected_visual_retrieval_text(row), validator.build_retrieval_record(row)["retrieval_text"])

    def test_windows_preserve_all_source_characters_and_symbols(self):
        text = "  A + $\n eGFR <30 AND not pregnancy; \u4e2d\u6587 test trailing  "
        windows = runtime.token_windows(text, FakeTokenizer(), max_length=6, overlap=1)
        coverage = np.zeros(len(text), dtype=bool)
        for window in windows:
            coverage[window["start_char"] : window["end_char"]] = True
            self.assertEqual(window["text"], text[window["start_char"] : window["end_char"]])
            self.assertLessEqual(window["token_count"], 6)
        self.assertTrue(coverage.all())
        self.assertGreater(len(windows), 1)

    def test_default_overlap_clamps_for_small_limit(self):
        windows = runtime.token_windows("a b c d e", FakeTokenizer(), max_length=4)
        self.assertEqual(windows[0]["start_char"], 0)
        self.assertEqual(windows[-1]["end_char"], len("a b c d e"))
        self.assertTrue(all(window["token_count"] <= 4 for window in windows))

    def test_empty_and_impossible_window_limits_rejected(self):
        with self.assertRaisesRegex(ValueError, "empty"):
            runtime.token_windows(" ", FakeTokenizer(), 5)
        with self.assertRaisesRegex(ValueError, "no space"):
            runtime.token_windows("a", FakeTokenizer(), 2)

    def test_max_query_window_score_and_tie_break_are_deterministic(self):
        records = [{"record_id": identifier, "source_id": "s", "source_text": identifier} for identifier in ("b", "a", "c")]
        units = [{"record_id": "b", "unit_id": "b1", "text": "b"}, {"record_id": "a", "unit_id": "a2", "text": "a"}, {"record_id": "a", "unit_id": "a1", "text": "a"}, {"record_id": "c", "unit_id": "c1", "text": "c"}]
        vectors = np.array([[1, 0], [0, 1], [0, 1], [-1, 0]], dtype="float32")
        queries = np.eye(2, dtype="float32")
        hits = runtime.rank_evidence(records, units, vectors, queries, top_k=3)
        self.assertEqual([hit["record_id"] for hit in hits], ["a", "b", "c"])
        self.assertEqual(hits[0]["matched_unit_id"], "a1")
        self.assertEqual(hits[0]["matched_query_window"], 1)

    def test_evidence_groups_and_exact_source_duplicates_collapse(self):
        records = [{"record_id": "a", "source_id": "s", "source_text": "a", "evidence_group_id": "same"}, {"record_id": "b", "source_id": "s", "source_text": "b", "evidence_group_id": "same"}, {"record_id": "c", "source_id": "s", "source_text": "a"}]
        units = [{"record_id": row["record_id"], "unit_id": row["record_id"], "text": row["record_id"]} for row in records]
        hits = runtime.rank_evidence(records, units, np.array([[1, 0]] * 3, dtype="float32"), np.array([[1, 0]], dtype="float32"))
        self.assertEqual([hit["record_id"] for hit in hits], ["a"])

    def test_numpy_exact_scores_match_direct_dot_products(self):
        random = np.random.default_rng(88)
        vectors = random.normal(size=(12, 5)).astype("float32")
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
        query = random.normal(size=(1, 5)).astype("float32")
        query /= np.linalg.norm(query, axis=1, keepdims=True)
        records = [{"record_id": str(i), "source_id": "s", "source_text": str(i)} for i in range(12)]
        units = [{"record_id": str(i), "unit_id": str(i), "text": str(i)} for i in range(12)]
        hits = runtime.rank_evidence(records, units, vectors, query, top_k=12)
        expected = sorted(range(12), key=lambda i: -float(np.dot(vectors[i], query[0])))
        self.assertEqual([hit["record_id"] for hit in hits], list(map(str, expected)))
        for hit in hits:
            self.assertAlmostEqual(hit["similarity_score"], float(np.dot(vectors[int(hit["record_id"])], query[0])), places=6)


if __name__ == "__main__":
    unittest.main()
