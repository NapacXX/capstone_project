"""Synthetic review-only supplement tests; no real approval is created."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kb_v1_chart_release as release
import kb_v1_figure_review as review
from test_kb_v1_figure_review import FigureFixture
from test_kb_v1_figure_review_en import english_document


class SupplementScopeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.fixture = FigureFixture(self.root)
        # Exercise packaging against synthetic bytes without reading clinical
        # source content or weakening the actual production source pin.
        source_patch = patch.object(review, "ADA_SOURCE_SHA256", review.sha256_file(self.fixture.pdf))
        source_patch.start()
        self.addCleanup(source_patch.stop)
        self.scope = copy.deepcopy(self.fixture.scope)
        self.scope.update(schema_version=review.SUPPLEMENT_SCOPE_SCHEMA,
                          scope_id=review.SUPPLEMENT_SCOPE_ID,
                          parent_scope=copy.deepcopy(review.SUPPLEMENT_PARENT))
        self.scope["figures"] = [
            {"figure_id": figure, "title": "Synthetic " + figure, "page_numbers": pages.copy()}
            for figure, pages in review.ADA_SUPPLEMENT_PAGES.items()
        ]
        self.scope["legacy_lineage"].update(review.SUPPLEMENT_LINEAGE_HASHES)
        self.scope["source_page_images"] = []
        for figure, pages in review.ADA_SUPPLEMENT_PAGES.items():
            for page in pages:
                image = self.root / f"synthetic-page-{page}.png"
                image.write_bytes(f"Synthetic image {page}; not actual guideline content".encode())
                self.scope["source_page_images"].append({
                    "figure_id": figure, "page_number": page,
                    "image_path": str(image), "image_sha256": review.sha256_file(image),
                })
        self.fixture.write(self.fixture.scope_path, self.scope)
        self.document = self.document_for("ada2026-ch9-table-9-1")

    def document_for(self, figure):
        document = english_document(self.fixture.document)
        row = next(row for row in self.scope["figures"] if row["figure_id"] == figure)
        images = [row for row in self.scope["source_page_images"] if row["figure_id"] == figure]
        document.update(figure_id=figure, title=row["title"], page_numbers=row["page_numbers"],
                        printed_pages=[f"S{page}" for page in row["page_numbers"]],
                        image_path=images[0]["image_path"], image_sha256=images[0]["image_sha256"])
        return document

    def prepare(self, name="supplement", document=None):
        return self.fixture.prepare(name, document or self.document)

    def rehash_file(self, directory, filename):
        manifest_path = directory / "review_manifest.json"
        manifest = json.loads(manifest_path.read_text())
        for entry in manifest["files"]:
            if entry["path"] == filename:
                entry["sha256"] = review.sha256_file(directory / filename)
        self.fixture.write(manifest_path, manifest)

    def test_exact_supplement_scope_and_document_validate(self):
        review.validate_scope(self.scope)
        review.validate_document(self.document, self.scope)
        review.validate_document(self.document_for("ada2026-ch9-table-9-4"), self.scope)

    def test_legacy_scope_cannot_be_shrunk_or_redefined_as_supplement(self):
        legacy = copy.deepcopy(self.scope)
        legacy["schema_version"] = review.SCOPE_SCHEMA
        legacy.pop("parent_scope")
        legacy.pop("source_page_images")
        with self.assertRaisesRegex(ValueError, "Fixed ADA scope"):
            review.validate_scope(legacy)
        original = copy.deepcopy(legacy)
        original["figures"] = [{"figure_id": key, "title": "Synthetic", "page_numbers": pages.copy()}
                               for key, pages in review.ADA_FIGURE_PAGES.items()]
        review.validate_scope(original)
        original["figures"].append(copy.deepcopy(self.scope["figures"][0]))
        with self.assertRaisesRegex(ValueError, "Fixed ADA scope"):
            review.validate_scope(original)

    def test_scope_identity_parent_and_historical_lineage_are_fixed(self):
        mutations = [
            lambda s: s.update(scope_id="entire-chapter-complete"),
            lambda s: s.update(source_pdf_sha256="0" * 64),
            lambda s: s["parent_scope"].update(relationship="replaces_original"),
            lambda s: s["parent_scope"].update(scope_manifest_sha256="0" * 64),
            lambda s: s["parent_scope"].update(scope_id="other-parent"),
            lambda s: s["legacy_lineage"].update(canonical_records_sha256="0" * 64),
            lambda s: s["legacy_lineage"].update(latest_human_ledger_sha256="0" * 64),
            lambda s: s["legacy_lineage"].update(record_count=2),
            lambda s: s.update(figures=s["figures"][:1]),
            lambda s: s["figures"][0].update(page_numbers=[3]),
            lambda s: s["figures"][1].update(page_numbers=[23]),
        ]
        for index, mutate in enumerate(mutations):
            changed = copy.deepcopy(self.scope)
            mutate(changed)
            with self.subTest(index=index), self.assertRaises(ValueError):
                review.validate_scope(changed)

    def test_source_images_cover_exactly_three_scoped_pages(self):
        mutations = [
            lambda s: s["source_page_images"].pop(),
            lambda s: s["source_page_images"].append(copy.deepcopy(s["source_page_images"][0])),
            lambda s: s["source_page_images"][0].update(page_number=True),
            lambda s: s["source_page_images"][0].update(page_number=22),
            lambda s: s["source_page_images"][1].update(image_sha256="not-a-hash"),
            lambda s: s["source_page_images"][0].update(figure_id="ada2026-ch9-figure-9-1"),
        ]
        for index, mutate in enumerate(mutations):
            changed = copy.deepcopy(self.scope)
            mutate(changed)
            with self.subTest(index=index), self.assertRaises(ValueError):
                review.validate_scope(changed)

    def test_two_page_package_is_unsigned_complete_and_portable(self):
        directory, result = self.prepare()
        self.assertEqual(result["scope_kind"], "supplement_only")
        self.assertEqual(result["n_source_page_images"], 2)
        self.assertEqual(result["n_frozen_files_verified"], 8)
        self.assertFalse(result["human_approval_verified"])
        self.assertFalse(result["kb_release_performed"])
        mapping = json.loads((directory / "source_path_map.json").read_text())
        self.assertEqual([row["page_number"] for row in mapping["source_pages"]], [3, 4])
        markdown = (directory / "REVIEW.md").read_text()
        self.assertIn("PDF page 3 image", markdown)
        self.assertIn("PDF page 4 image", markdown)
        self.assertIn("does not replace the approved seven-group scope", markdown)
        self.assertIn("not integrated or approved", markdown)
        template = json.loads((directory / "human_decision_template.json").read_text())
        self.assertEqual(template["status"], "pending")
        self.assertEqual(template["reviewer"], "")
        for row in self.scope["source_page_images"]:
            Path(row["image_path"]).unlink()
        self.fixture.pdf.unlink()
        self.assertEqual(review.verify_figure_review(directory), result)

    def test_single_page_supplement_has_primary_image_and_no_extra_roles(self):
        directory, result = self.prepare("table-9-4", self.document_for("ada2026-ch9-table-9-4"))
        self.assertEqual(result["n_source_page_images"], 1)
        self.assertEqual(result["n_frozen_files_verified"], 7)
        mapping = json.loads((directory / "source_path_map.json").read_text())
        self.assertEqual(mapping["source_pages"][0]["relative_path"], mapping["image"]["relative_path"])

    def test_wrong_missing_or_changed_continuation_image_fails_before_output_creation(self):
        continuation = self.scope["source_page_images"][1]
        Path(continuation["image_path"]).write_bytes(b"changed synthetic image")
        with self.assertRaisesRegex(ValueError, "page image hash mismatch"):
            self.prepare("bad-source")
        self.assertFalse((self.root / "bad-source").exists())

    def test_wrong_primary_page_and_non_english_schema_fail(self):
        changed = copy.deepcopy(self.document)
        changed["image_path"] = self.scope["source_page_images"][1]["image_path"]
        changed["image_sha256"] = self.scope["source_page_images"][1]["image_sha256"]
        with self.assertRaisesRegex(ValueError, "first scoped source page"):
            review.validate_document(changed, self.scope)
        changed = copy.deepcopy(self.fixture.document)
        for key in ("figure_id", "title", "page_numbers", "printed_pages", "image_path", "image_sha256"):
            changed[key] = self.document[key]
        with self.assertRaisesRegex(ValueError, "English v2"):
            review.validate_document(changed, self.scope)

    def test_rehashed_continuation_content_or_page_map_cannot_bypass_scope(self):
        directory, _ = self.prepare("tampered-image")
        (directory / "sources/source_page_4.png").write_bytes(b"changed copy")
        self.rehash_file(directory, "sources/source_page_4.png")
        with self.assertRaisesRegex(ValueError, "source page image/hash"):
            review.verify_figure_review(directory)
        directory, _ = self.prepare("tampered-map")
        map_path = directory / "source_path_map.json"
        mapping = json.loads(map_path.read_text())
        mapping["source_pages"] = mapping["source_pages"][:1]
        self.fixture.write(map_path, mapping)
        self.rehash_file(directory, "source_path_map.json")
        with self.assertRaisesRegex(ValueError, "every table page"):
            review.verify_figure_review(directory)

    def test_omitted_continuation_role_and_guide_link_fail(self):
        directory, _ = self.prepare("missing-role")
        manifest_path = directory / "review_manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["files"] = [entry for entry in manifest["files"] if entry["role"] != "source_page_4"]
        self.fixture.write(manifest_path, manifest)
        with self.assertRaisesRegex(ValueError, "missing source-page roles"):
            review.verify_figure_review(directory)
        directory, _ = self.prepare("missing-guide-link")
        guide = directory / "REVIEW.md"
        guide.write_text(guide.read_text().replace("PDF page 4 image", "omitted"))
        self.rehash_file(directory, "REVIEW.md")
        with self.assertRaisesRegex(ValueError, "complete frozen document"):
            review.verify_figure_review(directory)

    def test_pending_scope_is_blocked_and_approval_readiness_does_not_release_kb(self):
        directories, templates, decisions = [], [], []
        for index, figure in enumerate(review.ADA_SUPPLEMENT_PAGES):
            document = self.document_for(figure)
            directory, _ = self.prepare(f"package-{index}", document)
            directories.append(directory)
            templates.append(directory / "human_decision_template.json")
            decision_path = self.root / f"synthetic-decision-{index}.json"
            self.fixture.write(decision_path, self.fixture.decision(document))
            decisions.append(decision_path)
        result = review.check_figure_scope(self.fixture.scope_path, directories, templates)
        self.assertFalse(result["ready"])
        self.assertEqual(result["n_approved_figures"], 0)
        self.assertEqual(result["n_expected_figures"], 2)
        self.assertFalse(result["kb_release_performed"])
        ready = review.check_figure_scope(self.fixture.scope_path, directories, decisions)
        self.assertTrue(ready["ready"])
        self.assertEqual(ready["status"], "SUPPLEMENT_REVIEW_READY")
        self.assertFalse(ready["kb_release_performed"])
        self.assertIn("does not reuse parent approvals", ready["notice"])

    def test_existing_release_contract_rejects_supplement_as_replacement(self):
        candidate = self.root / "candidate-release"
        candidate.mkdir()
        self.fixture.write(candidate / "scope_manifest.json", self.scope)
        self.fixture.write(candidate / "migration_inventory.json", {})
        with self.assertRaisesRegex(ValueError, "Pinned chart release input.*scope manifest"):
            release.load_chart_release(candidate)


if __name__ == "__main__":
    unittest.main()
