"""English v2 tests; no real human approval or production output is written."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kb_v1_figure_review as review
from test_kb_v1_figure_review import FigureFixture


def english_document(document):
    converted = copy.deepcopy(document)
    converted["schema_version"] = review.DOCUMENT_SCHEMA_EN
    converted["language"] = "en"
    converted["description_en"] = "AI-organized synthetic description; not a clinical guideline."
    converted.pop("description_zh")
    converted["generation_note"] = "Unverified transcription; no human approval inferred."
    for row in converted["paths"]:
        row.pop("logic_text_zh")
        row["logic_text_en"] = "Synthetic conditional path with its source qualifier."
        row["critical_checks"] = ["Verify the full qualifier and source relationship."]
    for row in converted["symbols"]:
        row.pop("meaning_zh")
        row["meaning_en"] = "Synthetic footnote marker."
    converted["review_checklist"] = ["Verify every source block", "Verify all paths and footnotes"]
    return converted


class EnglishFigureReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.fixture = FigureFixture(self.root)
        self.document = english_document(self.fixture.document)

    def test_frozen_v1_renderer_is_byte_identical_to_pre_v2_snapshot(self):
        rendered = review._render_review_markdown(
            self.fixture.document, {"source_pdf": "sources/source.pdf", "image": "sources/source_image.png"})
        # Captured before adding the v2 code. This locks the complete v1 output,
        # including its Chinese labels, source quoting, spacing and final LF.
        self.assertEqual(hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
                         "60a9641d61b9976d713d7c73d157316299a74ad675dd3e4b2cd8e23133423d5d")
        directory, before = self.fixture.prepare("v1")
        frozen = {path: review.sha256_file(path) for path in directory.rglob("*") if path.is_file()}
        self.assertEqual(review.verify_figure_review(directory), before)
        self.assertEqual(frozen, {path: review.sha256_file(path) for path in frozen})

    def test_english_prepare_verify_and_unsigned_template(self):
        directory, result = self.fixture.prepare("english", self.document)
        self.assertEqual(result["status"], "VERIFIED_CANDIDATE")
        self.assertFalse(result["human_approval_verified"])
        self.assertEqual(result["n_frozen_files_verified"], 7)
        self.assertEqual(json.loads((directory / "document.json").read_text()), self.document)
        template = json.loads((directory / "human_decision_template.json").read_text())
        self.assertEqual(template["status"], "pending")
        for key in ("reviewer", "reviewed_at", "reviewed_content_sha256", "notes"):
            self.assertEqual(template[key], "")
        self.assertEqual(template["checklist_confirmations"], {key: False for key in self.document["review_checklist"]})
        markdown = (directory / "REVIEW.md").read_text()
        self.assertIsNone(review.CJK_TEXT_RE.search(markdown))
        self.assertIn("AI-organized description", markdown)
        self.assertIn("not general English-language certification", markdown)
        self.assertIn("not replace the original 623-record", markdown)
        self.assertEqual(review.verify_figure_review(directory), result)

    def test_english_renderer_keeps_all_blocks_paths_questions_and_symbols(self):
        document = copy.deepcopy(self.document)
        for index in range(5, 41):
            document["source_blocks"].append({"id": f"extra-{index}", "kind": "heading", "region": f"region-{index}", "text": f"Unique source line {index}"})
        for index in range(2, 14):
            path = copy.deepcopy(document["paths"][0])
            path.update(path_id=f"path-{index}", title=f"Path {index}", logic_text_en=f"Unique English path {index}")
            document["paths"].append(path)
        document["open_questions"] = [{"question_id": "q1", "severity": "nonblocking", "text": "Synthetic question for human review"}]
        directory, result = self.fixture.prepare("complete-english", document)
        self.assertEqual((result["n_source_blocks"], result["n_paths"]), (40, 13))
        markdown = (directory / "REVIEW.md").read_text()
        for block in document["source_blocks"]:
            self.assertIn(review._source_quote(block["text"]), markdown)
        for path in document["paths"]:
            self.assertIn(path["logic_text_en"], markdown)
        self.assertIn(document["symbols"][0]["meaning_en"], markdown)
        self.assertIn(document["open_questions"][0]["text"], markdown)

    def test_language_marker_and_mixed_schema_fields_rejected(self):
        mutations = [
            lambda d: d.pop("language"),
            lambda d: d.update(language="zh"),
            lambda d: d.update(description_zh="legacy field"),
            lambda d: d["paths"][0].update(logic_text_zh="legacy field"),
            lambda d: d["symbols"][0].update(meaning_zh="legacy field"),
            lambda d: d.update(schema_version=review.DOCUMENT_SCHEMA),
        ]
        for index, mutate in enumerate(mutations):
            document = copy.deepcopy(self.document)
            mutate(document)
            with self.subTest(index=index), self.assertRaises(ValueError):
                review.validate_document(document, self.fixture.scope)
        old = copy.deepcopy(self.fixture.document)
        old["language"] = "en"
        with self.assertRaises(ValueError):
            review.validate_document(old, self.fixture.scope)

    def test_cjk_leakage_rejected_in_each_review_text_area(self):
        mutations = [
            lambda d: d.update(description_en="English 中文"),
            lambda d: d.update(generation_note="English 中文"),
            lambda d: d["source_blocks"][0].update(region="中文 region"),
            lambda d: d["source_blocks"][0].update(text="中文 source"),
            lambda d: d["paths"][0].update(title="中文 path"),
            lambda d: d["paths"][0].update(logic_text_en="中文 logic"),
            lambda d: d["paths"][0].update(critical_checks=["中文 check"]),
            lambda d: d["symbols"][0].update(meaning_en="中文 meaning"),
            lambda d: d.update(open_questions=[{"question_id": "q1", "severity": "nonblocking", "text": "中文 question"}]),
            lambda d: d.update(review_checklist=["中文 review"]),
            lambda d: d.update(printed_pages=["第1页"]),
        ]
        for index, mutate in enumerate(mutations):
            document = copy.deepcopy(self.document)
            mutate(document)
            with self.subTest(index=index), self.assertRaisesRegex(ValueError, "CJK/Han"):
                review.validate_document(document, self.fixture.scope)
        document, scope = copy.deepcopy(self.document), copy.deepcopy(self.fixture.scope)
        document["title"] = scope["figures"][0]["title"] = "中文 title"
        with self.assertRaisesRegex(ValueError, "CJK/Han"):
            review.validate_document(document, scope)

    def test_extended_han_is_rejected_without_rejecting_medical_symbols(self):
        for character in ("\u3400", "\uf900", "\U00020000", "\U0002f800", "\U00030000", "\U00031350", "\U000323b0", "\U00033479", "あ", "한"):
            document = copy.deepcopy(self.document)
            document["description_en"] += character
            with self.subTest(character=hex(ord(character))), self.assertRaisesRegex(ValueError, "CJK/Han"):
                review.validate_document(document, self.fixture.scope)
        document = copy.deepcopy(self.document)
        document["source_blocks"][1]["text"] = "β-cell; ≥20 mL/min/1.73 m²; † ‡ ≈ + $; 1–4 units; ≤7.0%"
        review.validate_document(document, self.fixture.scope)

    def test_cjk_source_paths_are_allowed_and_portable_copy_is_still_valid(self):
        pdf, image = self.root / "源材料 ADA.pdf", self.root / "原图 空格.png"
        shutil.copyfile(self.fixture.pdf, pdf)
        shutil.copyfile(self.fixture.image, image)
        document = copy.deepcopy(self.document)
        document.update(source_pdf=str(pdf), image_path=str(image))
        directory, _ = self.fixture.prepare("english-paths", document)
        self.assertEqual(review.verify_figure_review(directory)["status"], "VERIFIED_CANDIDATE")
        mapping = json.loads((directory / "source_path_map.json").read_text())
        self.assertEqual(mapping["source_pdf"]["original_path"], str(pdf))
        self.assertIsNone(review.CJK_TEXT_RE.search((directory / "REVIEW.md").read_text()))

    def test_old_v1_fingerprint_cannot_approve_new_english_revision(self):
        old = copy.deepcopy(self.fixture.document)
        # Keep checklist labels identical so this test specifically reaches the
        # old fingerprint rejection instead of stopping at a checklist mismatch.
        old["review_checklist"] = self.document["review_checklist"].copy()
        old_directory, _ = self.fixture.prepare("old-v1", old)
        old_decision = self.fixture.decision(old)
        decision_path = self.root / "old-human-decision.json"
        self.fixture.write(decision_path, old_decision)
        review.import_figure_decision(old_directory, decision_path, self.root / "old-import.json")
        directory, _ = self.fixture.prepare("new-english", self.document)
        self.assertNotEqual(review.content_fingerprint(old), review.content_fingerprint(self.document))
        with self.assertRaisesRegex(ValueError, "Stale"):
            review.import_figure_decision(directory, decision_path, self.root / "must-not-import.json")
        self.assertFalse((self.root / "must-not-import.json").exists())
        self.assertEqual(json.loads((directory / "human_decision_template.json").read_text())["status"], "pending")

    def test_english_source_symbols_and_line_breaks_are_literal(self):
        document = copy.deepcopy(self.document)
        text = "# For synthetic agents\n* Full footnote\n+ Scope label\n† Definition\n‡ Evidence\n$ Cost glyph\nβ-cell ≥20 mL/min/1.73 m²"
        document["source_blocks"][2]["text"] = text
        directory, _ = self.fixture.prepare("symbols-en", document)
        markdown = (directory / "REVIEW.md").read_text()
        self.assertIn("> \\# For synthetic agents  \n> \\* Full footnote  \n> \\+ Scope label  ", markdown)
        self.assertIn("> † Definition  \n> ‡ Evidence  \n> $ Cost glyph  ", markdown)
        self.assertEqual(json.loads((directory / "document.json").read_text())["source_blocks"][2]["text"], text)

    def test_english_pending_and_blocking_questions_do_not_bypass_approval(self):
        document = copy.deepcopy(self.document)
        document["open_questions"] = [{"question_id": "q1", "severity": "blocking", "text": "Unresolved test source qualifier"}]
        directory, _ = self.fixture.prepare("blocked-en", document)
        template = directory / "human_decision_template.json"
        pending = self.root / "pending-en.json"
        review.import_figure_decision(directory, template, pending)
        self.assertFalse(review.check_figure_scope(self.fixture.scope_path, [directory], [pending])["ready"])
        decision = self.root / "blocked-en-decision.json"
        self.fixture.write(decision, self.fixture.decision(document))
        with self.assertRaisesRegex(ValueError, "blocking questions"):
            review.import_figure_decision(directory, decision, self.root / "blocked-en-output.json")


if __name__ == "__main__":
    unittest.main()
