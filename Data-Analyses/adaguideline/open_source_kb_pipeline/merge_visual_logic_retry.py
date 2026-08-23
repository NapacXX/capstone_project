#!/usr/bin/env python3
"""Safely merge one successful Step 06 retry into a completed raw run.

The primary and retry directories are treated as immutable evidence.  A merge
is written to a new directory only after the two runs pass strict provenance,
runtime, pass-completeness, and status checks.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MANIFEST_NAME = "visual_logic_manifest.csv"
RUN_NAME = "extraction_run.json"
COMPLETED_STATUS = "completed_unvalidated"
EXTRACTED_STATUS = "extracted_unvalidated"
PARTIAL_STATUS = "partial_extraction"

REQUIRED_MANIFEST_FIELDS = {
    "asset_id",
    "page_number",
    "asset_type",
    "image_path",
    "image_sha256",
    "evidence_bbox_space",
    "source_pdf",
    "source_pdf_sha256",
    "visual_logic_json",
    "model",
    "model_digest",
    "ollama_version",
    "extraction_passes",
    "status",
    "error_message",
}

IDENTITY_FIELDS = (
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
    "figure_ids",
    "table_ids",
    "model",
    "model_digest",
    "ollama_version",
)

RUN_IDENTITY_FIELDS = (
    "model",
    "model_digest",
    "ollama_version",
    "model_family",
    "model_architecture",
)


class MergeValidationError(ValueError):
    """The primary/retry evidence is not safe to merge."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalized(value: Any) -> str:
    return "" if value is None else str(value).strip()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json_object(path: Path, description: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise MergeValidationError(f"{description} is not a regular file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MergeValidationError(f"Could not read {description} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise MergeValidationError(f"{description} must contain a JSON object: {path}")
    return value


def read_manifest(raw_dir: Path) -> tuple[list[str], list[dict[str, str]]]:
    path = raw_dir / MANIFEST_NAME
    if path.is_symlink() or not path.is_file():
        raise MergeValidationError(f"Missing regular-file manifest: {path}")
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            headers = list(reader.fieldnames or [])
            rows = list(reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise MergeValidationError(f"Could not read manifest {path}: {exc}") from exc
    if not headers or len(headers) != len(set(headers)):
        raise MergeValidationError(f"Manifest has an empty or duplicate header: {path}")
    missing = sorted(REQUIRED_MANIFEST_FIELDS - set(headers))
    if missing:
        raise MergeValidationError(f"Manifest is missing required fields {missing}: {path}")
    if any(None in row for row in rows):
        raise MergeValidationError(f"Manifest contains rows wider than its header: {path}")
    asset_ids = [normalized(row.get("asset_id")) for row in rows]
    if any(not asset_id for asset_id in asset_ids):
        raise MergeValidationError(f"Manifest contains a blank asset_id: {path}")
    duplicates = sorted({asset_id for asset_id in asset_ids if asset_ids.count(asset_id) > 1})
    if duplicates:
        raise MergeValidationError(f"Manifest contains duplicate asset_id values: {duplicates}")
    return headers, rows


def pass_list(value: Any, description: str) -> list[str]:
    if isinstance(value, str):
        passes = [part.strip() for part in value.split(";") if part.strip()]
    elif isinstance(value, list) and all(isinstance(part, str) for part in value):
        passes = [part.strip() for part in value if part.strip()]
    else:
        raise MergeValidationError(f"{description} must be a pass list")
    if not passes or len(passes) != len(set(passes)):
        raise MergeValidationError(f"{description} must contain unique, nonempty passes")
    return passes


def integer(value: Any, description: str) -> int:
    if isinstance(value, bool):
        raise MergeValidationError(f"{description} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise MergeValidationError(f"{description} must be an integer") from exc
    if normalized(value) not in {str(parsed), f"{parsed}.0"}:
        raise MergeValidationError(f"{description} must be an exact integer")
    return parsed


def status_counts(rows: list[dict[str, str]]) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in rows:
        status = normalized(row.get("status"))
        result[status] = result.get(status, 0) + 1
    return dict(sorted(result.items()))


def validate_run_counts(
    run: dict[str, Any], rows: list[dict[str, str]], description: str
) -> None:
    if normalized(run.get("status")) != COMPLETED_STATUS:
        raise MergeValidationError(
            f"{description} status must be {COMPLETED_STATUS!r}, got {run.get('status')!r}"
        )
    if integer(run.get("n_assets"), f"{description} n_assets") != len(rows):
        raise MergeValidationError(f"{description} n_assets does not match its manifest")
    reported = run.get("status_counts")
    if not isinstance(reported, dict):
        raise MergeValidationError(f"{description} status_counts must be an object")
    try:
        normalized_reported = {
            normalized(key): integer(value, f"{description} status_counts[{key!r}]")
            for key, value in reported.items()
        }
    except AttributeError as exc:
        raise MergeValidationError(f"{description} status_counts must be an object") from exc
    if normalized_reported != status_counts(rows):
        raise MergeValidationError(f"{description} status_counts does not match its manifest")


def payload_path(raw_dir: Path, row: dict[str, str]) -> Path:
    raw_value = normalized(row.get("visual_logic_json"))
    name = Path(raw_value).name
    if not raw_value or name in {"", ".", RUN_NAME, MANIFEST_NAME}:
        raise MergeValidationError(
            f"Asset {row.get('asset_id')!r} has an invalid visual_logic_json path"
        )
    path = raw_dir / name
    if path.is_symlink() or not path.is_file():
        raise MergeValidationError(
            f"Asset {row.get('asset_id')!r} payload is not a regular file in {raw_dir}: {name}"
        )
    return path


def validate_payload_set(
    raw_dir: Path, rows: list[dict[str, str]]
) -> dict[str, tuple[Path, dict[str, Any]]]:
    paths: dict[str, tuple[Path, dict[str, Any]]] = {}
    names: list[str] = []
    for row in rows:
        asset_id = normalized(row["asset_id"])
        path = payload_path(raw_dir, row)
        names.append(path.name)
        paths[asset_id] = (path, read_json_object(path, f"payload for {asset_id}"))
    if len(names) != len(set(names)):
        raise MergeValidationError("Manifest maps multiple assets to the same payload filename")
    actual = {
        path.name
        for path in raw_dir.glob("*.json")
        if path.name != RUN_NAME
    }
    expected = set(names)
    if actual != expected:
        raise MergeValidationError(
            "Raw directory JSON set does not exactly match its manifest "
            f"(missing={sorted(expected - actual)}, extra={sorted(actual - expected)})"
        )
    return paths


def validate_row_and_payload(
    row: dict[str, str],
    payload: dict[str, Any],
    run: dict[str, Any],
    *,
    description: str,
) -> set[str]:
    asset_id = normalized(row.get("asset_id"))
    if normalized(payload.get("asset_id")) != asset_id:
        raise MergeValidationError(f"{description} payload asset_id does not match its manifest")
    if integer(payload.get("page_number"), f"{description} payload page_number") != integer(
        row.get("page_number"), f"{description} manifest page_number"
    ):
        raise MergeValidationError(f"{description} payload page_number does not match its manifest")
    for field in (
        "asset_type",
        "image_sha256",
        "evidence_bbox_space",
        "source_pdf",
        "source_pdf_sha256",
    ):
        if normalized(payload.get(field)) != normalized(row.get(field)):
            raise MergeValidationError(
                f"{description} payload {field} does not match its manifest"
            )

    row_passes = pass_list(row.get("extraction_passes"), f"{description} manifest passes")
    run_passes = pass_list(run.get("extraction_passes"), f"{description} run passes")
    payload_passes = pass_list(payload.get("extraction_passes"), f"{description} payload passes")
    if row_passes != run_passes or payload_passes != run_passes:
        raise MergeValidationError(f"{description} configured pass lists do not match")

    for field in ("model", "model_digest", "ollama_version"):
        if not normalized(row.get(field)) or normalized(row.get(field)) != normalized(run.get(field)):
            raise MergeValidationError(
                f"{description} manifest {field} does not match the extraction run"
            )
    runtime = payload.get("runtime")
    if not isinstance(runtime, dict):
        raise MergeValidationError(f"{description} payload runtime must be an object")
    for field in RUN_IDENTITY_FIELDS:
        if normalized(runtime.get(field)) != normalized(run.get(field)):
            raise MergeValidationError(
                f"{description} payload runtime {field} does not match the extraction run"
            )

    responses = payload.get("raw_model_responses")
    metrics = payload.get("raw_model_metrics")
    items = payload.get("visual_items")
    warnings = payload.get("extraction_warnings")
    if not isinstance(responses, dict) or not all(isinstance(key, str) for key in responses):
        raise MergeValidationError(f"{description} raw_model_responses must be an object")
    if not isinstance(metrics, dict) or not all(isinstance(key, str) for key in metrics):
        raise MergeValidationError(f"{description} raw_model_metrics must be an object")
    if set(responses) != set(metrics):
        raise MergeValidationError(f"{description} response and metric pass names do not match")
    successful = set(responses)
    if not successful <= set(run_passes):
        raise MergeValidationError(f"{description} contains an unconfigured successful pass")
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise MergeValidationError(f"{description} visual_items must be an array of objects")
    item_passes = {normalized(item.get("extraction_pass")) for item in items}
    if "" in item_passes or not item_passes <= successful:
        raise MergeValidationError(f"{description} item pass names are not successful pass names")
    if not isinstance(warnings, list) or not all(isinstance(value, str) for value in warnings):
        raise MergeValidationError(f"{description} extraction_warnings must be strings")
    return successful


def compare_identity(
    primary_row: dict[str, str],
    retry_row: dict[str, str],
    primary_run: dict[str, Any],
    retry_run: dict[str, Any],
) -> None:
    for field in IDENTITY_FIELDS:
        if normalized(primary_row.get(field)) != normalized(retry_row.get(field)):
            raise MergeValidationError(f"Retry {field} does not match the primary asset")
    for field in RUN_IDENTITY_FIELDS:
        if normalized(primary_run.get(field)) != normalized(retry_run.get(field)):
            raise MergeValidationError(f"Retry run {field} does not match the primary run")


def validate_directory_relationships(primary: Path, retry: Path, output: Path) -> None:
    primary_resolved = primary.resolve()
    retry_resolved = retry.resolve()
    output_resolved = output.resolve(strict=False)
    if primary_resolved == retry_resolved:
        raise MergeValidationError("Primary and retry directories must be different")
    if output.exists():
        raise MergeValidationError(f"Output directory already exists; refusing to overwrite: {output}")
    for source in (primary_resolved, retry_resolved):
        if output_resolved == source or source in output_resolved.parents:
            raise MergeValidationError("Output directory must be separate from both source directories")
    if not primary.is_dir() or primary.is_symlink():
        raise MergeValidationError(f"Primary raw directory is not a regular directory: {primary}")
    if not retry.is_dir() or retry.is_symlink():
        raise MergeValidationError(f"Retry raw directory is not a regular directory: {retry}")


def merge_visual_logic_retry(
    primary_raw_dir: Path,
    retry_raw_dir: Path,
    output_dir: Path,
    *,
    expected_assets: int,
) -> dict[str, Any]:
    """Validate and merge a one-pass retry, returning the written run record."""
    primary = Path(primary_raw_dir)
    retry = Path(retry_raw_dir)
    output = Path(output_dir)
    if expected_assets <= 0:
        raise MergeValidationError("expected_assets must be positive")
    validate_directory_relationships(primary, retry, output)

    primary_headers, primary_rows = read_manifest(primary)
    retry_headers, retry_rows = read_manifest(retry)
    if primary_headers != retry_headers:
        raise MergeValidationError("Primary and retry manifest headers do not match exactly")
    if len(primary_rows) != expected_assets:
        raise MergeValidationError(
            f"Primary manifest has {len(primary_rows)} rows; expected {expected_assets}"
        )
    if len(retry_rows) != 1:
        raise MergeValidationError("Retry manifest must contain exactly one asset")

    primary_run_path = primary / RUN_NAME
    retry_run_path = retry / RUN_NAME
    primary_run = read_json_object(primary_run_path, "primary extraction run")
    retry_run = read_json_object(retry_run_path, "retry extraction run")
    validate_run_counts(primary_run, primary_rows, "Primary run")
    validate_run_counts(retry_run, retry_rows, "Retry run")
    primary_payloads = validate_payload_set(primary, primary_rows)
    retry_payloads = validate_payload_set(retry, retry_rows)

    retry_row = retry_rows[0]
    asset_id = normalized(retry_row["asset_id"])
    primary_by_id = {normalized(row["asset_id"]): row for row in primary_rows}
    if asset_id not in primary_by_id:
        raise MergeValidationError(f"Retry asset {asset_id!r} is absent from the primary manifest")
    primary_row = primary_by_id[asset_id]
    compare_identity(primary_row, retry_row, primary_run, retry_run)

    primary_success: dict[str, set[str]] = {}
    for row in primary_rows:
        row_asset = normalized(row["asset_id"])
        primary_success[row_asset] = validate_row_and_payload(
            row,
            primary_payloads[row_asset][1],
            primary_run,
            description=f"Primary asset {row_asset}",
        )
    retry_success = validate_row_and_payload(
        retry_row,
        retry_payloads[asset_id][1],
        retry_run,
        description=f"Retry asset {asset_id}",
    )

    primary_passes = pass_list(primary_run.get("extraction_passes"), "primary run passes")
    retry_passes = pass_list(retry_run.get("extraction_passes"), "retry run passes")
    if len(retry_passes) != 1:
        raise MergeValidationError("Retry must contain exactly one configured pass")
    retry_pass = retry_passes[0]
    if retry_success != {retry_pass}:
        raise MergeValidationError("Retry pass did not complete successfully")
    if retry_pass not in primary_passes:
        raise MergeValidationError("Retry pass was not configured in the primary run")
    if primary_success[asset_id] & retry_success:
        raise MergeValidationError("Primary and retry successful pass names must be disjoint")
    missing_target_passes = set(primary_passes) - primary_success[asset_id]
    if missing_target_passes != retry_success:
        raise MergeValidationError(
            "Retry must supply every and only missing successful pass for its primary asset"
        )

    for row in primary_rows:
        row_asset = normalized(row["asset_id"])
        if row_asset == asset_id:
            if normalized(row.get("status")) != PARTIAL_STATUS:
                raise MergeValidationError("Retry target must be partial_extraction in the primary run")
        else:
            if normalized(row.get("status")) != EXTRACTED_STATUS:
                raise MergeValidationError(
                    f"Non-target primary asset {row_asset} is not fully extracted"
                )
            if primary_success[row_asset] != set(primary_passes):
                raise MergeValidationError(
                    f"Non-target primary asset {row_asset} is missing a configured pass"
                )
            if normalized(row.get("error_message")):
                raise MergeValidationError(
                    f"Non-target primary asset {row_asset} has an extraction error"
                )
    if normalized(retry_row.get("status")) != EXTRACTED_STATUS:
        raise MergeValidationError("Retry manifest status must be extracted_unvalidated")
    if normalized(retry_row.get("error_message")):
        raise MergeValidationError("Successful retry manifest must not contain an error_message")

    primary_payload = primary_payloads[asset_id][1]
    retry_payload = retry_payloads[asset_id][1]
    original_error = normalized(primary_row.get("error_message"))
    if not original_error.startswith(f"{retry_pass}:"):
        raise MergeValidationError(
            "Primary error_message is not the failed retry-pass error"
        )
    warnings = primary_payload["extraction_warnings"]
    exact_error_indexes = [index for index, warning in enumerate(warnings) if warning == original_error]
    if len(exact_error_indexes) != 1:
        raise MergeValidationError(
            "Primary payload must contain exactly one warning equal to its retry-pass error_message"
        )

    merged_payload = deepcopy(primary_payload)
    merged_payload["visual_items"] = deepcopy(primary_payload["visual_items"]) + deepcopy(
        retry_payload["visual_items"]
    )
    merged_payload["raw_model_responses"] = {
        **deepcopy(primary_payload["raw_model_responses"]),
        **deepcopy(retry_payload["raw_model_responses"]),
    }
    merged_payload["raw_model_metrics"] = {
        **deepcopy(primary_payload["raw_model_metrics"]),
        **deepcopy(retry_payload["raw_model_metrics"]),
    }
    remove_index = exact_error_indexes[0]
    merged_payload["extraction_warnings"] = [
        warning for index, warning in enumerate(warnings) if index != remove_index
    ] + deepcopy(retry_payload["extraction_warnings"])

    merged_rows = deepcopy(primary_rows)
    for row in merged_rows:
        row_asset = normalized(row["asset_id"])
        source_path = primary_payloads[row_asset][0]
        row["visual_logic_json"] = source_path.name
        if row_asset == asset_id:
            row["status"] = EXTRACTED_STATUS
            row["error_message"] = ""
            row["extraction_passes"] = ";".join(primary_passes)

    merged_run = deepcopy(primary_run)
    merged_run["status"] = COMPLETED_STATUS
    merged_run["n_assets"] = len(merged_rows)
    merged_run["status_counts"] = status_counts(merged_rows)
    merged_run["merged_at"] = utc_now()
    merged_run["retry_provenance"] = [
        {
            "merge_utility": Path(__file__).name,
            "asset_id": asset_id,
            "extraction_passes": retry_passes,
            "primary_raw_dir": str(primary.resolve()),
            "primary_manifest_sha256": sha256_file(primary / MANIFEST_NAME),
            "primary_extraction_run_sha256": sha256_file(primary_run_path),
            "primary_payload_sha256": sha256_file(primary_payloads[asset_id][0]),
            "retry_raw_dir": str(retry.resolve()),
            "retry_started_at": retry_run.get("started_at", ""),
            "retry_finished_at": retry_run.get("finished_at", ""),
            "retry_num_ctx": retry_run.get("num_ctx"),
            "retry_num_predict": retry_run.get("num_predict"),
            "retry_manifest_sha256": sha256_file(retry / MANIFEST_NAME),
            "retry_extraction_run_sha256": sha256_file(retry_run_path),
            "retry_payload_sha256": sha256_file(retry_payloads[asset_id][0]),
            "replaced_primary_error": original_error,
        }
    ]

    if merged_run["status_counts"] != {EXTRACTED_STATUS: expected_assets}:
        raise MergeValidationError("Merged manifest is not fully extracted")
    if set(merged_payload["raw_model_responses"]) != set(primary_passes):
        raise MergeValidationError("Merged payload does not contain every configured response pass")
    if set(merged_payload["raw_model_metrics"]) != set(primary_passes):
        raise MergeValidationError("Merged payload does not contain every configured metric pass")

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output.name}.tmp-", dir=output.parent) as temp_name:
        staging = Path(temp_name)
        for row in primary_rows:
            row_asset = normalized(row["asset_id"])
            source_path = primary_payloads[row_asset][0]
            destination_path = staging / source_path.name
            if row_asset == asset_id:
                destination_path.write_text(
                    json.dumps(merged_payload, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            else:
                shutil.copy2(source_path, destination_path)

        with (staging / MANIFEST_NAME).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=primary_headers, extrasaction="raise")
            writer.writeheader()
            writer.writerows(merged_rows)
        (staging / RUN_NAME).write_text(
            json.dumps(merged_run, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        written_headers, written_rows = read_manifest(staging)
        if written_headers != primary_headers or len(written_rows) != expected_assets:
            raise MergeValidationError("Staged merged manifest failed write verification")
        written_run = read_json_object(staging / RUN_NAME, "staged merged extraction run")
        validate_run_counts(written_run, written_rows, "Staged merged run")
        validate_payload_set(staging, written_rows)
        os.replace(staging, output)

    return merged_run


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Merge one successful one-pass Step 06 retry into a completed raw run, "
            "writing a separate destination without modifying either source."
        )
    )
    parser.add_argument("--primary-raw-dir", required=True)
    parser.add_argument("--retry-raw-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--expected-assets", required=True, type=int)
    args = parser.parse_args()
    try:
        run = merge_visual_logic_retry(
            Path(args.primary_raw_dir),
            Path(args.retry_raw_dir),
            Path(args.output_dir),
            expected_assets=args.expected_assets,
        )
    except (MergeValidationError, OSError) as exc:
        raise SystemExit(f"Retry merge failed: {exc}") from exc
    provenance = run["retry_provenance"][0]
    print(
        "Merged retry for "
        f"{provenance['asset_id']} ({';'.join(provenance['extraction_passes'])})."
    )
    print(f"Wrote {run['n_assets']} assets to {args.output_dir}.")
    print(f"Status counts: {run['status_counts']}")
    print("Primary and retry source directories were not modified.")


if __name__ == "__main__":
    main()
