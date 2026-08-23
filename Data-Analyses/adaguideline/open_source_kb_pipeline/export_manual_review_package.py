#!/usr/bin/env python3
"""Export a sanitized, auditable visual-guideline manual-review package.

The canonical Step 06 and Step 07 directories remain under ``outputs/`` and
are never modified by this script.  The exporter copies only an explicit
allowlist of review-critical CSV/JSON metadata to a destination outside any
``outputs`` directory.  Source PDFs, rendered images, raw per-asset model JSON,
release records, enhanced knowledge-base files, and vector indexes are never
copied.

Absolute local paths are replaced with logical references.  Reviewers must use
controlled copies of the source PDF and rendered images whose SHA-256 values
match the package manifest; this package is not self-sufficient source evidence.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import tempfile
from collections import Counter
from pathlib import Path, PureWindowsPath
from typing import Any, Iterable


SCHEMA_VERSION = "1.0"
EXTRACTED_STATUS = "extracted_unvalidated"
PENDING_STATUS = "pending"
UNRELEASED_STATUS = "unreleased"

RAW_FILES = {
    "extraction_run.json": "Step 06 execution metadata",
    "visual_logic_manifest.csv": "Step 06 asset and source-hash manifest",
}

STRUCTURED_FILES = {
    "visual_logic_summary.json": "Step 07 validation summary",
    "visual_review_approvals.csv": "Reviewer-editable approval sheet",
    "visual_manual_review_queue.csv": "Validation and review triage queue",
    "visual_decision_nodes.csv": "Detailed decision-node candidates",
    "visual_recommendation_edges.csv": "Detailed recommendation-edge candidates",
    "visual_drug_actions.csv": "Detailed medication-action candidates",
    "visual_symbols_footnotes.csv": "Detailed symbol and footnote candidates",
    "visual_ordinal_symbols.csv": "Detailed ordinal-symbol candidates",
    "visual_candidate_retrieval_records.csv": "Valid, unreleased retrieval candidates",
}

DETAIL_FILES = (
    "visual_decision_nodes.csv",
    "visual_recommendation_edges.csv",
    "visual_drug_actions.csv",
    "visual_symbols_footnotes.csv",
    "visual_ordinal_symbols.csv",
)

CLINICAL_QUEUE_RECORD_TYPES = {"node", "edge", "action", "footnote", "symbol"}
MANIFEST_PROVENANCE_FIELDS = (
    "page_number",
    "source_pdf_sha256",
    "image_sha256",
)

SUMMARY_COUNT_FILES = {
    "n_decision_nodes": "visual_decision_nodes.csv",
    "n_recommendation_edges": "visual_recommendation_edges.csv",
    "n_drug_actions": "visual_drug_actions.csv",
    "n_symbols_or_footnotes": "visual_symbols_footnotes.csv",
    "n_ordinal_symbols": "visual_ordinal_symbols.csv",
    "n_valid_candidate_records": "visual_candidate_retrieval_records.csv",
    "n_manual_review_items": "visual_manual_review_queue.csv",
}

PATH_FIELD_PREFIXES = {
    "source_pdf": "CONTROLLED_SOURCE_PDF",
    "image_path": "CONTROLLED_SOURCE_IMAGE",
    "source_json": "OMITTED_RAW_MODEL_JSON",
    "visual_logic_json": "OMITTED_RAW_MODEL_JSON",
    "image_manifest": "OMITTED_IMAGE_MANIFEST",
    "approval_csv": "PACKAGE",
    "symbol_registry": "PIPELINE_METADATA",
}

SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
POSIX_ABSOLUTE_FRAGMENT_RE = re.compile(
    r"(?<![:/A-Za-z0-9_.-])/(?:[^/\s,;'\"<>]+/)+[^/\s,;'\"<>]+"
)
WINDOWS_ABSOLUTE_FRAGMENT_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])[A-Za-z]:\\(?:[^\s,;'\"<>]+\\)*[^\s,;'\"<>]*"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def is_absolute_path(value: str) -> bool:
    text = value.strip()
    if not text:
        return False
    return Path(text).is_absolute() or PureWindowsPath(text).is_absolute()


def logical_path(field: str | None, value: str) -> str:
    normalized = value.replace("\\", "/").rstrip("/")
    basename = normalized.rsplit("/", 1)[-1] or "path"
    prefix = PATH_FIELD_PREFIXES.get(field or "", "SANITIZED_ABSOLUTE_PATH")
    return f"{prefix}/{basename}"


def iter_strings(value: Any) -> Iterable[tuple[str | None, str]]:
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(child, str):
                yield str(key), child
            else:
                yield from iter_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_strings(child)


def build_absolute_path_map(documents: Iterable[Any]) -> dict[str, str]:
    replacements: dict[str, str] = {}
    for document in documents:
        for field, value in iter_strings(document):
            if is_absolute_path(value):
                replacements[value] = logical_path(field, value)
    return replacements


def sanitize_string(
    value: str,
    *,
    field: str | None,
    replacements: dict[str, str],
) -> str:
    if field in PATH_FIELD_PREFIXES and value.strip():
        return logical_path(field, value)
    if is_absolute_path(value):
        return logical_path(field, value)

    sanitized = value
    for source, replacement in sorted(
        replacements.items(), key=lambda pair: len(pair[0]), reverse=True
    ):
        sanitized = sanitized.replace(source, replacement)
    sanitized = POSIX_ABSOLUTE_FRAGMENT_RE.sub(
        lambda match: logical_path(None, match.group(0)), sanitized
    )
    sanitized = WINDOWS_ABSOLUTE_FRAGMENT_RE.sub(
        lambda match: logical_path(None, match.group(0)), sanitized
    )
    return sanitized


def sanitize_value(
    value: Any,
    *,
    field: str | None = None,
    replacements: dict[str, str],
) -> Any:
    if isinstance(value, dict):
        return {
            key: sanitize_value(child, field=str(key), replacements=replacements)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [
            sanitize_value(child, field=field, replacements=replacements)
            for child in value
        ]
    if isinstance(value, str):
        return sanitize_string(value, field=field, replacements=replacements)
    return value


def assert_sanitized(value: Any, *, location: str) -> None:
    for _field, text in iter_strings(value):
        if (
            is_absolute_path(text)
            or POSIX_ABSOLUTE_FRAGMENT_RE.search(text)
            or WINDOWS_ABSOLUTE_FRAGMENT_RE.search(text)
        ):
            raise ValueError(f"absolute path remained after sanitization in {location}")


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path.name}: {exc}") from exc


def read_csv_document(path: Path) -> dict[str, Any]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"CSV has no header: {path.name}")
        rows = [dict(row) for row in reader]
    return {"fieldnames": list(reader.fieldnames), "rows": rows}


def write_csv_document(path: Path, document: dict[str, Any]) -> None:
    fieldnames = document["fieldnames"]
    rows = document["rows"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def require_files(directory: Path, names: Iterable[str], label: str) -> None:
    if not directory.is_dir():
        raise FileNotFoundError(f"{label} directory does not exist: {directory}")
    missing = sorted(name for name in names if not (directory / name).is_file())
    if missing:
        raise FileNotFoundError(f"{label} is missing required files: {', '.join(missing)}")


def integer(value: Any, *, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an integer") from exc
    if parsed < 0:
        raise ValueError(f"{label} must not be negative")
    return parsed


def normalized(value: Any) -> str:
    return "" if value is None else str(value).strip()


def is_explicit_false(value: Any) -> bool:
    return normalized(value).lower() in {"false", "0", "no"}


def require_columns(document: dict[str, Any], fields: set[str], filename: str) -> None:
    missing = fields - set(document["fieldnames"])
    if missing:
        raise ValueError(
            f"{filename} is missing fields required for fail-closed review export: "
            + ", ".join(sorted(missing))
        )


def manifest_asset_index(manifest: dict[str, Any]) -> dict[str, dict[str, str]]:
    require_columns(
        manifest,
        {
            "asset_id",
            "page_number",
            "source_pdf_sha256",
            "image_sha256",
            "status",
        },
        "visual_logic_manifest.csv",
    )
    by_asset: dict[str, dict[str, str]] = {}
    for row_number, row in enumerate(manifest["rows"], start=2):
        asset_id = normalized(row.get("asset_id"))
        if not asset_id:
            raise ValueError(
                f"visual_logic_manifest.csv row {row_number} has an empty asset_id"
            )
        if asset_id in by_asset:
            raise ValueError(
                f"visual_logic_manifest.csv contains duplicate asset_id {asset_id!r}"
            )
        by_asset[asset_id] = row
    return by_asset


def provenance_value(field: str, value: Any, *, label: str) -> str:
    if field == "page_number":
        return str(integer(value, label=label))
    return normalized(value).lower()


def bind_rows_to_manifest(
    filename: str,
    document: dict[str, Any],
    manifest_by_asset: dict[str, dict[str, str]],
    *,
    rows: list[dict[str, str]] | None = None,
) -> None:
    """Fail if any selected Step 07 row is not bound to its Step 06 asset."""
    require_columns(document, {"asset_id"}, filename)
    selected_rows = document["rows"] if rows is None else rows
    available_fields = set(document["fieldnames"])
    for row_number, row in enumerate(selected_rows, start=2):
        asset_id = normalized(row.get("asset_id"))
        manifest_row = manifest_by_asset.get(asset_id)
        if manifest_row is None:
            raise ValueError(
                f"{filename} row {row_number} asset_id {asset_id!r} is absent "
                "from the Step 06 manifest"
            )
        for field in MANIFEST_PROVENANCE_FIELDS:
            if field not in available_fields:
                continue
            observed = provenance_value(
                field,
                row.get(field),
                label=f"{filename} row {row_number} {field}",
            )
            expected = provenance_value(
                field,
                manifest_row.get(field),
                label=f"Step 06 asset {asset_id} {field}",
            )
            if observed != expected:
                raise ValueError(
                    f"{filename} row {row_number} {field} does not match "
                    f"Step 06 asset {asset_id!r}"
                )


def bind_rows_to_details(
    filename: str,
    document: dict[str, Any],
    details_by_id: dict[str, dict[str, str]],
    *,
    fields: tuple[str, ...],
    rows: list[dict[str, str]] | None = None,
) -> None:
    """Bind repeated review rows to the canonical Step 07 detail row."""
    selected_rows = document["rows"] if rows is None else rows
    available_fields = set(document["fieldnames"])
    for row_number, row in enumerate(selected_rows, start=2):
        record_id = normalized(row.get("record_id"))
        detail = details_by_id.get(record_id)
        if detail is None:
            raise ValueError(
                f"{filename} row {row_number} record_id {record_id!r} is absent "
                "from the detailed clinical records"
            )
        for field in fields:
            if field not in available_fields or field not in detail:
                continue
            if normalized(row.get(field)) != normalized(detail.get(field)):
                raise ValueError(
                    f"{filename} row {row_number} {field} does not match "
                    f"detailed record {record_id!r}"
                )


def validate_sources(
    raw_json: dict[str, Any],
    raw_manifest: dict[str, Any],
    structured_json: dict[str, Any],
    structured_csvs: dict[str, dict[str, Any]],
    *,
    require_review_records: bool,
) -> dict[str, Any]:
    if raw_json.get("status") != "completed_unvalidated":
        raise ValueError(
            "Step 06 extraction_run.json is not complete: expected status "
            f"'completed_unvalidated', found {raw_json.get('status')!r}"
        )

    manifest_rows = raw_manifest["rows"]
    run_assets = integer(raw_json.get("n_assets"), label="Step 06 n_assets")
    if run_assets != len(manifest_rows):
        raise ValueError(
            "Step 06 run/manifest mismatch: "
            f"n_assets={run_assets}, manifest rows={len(manifest_rows)}"
        )
    manifest_by_asset = manifest_asset_index(raw_manifest)

    observed_statuses = Counter(normalized(row.get("status")) for row in manifest_rows)
    declared_statuses = raw_json.get("status_counts")
    if isinstance(declared_statuses, dict):
        normalized_declared = {
            normalized(key): integer(value, label=f"Step 06 status_counts[{key!r}]")
            for key, value in declared_statuses.items()
        }
        if normalized_declared != dict(observed_statuses):
            raise ValueError(
                "Step 06 status_counts do not match visual_logic_manifest.csv"
            )
    expected_statuses = {EXTRACTED_STATUS: run_assets}
    if dict(observed_statuses) != expected_statuses:
        raise ValueError(
            "Step 06 is not fully extracted: every visual_logic_manifest.csv "
            f"row must have status {EXTRACTED_STATUS!r}; observed "
            f"{dict(observed_statuses)!r}"
        )

    csv_counts = {
        name: len(document["rows"]) for name, document in structured_csvs.items()
    }
    for summary_field, filename in SUMMARY_COUNT_FILES.items():
        declared = integer(
            structured_json.get(summary_field), label=f"Step 07 {summary_field}"
        )
        if declared != csv_counts[filename]:
            raise ValueError(
                f"Step 07 summary mismatch for {summary_field}: "
                f"summary={declared}, {filename} rows={csv_counts[filename]}"
            )

    visual_assets = integer(
        structured_json.get("n_visual_assets"), label="Step 07 n_visual_assets"
    )
    if visual_assets != len(manifest_rows):
        raise ValueError(
            "Step 06/07 asset-count mismatch: "
            f"manifest rows={len(manifest_rows)}, Step 07 assets={visual_assets}"
        )

    released_records = integer(
        structured_json.get("n_human_approved_release_records"),
        label="Step 07 n_human_approved_release_records",
    )
    if released_records != 0:
        raise ValueError(
            "Manual-review package export requires zero human-approved/released "
            f"records; Step 07 reports {released_records}"
        )

    detail_ids: list[str] = []
    detail_rows: list[dict[str, str]] = []
    for filename in DETAIL_FILES:
        document = structured_csvs[filename]
        require_columns(
            document,
            {"record_id", "asset_id", "review_status", "release_eligible"},
            filename,
        )
        for row in document["rows"]:
            if normalized(row.get("review_status")).lower() != PENDING_STATUS:
                raise ValueError(
                    f"{filename} contains a non-pending review_status in a "
                    "manual-review baseline"
                )
            if not is_explicit_false(row.get("release_eligible")):
                raise ValueError(
                    f"{filename} contains a release-eligible record in a "
                    "manual-review baseline"
                )
        detail_ids.extend(row.get("record_id", "") for row in document["rows"])
        detail_rows.extend(document["rows"])
    if "" in detail_ids:
        raise ValueError("A detailed candidate row has an empty record_id")
    duplicates = sorted(
        record_id for record_id, count in Counter(detail_ids).items() if count > 1
    )
    if duplicates:
        raise ValueError(
            "Detailed candidate record_id values are not unique: "
            + ", ".join(duplicates[:5])
        )
    details_by_id = {row["record_id"]: row for row in detail_rows}

    approvals = structured_csvs["visual_review_approvals.csv"]
    require_columns(
        approvals,
        {"record_id", "asset_id", "page_number", "content_sha256", "review_status"},
        "visual_review_approvals.csv",
    )
    approval_ids = [row.get("record_id", "") for row in approvals["rows"]]
    if "" in approval_ids or len(approval_ids) != len(set(approval_ids)):
        raise ValueError("visual_review_approvals.csv contains duplicate record_id values")
    if set(approval_ids) != set(detail_ids):
        raise ValueError(
            "Approval rows do not exactly match the detailed clinical candidate records"
        )
    for row in approvals["rows"]:
        content_hash = row.get("content_sha256", "")
        if not SHA256_RE.fullmatch(content_hash):
            raise ValueError(
                "visual_review_approvals.csv contains an invalid content_sha256"
            )
        if normalized(row.get("review_status")).lower() != PENDING_STATUS:
            raise ValueError(
                "Manual-review package export requires every approval row to be "
                f"{PENDING_STATUS!r}"
            )

    candidates = structured_csvs["visual_candidate_retrieval_records.csv"]
    require_columns(
        candidates,
        {
            "record_id",
            "asset_id",
            "page_number",
            "source_pdf_sha256",
            "image_sha256",
            "validation_status",
            "review_status",
            "human_approved",
            "approval_status",
            "release_status",
        },
        "visual_candidate_retrieval_records.csv",
    )
    candidate_rows = candidates["rows"]
    candidate_ids = [row.get("record_id", "") for row in candidate_rows]
    if "" in candidate_ids or len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError(
            "visual_candidate_retrieval_records.csv contains empty or duplicate record_id values"
        )
    if not set(candidate_ids).issubset(detail_ids):
        raise ValueError(
            "A valid retrieval candidate is missing from the detailed clinical tables"
        )
    for row in candidate_rows:
        statuses = {
            "validation_status": "valid",
            "review_status": PENDING_STATUS,
            "approval_status": PENDING_STATUS,
            "release_status": UNRELEASED_STATUS,
        }
        for field, expected in statuses.items():
            if normalized(row.get(field)).lower() != expected:
                raise ValueError(
                    "Manual-review package candidate "
                    f"{row.get('record_id')!r} must have {field}={expected!r}"
                )
        if not is_explicit_false(row.get("human_approved")):
            raise ValueError(
                "Manual-review package candidate "
                f"{row.get('record_id')!r} must not be human-approved"
            )
    if require_review_records and not candidate_rows:
        raise ValueError(
            "No valid visual candidates are available; refusing to create a review package"
        )

    review_queue = structured_csvs["visual_manual_review_queue.csv"]
    require_columns(
        review_queue,
        {"record_id", "record_type", "asset_id", "review_status", "release_eligible"},
        "visual_manual_review_queue.csv",
    )
    clinical_queue_rows = [
        row
        for row in review_queue["rows"]
        if normalized(row.get("record_type")).lower()
        in CLINICAL_QUEUE_RECORD_TYPES
    ]
    for row in clinical_queue_rows:
        if normalized(row.get("review_status")).lower() != PENDING_STATUS:
            raise ValueError(
                "Clinical visual_manual_review_queue.csv rows must remain pending"
            )
        if not is_explicit_false(row.get("release_eligible")):
            raise ValueError(
                "Clinical visual_manual_review_queue.csv rows must remain unreleased"
            )
    clinical_queue_ids = [row.get("record_id", "") for row in clinical_queue_rows]
    if (
        "" in clinical_queue_ids
        or len(clinical_queue_ids) != len(set(clinical_queue_ids))
        or set(clinical_queue_ids) != set(detail_ids)
    ):
        raise ValueError(
            "Clinical visual_manual_review_queue.csv rows must exactly match the "
            "detailed clinical records"
        )

    invalid_pdf_hash_assets = [
        row.get("asset_id", f"row {index + 1}")
        for index, row in enumerate(manifest_rows)
        if not SHA256_RE.fullmatch(row.get("source_pdf_sha256", ""))
    ]
    invalid_image_hash_assets = [
        row.get("asset_id", f"row {index + 1}")
        for index, row in enumerate(manifest_rows)
        if not SHA256_RE.fullmatch(row.get("image_sha256", ""))
    ]
    if invalid_pdf_hash_assets or invalid_image_hash_assets:
        raise ValueError(
            "Every Step 06 manifest asset must have valid source-PDF and image "
            "SHA-256 values"
        )
    source_pdf_hashes = sorted(
        {
            row.get("source_pdf_sha256", "").lower()
            for row in manifest_rows
            if SHA256_RE.fullmatch(row.get("source_pdf_sha256", ""))
        }
    )
    image_hashes = sorted(
        {
            row.get("image_sha256", "").lower()
            for row in manifest_rows
            if SHA256_RE.fullmatch(row.get("image_sha256", ""))
        }
    )
    if not source_pdf_hashes or not image_hashes:
        raise ValueError("Step 06 manifest lacks valid source-PDF or image SHA-256 values")

    for filename in DETAIL_FILES:
        bind_rows_to_manifest(
            filename,
            structured_csvs[filename],
            manifest_by_asset,
        )
    bind_rows_to_manifest(
        "visual_candidate_retrieval_records.csv",
        candidates,
        manifest_by_asset,
    )
    bind_rows_to_manifest(
        "visual_review_approvals.csv",
        approvals,
        manifest_by_asset,
    )
    bind_rows_to_manifest(
        "visual_manual_review_queue.csv",
        review_queue,
        manifest_by_asset,
        rows=clinical_queue_rows,
    )

    repeated_record_fields = (
        "asset_id",
        "page_number",
        "source_pdf_sha256",
        "image_sha256",
        "content_sha256",
    )
    bind_rows_to_details(
        "visual_candidate_retrieval_records.csv",
        candidates,
        details_by_id,
        fields=repeated_record_fields,
    )
    bind_rows_to_details(
        "visual_review_approvals.csv",
        approvals,
        details_by_id,
        fields=repeated_record_fields,
    )
    bind_rows_to_details(
        "visual_manual_review_queue.csv",
        review_queue,
        details_by_id,
        fields=repeated_record_fields,
        rows=clinical_queue_rows,
    )

    return {
        "csv_row_counts": csv_counts,
        "source_pdf_sha256": source_pdf_hashes,
        "image_sha256": image_hashes,
        "manifest_status_counts": dict(sorted(observed_statuses.items())),
        "approval_status_counts": dict(
            sorted(
                Counter(
                    row.get("review_status", "") or "missing"
                    for row in approvals["rows"]
                ).items()
            )
        ),
        "validation_status_counts": dict(
            sorted(
                Counter(
                    row.get("validation_status", "") or "missing"
                    for filename in DETAIL_FILES
                    for row in structured_csvs[filename]["rows"]
                ).items()
            )
        ),
        "n_detail_records": len(detail_ids),
        "n_reviewable_valid_candidates": len(candidate_rows),
    }


def ensure_destination_is_safe(destination: Path) -> None:
    if any(part.lower() == "outputs" for part in destination.parts):
        raise ValueError("Review package destination must be outside every outputs directory")
    if destination.exists():
        if not destination.is_dir() or any(destination.iterdir()):
            raise FileExistsError(
                f"Destination already exists and is not empty: {destination}"
            )


def package_readme(
    *,
    validation: dict[str, Any],
    files: list[dict[str, Any]],
    run_info: dict[str, Any],
) -> str:
    rows = [
        f"| `{item['path']}` | {item['role']} | {item.get('row_count', '—')} |"
        for item in files
    ]
    pdf_hashes = "\n".join(
        f"- `{value}`" for value in validation["source_pdf_sha256"]
    )
    table_rows = "\n".join(rows)
    merged_at = normalized(run_info.get("merged_at"))
    merge_line = (
        f"- Step 06 retry/merge completed: `{merged_at}`\n" if merged_at else ""
    )
    return f"""# ADA Visual Guideline Manual-Review Package

## Critical source-evidence warning

This package contains **model-generated candidates and audit metadata only**.
It deliberately excludes the ADA PDF, rendered page/tile images, raw per-asset
model JSON, released retrieval records, the enhanced knowledge base, and vector
indexes. It is not sufficient evidence by itself.

Reviewers must use controlled copies of the source PDF and rendered images and
verify that their SHA-256 hashes match `package_inventory.json`. Do not approve
a clinical statement from these CSVs alone. The expected source-PDF hash is:

{pdf_hashes}

- Step 06 model: `{run_info.get('model', '')}`
- Step 06 model digest: `{run_info.get('model_digest', '')}`
- Step 06 initial run finished: `{run_info.get('finished_at', '')}`
{merge_line}

## Package contents

| File | Role | Data rows |
| --- | --- | ---: |
{table_rows}

`package_inventory.json` records all row counts, source hashes, and checksums
for the copied review files and this README. `SHA256SUMS` also covers the
inventory itself. Recalculate the checksums before review if the package was
transferred outside Git.

Import every CSV column as **text** in spreadsheet software. Clinical `+`
markers such as `+HF`, `++`, and `+++++` can otherwise be interpreted as
formulas or altered during automatic type detection. Never approve a value
whose imported display differs from the raw CSV text.

The checksums attest the exported baseline. An authorized edit to
`visual_review_approvals.csv` will intentionally change that file's checksum.
Preserve the baseline commit and review the Git diff; do not regenerate the
inventory or checksums to conceal which review fields changed.

## How to review

1. Open `visual_manual_review_queue.csv` for triage. Asset/item rows are
   diagnostics and cannot be approved directly.
2. Join each clinical `record_id` in `visual_review_approvals.csv` to the
   appropriate detailed table: decision nodes, recommendation edges, drug
   actions, symbols/footnotes, or ordinal symbols.
3. Locate the exact source page/tile using `asset_id`, `page_number`, evidence
   text/bounding box, and the controlled image whose hash matches the manifest.
4. Check both correctness and completeness. Review the complete figure/table,
   not just isolated rows; an approved edge does not prove that both endpoint
   nodes or the full pathway are complete.
5. In `visual_review_approvals.csv`, do not change `record_id`, asset/item IDs,
   or `content_sha256`. Set the review status, accountable reviewer,
   timezone-aware `reviewed_at`, optional allowed `corrections_json`, and notes.
6. Return the edited approval CSV to the pipeline operator. The operator must
   run Step 07 again against the same canonical Step 06 output. New or corrected
   fingerprints require another review pass before release.

Do not edit generated detail tables, the Step 06 manifest, package inventory,
or checksum file to make a record pass. This package contains no released
guideline evidence; release remains governed by Steps 07 and 08.
"""


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def export_package(
    *,
    raw_dir: Path,
    structured_dir: Path,
    destination: Path,
    symbol_registry: Path,
    require_review_records: bool,
) -> dict[str, Any]:
    raw_dir = raw_dir.resolve()
    structured_dir = structured_dir.resolve()
    destination = destination.resolve()
    symbol_registry = symbol_registry.resolve()

    ensure_destination_is_safe(destination)
    require_files(raw_dir, RAW_FILES, "Step 06")
    require_files(structured_dir, STRUCTURED_FILES, "Step 07")
    if not symbol_registry.is_file():
        raise FileNotFoundError(f"Symbol registry does not exist: {symbol_registry}")

    raw_json = read_json(raw_dir / "extraction_run.json")
    raw_manifest = read_csv_document(raw_dir / "visual_logic_manifest.csv")
    structured_json = read_json(structured_dir / "visual_logic_summary.json")
    structured_csvs = {
        name: read_csv_document(structured_dir / name)
        for name in STRUCTURED_FILES
        if name.endswith(".csv")
    }
    registry_document = read_csv_document(symbol_registry)

    validation = validate_sources(
        raw_json,
        raw_manifest,
        structured_json,
        structured_csvs,
        require_review_records=require_review_records,
    )

    documents: list[Any] = [
        raw_json,
        raw_manifest,
        structured_json,
        *structured_csvs.values(),
        registry_document,
    ]
    replacements = build_absolute_path_map(documents)
    sanitized_raw_json = sanitize_value(raw_json, replacements=replacements)
    sanitized_raw_manifest = sanitize_value(raw_manifest, replacements=replacements)
    sanitized_structured_json = sanitize_value(
        structured_json, replacements=replacements
    )
    sanitized_structured_csvs = {
        name: sanitize_value(document, replacements=replacements)
        for name, document in structured_csvs.items()
    }
    sanitized_registry = sanitize_value(registry_document, replacements=replacements)

    sanitized_documents = {
        "extraction_run.json": sanitized_raw_json,
        "visual_logic_manifest.csv": sanitized_raw_manifest,
        "visual_logic_summary.json": sanitized_structured_json,
        **sanitized_structured_csvs,
        "guideline_symbol_registry.csv": sanitized_registry,
    }
    for name, document in sanitized_documents.items():
        assert_sanitized(document, location=name)

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{destination.name}.tmp-", dir=destination.parent
    ) as temporary_directory:
        temporary = Path(temporary_directory) / "package"
        temporary.mkdir()
        write_json(temporary / "extraction_run.json", sanitized_raw_json)
        write_csv_document(
            temporary / "visual_logic_manifest.csv", sanitized_raw_manifest
        )
        write_json(
            temporary / "visual_logic_summary.json", sanitized_structured_json
        )
        for name, document in sanitized_structured_csvs.items():
            write_csv_document(temporary / name, document)
        write_csv_document(
            temporary / "guideline_symbol_registry.csv", sanitized_registry
        )

        roles = {
            **RAW_FILES,
            **STRUCTURED_FILES,
            "guideline_symbol_registry.csv": "Symbol semantics used by Step 07",
        }
        data_files: list[dict[str, Any]] = []
        for name in sorted(roles):
            path = temporary / name
            item: dict[str, Any] = {
                "path": name,
                "role": roles[name],
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
            if name.endswith(".csv"):
                document = sanitized_documents[name]
                item["row_count"] = len(document["rows"])
                item["columns"] = document["fieldnames"]
            data_files.append(item)

        readme = package_readme(
            validation=validation, files=data_files, run_info=sanitized_raw_json
        )
        (temporary / "README.md").write_text(readme, encoding="utf-8")
        readme_item = {
            "path": "README.md",
            "role": "Reviewer instructions and source-evidence warning",
            "sha256": sha256_file(temporary / "README.md"),
            "bytes": (temporary / "README.md").stat().st_size,
        }
        inventory_files = data_files + [readme_item]

        inventory = {
            "schema_version": SCHEMA_VERSION,
            "package_type": "sanitized_visual_guideline_manual_review",
            "release_status": "unreleased_human_review_required",
            "source_run": {
                key: sanitized_raw_json.get(key)
                for key in (
                    "started_at",
                    "finished_at",
                    "merged_at",
                    "status",
                    "model",
                    "model_digest",
                    "ollama_version",
                    "extraction_passes",
                    "n_assets",
                    "status_counts",
                )
            },
            "counts": {
                **validation["csv_row_counts"],
                "all_detailed_clinical_records": validation["n_detail_records"],
                "reviewable_valid_candidates": validation[
                    "n_reviewable_valid_candidates"
                ],
            },
            "manifest_status_counts": validation["manifest_status_counts"],
            "approval_status_counts": validation["approval_status_counts"],
            "validation_status_counts": validation["validation_status_counts"],
            "controlled_source_hashes": {
                "source_pdf_sha256": validation["source_pdf_sha256"],
                "rendered_image_sha256": validation["image_sha256"],
            },
            "excluded_content": [
                "source PDF",
                "rendered page/tile images",
                "raw per-asset model JSON",
                "visual release/retrieval records",
                "enhanced knowledge base",
                "vector index",
            ],
            "files": inventory_files,
            "checksum_scope": (
                "Inventory file entries cover copied review files and README.md; "
                "SHA256SUMS covers every other package file, including "
                "package_inventory.json"
            ),
        }
        write_json(temporary / "package_inventory.json", inventory)

        checksum_paths = sorted(
            path
            for path in temporary.iterdir()
            if path.is_file() and path.name != "SHA256SUMS"
        )
        checksum_text = "".join(
            f"{sha256_file(path)}  {path.name}\n" for path in checksum_paths
        )
        (temporary / "SHA256SUMS").write_text(checksum_text, encoding="utf-8")

        if destination.exists():
            # An existing empty directory was allowed during the initial safety
            # check; remove only that exact empty directory before atomic rename.
            destination.rmdir()
        temporary.rename(destination)

    return inventory


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export a sanitized ADA visual manual-review package."
    )
    parser.add_argument("--raw-dir", required=True, help="Completed Step 06 directory")
    parser.add_argument(
        "--structured-dir", required=True, help="Matching Step 07 output directory"
    )
    parser.add_argument(
        "--destination",
        required=True,
        help="New tracked package directory outside outputs/",
    )
    parser.add_argument(
        "--symbol-registry",
        default=str(Path(__file__).with_name("guideline_symbol_registry.csv")),
    )
    parser.add_argument(
        "--require-review-records",
        action="store_true",
        help="Refuse to export unless Step 07 produced at least one valid candidate.",
    )
    args = parser.parse_args()

    inventory = export_package(
        raw_dir=Path(args.raw_dir),
        structured_dir=Path(args.structured_dir),
        destination=Path(args.destination),
        symbol_registry=Path(args.symbol_registry),
        require_review_records=args.require_review_records,
    )
    print(
        json.dumps(
            {
                "destination": str(Path(args.destination)),
                "n_assets": inventory["source_run"].get("n_assets"),
                "n_detail_records": inventory["counts"][
                    "all_detailed_clinical_records"
                ],
                "n_valid_candidates": inventory["counts"][
                    "reviewable_valid_candidates"
                ],
                "release_status": inventory["release_status"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
