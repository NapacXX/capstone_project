"""Tests for the fail-closed Step 06 retry merge utility."""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "merge_visual_logic_retry.py"
SPEC = importlib.util.spec_from_file_location("merge_visual_logic_retry", MODULE_PATH)
assert SPEC and SPEC.loader
merger = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = merger
SPEC.loader.exec_module(merger)


PASSES = ["structure", "actions", "symbols"]
TARGET = "page_009_tile_r02_c02"
FAILED_WARNING = (
    "structure: Ollama truncated the structure response at 6144 generated tokens. "
    "Increase the configured context/output budget."
)
MODEL = "qwen3-vl:8b-instruct"
DIGEST = "d" * 64
VERSION = "0.32.9"
PDF_HASH = "p" * 64

HEADERS = [
    "asset_id",
    "page_number",
    "asset_type",
    "image_path",
    "image_sha256",
    "evidence_bbox_space",
    "source_pdf",
    "source_pdf_sha256",
    "bbox_x0",
    "bbox_y0",
    "bbox_x1",
    "bbox_y1",
    "visual_logic_json",
    "model",
    "model_digest",
    "ollama_version",
    "extraction_passes",
    "status",
    "error_message",
    "figure_ids",
    "table_ids",
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def runtime(*, digest: str = DIGEST, version: str = VERSION) -> dict:
    return {
        "ollama_version": version,
        "model": MODEL,
        "model_digest": digest,
        "model_family": "qwen3vl",
        "model_architecture": "qwen3vl",
        "vision_probe_completed": True,
    }


def item(pass_name: str, asset_id: str) -> dict:
    return {
        "item_id": f"{asset_id}__{pass_name}__item",
        "item_type": "flowchart",
        "title": "Figure 9.4",
        "clinical_scope": "Type 2 diabetes",
        "symbols_or_footnotes": [],
        "ordinal_symbols": [],
        "decision_nodes": [],
        "recommendation_edges": [],
        "drug_actions": [],
        "retrieval_summary": "Visible evidence",
        "extraction_pass": pass_name,
    }


def payload(
    asset_id: str,
    page: int,
    configured_passes: list[str],
    successful_passes: list[str],
    *,
    image_hash: str,
    pdf_hash: str = PDF_HASH,
    digest: str = DIGEST,
    version: str = VERSION,
    warnings: list[str] | None = None,
) -> dict:
    return {
        "asset_id": asset_id,
        "page_number": page,
        "asset_type": "tile",
        "source_pdf": "/evidence/guideline.pdf",
        "source_pdf_sha256": pdf_hash,
        "image_sha256": image_hash,
        "evidence_bbox_space": "normalized_0_1000",
        "visual_items": [item(name, asset_id) for name in successful_passes],
        "extraction_passes": configured_passes,
        "extraction_warnings": list(warnings or []),
        "raw_model_responses": {name: f"raw {name}" for name in successful_passes},
        "raw_model_metrics": {name: {"eval_count": 10} for name in successful_passes},
        "runtime": runtime(digest=digest, version=version),
    }


def write_manifest(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows(rows)


def build_fixture(
    root: Path,
    *,
    expected_assets: int = 3,
    retry_overrides: dict[str, str | int] | None = None,
    overlap_retry_pass: bool = False,
    retry_passes: list[str] | None = None,
    include_exact_warning: bool = True,
) -> tuple[Path, Path, Path]:
    primary = root / "primary"
    retry = root / "retry"
    output = root / "merged"
    primary.mkdir()
    retry.mkdir()
    overrides = retry_overrides or {}

    asset_ids = ["page_002_tile_r01_c01", TARGET, "page_011_tile_r03_c02"]
    if expected_assets != 3:
        asset_ids = [f"asset_{index:03d}" for index in range(expected_assets - 1)] + [TARGET]
    primary_rows: list[dict[str, str]] = []
    for index, asset_id in enumerate(asset_ids, start=1):
        page = 9 if asset_id == TARGET else index
        image_hash = hashlib.sha256(asset_id.encode()).hexdigest()
        name = f"visual_logic_{asset_id}.json"
        is_target = asset_id == TARGET
        successful = ["actions", "symbols"] if is_target else list(PASSES)
        if is_target and overlap_retry_pass:
            successful = list(PASSES)
        warning_values = ["actions: keep this warning", "structure: similar but not exact"]
        if is_target and include_exact_warning:
            warning_values.append(FAILED_WARNING)
        data = payload(
            asset_id,
            page,
            list(PASSES),
            successful,
            image_hash=image_hash,
            warnings=warning_values if is_target else [],
        )
        (primary / name).write_text(json.dumps(data, indent=2), encoding="utf-8")
        primary_rows.append(
            {
                "asset_id": asset_id,
                "page_number": str(page),
                "asset_type": "tile",
                "image_path": f"/evidence/{asset_id}.png",
                "image_sha256": image_hash,
                "evidence_bbox_space": "normalized_0_1000",
                "source_pdf": "/evidence/guideline.pdf",
                "source_pdf_sha256": PDF_HASH,
                "bbox_x0": "1",
                "bbox_y0": "2",
                "bbox_x1": "3",
                "bbox_y1": "4",
                "visual_logic_json": str(primary / name),
                "model": MODEL,
                "model_digest": DIGEST,
                "ollama_version": VERSION,
                "extraction_passes": ";".join(PASSES),
                "status": "partial_extraction" if is_target else "extracted_unvalidated",
                "error_message": FAILED_WARNING if is_target else "",
                "figure_ids": "Figure 9.4" if is_target else "",
                "table_ids": "",
            }
        )
    write_manifest(primary / "visual_logic_manifest.csv", primary_rows)
    primary_run = {
        "started_at": "2026-08-23T15:00:00+00:00",
        "finished_at": "2026-08-23T16:00:00+00:00",
        "status": "completed_unvalidated",
        "model": MODEL,
        "model_digest": DIGEST,
        "ollama_version": VERSION,
        "model_family": "qwen3vl",
        "model_architecture": "qwen3vl",
        "extraction_passes": list(PASSES),
        "num_ctx": 12288,
        "num_predict": 6144,
        "n_assets": expected_assets,
        "status_counts": {
            "extracted_unvalidated": expected_assets - 1,
            "partial_extraction": 1,
        },
    }
    (primary / "extraction_run.json").write_text(
        json.dumps(primary_run, indent=2), encoding="utf-8"
    )

    retry_asset = str(overrides.get("asset_id", TARGET))
    retry_page = int(overrides.get("page_number", 9))
    retry_image = str(
        overrides.get("image_sha256", hashlib.sha256(TARGET.encode()).hexdigest())
    )
    retry_pdf = str(overrides.get("source_pdf_sha256", PDF_HASH))
    retry_digest = str(overrides.get("model_digest", DIGEST))
    retry_version = str(overrides.get("ollama_version", VERSION))
    selected_retry_passes = list(retry_passes or ["structure"])
    retry_name = f"visual_logic_{retry_asset}.json"
    retry_data = payload(
        retry_asset,
        retry_page,
        selected_retry_passes,
        selected_retry_passes,
        image_hash=retry_image,
        pdf_hash=retry_pdf,
        digest=retry_digest,
        version=retry_version,
        warnings=["structure: retry evidence needs review"],
    )
    (retry / retry_name).write_text(json.dumps(retry_data, indent=2), encoding="utf-8")
    retry_row = {
        "asset_id": retry_asset,
        "page_number": str(retry_page),
        "asset_type": "tile",
        "image_path": str(overrides.get("image_path", f"/evidence/{TARGET}.png")),
        "image_sha256": retry_image,
        "evidence_bbox_space": "normalized_0_1000",
        "source_pdf": str(overrides.get("source_pdf", "/evidence/guideline.pdf")),
        "source_pdf_sha256": retry_pdf,
        "bbox_x0": "1",
        "bbox_y0": "2",
        "bbox_x1": "3",
        "bbox_y1": "4",
        "visual_logic_json": str(retry / retry_name),
        "model": MODEL,
        "model_digest": retry_digest,
        "ollama_version": retry_version,
        "extraction_passes": ";".join(selected_retry_passes),
        "status": "extracted_unvalidated",
        "error_message": "",
        "figure_ids": "Figure 9.4",
        "table_ids": "",
    }
    write_manifest(retry / "visual_logic_manifest.csv", [retry_row])
    retry_run = {
        "started_at": "2026-08-23T16:01:00+00:00",
        "finished_at": "2026-08-23T16:03:00+00:00",
        "status": "completed_unvalidated",
        "model": MODEL,
        "model_digest": retry_digest,
        "ollama_version": retry_version,
        "model_family": "qwen3vl",
        "model_architecture": "qwen3vl",
        "extraction_passes": selected_retry_passes,
        "num_ctx": 16384,
        "num_predict": 8192,
        "n_assets": 1,
        "status_counts": {"extracted_unvalidated": 1},
    }
    (retry / "extraction_run.json").write_text(
        json.dumps(retry_run, indent=2), encoding="utf-8"
    )
    return primary, retry, output


class MergeVisualLogicRetryTest(unittest.TestCase):
    def test_success_writes_separate_complete_run_and_preserves_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            primary, retry, output = build_fixture(Path(directory))
            source_hashes = {
                path.name: sha256(path) for path in primary.iterdir() if path.is_file()
            }

            run = merger.merge_visual_logic_retry(
                primary, retry, output, expected_assets=3
            )

            self.assertEqual(run["status_counts"], {"extracted_unvalidated": 3})
            self.assertEqual(
                {path.name: sha256(path) for path in primary.iterdir() if path.is_file()},
                source_hashes,
            )
            with (output / "visual_logic_manifest.csv").open(newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 3)
            self.assertTrue(all(row["status"] == "extracted_unvalidated" for row in rows))
            self.assertTrue(all(Path(row["visual_logic_json"]).parent == Path(".") for row in rows))
            target = next(row for row in rows if row["asset_id"] == TARGET)
            self.assertEqual(target["error_message"], "")
            merged_payload = json.loads((output / target["visual_logic_json"]).read_text())
            self.assertEqual(set(merged_payload["raw_model_responses"]), set(PASSES))
            self.assertEqual(set(merged_payload["raw_model_metrics"]), set(PASSES))
            self.assertNotIn(FAILED_WARNING, merged_payload["extraction_warnings"])
            self.assertIn("structure: similar but not exact", merged_payload["extraction_warnings"])
            self.assertIn(
                "structure: retry evidence needs review",
                merged_payload["extraction_warnings"],
            )
            self.assertEqual(run["retry_provenance"][0]["asset_id"], TARGET)
            self.assertEqual(run["retry_provenance"][0]["extraction_passes"], ["structure"])
            non_target = "visual_logic_page_002_tile_r01_c01.json"
            self.assertEqual((output / non_target).read_bytes(), (primary / non_target).read_bytes())

    def test_rejects_identity_and_runtime_mismatches_without_output(self) -> None:
        cases = {
            "asset_id": "different_asset",
            "page_number": 99,
            "image_sha256": "i" * 64,
            "source_pdf_sha256": "q" * 64,
            "model_digest": "m" * 64,
            "ollama_version": "9.9.9",
        }
        for field, value in cases.items():
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                primary, retry, output = build_fixture(
                    Path(directory), retry_overrides={field: value}
                )
                with self.assertRaises(merger.MergeValidationError):
                    merger.merge_visual_logic_retry(
                        primary, retry, output, expected_assets=3
                    )
                self.assertFalse(output.exists())

    def test_rejects_overlapping_successful_pass_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            primary, retry, output = build_fixture(
                Path(directory), overlap_retry_pass=True
            )
            with self.assertRaisesRegex(
                merger.MergeValidationError, "successful pass names must be disjoint"
            ):
                merger.merge_visual_logic_retry(primary, retry, output, expected_assets=3)
            self.assertFalse(output.exists())

    def test_rejects_retry_with_more_than_one_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            primary, retry, output = build_fixture(
                Path(directory), retry_passes=["structure", "actions"]
            )
            with self.assertRaisesRegex(merger.MergeValidationError, "exactly one"):
                merger.merge_visual_logic_retry(primary, retry, output, expected_assets=3)
            self.assertFalse(output.exists())

    def test_rejects_missing_exact_original_error_warning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            primary, retry, output = build_fixture(
                Path(directory), include_exact_warning=False
            )
            with self.assertRaisesRegex(
                merger.MergeValidationError, "exactly one warning equal"
            ):
                merger.merge_visual_logic_retry(primary, retry, output, expected_assets=3)
            self.assertFalse(output.exists())

    def test_rejects_existing_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            primary, retry, output = build_fixture(Path(directory))
            output.mkdir()
            with self.assertRaisesRegex(merger.MergeValidationError, "refusing to overwrite"):
                merger.merge_visual_logic_retry(primary, retry, output, expected_assets=3)


if __name__ == "__main__":
    unittest.main()
