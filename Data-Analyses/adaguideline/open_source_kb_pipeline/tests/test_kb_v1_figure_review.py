"""Synthetic-only tests for the independent whole-figure review workflow."""

from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kb_v1_figure_review as review


class FigureFixture:
    def __init__(self, root: Path, n_figures: int = 1):
        self.root = root
        self.pdf = root / "synthetic source.pdf"
        self.image = root / "synthetic figure.png"
        self.pdf.write_bytes(b"%PDF-synthetic fixture; not clinical source")
        self.image.write_bytes(b"synthetic image fixture; not actual guideline evidence")
        self.scope = {
            "schema_version": review.SCOPE_SCHEMA, "scope_id": "synthetic-whole-scope",
            "source_pdf_sha256": review.sha256_file(self.pdf),
            "figures": [{"figure_id": f"figure-{i}", "title": f"Synthetic Figure {i}", "page_numbers": [i]} for i in range(1, n_figures + 1)],
            "legacy_lineage": {"canonical_records_sha256": "a" * 64, "record_count": 623,
                               "latest_human_ledger_sha256": "b" * 64, "note": "Synthetic identity-only lineage; no real data read."},
            "scope_limitations": ["Synthetic test only; not a clinical approval."],
        }
        self.scope_path = root / "scope.json"
        self.write(self.scope_path, self.scope)
        self.document = self.document_for(1)
        self.document_path = root / "document.json"
        self.write(self.document_path, self.document)

    @staticmethod
    def write(path: Path, value):
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def document_for(self, number):
        return {
            "schema_version": review.DOCUMENT_SCHEMA, "figure_id": f"figure-{number}",
            "title": f"Synthetic Figure {number}", "revision": "r1", "source_pdf": str(self.pdf),
            "source_pdf_sha256": review.sha256_file(self.pdf), "page_numbers": [number], "printed_pages": [f"S{number}"],
            "image_path": str(self.image), "image_sha256": review.sha256_file(self.image),
            "description_zh": "\u6d4b\u8bd5AI\u63cf\u8ff0\uff0c\u4e0d\u662f\u4e34\u5e8a\u6307\u5357\u3002", "generation_note": "\u5f85\u6838\u6e90\u6587\u8f6c\u5f55\uff0c\u975e\u5df2\u6279\u51c6\u539f\u6587\u3002",
            "source_blocks": [
                {"id": "heading", "kind": "heading", "region": "top", "text": "Synthetic scope"},
                {"id": "node", "kind": "node", "region": "center", "text": "Synthetic condition and action"},
                {"id": "fn", "kind": "footnote", "region": "bottom", "text": "Synthetic qualifier"},
                {"id": "caption", "kind": "caption", "region": "below figure", "text": "Synthetic caption"},
            ],
            "paths": [{"path_id": "path-1", "title": "Synthetic path", "kind": "decision_path",
                       "source_block_ids": ["heading", "node"], "footnote_ids": ["fn"],
                       "logic_text_zh": "\u6d4b\u8bd5\u8def\u5f84\uff0c\u9644\u9650\u5b9a\u3002", "critical_checks": ["\u68c0\u67e5\u9650\u5b9a\u662f\u5426\u5b8c\u6574"]}],
            "symbols": [{"symbol": "*", "kind": "footnote_reference", "meaning_zh": "\u6d4b\u8bd5\u811a\u6ce8\u6807\u8bb0", "source_block_ids": ["fn"]}],
            "open_questions": [], "review_checklist": ["\u6838\u5bf9\u6240\u6709\u6e90\u6587", "\u6838\u5bf9\u5168\u90e8\u8def\u5f84\u4e0e\u811a\u6ce8"],
        }

    def prepare(self, name="review", document=None):
        if document is not None:
            self.write(self.document_path, document)
        destination = self.root / name
        result = review.prepare_figure_review(self.document_path, self.scope_path, destination)
        return destination, result

    def decision(self, document=None, status="approved"):
        document = document or self.document
        return {"figure_id": document["figure_id"], "status": status, "reviewer": "SYNTHETIC TEST HUMAN",
                "reviewed_at": "2026-09-28T11:20:00-04:00", "reviewed_content_sha256": review.content_fingerprint(document),
                "checklist_confirmations": {item: True for item in document["review_checklist"]},
                "notes": "Synthetic explicit review only; not real clinical approval."}


class FigureReviewTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.fixture = FigureFixture(self.root)

    def test_prepare_copies_sources_keeps_originals_unsigned_and_complete(self):
        before = {path: review.sha256_file(path) for path in self.root.iterdir() if path.is_file()}
        directory, result = self.fixture.prepare()
        self.assertEqual(result["status"], "VERIFIED_CANDIDATE")
        self.assertFalse(result["human_approval_verified"])
        self.assertEqual(result["n_frozen_files_verified"], 7)
        self.assertEqual(result["n_source_blocks"], 4)
        for path, digest in before.items():
            self.assertEqual(review.sha256_file(path), digest)
        self.assertEqual((directory / "document.json").read_bytes(), self.fixture.document_path.read_bytes())
        template = json.loads((directory / "human_decision_template.json").read_text())
        self.assertEqual(template["status"], "pending")
        self.assertEqual(template["reviewer"], "")
        self.assertEqual(template["reviewed_content_sha256"], "")
        self.assertTrue(all(value is False for value in template["checklist_confirmations"].values()))
        markdown = (directory / "REVIEW.md").read_text()
        for block in self.fixture.document["source_blocks"]:
            self.assertIn(block["text"], markdown)
        self.assertIn(self.fixture.document["paths"][0]["logic_text_zh"], markdown)
        self.assertIn("sources/source.pdf#page=1", markdown)
        self.assertIn("\u5f85\u6838\u8f6c\u5f55", markdown)

    def test_source_transcription_uses_literal_quotes_and_hard_line_breaks(self):
        document = copy.deepcopy(self.fixture.document)
        source = "# For test drugs\n* Source footnote\n+ Source label\n- Source bullet\n1. Source item\nDrug | Outcome\n\nT2D **literal**"
        document["source_blocks"][2]["text"] = source
        directory, _ = self.fixture.prepare(document=document)
        markdown = (directory / "REVIEW.md").read_text()
        self.assertIn("> \\# For test drugs  \n> \\* Source footnote  \n> \\+ Source label  ", markdown)
        self.assertIn("> \\- Source bullet  \n> 1\\. Source item  ", markdown)
        self.assertIn("> Drug \\| Outcome  \n>\n> T2D \\*\\*literal\\*\\*  ", markdown)
        self.assertNotIn("\n# For test drugs\n", markdown)
        # Presentation escaping must not rewrite the frozen source text.
        self.assertEqual(json.loads((directory / "document.json").read_text())["source_blocks"][2]["text"], source)

    def test_no_overwrite_review_or_decision(self):
        directory, _ = self.fixture.prepare()
        with self.assertRaises(FileExistsError):
            self.fixture.prepare()
        decision_path = self.root / "decision.json"
        self.fixture.write(decision_path, self.fixture.decision())
        output = self.root / "imported.json"
        review.import_figure_decision(directory, decision_path, output)
        with self.assertRaises(FileExistsError):
            review.import_figure_decision(directory, decision_path, output)

    def test_wrong_source_hash_page_scope_and_title_fail_before_creation(self):
        variations = [
            (lambda d: d.update(source_pdf_sha256="c" * 64), "PDF hash"),
            (lambda d: d.update(image_sha256="c" * 64), "Source file hash"),
            (lambda d: d.update(page_numbers=[2]), "title/pages"),
            (lambda d: d.update(figure_id="not-in-scope"), "not in"),
            (lambda d: d.update(title="Other title"), "title/pages"),
            (lambda d: d.update(printed_pages=[]), "nonempty"),
        ]
        for index, (modify, message) in enumerate(variations):
            document = copy.deepcopy(self.fixture.document)
            modify(document)
            with self.subTest(index=index), self.assertRaisesRegex(ValueError, message):
                self.fixture.prepare(f"bad-{index}", document)
            self.assertFalse((self.root / f"bad-{index}").exists())

    def test_duplicate_ids_missing_refs_and_wrong_footnote_kind_fail(self):
        variations = [
            lambda d: d["source_blocks"].append(copy.deepcopy(d["source_blocks"][0])),
            lambda d: d["paths"].append(copy.deepcopy(d["paths"][0])),
            lambda d: d["paths"][0].update(source_block_ids=["missing"]),
            lambda d: d["paths"][0].update(footnote_ids=["node"]),
            lambda d: d["symbols"][0].update(source_block_ids=["missing"]),
            lambda d: d.update(paths=[]),
            lambda d: d["paths"][0].update(source_block_ids=["fn"]),
            lambda d: d["source_blocks"][0].update(text=""),
            lambda d: d["paths"][0].update(critical_checks=[]),
            lambda d: d["paths"][0].update(kind="unrestricted-summary"),
            lambda d: d["review_checklist"].append(d["review_checklist"][0]),
        ]
        for index, modify in enumerate(variations):
            document = copy.deepcopy(self.fixture.document)
            modify(document)
            with self.subTest(index=index), self.assertRaises(ValueError):
                review.validate_document(document, self.fixture.scope)

    def test_all_substantive_blocks_need_path_context_not_only_symbol_references(self):
        for kind in ("node", "comparison", "footnote"):
            document = copy.deepcopy(self.fixture.document)
            document["source_blocks"].append({"id": "orphan", "kind": kind, "region": "test", "text": "Unconnected test block"})
            document["symbols"][0]["source_block_ids"].append("orphan")
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "missing from all paths"):
                review.validate_document(document, self.fixture.scope)
            field = "footnote_ids" if kind == "footnote" else "source_block_ids"
            document["paths"][0][field].append("orphan")
            review.validate_document(document, self.fixture.scope)
        for kind in review.PATH_KINDS:
            document = copy.deepcopy(self.fixture.document)
            document["paths"][0]["kind"] = kind
            review.validate_document(document, self.fixture.scope)

    def test_real_ada_hash_requires_exact_seven_group_ids_and_pages(self):
        scope = copy.deepcopy(self.fixture.scope)
        scope["source_pdf_sha256"] = review.ADA_SOURCE_SHA256
        scope["figures"] = [{"figure_id": key, "title": "Synthetic scope identity test", "page_numbers": pages.copy()}
                            for key, pages in review.ADA_FIGURE_PAGES.items()]
        review.validate_scope(scope)
        variations = [
            lambda s: s.update(figures=[s["figures"][3]]),
            lambda s: s["figures"].pop(),
            lambda s: s["figures"][1].update(page_numbers=s["figures"][0]["page_numbers"]),
            lambda s: s["figures"][4].update(page_numbers=[11, 12, 13]),
            lambda s: s["figures"][0].update(figure_id="renamed-group"),
        ]
        for index, modify in enumerate(variations):
            changed = copy.deepcopy(scope)
            modify(changed)
            with self.subTest(index=index), self.assertRaisesRegex(ValueError, "Fixed ADA scope"):
                review.validate_scope(changed)
        duplicated = copy.deepcopy(scope)
        duplicated["figures"].append(copy.deepcopy(duplicated["figures"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate figure_id"):
            review.validate_scope(duplicated)
        # Only the designated source is pinned; synthetic/new unrelated source
        # configurations do not falsely claim to belong to this ADA cohort.
        generic = copy.deepcopy(self.fixture.scope)
        review.validate_scope(generic)

    def test_blank_approval_and_ai_fields_cannot_sign(self):
        directory, _ = self.fixture.prepare()
        template = json.loads((directory / "human_decision_template.json").read_text())
        template["status"] = "approved"
        with self.assertRaisesRegex(ValueError, "reviewer"):
            review.validate_decision(self.fixture.document, template)
        decision = self.fixture.decision()
        decision["ai_reviewer"] = decision.pop("reviewer")
        with self.assertRaisesRegex(ValueError, "AI fields"):
            review.validate_decision(self.fixture.document, decision)

    def test_approval_requires_current_hash_boolean_checklist_timezone_and_notes(self):
        variations = [
            lambda d: d.update(reviewed_content_sha256=""),
            lambda d: d.update(reviewed_content_sha256="0" * 64),
            lambda d: d.update(reviewed_at="2026-09-28"),
            lambda d: d.update(notes=""),
            lambda d: d.update(checklist_confirmations={}),
            lambda d: d["checklist_confirmations"].update({"\u6838\u5bf9\u6240\u6709\u6e90\u6587": False}),
            lambda d: d["checklist_confirmations"].update({"\u6838\u5bf9\u6240\u6709\u6e90\u6587": "true"}),
            lambda d: d["checklist_confirmations"].update({"\u6838\u5bf9\u6240\u6709\u6e90\u6587": 1}),
        ]
        for index, modify in enumerate(variations):
            decision = self.fixture.decision()
            modify(decision)
            with self.subTest(index=index), self.assertRaises(ValueError):
                review.validate_decision(self.fixture.document, decision)

    def test_blocking_question_cannot_be_approved(self):
        document = copy.deepcopy(self.fixture.document)
        document["open_questions"] = [{"question_id": "q1", "severity": "blocking", "text": "Test unresolved arrow"}]
        directory, result = self.fixture.prepare(document=document)
        self.assertEqual(result["n_blocking_questions"], 1)
        decision_path = self.root / "blocked.json"
        self.fixture.write(decision_path, self.fixture.decision(document))
        with self.assertRaisesRegex(ValueError, "blocking questions"):
            review.import_figure_decision(directory, decision_path, self.root / "must-not-exist.json")
        self.assertFalse((self.root / "must-not-exist.json").exists())

    def test_pending_and_correction_decisions_import_but_do_not_make_scope_ready(self):
        directory, _ = self.fixture.prepare()
        template = json.loads((directory / "human_decision_template.json").read_text())
        for status in ("pending", "corrections_required", "rejected"):
            row = template if status == "pending" else self.fixture.decision(status=status)
            source, output = self.root / f"{status}.json", self.root / f"{status}-imported.json"
            self.fixture.write(source, row)
            review.import_figure_decision(directory, source, output)
            result = review.check_figure_scope(self.fixture.scope_path, [directory], [output])
            self.assertFalse(result["ready"])
            self.assertEqual(result["n_approved_figures"], 0)

    def test_approved_scope_is_not_kb_release_and_import_never_mutates_package(self):
        directory, _ = self.fixture.prepare()
        before = {path: review.sha256_file(path) for path in directory.rglob("*") if path.is_file()}
        source, output = self.root / "decision.json", self.root / "imported.json"
        self.fixture.write(source, self.fixture.decision())
        review.import_figure_decision(directory, source, output)
        self.assertEqual(json.loads(output.read_text()), self.fixture.decision())
        for path, digest in before.items():
            self.assertEqual(review.sha256_file(path), digest)
        result = review.check_figure_scope(self.fixture.scope_path, [directory], [output])
        self.assertTrue(result["ready"])
        self.assertIn("never waives", result["notice"])

    def test_seven_figure_scope_with_only_one_approved_blocks_six_missing(self):
        fixture = FigureFixture(self.root, n_figures=7)
        directory, _ = fixture.prepare()
        decision = self.root / "one-approved.json"
        fixture.write(decision, fixture.decision())
        result = review.check_figure_scope(fixture.scope_path, [directory], [decision])
        self.assertFalse(result["ready"])
        self.assertEqual(result["n_expected_figures"], 7)
        self.assertEqual(result["n_approved_figures"], 1)
        self.assertEqual(len(result["errors"]), 6)

    def test_changed_content_fails_package_and_old_decision_fails_new_revision(self):
        directory, _ = self.fixture.prepare()
        changed = copy.deepcopy(self.fixture.document)
        changed["source_blocks"][1]["text"] = "Changed test source transcription"
        self.fixture.write(directory / "document.json", changed)
        with self.assertRaisesRegex(ValueError, "Frozen file"):
            review.verify_figure_review(directory)
        new_directory, _ = self.fixture.prepare("new-revision", changed)
        decision = self.root / "old-decision.json"
        self.fixture.write(decision, self.fixture.decision())
        with self.assertRaisesRegex(ValueError, "Stale"):
            review.import_figure_decision(new_directory, decision, self.root / "invalid-import.json")

    def test_document_change_after_verification_is_rejected_by_import_and_scope_check(self):
        original_verify = review.verify_figure_review
        changed = copy.deepcopy(self.fixture.document)
        changed["description_zh"] = "Concurrent synthetic edit after verification"
        decision = self.root / "concurrent-decision.json"
        self.fixture.write(decision, self.fixture.decision(changed))

        def verify_then_change(directory):
            result = original_verify(directory)
            self.fixture.write(Path(directory) / "document.json", changed)
            return result

        for operation in ("import", "scope"):
            directory, _ = self.fixture.prepare(f"concurrent-{operation}")
            with self.subTest(operation=operation), patch.object(review, "verify_figure_review", side_effect=verify_then_change):
                with self.assertRaisesRegex(ValueError, "changed after package verification"):
                    if operation == "import":
                        review.import_figure_decision(directory, decision, self.root / "concurrent-output.json")
                    else:
                        review.check_figure_scope(self.fixture.scope_path, [directory], [decision])
        self.assertFalse((self.root / "concurrent-output.json").exists())

    def test_source_tamper_scope_tamper_and_template_approval_detected(self):
        for filename in ("sources/source.pdf", "sources/source_image.png", "scope_manifest.json", "human_decision_template.json", "REVIEW.md"):
            with self.subTest(filename=filename):
                directory, _ = self.fixture.prepare(filename.replace("/", "-") + "-review")
                path = directory / filename
                path.write_bytes(path.read_bytes() + b" ")
                with self.assertRaisesRegex(ValueError, "Frozen file"):
                    review.verify_figure_review(directory)

    def test_scope_cannot_be_replaced_by_smaller_or_different_snapshot(self):
        directory, _ = self.fixture.prepare()
        scope = copy.deepcopy(self.fixture.scope)
        scope["scope_limitations"].append("Changed scope version")
        other = self.root / "other-scope.json"
        self.fixture.write(other, scope)
        with self.assertRaisesRegex(ValueError, "selected full scope"):
            review.check_figure_scope(other, [directory], [])
        with self.assertRaisesRegex(ValueError, "Multiple review versions"):
            review.check_figure_scope(self.fixture.scope_path, [directory, directory], [])

    def test_relocation_needs_no_original_paths_and_relative_sources_work(self):
        document = copy.deepcopy(self.fixture.document)
        document["source_pdf"] = self.fixture.pdf.name
        document["image_path"] = self.fixture.image.name
        directory, _ = self.fixture.prepare(document=document)
        copied = self.root / "\u79fb\u52a8 \u5305 with spaces"
        shutil.copytree(directory, copied)
        self.fixture.pdf.unlink()
        self.fixture.image.unlink()
        self.assertEqual(review.verify_figure_review(copied)["status"], "VERIFIED_CANDIDATE")

    def test_duplicate_json_keys_and_unsafe_inventory_path_rejected(self):
        bad_json = self.root / "duplicate.json"
        bad_json.write_text('{"status":"pending","status":"approved"}')
        with self.assertRaisesRegex(ValueError, "Duplicate JSON"):
            review._read_json(bad_json)
        directory, _ = self.fixture.prepare()
        path = directory / "review_manifest.json"
        manifest = json.loads(path.read_text())
        manifest["files"][0]["path"] = "../document.json"
        self.fixture.write(path, manifest)
        with self.assertRaisesRegex(ValueError, "Unsafe package path"):
            review.verify_figure_review(directory)


if __name__ == "__main__":
    unittest.main()
