"""Whole-chart release is a separate, pinned representation, not 623 approvals."""

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import kb_v1_chart_release as release
from kb_v1_figure_review import content_fingerprint


def synthetic_document():
    return {
        "schema_version": "ada-figure-reconstruction-v2", "figure_id": "ada2026-ch9-figure-9-5",
        "title": "Figure 9.5", "revision": "synthetic-test-en", "language": "en",
        "source_pdf": "/historical/computer/source.pdf", "source_pdf_sha256": "a" * 64,
        "page_numbers": [16], "printed_pages": ["S198"],
        "image_path": "/historical/computer/page.png", "image_sha256": "b" * 64,
        "description_en": "AI-organized interpretation, not source wording.",
        "generation_note": "Synthetic fixture, not clinical evidence.",
        "source_blocks": [
            {"id": "N01", "kind": "node", "region": "left", "text": "Literal source + $$ ≈ > text."},
            {"id": "F01", "kind": "footnote", "region": "bottom", "text": "Qualifying source condition."},
        ],
        "paths": [{"path_id": "P01", "title": "Source-bound path", "kind": "decision_path",
                   "source_block_ids": ["N01"], "footnote_ids": ["F01"],
                   "logic_text_en": "AI path explanation.", "critical_checks": ["Retain condition."]}],
        "symbols": [{"symbol": "≈", "kind": "footnote", "meaning_en": "Source note marker", "source_block_ids": ["F01"]}],
        "open_questions": [{"question_id": "Q01", "severity": "nonblocking", "text": "Unresolved source ambiguity."}],
        "review_checklist": ["Confirm full content", "Confirm qualifiers"],
    }


def approved(document):
    return {"figure_id": document["figure_id"], "status": "approved", "reviewer": "Synthetic reviewer",
            "reviewed_at": "2026-09-29T12:00:00+00:00", "reviewed_content_sha256": content_fingerprint(document),
            "checklist_confirmations": {x: True for x in document["review_checklist"]},
            "notes": "Synthetic explicit test approval; not a real review."}


class WholeChartProjectionTests(unittest.TestCase):
    def setUp(self):
        self.document = synthetic_document()
        self.decision = approved(self.document)

    def test_source_ai_paths_and_original_fingerprint_stay_separate(self):
        original = copy.deepcopy(self.document)
        record = release.project_chart(self.document, self.decision)
        self.assertEqual(record["source_text"], "Literal source + $$ ≈ > text.\n\nQualifying source condition.")
        self.assertNotIn("AI path", record["source_text"])
        self.assertEqual(record["approved_document"], original)
        self.assertEqual(record["source_blocks"], original["source_blocks"])
        self.assertEqual(record["logic_paths"], original["paths"])
        self.assertEqual(record["content_sha256"], content_fingerprint(original))
        self.assertIn("AI-ORGANIZED PATHS", record["retrieval_text"])
        self.assertIn("Unresolved source ambiguity.", record["retrieval_text"])
        self.assertEqual(self.document, original)

    def test_whole_parent_contains_all_dependencies_no_independent_path_release(self):
        record = release.project_chart(self.document, self.decision)
        self.assertEqual(record["dependency_ids"], [])
        self.assertEqual(record["internal_dependencies"], [{"path_id": "P01", "source_block_ids": ["N01"],
                                                          "footnote_ids": ["F01"], "critical_checks": ["Retain condition."]}])
        self.assertTrue(record["use_policy"]["whole_parent_required"])
        self.assertEqual(record["evidence_group_id"], self.document["figure_id"])

    def test_figure_95_source_ambiguity_policy_is_not_executable(self):
        policy = release.project_chart(self.document, self.decision)["use_policy"]
        self.assertEqual(policy["purpose"], "source_evidence_only")
        for key in ("clinical_execution_allowed", "automatic_dose_calculation_allowed", "automated_prescribing_allowed", "external_model_enforcement"):
            self.assertIs(policy[key], False)
        self.assertEqual(len(policy["figure_9_5_restrictions"]), 3)
        self.assertEqual(policy["unresolved_source_question_ids"], ["Q01"])

    def test_pending_rejected_and_corrections_never_project(self):
        for status in ("pending", "rejected", "corrections_required"):
            with self.subTest(status=status):
                decision = dict(self.decision, status=status)
                with self.assertRaises(ValueError):
                    release.project_chart(self.document, decision)

    def test_stale_false_checklist_and_blocking_decisions_fail(self):
        variants = [dict(self.decision, reviewed_content_sha256="0" * 64),
                    dict(self.decision, checklist_confirmations={x: False for x in self.document["review_checklist"]})]
        for decision in variants:
            with self.assertRaises(ValueError):
                release.project_chart(self.document, decision)
        self.document["open_questions"][0]["severity"] = "blocking"
        with self.assertRaises(ValueError):
            release.project_chart(self.document, approved(self.document))

    def test_projection_is_deep_copy(self):
        record = release.project_chart(self.document, self.decision)
        record["source_blocks"][0]["text"] = "changed"
        record["approved_document"]["paths"][0]["footnote_ids"].clear()
        self.assertEqual(self.document["source_blocks"][0]["text"], "Literal source + $$ ≈ > text.")
        self.assertEqual(self.document["paths"][0]["footnote_ids"], ["F01"])


WORKSPACE = Path(__file__).resolve().parents[1] / "outputs/kb_v1_work/20260928/figure_reconstruction_v1"


@unittest.skipUnless((WORKSPACE / "migration_inventory.json").is_file(), "Local immutable approval corpus not bundled with source checkout")
class PinnedWholeChartAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = release.load_chart_release(WORKSPACE)
        cls.fixture = tempfile.TemporaryDirectory()
        cls.template = Path(cls.fixture.name) / "template"
        release.copy_chart_audit(WORKSPACE, cls.template)
        registry = release._expected_source_registry(cls.original)
        (cls.template / "sources/images").mkdir(parents=True)
        shutil.copyfile(cls.original["source_pdf"], cls.template / registry[release.ADA_SOURCE_SHA256]["relative_path"])
        image_sources = {x["document"]["image_sha256"]: x["source_image"] for x in cls.original["charts"]}
        image_sources.update({x["sha256"]: WORKSPACE / x["relative_image_path"] for x in cls.original["table_source_pages"]})
        for image in registry[release.ADA_SOURCE_SHA256]["images"]:
            shutil.copyfile(image_sources[image["sha256"]], cls.template / image["relative_path"])
        (cls.template / "sources.json").write_text(json.dumps(registry), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.fixture.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bundle = Path(self.tmp.name) / "中文 path with spaces" / "bundle"
        shutil.copytree(self.template, self.bundle)
        self.root = self.bundle / release.AUDIT_RELATIVE
        self.records = copy.deepcopy(self.original["records"])

    def files(self):
        return {x.relative_to(self.bundle).as_posix(): release.sha256_file(x)
                for x in self.bundle.rglob("*") if x.is_file()}

    def verify(self):
        return release.verify_chart_audit(self.bundle, self.files(), self.records)

    def rewrite(self, path, value):
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def test_portable_replay_has_seven_approvals_no_legacy_approvals_and_four_table_pages(self):
        report = self.verify()
        self.assertEqual(report["n_approved_charts"], 7)
        self.assertEqual(report["n_historical_candidate_ids"], 623)
        self.assertEqual(report["n_legacy_candidates_approved_by_migration"], 0)
        table = next(x for x in self.records if x["figure_id"] == "ada2026-ch9-table-9-2")
        self.assertEqual(table["page_numbers"], [11, 12, 13, 14])
        contract = json.loads((self.root / "release_contract.json").read_text())
        self.assertEqual([x["pdf_page"] for x in contract["table_9_2_source_pages"]], [11, 12, 13, 14])
        self.assertEqual(len(self.records), 7)

    def test_audit_inventory_omission_and_rehashed_decision_tamper_fail(self):
        inventory = self.files()
        decision = self.root / "decisions" / (release.SELECTED[0][0] + "_approved_001.json")
        inventory.pop(decision.relative_to(self.bundle).as_posix())
        with self.assertRaises(ValueError):
            release.verify_chart_audit(self.bundle, inventory, self.records)
        data = json.loads(decision.read_text())
        data["reviewer"] = "Impersonated replacement"
        self.rewrite(decision, data)
        with self.assertRaisesRegex(ValueError, "Pinned chart release"):
            self.verify()

    def test_pending_or_rejected_recorded_decision_fails_even_if_pin_were_changed(self):
        path = self.root / "decisions" / (release.SELECTED[0][0] + "_approved_001.json")
        data = json.loads(path.read_text())
        for status in ("pending", "rejected"):
            data["status"] = status
            self.rewrite(path, data)
            first = (release.SELECTED[0][0], release.SELECTED[0][1], release.sha256_file(path))
            with patch.object(release, "SELECTED", (first,) + release.SELECTED[1:]):
                with self.assertRaisesRegex(ValueError, "must be approved"):
                    self.verify()

    def test_migration_truncation_reassignment_or_autoapproval_fail_rehashed_inventory(self):
        path = self.root / "migration_inventory.json"
        original = json.loads(path.read_text())
        for field in ("drop", "page", "approval"):
            data = copy.deepcopy(original)
            if field == "drop":
                data["mapping"].pop()
            elif field == "page":
                data["mapping"][0]["page_number"] = 6
            else:
                data["mapping"][0]["legacy_disposition_changed"] = True
            self.rewrite(path, data)
            with patch.object(release, "MIGRATION_SHA256", release.sha256_file(path)):
                with self.assertRaisesRegex(ValueError, "Migration mapping"):
                    self.verify()

    def test_changed_projection_policy_dependency_source_ai_or_questions_fail(self):
        first = copy.deepcopy(self.records[0])
        for key, value in [("source_text", "unreviewed source"), ("ai_description", "unreviewed AI"),
                           ("open_questions", [{"question_id": "invented"}]), ("internal_dependencies", []),
                           ("retrieval_text", "independent action"), ("approved_document", {}),
                           ("use_policy", {"purpose": "clinical_execution"})]:
            self.records[0] = copy.deepcopy(first)
            self.records[0][key] = value
            with self.subTest(field=key), self.assertRaisesRegex(ValueError, "exact approved projection"):
                self.verify()

    def test_chart_missing_duplicate_or_legacy_visual_cannot_enter(self):
        for rows in (self.records[:-1], self.records + [self.records[0]],
                     self.records + [{"record_id": "old-extraction", "content_type": "visual_action"}]):
            with self.assertRaisesRegex(ValueError, "exactly the seven"):
                release.verify_chart_audit(self.bundle, self.files(), rows)

    def test_table_fourth_source_image_missing_fails(self):
        (self.root / "table_9_2_source_pages/ada_page_014.png").unlink()
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.verify()

    def test_modified_frozen_source_or_legacy_canonical_fails_even_with_rehashed_bundle(self):
        path = self.root / "legacy/canonical_records.jsonl"
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "historical canonical"):
            self.verify()

    def test_scope_contract_claiming_legacy_approval_fails(self):
        path = self.root / "release_contract.json"
        data = json.loads(path.read_text())
        data["n_legacy_candidates_approved_by_migration"] = 623
        self.rewrite(path, data)
        with self.assertRaisesRegex(ValueError, "release contract"):
            self.verify()

    def test_public_source_registry_page_chart_pdf_and_title_must_match_frozen_sources(self):
        path = self.bundle / "sources.json"
        original = json.loads(path.read_text())
        for kind in ("page", "chart", "title", "pdf", "omit_fourth_table_page"):
            registry = copy.deepcopy(original)
            source = registry[release.ADA_SOURCE_SHA256]
            if kind == "page":
                source["images"][0]["page_number"] = 6
            elif kind == "chart":
                source["images"][0]["figure_id"] = "ada2026-ch9-figure-9-5"
            elif kind == "title":
                source["title"] = "Different guideline year"
            elif kind == "pdf":
                source["relative_path"] = "audit/whole_charts/reviews/figure_9_1_draft_001_en/sources/source.pdf"
            else:
                source["images"].pop()
            self.rewrite(path, registry)
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "source registry differs"):
                self.verify()

    def test_no_overwrite_or_output_inside_frozen_workspace(self):
        with self.assertRaises(FileExistsError):
            release.copy_chart_audit(WORKSPACE, self.bundle)
        with self.assertRaisesRegex(ValueError, "overlap"):
            release.copy_chart_audit(WORKSPACE, WORKSPACE / "not-created")

    def test_flattened_chart_only_text_cannot_reenter_but_mixed_page_prose_is_allowed(self):
        for page in release.CHART_ONLY_PAGES:
            row = {"record_id": "flattened", "content_type": "text", "source_id": release.ADA_SOURCE_SHA256,
                   "page_number": page, "source_text": "Historical flattened chart.", "retrieval_text": "Historical flattened chart."}
            with self.subTest(page=page), self.assertRaisesRegex(ValueError, "chart-only source page"):
                release.verify_chart_audit(self.bundle, self.files(), self.records + [row])
            row.update(indexable=False, retrieval_text="")
            release.verify_chart_audit(self.bundle, self.files(), self.records + [row])
        for page in (8, 14, 21):
            row = {"record_id": "mixed-page-prose", "content_type": "text", "source_id": release.ADA_SOURCE_SHA256,
                   "page_number": page, "source_text": "Ordinary unreviewed prose.", "retrieval_text": "Ordinary unreviewed prose."}
            release.verify_chart_audit(self.bundle, self.files(), self.records + [row])


SUPPLEMENT = WORKSPACE.parent.parent / "20260929_tables_9_1_9_4"


@unittest.skipUnless((SUPPLEMENT / "decisions/table_9_4_draft_001_en_approved_001.json").is_file()
                     and (WORKSPACE / "migration_inventory.json").is_file(),
                     "Local immutable two-table approvals not bundled with source checkout")
class PinnedSupplementContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = release.load_chart_release(WORKSPACE)
        cls.expanded = release.load_chart_release(WORKSPACE, SUPPLEMENT)
        cls.fixture = tempfile.TemporaryDirectory()
        cls.template = Path(cls.fixture.name) / "template"
        release.copy_chart_audit(WORKSPACE, cls.template, SUPPLEMENT)

    @classmethod
    def tearDownClass(cls):
        cls.fixture.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bundle = Path(self.tmp.name) / "中文 supplemental bundle"
        shutil.copytree(self.template, self.bundle)
        self.root = self.bundle / release.AUDIT_RELATIVE
        self.supplement = self.root / "supplement"

    def load_copied(self):
        data = release._load(self.root, self.root / "legacy/canonical_records.jsonl",
                             self.root / "legacy/human_review_ledger.jsonl")
        return release._add_supplement(data, self.supplement)

    def test_nine_is_additive_to_seven_not_another_623_approvals(self):
        self.assertEqual(self.expanded["records"][:7], self.original["records"])
        self.assertEqual(self.expanded["migration"], self.original["migration"])
        contract = release._contract(self.expanded)
        self.assertEqual(contract["schema_version"], release.REPRESENTATION_SUPPLEMENT)
        self.assertEqual(contract["n_approved_charts"], 9)
        self.assertEqual(contract["n_source_pages"], 13)
        self.assertEqual(contract["n_historical_candidate_ids"], 623)
        self.assertEqual(contract["n_supplement_legacy_candidate_ids"], 0)
        self.assertEqual(contract["n_legacy_candidates_approved_by_migration"], 0)
        self.assertEqual(len(contract["records"]), 9)
        self.assertEqual(contract["scope_check"]["n_approved_figures"], 7)
        self.assertEqual(contract["supplement_scope_check"]["n_approved_figures"], 2)

    def test_portable_replay_preserves_all_pages_and_exact_approved_content(self):
        copied = self.load_copied()
        self.assertEqual(copied["records"], self.expanded["records"])
        self.assertEqual(release._contract(copied), release._contract(self.expanded))
        self.assertEqual([x["pdf_page"] for x in copied["supplement_source_pages"]], [3, 4, 22])
        for row in copied["supplement_source_pages"]:
            self.assertTrue(row["image_path"].is_relative_to(self.bundle.resolve()))
            self.assertEqual(release.sha256_file(row["image_path"]), row["sha256"])
        images = release._expected_source_registry(copied)[release.ADA_SOURCE_SHA256]["images"]
        self.assertEqual(len(images), 13)
        self.assertEqual(sorted(x["page_number"] for x in images if x["figure_id"] == "ada2026-ch9-table-9-1"), [3, 4])

    def test_both_new_tables_retain_all_paths_footnotes_and_source_limitations(self):
        for record in self.expanded["records"][7:]:
            document = record["approved_document"]
            self.assertEqual(record["logic_paths"], document["paths"])
            self.assertEqual(record["open_questions"], document["open_questions"])
            self.assertEqual(len(record["internal_dependencies"]), len(document["paths"]))
            self.assertTrue(record["use_policy"]["whole_parent_required"])
            self.assertFalse(record["use_policy"]["automatic_dose_calculation_allowed"])
            self.assertFalse(record["use_policy"]["automatic_unit_conversion_allowed"])
            for question in document["open_questions"]:
                self.assertIn(question["text"], record["retrieval_text"])
        self.assertEqual([len(x["open_questions"]) for x in self.expanded["records"][7:]], [2, 3])
        self.assertFalse(self.expanded["records"][7]["use_policy"]["automatic_percentage_normalization_allowed"])
        self.assertFalse(self.expanded["records"][8]["use_policy"]["automatic_pricing_calculation_allowed"])
        self.assertFalse(self.expanded["records"][8]["use_policy"]["current_price_claims_allowed"])

    def test_source_layout_only_suppresses_table91_pages_not_mixed_page22(self):
        self.assertEqual(self.original["chart_only_pages"], release.CHART_ONLY_PAGES)
        self.assertEqual(set(self.expanded["chart_only_pages"]) - set(release.CHART_ONLY_PAGES), {3, 4})
        self.assertNotIn(22, self.expanded["chart_only_pages"])
        policy = release._contract(self.expanded)["chart_only_page_text_policy"]
        self.assertEqual([x["pdf_page"] for x in policy["supplement_layout_proof"]["source_page_images"]], [3, 4, 22])

    def test_changed_supplement_approval_cannot_inherit_parent_approval(self):
        path = self.supplement / "decisions/table_9_1_draft_001_en_approved_001.json"
        decision = json.loads(path.read_text())
        decision["reviewer"] = "Replacement reviewer"
        path.write_text(json.dumps(decision), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "recorded supplement approval"):
            self.load_copied()

    def test_pending_supplement_fails_even_if_approval_pin_were_updated(self):
        path = self.supplement / "decisions/table_9_1_draft_001_en_approved_001.json"
        decision = json.loads(path.read_text())
        decision["status"] = "pending"
        path.write_text(json.dumps(decision), encoding="utf-8")
        selected = release.SUPPLEMENT_SELECTED
        with patch.object(release, "SUPPLEMENT_SELECTED", ((selected[0][0], selected[0][1], release.sha256_file(path)),) + selected[1:]):
            with self.assertRaisesRegex(ValueError, "must be approved"):
                self.load_copied()

    def test_second_page_source_and_parent_scope_are_required(self):
        path = self.supplement / "reviews/table_9_1_draft_001_en/sources/source_page_4.png"
        path.unlink()
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.load_copied()
        shutil.copyfile(self.template / release.AUDIT_RELATIVE / path.relative_to(self.root), path)
        (self.supplement / "parent_scope_manifest.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "supplement parent scope"):
            self.load_copied()

    def test_output_cannot_overlap_supplement(self):
        with self.assertRaisesRegex(ValueError, "overlap the supplement"):
            release.copy_chart_audit(WORKSPACE, SUPPLEMENT / "must-not-be-created", SUPPLEMENT)
        self.assertFalse((SUPPLEMENT / "must-not-be-created").exists())


if __name__ == "__main__":
    unittest.main()
