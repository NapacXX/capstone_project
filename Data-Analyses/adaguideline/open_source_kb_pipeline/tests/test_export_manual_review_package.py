"""Tests for the sanitized manual-review package exporter."""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


PIPELINE_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = PIPELINE_DIR / "export_manual_review_package.py"
SPEC = importlib.util.spec_from_file_location("export_manual_review_package", MODULE_PATH)
assert SPEC and SPEC.loader
exporter = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = exporter
SPEC.loader.exec_module(exporter)


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


class PackageFixture:
    def __init__(self, root: Path, *, candidate_count: int = 1) -> None:
        self.root = root
        self.raw = root / "outputs" / "raw"
        self.structured = root / "outputs" / "structured"
        self.destination = root / "manual_review_packages" / "test_run"
        self.registry = root / "guideline_symbol_registry.csv"
        self.raw.mkdir(parents=True)
        self.structured.mkdir(parents=True)

        self.pdf_hash = digest(b"controlled-pdf")
        self.image_hash = digest(b"controlled-image")
        self.content_hash = digest(b"candidate-content")
        self.record_id = "p009-asset::item::node::n1"
        self.absolute_pdf = "/Users/reviewer/Controlled Sources/ADA guideline.pdf"
        self.absolute_image = "/Users/reviewer/Controlled Sources/page 9 tile.png"
        self.absolute_json = "/Users/reviewer/project/outputs/raw/asset.json"

        write_json(
            self.raw / "extraction_run.json",
            {
                "started_at": "2026-08-23T01:00:00+00:00",
                "finished_at": "2026-08-23T02:00:00+00:00",
                "merged_at": "2026-08-23T02:30:00+00:00",
                "status": "completed_unvalidated",
                "image_manifest": "/Users/reviewer/project/outputs/images/manifest.csv",
                "model": "qwen3-vl:8b-instruct",
                "model_digest": "model-digest",
                "ollama_version": "0.11.0",
                "extraction_passes": ["structure", "actions", "symbols"],
                "n_assets": 1,
                "status_counts": {"extracted_unvalidated": 1},
            },
        )
        write_csv(
            self.raw / "visual_logic_manifest.csv",
            [
                "asset_id",
                "page_number",
                "image_path",
                "image_sha256",
                "source_pdf",
                "source_pdf_sha256",
                "visual_logic_json",
                "status",
                "error_message",
            ],
            [
                {
                    "asset_id": "page_009_tile_r01_c01",
                    "page_number": "9",
                    "image_path": self.absolute_image,
                    "image_sha256": self.image_hash,
                    "source_pdf": self.absolute_pdf,
                    "source_pdf_sha256": self.pdf_hash,
                    "visual_logic_json": self.absolute_json,
                    "status": "extracted_unvalidated",
                    "error_message": f"Source checked at {self.absolute_image}",
                }
            ],
        )

        detail_fields = [
            "record_id",
            "asset_id",
            "page_number",
            "source_pdf",
            "source_pdf_sha256",
            "image_path",
            "image_sha256",
            "source_json",
            "content_sha256",
            "validation_status",
            "review_status",
            "release_eligible",
            "validation_warnings",
        ]
        node_row = {
            "record_id": self.record_id,
            "asset_id": "page_009_tile_r01_c01",
            "page_number": "9",
            "source_pdf": self.absolute_pdf,
            "source_pdf_sha256": self.pdf_hash,
            "image_path": self.absolute_image,
            "image_sha256": self.image_hash,
            "source_json": self.absolute_json,
            "content_sha256": self.content_hash,
            "validation_status": "valid",
            "review_status": "pending",
            "release_eligible": "False",
            "validation_warnings": f"Inspect {self.absolute_image}",
        }
        detail_files = {
            "visual_decision_nodes.csv": [node_row],
            "visual_recommendation_edges.csv": [],
            "visual_drug_actions.csv": [],
            "visual_symbols_footnotes.csv": [],
            "visual_ordinal_symbols.csv": [],
        }
        for name, rows in detail_files.items():
            write_csv(self.structured / name, detail_fields, rows)

        approval_fields = [
            "record_id",
            "asset_id",
            "page_number",
            "content_sha256",
            "review_status",
        ]
        write_csv(
            self.structured / "visual_review_approvals.csv",
            approval_fields,
            [
                {
                    "record_id": self.record_id,
                    "asset_id": "page_009_tile_r01_c01",
                    "page_number": "9",
                    "content_sha256": self.content_hash,
                    "review_status": "pending",
                }
            ],
        )
        review_fields = [
            "record_id",
            "record_type",
            "asset_id",
            "page_number",
            "source_pdf",
            "source_pdf_sha256",
            "image_path",
            "image_sha256",
            "source_json",
            "content_sha256",
            "review_status",
            "release_eligible",
        ]
        write_csv(
            self.structured / "visual_manual_review_queue.csv",
            review_fields,
            [
                {
                    "record_id": self.record_id,
                    "record_type": "node",
                    "asset_id": "page_009_tile_r01_c01",
                    "page_number": "9",
                    "source_pdf": self.absolute_pdf,
                    "source_pdf_sha256": self.pdf_hash,
                    "image_path": self.absolute_image,
                    "image_sha256": self.image_hash,
                    "source_json": self.absolute_json,
                    "content_sha256": self.content_hash,
                    "review_status": "pending",
                    "release_eligible": "False",
                }
            ],
        )
        candidate_rows = (
            [
                {
                    "record_id": self.record_id,
                    "asset_id": "page_009_tile_r01_c01",
                    "page_number": "9",
                    "source_pdf": self.absolute_pdf,
                    "source_pdf_sha256": self.pdf_hash,
                    "image_path": self.absolute_image,
                    "image_sha256": self.image_hash,
                    "source_json": self.absolute_json,
                    "content_sha256": self.content_hash,
                    "validation_status": "valid",
                    "review_status": "pending",
                    "human_approved": "False",
                    "approval_status": "pending",
                    "release_status": "unreleased",
                }
            ]
            if candidate_count
            else []
        )
        write_csv(
            self.structured / "visual_candidate_retrieval_records.csv",
            [
                "record_id",
                "asset_id",
                "page_number",
                "source_pdf",
                "source_pdf_sha256",
                "image_path",
                "image_sha256",
                "source_json",
                "content_sha256",
                "validation_status",
                "review_status",
                "human_approved",
                "approval_status",
                "release_status",
            ],
            candidate_rows,
        )
        write_json(
            self.structured / "visual_logic_summary.json",
            {
                "n_visual_assets": 1,
                "n_decision_nodes": 1,
                "n_recommendation_edges": 0,
                "n_drug_actions": 0,
                "n_symbols_or_footnotes": 0,
                "n_ordinal_symbols": 0,
                "n_valid_candidate_records": candidate_count,
                "n_invalid_candidate_records": 0,
                "n_human_approved_release_records": 0,
                "n_manual_review_items": 1,
                "approval_csv": "/Users/reviewer/project/outputs/structured/visual_review_approvals.csv",
                "symbol_registry": "/Users/reviewer/project/guideline_symbol_registry.csv",
            },
        )
        write_csv(
            self.registry,
            ["raw_symbol", "meaning", "manual_review_required"],
            [{"raw_symbol": "+", "meaning": "Context-dependent", "manual_review_required": "true"}],
        )

        # These files exercise the explicit exclusion boundary.
        (self.raw / "asset.json").write_text("raw model JSON", encoding="utf-8")
        (self.raw / "source.pdf").write_bytes(b"pdf")
        (self.raw / "tile.png").write_bytes(b"png")
        write_csv(
            self.structured / "visual_release_records.csv",
            ["record_id"],
            [{"record_id": "must-not-export"}],
        )
        write_csv(
            self.structured / "visual_retrieval_records.csv",
            ["record_id"],
            [{"record_id": "must-not-export"}],
        )

    def source_hashes(self) -> dict[str, str]:
        paths = sorted(
            path
            for directory in (self.raw, self.structured)
            for path in directory.iterdir()
            if path.is_file()
        )
        return {str(path): exporter.sha256_file(path) for path in paths}

    def export(self, *, require_review_records: bool = True) -> dict[str, object]:
        return exporter.export_package(
            raw_dir=self.raw,
            structured_dir=self.structured,
            destination=self.destination,
            symbol_registry=self.registry,
            require_review_records=require_review_records,
        )


class ManualReviewPackageExporterTests(unittest.TestCase):
    def test_exports_allowlisted_sanitized_package_with_counts_and_checksums(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = PackageFixture(Path(directory))
            before = fixture.source_hashes()
            inventory = fixture.export()
            after = fixture.source_hashes()

            self.assertEqual(before, after, "canonical Step 06/07 files were modified")
            expected = {
                *exporter.RAW_FILES,
                *exporter.STRUCTURED_FILES,
                "guideline_symbol_registry.csv",
                "README.md",
                "package_inventory.json",
                "SHA256SUMS",
            }
            actual = {path.name for path in fixture.destination.iterdir()}
            self.assertEqual(actual, expected)
            self.assertFalse(any(path.suffix in {".pdf", ".png"} for path in fixture.destination.iterdir()))
            self.assertNotIn("asset.json", actual)
            self.assertNotIn("visual_release_records.csv", actual)
            self.assertNotIn("visual_retrieval_records.csv", actual)

            all_text = "\n".join(
                path.read_text(encoding="utf-8")
                for path in fixture.destination.iterdir()
                if path.is_file()
            )
            self.assertNotIn("/Users/reviewer", all_text)
            self.assertIn("CONTROLLED_SOURCE_PDF/ADA guideline.pdf", all_text)
            self.assertIn("CONTROLLED_SOURCE_IMAGE/page 9 tile.png", all_text)
            self.assertIn("OMITTED_RAW_MODEL_JSON/asset.json", all_text)
            self.assertIn("Import every CSV column as **text**", all_text)
            self.assertIn(
                "Step 06 retry/merge completed: `2026-08-23T02:30:00+00:00`",
                all_text,
            )

            stored_inventory = json.loads(
                (fixture.destination / "package_inventory.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(inventory, stored_inventory)
            self.assertEqual(
                stored_inventory["counts"]["all_detailed_clinical_records"], 1
            )
            self.assertEqual(
                stored_inventory["counts"]["reviewable_valid_candidates"], 1
            )
            self.assertEqual(
                stored_inventory["controlled_source_hashes"]["source_pdf_sha256"],
                [fixture.pdf_hash],
            )
            self.assertEqual(
                stored_inventory["source_run"]["merged_at"],
                "2026-08-23T02:30:00+00:00",
            )
            for item in stored_inventory["files"]:
                package_file = fixture.destination / item["path"]
                self.assertEqual(exporter.sha256_file(package_file), item["sha256"])

            checksum_rows = {
                line.split("  ", 1)[1]: line.split("  ", 1)[0]
                for line in (fixture.destination / "SHA256SUMS")
                .read_text(encoding="utf-8")
                .splitlines()
            }
            self.assertEqual(checksum_rows.keys(), expected - {"SHA256SUMS"})
            for name, checksum in checksum_rows.items():
                self.assertEqual(exporter.sha256_file(fixture.destination / name), checksum)

            second_destination = fixture.root / "manual_review_packages" / "second_run"
            exporter.export_package(
                raw_dir=fixture.raw,
                structured_dir=fixture.structured,
                destination=second_destination,
                symbol_registry=fixture.registry,
                require_review_records=True,
            )
            first_hashes = {
                path.name: exporter.sha256_file(path)
                for path in fixture.destination.iterdir()
                if path.is_file()
            }
            second_hashes = {
                path.name: exporter.sha256_file(path)
                for path in second_destination.iterdir()
                if path.is_file()
            }
            self.assertEqual(first_hashes, second_hashes)

    def test_rejects_incomplete_step06_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = PackageFixture(Path(directory))
            run_path = fixture.raw / "extraction_run.json"
            run = json.loads(run_path.read_text(encoding="utf-8"))
            run["status"] = "starting"
            write_json(run_path, run)
            with self.assertRaisesRegex(ValueError, "not complete"):
                fixture.export()
            self.assertFalse(fixture.destination.exists())

    def test_rejects_completed_run_with_partial_manifest_asset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = PackageFixture(Path(directory))
            manifest_path = fixture.raw / "visual_logic_manifest.csv"
            manifest = exporter.read_csv_document(manifest_path)
            manifest["rows"][0]["status"] = "partial_extraction"
            exporter.write_csv_document(manifest_path, manifest)

            run_path = fixture.raw / "extraction_run.json"
            run = json.loads(run_path.read_text(encoding="utf-8"))
            run["status_counts"] = {"partial_extraction": 1}
            write_json(run_path, run)

            with self.assertRaisesRegex(ValueError, "not fully extracted"):
                fixture.export()
            self.assertFalse(fixture.destination.exists())

    def test_rejects_step07_rows_not_bound_to_step06_provenance(self) -> None:
        cases = (
            ("visual_decision_nodes.csv", "image_sha256", "0" * 64),
            ("visual_candidate_retrieval_records.csv", "source_pdf_sha256", "1" * 64),
            ("visual_review_approvals.csv", "page_number", "99"),
            ("visual_manual_review_queue.csv", "asset_id", "missing_asset"),
        )
        for filename, field, replacement in cases:
            with self.subTest(filename=filename, field=field), tempfile.TemporaryDirectory() as directory:
                fixture = PackageFixture(Path(directory))
                path = fixture.structured / filename
                document = exporter.read_csv_document(path)
                document["rows"][0][field] = replacement
                exporter.write_csv_document(path, document)

                with self.assertRaisesRegex(ValueError, "Step 06"):
                    fixture.export()
                self.assertFalse(fixture.destination.exists())

    def test_rejects_nonzero_human_approved_release_count(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = PackageFixture(Path(directory))
            summary_path = fixture.structured / "visual_logic_summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["n_human_approved_release_records"] = 1
            write_json(summary_path, summary)

            with self.assertRaisesRegex(ValueError, "zero human-approved/released"):
                fixture.export()
            self.assertFalse(fixture.destination.exists())

    def test_rejects_nonpending_approval_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = PackageFixture(Path(directory))
            path = fixture.structured / "visual_review_approvals.csv"
            document = exporter.read_csv_document(path)
            document["rows"][0]["review_status"] = "approved"
            exporter.write_csv_document(path, document)

            with self.assertRaisesRegex(ValueError, "every approval row"):
                fixture.export()
            self.assertFalse(fixture.destination.exists())

    def test_rejects_detail_that_is_reviewed_or_release_eligible(self) -> None:
        cases = (
            ("review_status", "approved", "non-pending review_status"),
            ("release_eligible", "True", "release-eligible record"),
        )
        for field, replacement, message in cases:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                fixture = PackageFixture(Path(directory))
                path = fixture.structured / "visual_decision_nodes.csv"
                document = exporter.read_csv_document(path)
                document["rows"][0][field] = replacement
                exporter.write_csv_document(path, document)

                with self.assertRaisesRegex(ValueError, message):
                    fixture.export()
                self.assertFalse(fixture.destination.exists())

    def test_rejects_candidate_that_is_not_valid_pending_and_unreleased(self) -> None:
        cases = (
            ("validation_status", "invalid"),
            ("review_status", "approved"),
            ("approval_status", "approved"),
            ("release_status", "released"),
            ("human_approved", "True"),
        )
        for field, replacement in cases:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                fixture = PackageFixture(Path(directory))
                path = fixture.structured / "visual_candidate_retrieval_records.csv"
                document = exporter.read_csv_document(path)
                document["rows"][0][field] = replacement
                exporter.write_csv_document(path, document)

                with self.assertRaisesRegex(ValueError, "Manual-review package candidate"):
                    fixture.export()
                self.assertFalse(fixture.destination.exists())

    def test_require_review_records_rejects_zero_valid_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = PackageFixture(Path(directory), candidate_count=0)
            with self.assertRaisesRegex(ValueError, "No valid visual candidates"):
                fixture.export(require_review_records=True)
            self.assertFalse(fixture.destination.exists())

    def test_rejects_outputs_destination_and_existing_nonempty_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = PackageFixture(Path(directory))
            with self.assertRaisesRegex(ValueError, "outside every outputs"):
                exporter.export_package(
                    raw_dir=fixture.raw,
                    structured_dir=fixture.structured,
                    destination=fixture.root / "outputs" / "review_package",
                    symbol_registry=fixture.registry,
                    require_review_records=True,
                )

            fixture.destination.mkdir(parents=True)
            (fixture.destination / "keep.txt").write_text("keep", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                fixture.export()
            self.assertEqual(
                (fixture.destination / "keep.txt").read_text(encoding="utf-8"),
                "keep",
            )


if __name__ == "__main__":
    unittest.main()
