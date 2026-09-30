"""Release safety tests use synthetic evidence, never human approval artifacts."""

from __future__ import annotations

import copy
import csv
import importlib.util
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import pandas as pd

PIPELINE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE_DIR))
import kb_v1_review as review

SPEC = importlib.util.spec_from_file_location("v1_review_fixtures", Path(__file__).with_name("test_validate_visual_logic_outputs.py"))
fixtures = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(fixtures)


def record(record_id="node-1", **changes):
    row = {
        "record_id": record_id, "_record_type": "node", "node_id": record_id,
        "title": "Synthetic test figure", "condition": "Synthetic condition", "patient_variables": "test only",
        "true_branch": "", "false_branch": "", "source_pdf": "synthetic.pdf", "page_number": 1,
        "asset_id": "test", "item_id": "test", "evidence_text": "Synthetic test text",
        "content_sha256": "a" * 64, "effective_content_sha256": "a" * 64,
        "validation_status": "valid", "review_status": "approved", "reviewer": "SYNTHETIC TEST",
        "reviewed_at": "2026-09-01T00:00:00+00:00", "release_eligible": True,
        "requires_manual_review": False, "_approval_valid": True,
    }
    row.update(changes)
    return row


def approved_context(records):
    ledger = [{
        "record_id": row["record_id"], "group_id": "synthetic-group", "final_disposition": "approved",
        "human_reviewer": "SYNTHETIC TEST", "human_reviewed_at": "2026-09-02T00:00:00+00:00",
        "human_notes": "Synthetic fixture, not a real clinical approval", "reviewed_content_sha256": review.record_fingerprint(row),
        "dependency_reviewed": True, "dependency_ids": [],
    } for row in records]
    group = {
        "group_id": "synthetic-group", "record_ids": [row["record_id"] for row in records],
        "group_status": "complete", "human_confirmed": True, "human_reviewer": "SYNTHETIC TEST",
        "human_reviewed_at": "2026-09-02T00:00:00+00:00",
    }
    group["reviewed_group_sha256"] = review.group_fingerprint(group, records)
    groups = {"expected_record_ids": group["record_ids"].copy(), "groups": [group]}
    return ledger, groups


class ReleaseGateTests(unittest.TestCase):
    def setUp(self):
        # This class isolates approval/graph gating; source replay is tested
        # below using real Step 07 fixture files, never synthetic paths.
        mocked = patch.object(review, "_revalidate_canonical", side_effect=lambda rows: {row["record_id"]: row for row in rows})
        mocked.start()
        self.addCleanup(mocked.stop)

    def test_valid_dispositions_release_copies_only(self):
        records = [record(), record("node-2")]
        ledger, groups = approved_context(records)
        ledger[1]["final_disposition"] = "rejected"
        before = copy.deepcopy((records, ledger, groups))
        released = review.assert_release_ready(records, ledger, groups)
        self.assertEqual([row["record_id"] for row in released], ["node-1"])
        self.assertEqual((records, ledger, groups), before)
        output = review.to_retrieval_record(released[0])
        self.assertTrue(output["human_approved"])
        self.assertEqual(output["release_status"], "released")
        self.assertEqual(output["v1_human_reviewer"], "SYNTHETIC TEST")

    def test_pending_or_ai_only_never_releases(self):
        rows = [record()]
        ledger, groups = approved_context(rows)
        ledger[0].update(final_disposition="pending", ai_review_status="reviewed_no_change")
        with self.assertRaisesRegex(ValueError, "no final human disposition"):
            review.assert_release_ready(rows, ledger, groups)

    def test_rejected_all_not_text_only_release(self):
        rows = [record()]
        ledger, groups = approved_context(rows)
        ledger[0]["final_disposition"] = "rejected"
        with self.assertRaisesRegex(ValueError, "cannot be text-only"):
            review.assert_release_ready(rows, ledger, groups)

    def test_historical_approval_without_group_confirmation_is_blocked(self):
        rows = [record()]
        ledger, groups = approved_context(rows)
        groups["groups"][0]["human_confirmed"] = False
        with self.assertRaisesRegex(ValueError, "human-confirmed"):
            review.assert_release_ready(rows, ledger, groups)

    def test_content_edit_invalidates_both_bindings(self):
        rows = [record()]
        ledger, groups = approved_context(rows)
        rows[0]["condition"] = "Changed after review"
        with self.assertRaisesRegex(ValueError, "stale or missing"):
            review.assert_release_ready(rows, ledger, groups)

    def test_cannot_drop_pending_candidates(self):
        rows = [record(), record("node-2")]
        ledger, groups = approved_context(rows)
        with self.assertRaisesRegex(ValueError, "original candidates are missing"):
            review.assert_release_ready(rows[:1], ledger[:1], groups)

    def test_new_candidates_also_need_disposition(self):
        rows = [record()]
        ledger, groups = approved_context(rows)
        rows.append(record("new-record"))
        with self.assertRaisesRegex(ValueError, "IDs differ"):
            review.assert_release_ready(rows, ledger, groups)

    def test_step07_and08_must_both_pass(self):
        for changes in ({"release_eligible": False}, {"_approval_valid": False}, {"effective_content_sha256": "b" * 64}, {"reviewed_at": "2026-09-01"}):
            with self.subTest(changes=changes):
                rows = [record(**changes)]
                ledger, groups = approved_context(rows)
                with self.assertRaisesRegex(ValueError, "Step 0[78]"):
                    review.assert_release_ready(rows, ledger, groups)

    def test_explicit_dependency_closure_and_edge_endpoints(self):
        rows = [record(), record("node-2"), record("edge", _record_type="edge", from_node="node-1", to_node="node-2")]
        ledger, groups = approved_context(rows)
        with self.assertRaisesRegex(ValueError, "node dependency"):
            review.assert_release_ready(rows, ledger, groups)
        ledger[2]["dependency_ids"] = ["node-1", "node-2"]
        self.assertEqual(len(review.assert_release_ready(rows, ledger, groups)), 3)
        ledger[1]["final_disposition"] = "rejected"
        with self.assertRaisesRegex(ValueError, "is not released"):
            review.assert_release_ready(rows, ledger, groups)

    def test_action_footnote_dependency_not_silently_removed(self):
        rows = [record("action", _record_type="action"), record("footnote", _record_type="footnote")]
        ledger, groups = approved_context(rows)
        ledger[0]["dependency_ids"] = ["footnote"]
        ledger[1]["final_disposition"] = "rejected"
        with self.assertRaisesRegex(ValueError, "dependency footnote"):
            review.assert_release_ready(rows, ledger, groups)

    def test_merge_must_target_an_approved_record(self):
        rows = [record(), record("node-2")]
        ledger, groups = approved_context(rows)
        ledger[1].update(final_disposition="merged", merge_target="node-1")
        self.assertEqual(len(review.assert_release_ready(rows, ledger, groups)), 1)
        ledger[1]["merge_target"] = "absent"
        with self.assertRaisesRegex(ValueError, "merge_target"):
                review.assert_release_ready(rows, ledger, groups)


class OriginAnchorTests(unittest.TestCase):
    def setUp(self):
        self.source_hash = "f" * 64
        self.rows = [record(source_pdf_sha256=self.source_hash), record("node-2", source_pdf_sha256=self.source_hash)]
        self.ids = [row["record_id"] for row in self.rows]
        anchor = patch.multiple(
            review, ORIGIN_SOURCE_SHA256=self.source_hash,
            ORIGIN_RECORD_COUNT=2, ORIGIN_IDS_SHA256=review._origin_list_hash(self.ids),
        )
        anchor.start()
        self.addCleanup(anchor.stop)

    def context(self, rows):
        ledger, groups = approved_context(rows)
        groups.update(review._prepare_origin_anchor(self.rows))
        return ledger, groups

    def test_initial_cohort_has_independently_pinned_identity(self):
        origin = review._prepare_origin_anchor(self.rows)
        self.assertEqual(origin["origin_record_ids"], sorted(self.ids))
        self.assertEqual(origin["origin_record_count"], 2)
        ledger, groups = self.context(self.rows)
        self.assertEqual(len(review.assert_review_audit(self.rows, ledger, groups)), 2)
        self.assertEqual(review._prepare_origin_anchor([record("unrelated")]), {})

    def test_truncated_scope_cannot_be_redeclared_in_every_editable_file(self):
        remaining = self.rows[:1]
        ledger, groups = approved_context(remaining)
        groups.update({
            "origin_source_pdf_sha256": self.source_hash, "origin_record_count": 1,
            "origin_record_ids": [remaining[0]["record_id"]],
            "origin_record_ids_sha256": review._origin_list_hash([remaining[0]["record_id"]]),
        })
        with self.assertRaisesRegex(ValueError, "origin anchor"):
            review.assert_review_audit(remaining, ledger, groups)
        with self.assertRaisesRegex(ValueError, "complete original ADA"):
            review._prepare_origin_anchor(remaining)
        groups.update(review._prepare_origin_anchor(self.rows))
        with self.assertRaisesRegex(ValueError, "cohort is incomplete"):
            review.assert_review_audit(remaining, ledger, groups)

    def test_additions_require_prior_anchor_and_full_original_dispositions(self):
        rows = self.rows + [record("new-node", source_pdf_sha256=self.source_hash)]
        with self.assertRaisesRegex(ValueError, "origin_manifest_path"):
            review._prepare_origin_anchor(rows)
        prior = review._prepare_origin_anchor(self.rows)
        self.assertEqual(review._prepare_origin_anchor(rows, prior), prior)
        ledger, groups = self.context(rows)
        self.assertEqual(len(review.assert_review_audit(rows, ledger, groups)), 3)
        ledger[1]["final_disposition"] = "pending"
        with self.assertRaisesRegex(ValueError, "no final human disposition"):
            review.assert_review_audit(rows, ledger, groups)

    def test_original_cannot_be_moved_to_different_source(self):
        changed = copy.deepcopy(self.rows)
        changed[1]["source_pdf_sha256"] = "e" * 64
        ledger, groups = self.context(changed)
        with self.assertRaisesRegex(ValueError, "cohort is incomplete"):
            review.assert_review_audit(changed, ledger, groups)


class PreparationTests(unittest.TestCase):
    def fixture(self, root):
        fixture = fixtures.Fixture(root)
        fixture.write(fixtures.payload())
        fixture.run()
        approvals = fixture.output / "visual_review_approvals.csv"
        return fixture, approvals

    def test_prepare_freezes_and_keeps_all_original_files_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture, approvals = self.fixture(root)
            original = {str(path): review.sha256_file(path) for path in root.rglob("*") if path.is_file()}
            destination = root / "new-review"
            result = review.prepare_review(fixture.raw, approvals, fixture.registry, destination)
            self.assertEqual(result["n_records"], 5)
            self.assertEqual(result["ai_review_counts"], {"not_reviewed": 5})
            self.assertEqual(result["human_disposition_counts"], {"pending": 5})
            for path, digest in original.items():
                self.assertEqual(review.sha256_file(Path(path)), digest)
            records = review.load_jsonl(destination / "canonical_records.jsonl")
            ledger = review.load_jsonl(destination / "review_ledger.jsonl")
            groups = json.loads((destination / "group_reviews.json").read_text())
            self.assertEqual(len(pd.read_csv(destination / "review_ledger.csv")), 5)
            self.assertIn("[Tile]", (destination / "review_ledger.md").read_text())
            with self.assertRaisesRegex(ValueError, "v1 release blocked"):
                review.assert_release_ready(records, ledger, groups)
            with self.assertRaises(FileExistsError):
                review.prepare_review(fixture.raw, approvals, fixture.registry, destination)

    def test_preparation_and_frozen_replay_apply_origin_anchor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture, approvals = self.fixture(root)
            baseline = root / "baseline"
            review.prepare_review(fixture.raw, approvals, fixture.registry, baseline)
            rows = review.load_jsonl(baseline / "canonical_records.jsonl")
            ids = [row["record_id"] for row in rows]
            with patch.multiple(
                review, ORIGIN_SOURCE_SHA256=rows[0]["source_pdf_sha256"],
                ORIGIN_RECORD_COUNT=len(ids), ORIGIN_IDS_SHA256=review._origin_list_hash(ids),
            ):
                anchored = root / "anchored"
                review.prepare_review(fixture.raw, approvals, fixture.registry, anchored)
                manifest_path = anchored / "review_manifest.json"
                manifest = json.loads(manifest_path.read_text())
                groups = json.loads((anchored / "group_reviews.json").read_text())
                self.assertEqual(manifest["origin_record_ids"], sorted(ids))
                self.assertEqual(groups["origin_record_ids_sha256"], manifest["origin_record_ids_sha256"])
                self.assertEqual(review.verify_review_inputs(anchored)["n_records"], 5)
                next_version = root / "next-version"
                review.prepare_review(fixture.raw, approvals, fixture.registry, next_version, origin_manifest_path=manifest_path)
                self.assertEqual(review.verify_review_inputs(next_version)["n_records"], 5)
                inventory = json.loads((next_version / "source_inventory.json").read_text())
                self.assertEqual(sum(entry["role"] == "origin_manifest" for entry in inventory["files"]), 1)
                manifest["origin_record_ids"].pop()
                manifest_path.write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, "origin anchor"):
                    review.verify_review_inputs(anchored)
                with self.assertRaisesRegex(ValueError, "origin anchor"):
                    review.prepare_review(fixture.raw, approvals, fixture.registry, root / "rejected", origin_manifest_path=manifest_path)
                self.assertFalse((root / "rejected").exists())

    def test_notes_cannot_approve_or_change_source_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture, approvals = self.fixture(root)
            first = root / "first"
            review.prepare_review(fixture.raw, approvals, fixture.registry, first)
            rows = review.load_jsonl(first / "canonical_records.jsonl")
            note = {"record_id": rows[0]["record_id"], "ai_review_status": "reviewed_with_findings", "findings": ["Synthetic test observation"], "suggested_corrections": {"condition": "Not applied"}, "checked_sources": [{"path": str(fixture.image), "sha256": review.sha256_file(fixture.image), "method": "synthetic fixture"}]}
            notes = root / "notes.json"
            notes.write_text(json.dumps([note]))
            second = root / "second"
            review.prepare_review(fixture.raw, approvals, fixture.registry, second, notes)
            self.assertEqual(rows, review.load_jsonl(second / "canonical_records.jsonl"))
            ledger = review.load_jsonl(second / "review_ledger.jsonl")
            self.assertEqual(ledger[0]["final_disposition"], "pending")
            self.assertEqual(ledger[0]["ai_review_status"], "reviewed_with_findings")
            note["final_disposition"] = "approved"
            notes.write_text(json.dumps([note]))
            with self.assertRaisesRegex(ValueError, "non-AI fields"):
                review.prepare_review(fixture.raw, approvals, fixture.registry, root / "third", notes)
            self.assertFalse((root / "third").exists())

    def test_missing_source_and_false_checked_source_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture, approvals = self.fixture(root)
            first = root / "first"
            review.prepare_review(fixture.raw, approvals, fixture.registry, first)
            row = review.load_jsonl(first / "canonical_records.jsonl")[0]
            notes = root / "notes.jsonl"
            notes.write_text(json.dumps({"record_id": row["record_id"], "ai_review_status": "uncertain", "checked_sources": [{"path": str(fixture.image), "sha256": "0" * 64, "method": "synthetic"}]}) + "\n")
            with self.assertRaisesRegex(ValueError, "hash/path mismatch"):
                review.prepare_review(fixture.raw, approvals, fixture.registry, root / "false-notes", notes)
            fixture.json_path.unlink()
            with self.assertRaises(FileNotFoundError):
                review.prepare_review(fixture.raw, approvals, fixture.registry, root / "missing")

    def test_source_replay_detects_canonical_or_raw_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture, approvals = self.fixture(root)
            destination = root / "new-review"
            review.prepare_review(fixture.raw, approvals, fixture.registry, destination)
            rows = review.load_jsonl(destination / "canonical_records.jsonl")
            self.assertEqual(set(review._revalidate_canonical(rows)), {row["record_id"] for row in rows})
            changed = copy.deepcopy(rows)
            changed[0]["condition"] = "Clinical content edited while cached approval stays unchanged"
            with self.assertRaisesRegex(ValueError, "freshly normalized"):
                review._revalidate_canonical(changed)
            fixture.json_path.write_text(fixture.json_path.read_text() + " ")
            with self.assertRaisesRegex(ValueError, "input changed"):
                review._revalidate_canonical(rows)

    def test_human_csv_import_preserves_draft_pending_and_originals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture, approvals = self.fixture(root)
            destination = root / "review"
            review.prepare_review(fixture.raw, approvals, fixture.registry, destination)
            initial = review.sha256_file(destination / "review_ledger.jsonl")
            output = destination / "human-draft.jsonl"
            result = review.import_human_ledger_csv(destination, destination / "review_ledger.csv", output)
            self.assertEqual(result["dispositions"], {"pending": 5})
            self.assertEqual(review.load_jsonl(output), review.load_jsonl(destination / "review_ledger.jsonl"))
            self.assertEqual(review.sha256_file(destination / "review_ledger.jsonl"), initial)
            with self.assertRaises(FileExistsError):
                review.import_human_ledger_csv(destination, destination / "review_ledger.csv", output)

    def test_frozen_raw_inventory_is_independent_of_editable_expected_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture, approvals = self.fixture(root)
            destination = root / "review"
            review.prepare_review(fixture.raw, approvals, fixture.registry, destination)
            result = review.verify_review_inputs(destination)
            self.assertEqual(result["n_records"], 5)
            self.assertEqual(result["n_assets"], 1)
            manifest_path = destination / "review_manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["expected_record_ids"].pop()
            manifest["n_records"] -= 1
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "frozen raw candidates"):
                review.verify_review_inputs(destination)

    def test_changed_frozen_raw_file_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture, approvals = self.fixture(root)
            destination = root / "review"
            review.prepare_review(fixture.raw, approvals, fixture.registry, destination)
            inventory = json.loads((destination / "source_inventory.json").read_text())
            entry = next(entry for entry in inventory["files"] if entry["role"] == "raw_json")
            path = destination / entry["frozen_path"]
            path.write_text(path.read_text() + " ")
            with self.assertRaisesRegex(ValueError, "Frozen input missing or changed"):
                review.verify_review_inputs(destination)

    def test_human_csv_import_accepts_only_explicit_complete_final_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture, approvals = self.fixture(root)
            destination = root / "review"
            review.prepare_review(fixture.raw, approvals, fixture.registry, destination)
            with (destination / "review_ledger.csv").open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                fields, base_rows = reader.fieldnames, list(reader)

            def attempt(name, changes, message=None, remove_row=False):
                incoming = copy.deepcopy(base_rows)
                incoming[0].update(changes)
                if remove_row:
                    incoming.pop()
                path = root / f"{name}.csv"
                with path.open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=fields)
                    writer.writeheader()
                    writer.writerows(incoming)
                output = destination / f"{name}.jsonl"
                if message:
                    with self.assertRaisesRegex(ValueError, message):
                        review.import_human_ledger_csv(destination, path, output)
                    self.assertFalse(output.exists())
                else:
                    return review.import_human_ledger_csv(destination, path, output)

            attempt("changed-ai", {"ai_review_status": "reviewed_no_change"}, "immutable field")
            attempt("changed-source", {"step07_content_sha256": "0" * 64}, "immutable field")
            attempt("deleted", {}, "every original", remove_row=True)
            attempt("bool", {"dependency_reviewed": "yes"}, "explicit true/false")
            attempt("unknown", {"final_disposition": "accepted"}, "unexpected final_disposition")
            attempt("autofill", {"final_disposition": "approved"}, "needs human reviewer")
            complete = {
                "final_disposition": "approved", "human_reviewer": "SYNTHETIC TEST",
                "human_reviewed_at": "2026-09-28T00:00:00+00:00", "human_notes": "Test only",
                "reviewed_content_sha256": base_rows[0]["current_v1_content_sha256"],
                "dependency_ids": "[]", "dependency_reviewed": "true",
            }
            result = attempt("signed", complete)
            self.assertEqual(result["dispositions"], {"approved": 1, "pending": 4})
            attempt("stale", {**complete, "reviewed_content_sha256": "0" * 64}, "current reviewed content hash")
            attempt("missing-deps", {**complete, "dependency_ids": "null"}, "reviewed dependency list")
            attempt("unknown-deps", {**complete, "dependency_ids": '["not-a-record"]'}, "unknown, duplicate")


if __name__ == "__main__":
    unittest.main()
