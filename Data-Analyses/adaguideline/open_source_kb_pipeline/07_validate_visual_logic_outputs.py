#!/usr/bin/env python3
"""Validate, review, and release extracted visual guideline logic.

This is a deliberately strict boundary between nondeterministic vision-model
output and a guideline knowledge base.  Model output is normalized into
reviewable candidate tables, but it is never released for retrieval unless:

1. the payload and its page/asset provenance pass structural validation;
2. graph and symbol-registry invariants pass; and
3. a human approval for the current record fingerprint is present.

The approval CSV is both an input and an output.  On the first run it is
created with ``pending`` rows.  A reviewer may set ``review_status`` to
``approved`` and provide ``reviewer`` plus an ISO-8601 ``reviewed_at`` value.
Optional ``corrections_json`` is a shallow JSON object applied to mutable
clinical fields before final validation.  Approvals are bound to an effective
content fingerprint that covers the model record, corrections, immutable item
identity, and hashed source provenance.  Adding or changing a correction
therefore creates a new pending fingerprint that must be reviewed on a
subsequent run before it can be released.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


ITEM_TYPES = {"figure", "table", "flowchart", "footnote", "page", "region"}
ASSET_TYPES = {
    "page",
    "page_image",
    "page_overview",
    "figure",
    "figure_crop",
    "table",
    "table_crop",
    "flowchart",
    "crop",
    "tile",
}
SYMBOL_TYPES = {"relative_advantage", "relative_cost", "presence_marker", "other"}
ORDINAL_LEVELS = {"low", "moderate", "high", "very_high", "highest", "unknown"}
DIRECTIONS = {"more_is_better", "more_is_worse", "context_dependent", "not_applicable"}
SYMBOL_ROLES = {
    "ordinal_scale",
    "flowchart_prefix",
    "presence_marker",
    "addition_prefix",
    "other",
    "unknown",
}
REVIEW_STATUSES = {"pending", "approved", "rejected", "corrections_required"}
EXTRACTABLE_STATUSES = {"extracted_unvalidated", "extracted_with_ollama"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
EVIDENCE_BBOX_SPACE = "normalized_0_1000"


PROVENANCE_FIELDS = [
    "asset_id",
    "page_number",
    "source_pdf",
    "source_pdf_sha256",
    "image_sha256",
    "asset_type",
    "asset_bbox",
    "evidence_bbox_space",
    "image_path",
    "source_json",
    "extraction_status",
]

AUDIT_FIELDS = [
    "record_id",
    *PROVENANCE_FIELDS,
    "item_id",
    "item_type",
    "title",
    "model_requires_manual_review",
    "requires_manual_review",
    "validation_status",
    "validation_errors",
    "validation_warnings",
    "content_sha256",
    "effective_content_sha256",
    "review_status",
    "reviewer",
    "reviewed_at",
    "corrections_json",
    "review_notes",
    "release_eligible",
]

NODE_FIELDS = AUDIT_FIELDS + [
    "node_id",
    "condition",
    "patient_variables",
    "true_branch",
    "false_branch",
    "evidence_text",
    "evidence_bbox",
]
EDGE_FIELDS = AUDIT_FIELDS + [
    "from_node",
    "to_node",
    "edge_condition",
    "arrow_text",
    "evidence_text",
    "evidence_bbox",
]
ACTION_FIELDS = AUDIT_FIELDS + [
    "drug_class",
    "action",
    "trigger",
    "strength",
    "dose_or_use_logic",
    "caution_or_contraindication",
    "evidence_text",
    "evidence_bbox",
]
FOOTNOTE_FIELDS = AUDIT_FIELDS + [
    "symbol",
    "meaning",
    "applies_to",
    "evidence_text",
    "evidence_bbox",
]
SYMBOL_FIELDS = AUDIT_FIELDS + [
    "row_label",
    "clinical_dimension",
    "raw_symbol",
    "symbol_type",
    "symbol_count",
    "ordinal_level",
    "direction",
    "symbol_role",
    "interpretation",
    "evidence_text",
    "evidence_bbox",
]

RETRIEVAL_FIELDS = [
    "chunk_id",
    "record_id",
    "source_pdf",
    "source_pdf_sha256",
    "image_sha256",
    "asset_id",
    "asset_type",
    "asset_bbox",
    "evidence_bbox_space",
    "page_number",
    "section_title",
    "content_type",
    "table_or_figure_id",
    "type2_relevance_score",
    "drug_class_tags",
    "topic_tags",
    "retrieval_text",
    "evidence_text",
    "evidence_bbox",
    "image_path",
    "source_json",
    "extraction_status",
    "validation_status",
    "review_status",
    "reviewer",
    "reviewed_at",
    "human_approved",
    "approval_status",
    "release_status",
    "approved_by",
    "approved_at",
    "content_sha256",
    "effective_content_sha256",
    "requires_manual_review",
]

REVIEW_FIELDS = [
    "record_id",
    *PROVENANCE_FIELDS,
    "record_type",
    "item_id",
    "local_id",
    "title",
    "validation_status",
    "review_status",
    "reviewer",
    "reviewed_at",
    "requires_manual_review",
    "review_reason",
    "validation_errors",
    "validation_warnings",
    "extraction_warnings",
    "content_sha256",
    "effective_content_sha256",
    "corrections_json",
    "review_notes",
    "release_eligible",
]

APPROVAL_FIELDS = [
    "record_id",
    "asset_id",
    "page_number",
    "record_type",
    "item_id",
    "local_id",
    "content_sha256",
    "review_status",
    "reviewer",
    "reviewed_at",
    "corrections_json",
    "review_notes",
]


def is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return False


def normalize(value: Any) -> str:
    if is_missing(value):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stable_part(value: Any, fallback: str = "unknown") -> str:
    text = normalize(value).lower()
    slug = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return slug or fallback


def stable_record_id(
    asset_id: str,
    page_number: int,
    item_id: str,
    record_type: str,
    identity: Any,
) -> str:
    scope = f"p{page_number:03d}-{stable_part(asset_id, 'asset')}"
    item = stable_part(item_id, "item")
    if isinstance(identity, str) and normalize(identity):
        local = stable_part(identity)
    else:
        local = sha256_text(identity)[:16]
    return f"{scope}::{item}::{record_type}::{local}"


def parse_string(
    value: Any,
    field: str,
    errors: list[str],
    *,
    required: bool = True,
) -> str:
    if not isinstance(value, str):
        if is_missing(value):
            text = ""
        else:
            errors.append(f"{field} must be a string")
            text = normalize(value)
    else:
        text = normalize(value)
    if required and not text:
        errors.append(f"{field} is required")
    return text


def parse_bool(value: Any, field: str, errors: list[str], warnings: list[str]) -> bool:
    """Parse booleans without Python's dangerous ``bool('false')`` behavior."""

    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool) and value in (0, 1):
        warnings.append(f"{field} integer {value} was coerced to boolean")
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "false"}:
            warnings.append(f"{field} string value was coerced to boolean")
            return lowered == "true"
    errors.append(f"{field} must be a boolean")
    return True


def parse_int(value: Any, field: str, errors: list[str]) -> int | None:
    if isinstance(value, bool):
        errors.append(f"{field} must be an integer, not a boolean")
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
        return int(value)
    errors.append(f"{field} must be an integer")
    return None


def parse_bbox(value: Any, field: str, errors: list[str]) -> list[float] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            errors.append(f"{field} must be a four-number JSON array or coordinate object")
            return None
    if isinstance(value, dict):
        keys = ("x0", "y0", "x1", "y1")
        if not all(key in value for key in keys):
            errors.append(f"{field} coordinate object requires x0, y0, x1, and y1")
            return None
        value = [value[key] for key in keys]
    if isinstance(value, list) and not value:
        return []
    if not isinstance(value, list) or len(value) != 4:
        errors.append(f"{field} must contain exactly four coordinates")
        return None
    coords: list[float] = []
    for coordinate in value:
        if isinstance(coordinate, bool) or not isinstance(coordinate, (int, float)):
            errors.append(f"{field} coordinates must be numeric")
            return None
        if not math.isfinite(float(coordinate)):
            errors.append(f"{field} coordinates must be finite")
            return None
        coords.append(float(coordinate))
    if coords[2] <= coords[0] or coords[3] <= coords[1]:
        errors.append(f"{field} must satisfy x1 > x0 and y1 > y0")
        return None
    return coords


def compact_messages(messages: Iterable[str]) -> str:
    return "; ".join(dict.fromkeys(normalize(message) for message in messages if normalize(message)))


def as_list(value: Any, field: str, errors: list[str]) -> list[Any]:
    if not isinstance(value, list):
        errors.append(f"{field} must be an array")
        return []
    return value


def manifest_bbox(row: pd.Series, errors: list[str]) -> list[float] | None:
    values = [row.get(name) for name in ("bbox_x0", "bbox_y0", "bbox_x1", "bbox_y1")]
    if all(is_missing(value) or normalize(value) == "" for value in values):
        errors.append("manifest asset bbox is required")
        return None
    if any(is_missing(value) or normalize(value) == "" for value in values):
        errors.append("manifest asset bbox requires bbox_x0, bbox_y0, bbox_x1, and bbox_y1")
        return None
    parsed: list[float] = []
    for value in values:
        try:
            parsed.append(float(value))
        except (TypeError, ValueError):
            errors.append("manifest asset bbox coordinates must be numeric")
            return None
    return parse_bbox(parsed, "manifest asset bbox", errors)


def resolve_input_path(value: Any, base_dir: Path) -> Path:
    path = Path(normalize(value))
    if path.exists() or path.is_absolute():
        return path
    candidate = base_dir / path
    return candidate if candidate.exists() else path


def load_manifest(raw_dir: Path) -> pd.DataFrame:
    manifest_path = raw_dir / "visual_logic_manifest.csv"
    if not manifest_path.exists():
        raise FileNotFoundError(
            "Missing visual_logic_manifest.csv. Run 06_extract_visual_guideline_logic.py first."
        )
    try:
        return pd.read_csv(manifest_path, keep_default_na=False)
    except pd.errors.EmptyDataError as exc:
        raise ValueError("visual_logic_manifest.csv is empty or has no header") from exc


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("visual logic JSON root must be an object")
    return payload


def load_symbol_registry(path: Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
    errors: list[str] = []
    required = {
        "symbol_family",
        "raw_symbol",
        "symbol_type",
        "symbol_count",
        "ordinal_level",
        "direction_default",
        "meaning",
        "scoring_use",
        "manual_review_required",
    }
    if not path.exists():
        return {}, [f"symbol registry not found: {path}"]
    try:
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    except pd.errors.EmptyDataError:
        return {}, ["symbol registry is empty or has no header"]
    missing = sorted(required - set(frame.columns))
    if missing:
        return {}, [f"symbol registry missing columns: {', '.join(missing)}"]

    registry: dict[str, dict[str, Any]] = {}
    for index, source in frame.iterrows():
        row_number = index + 2
        raw_symbol = normalize(source["raw_symbol"])
        row_errors: list[str] = []
        count = parse_int(source["symbol_count"], "symbol_count", row_errors)
        flag_errors: list[str] = []
        flag_warnings: list[str] = []
        manual = parse_bool(
            source["manual_review_required"],
            "manual_review_required",
            flag_errors,
            flag_warnings,
        )
        symbol_type = normalize(source["symbol_type"])
        level = normalize(source["ordinal_level"])
        direction = normalize(source["direction_default"])
        if not raw_symbol:
            row_errors.append("raw_symbol is required")
        if symbol_type not in SYMBOL_TYPES - {"other"}:
            row_errors.append(f"unsupported symbol_type {symbol_type!r}")
        if level not in ORDINAL_LEVELS - {"unknown"}:
            row_errors.append(f"unsupported ordinal_level {level!r}")
        if direction not in DIRECTIONS - {"unknown"}:
            row_errors.append(f"unsupported direction_default {direction!r}")
        if raw_symbol in registry:
            row_errors.append(f"duplicate raw_symbol {raw_symbol!r}")
        if row_errors or flag_errors:
            errors.extend(
                f"symbol registry row {row_number}: {message}"
                for message in row_errors + flag_errors
            )
            continue
        registry[raw_symbol] = {
            "symbol_family": normalize(source["symbol_family"]),
            "raw_symbol": raw_symbol,
            "symbol_type": symbol_type,
            "symbol_count": count,
            "ordinal_level": level,
            "direction_default": direction,
            "meaning": normalize(source["meaning"]),
            "scoring_use": normalize(source["scoring_use"]),
            "manual_review_required": manual,
        }
    return registry, errors


def load_approvals(path: Path) -> tuple[dict[str, dict[str, str]], list[str]]:
    if not path.exists():
        return {}, []
    try:
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    except pd.errors.EmptyDataError:
        return {}, ["approval CSV is empty or has no header; approvals were ignored"]
    missing = sorted(set(APPROVAL_FIELDS) - set(frame.columns))
    if missing:
        return {}, [f"approval CSV missing columns: {', '.join(missing)}"]
    approvals: dict[str, dict[str, str]] = {}
    errors: list[str] = []
    normalized_ids = frame["record_id"].map(normalize)
    duplicate_ids = {
        record_id
        for record_id, count in Counter(value for value in normalized_ids if value).items()
        if count > 1
    }
    for record_id in sorted(duplicate_ids):
        errors.append(f"approval record_id {record_id!r} is duplicated")
    for index, row in frame.iterrows():
        record_id = normalize(row["record_id"])
        if not record_id:
            errors.append(f"approval row {index + 2} has no record_id")
            continue
        if record_id in duplicate_ids:
            continue
        approvals[record_id] = {field: normalize(row.get(field)) for field in APPROVAL_FIELDS}
    return approvals, errors


def parse_corrections(
    approval: dict[str, str] | None,
    allowed_fields: set[str],
    errors: list[str],
) -> dict[str, Any]:
    if not approval or not approval.get("corrections_json"):
        return {}
    try:
        corrections = json.loads(approval["corrections_json"])
    except json.JSONDecodeError as exc:
        errors.append(f"corrections_json is not valid JSON: {exc}")
        return {}
    if not isinstance(corrections, dict):
        errors.append("corrections_json must be a JSON object")
        return {}
    forbidden = sorted(set(corrections) - allowed_fields)
    if forbidden:
        errors.append(f"corrections_json contains immutable or unknown fields: {', '.join(forbidden)}")
        return {}
    return corrections


def valid_review_timestamp(value: str) -> bool:
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def approval_state(
    approval: dict[str, str] | None,
    content_sha256: str,
) -> tuple[dict[str, str], list[str], bool]:
    state = {
        "review_status": "pending",
        "reviewer": "",
        "reviewed_at": "",
        "corrections_json": "",
        "review_notes": "",
    }
    errors: list[str] = []
    if not approval:
        return state, errors, False
    state.update({field: approval.get(field, "") for field in state})
    status = state["review_status"].lower() or "pending"
    state["review_status"] = status
    if approval.get("content_sha256") != content_sha256:
        errors.append("approval fingerprint is stale; content_sha256 does not match")
        state["review_status"] = "pending"
        state["reviewer"] = ""
        state["reviewed_at"] = ""
        return state, errors, False
    if status not in REVIEW_STATUSES:
        errors.append(f"review_status must be one of {sorted(REVIEW_STATUSES)}")
        return state, errors, False
    if status != "approved":
        return state, errors, False
    if not state["reviewer"]:
        errors.append("approved record requires reviewer")
    if not valid_review_timestamp(state["reviewed_at"]):
        errors.append("approved record requires timezone-aware ISO-8601 reviewed_at")
    return state, errors, not errors


def provenance_for_payload(
    payload: dict[str, Any],
    manifest_row: pd.Series,
    raw_dir: Path,
) -> tuple[dict[str, Any], list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    manifest_page = parse_int(manifest_row.get("page_number"), "manifest page_number", errors)
    payload_page = parse_int(payload.get("page_number"), "payload page_number", errors)
    page_number = manifest_page if manifest_page is not None else (payload_page or 0)
    if manifest_page is not None and payload_page is not None and manifest_page != payload_page:
        errors.append(
            f"payload page_number {payload_page} does not match manifest page_number {manifest_page}"
        )

    manifest_asset_id = normalize(manifest_row.get("asset_id"))
    payload_asset_id = parse_string(payload.get("asset_id"), "payload asset_id", errors)
    if not manifest_asset_id:
        errors.append("manifest asset_id is required")
    if manifest_asset_id and payload_asset_id and manifest_asset_id != payload_asset_id:
        errors.append(
            f"payload asset_id {payload_asset_id!r} does not match manifest asset_id {manifest_asset_id!r}"
        )
    asset_id = manifest_asset_id or payload_asset_id or f"invalid_page_{page_number:03d}"

    provenance: dict[str, Any] = {
        "asset_id": asset_id,
        "page_number": page_number,
        "image_path": normalize(manifest_row.get("image_path")),
        "source_json": normalize(manifest_row.get("visual_logic_json")),
        "extraction_status": normalize(manifest_row.get("status")),
        "source_pdf": normalize(manifest_row.get("source_pdf")),
    }

    manifest_bbox_space = normalize(manifest_row.get("evidence_bbox_space")).lower()
    payload_bbox_space = parse_string(
        payload.get("evidence_bbox_space"),
        "payload evidence_bbox_space",
        errors,
    ).lower()
    if not manifest_bbox_space:
        errors.append("manifest evidence_bbox_space is required")
    if (
        manifest_bbox_space
        and payload_bbox_space
        and manifest_bbox_space != payload_bbox_space
    ):
        errors.append(
            f"payload evidence_bbox_space {payload_bbox_space!r} does not match manifest "
            f"evidence_bbox_space {manifest_bbox_space!r}"
        )
    provenance["evidence_bbox_space"] = manifest_bbox_space or payload_bbox_space
    if provenance["evidence_bbox_space"] != EVIDENCE_BBOX_SPACE:
        errors.append(
            f"evidence_bbox_space must be {EVIDENCE_BBOX_SPACE!r}"
        )

    payload_source_pdf = parse_string(
        payload.get("source_pdf"), "payload source_pdf", errors
    )
    if (
        provenance["source_pdf"]
        and payload_source_pdf
        and provenance["source_pdf"] != payload_source_pdf
    ):
        errors.append(
            f"payload source_pdf {payload_source_pdf!r} does not match manifest "
            f"source_pdf {provenance['source_pdf']!r}"
        )
    if not provenance["source_pdf"]:
        errors.append("manifest source_pdf is required")
        provenance["source_pdf"] = payload_source_pdf

    for field in ("source_pdf_sha256", "image_sha256", "asset_type"):
        manifest_value = normalize(manifest_row.get(field)).lower()
        payload_value = parse_string(payload.get(field), f"payload {field}", errors).lower()
        if not manifest_value:
            errors.append(f"manifest {field} is required")
        if manifest_value and payload_value and manifest_value != payload_value:
            errors.append(
                f"payload {field} {payload_value!r} does not match manifest {field} {manifest_value!r}"
            )
        provenance[field] = manifest_value or payload_value

    for hash_field in ("source_pdf_sha256", "image_sha256"):
        if provenance[hash_field] and not SHA256_RE.fullmatch(provenance[hash_field]):
            errors.append(f"{hash_field} must be 64 lowercase hexadecimal characters")
    if provenance["asset_type"] not in ASSET_TYPES:
        errors.append(f"asset_type must be one of {sorted(ASSET_TYPES)}")

    bbox = manifest_bbox(manifest_row, errors)
    provenance["asset_bbox"] = canonical_json(bbox) if bbox else ""

    image_path = resolve_input_path(provenance["image_path"], raw_dir)
    if not provenance["image_path"]:
        errors.append("manifest image_path is required")
    elif not image_path.is_file():
        errors.append(
            f"manifest image_path does not exist or is not a file: {provenance['image_path']}"
        )
    elif provenance["image_sha256"]:
        actual = sha256_file(image_path)
        if actual != provenance["image_sha256"]:
            errors.append("image_sha256 does not match the referenced image bytes")

    source_pdf = provenance["source_pdf"]
    if source_pdf:
        source_path = resolve_input_path(source_pdf, raw_dir)
        if not source_path.is_file():
            errors.append(
                f"manifest source_pdf does not exist or is not a file: {source_pdf}"
            )
        elif provenance["source_pdf_sha256"]:
            actual = sha256_file(source_path)
            if actual != provenance["source_pdf_sha256"]:
                errors.append("source_pdf_sha256 does not match the referenced PDF bytes")

    status = provenance["extraction_status"]
    if status not in EXTRACTABLE_STATUSES:
        errors.append(f"manifest status {status!r} is not an extractable model-output status")
    elif status == "extracted_with_ollama":
        warnings.append("legacy extracted_with_ollama status treated as unvalidated")

    extraction_warnings = payload.get("extraction_warnings")
    if not isinstance(extraction_warnings, list) or not all(
        isinstance(item, str) for item in extraction_warnings
    ):
        errors.append("extraction_warnings must be an array of strings")
        extraction_warnings = []
    provenance["extraction_warnings"] = [normalize(item) for item in extraction_warnings]
    warnings.extend(provenance["extraction_warnings"])
    return provenance, errors, warnings


def base_record(
    provenance: dict[str, Any],
    item: dict[str, Any],
    record_id: str,
) -> dict[str, Any]:
    return {
        "record_id": record_id,
        **{field: provenance.get(field, "") for field in PROVENANCE_FIELDS},
        "item_id": normalize(item.get("item_id")),
        "item_type": normalize(item.get("item_type")),
        "title": normalize(item.get("title")),
    }


def evidence_fields(
    raw: dict[str, Any],
    errors: list[str],
    warnings: list[str],
    bbox_space: str,
) -> tuple[str, str]:
    evidence_text = parse_string(raw.get("evidence_text"), "evidence_text", errors)
    bbox = parse_bbox(raw.get("evidence_bbox"), "evidence_bbox", errors)
    if bbox == []:
        warnings.append("evidence_bbox is unknown and requires manual localization")
    elif bbox is not None:
        if bbox_space != EVIDENCE_BBOX_SPACE:
            errors.append(
                f"evidence_bbox_space must be {EVIDENCE_BBOX_SPACE!r} for nonempty evidence boxes"
            )
        elif any(coordinate < 0 or coordinate > 1000 for coordinate in bbox):
            errors.append("evidence_bbox coordinates must be within normalized range 0-1000")
    return evidence_text, canonical_json(bbox) if bbox is not None else ""


def item_metadata(item: Any, item_index: int, errors: list[str]) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        errors.append(f"visual_items[{item_index}] must be an object")
        return None
    local_errors: list[str] = []
    normalized = dict(item)
    normalized["item_id"] = parse_string(item.get("item_id"), "item_id", local_errors)
    normalized["item_type"] = parse_string(item.get("item_type"), "item_type", local_errors)
    normalized["title"] = parse_string(item.get("title"), "title", local_errors)
    normalized["clinical_scope"] = parse_string(
        item.get("clinical_scope"), "clinical_scope", local_errors
    )
    normalized["retrieval_summary"] = parse_string(
        item.get("retrieval_summary"), "retrieval_summary", local_errors, required=False
    )
    if normalized["item_type"] not in ITEM_TYPES:
        local_errors.append(f"item_type must be one of {sorted(ITEM_TYPES)}")
    for field in (
        "symbols_or_footnotes",
        "ordinal_symbols",
        "decision_nodes",
        "recommendation_edges",
        "drug_actions",
    ):
        normalized[field] = as_list(item.get(field), field, local_errors)
    normalized["_errors"] = local_errors
    errors.extend(f"item {normalized['item_id'] or item_index}: {message}" for message in local_errors)
    return normalized


def identity_for_raw(record_type: str, raw: dict[str, Any], index: int) -> Any:
    """Return a content-independent local identity for approval continuity.

    Explicit model IDs are honored when available.  Otherwise the ordinal is
    stable within the asset/item/record-type namespace.  Clinical content is
    intentionally excluded: it belongs in ``content_sha256`` so a correction
    invalidates approval without silently creating a new record identity.
    """

    id_fields = {
        "node": ("node_id", "record_id"),
        "edge": ("edge_id", "record_id"),
        "action": ("action_id", "record_id"),
        "footnote": ("footnote_id", "record_id"),
        "symbol": ("symbol_id", "record_id"),
    }[record_type]
    explicit = next((normalize(raw.get(field)) for field in id_fields if normalize(raw.get(field))), "")
    return explicit or f"{record_type}_{index + 1:03d}"


MUTABLE_FIELDS = {
    "node": {
        "condition",
        "patient_variables",
        "true_branch",
        "false_branch",
        "requires_manual_review",
        "evidence_text",
        "evidence_bbox",
    },
    "edge": {
        "from_node",
        "to_node",
        "edge_condition",
        "arrow_text",
        "requires_manual_review",
        "evidence_text",
        "evidence_bbox",
    },
    "action": {
        "drug_class",
        "action",
        "trigger",
        "strength",
        "dose_or_use_logic",
        "caution_or_contraindication",
        "requires_manual_review",
        "evidence_text",
        "evidence_bbox",
    },
    "footnote": {
        "symbol",
        "meaning",
        "applies_to",
        "requires_manual_review",
        "evidence_text",
        "evidence_bbox",
    },
    "symbol": {
        "row_label",
        "clinical_dimension",
        "raw_symbol",
        "symbol_type",
        "symbol_count",
        "ordinal_level",
        "direction",
        "symbol_role",
        "interpretation",
        "requires_manual_review",
        "evidence_text",
        "evidence_bbox",
    },
}


def normalize_child(
    record_type: str,
    source: Any,
    index: int,
    item: dict[str, Any],
    provenance: dict[str, Any],
    approvals: dict[str, dict[str, str]],
    registry: dict[str, dict[str, Any]],
    inherited_errors: list[str],
    inherited_warnings: list[str],
) -> dict[str, Any]:
    raw = source if isinstance(source, dict) else {}
    record_id = stable_record_id(
        provenance["asset_id"],
        provenance["page_number"],
        item["item_id"],
        record_type,
        identity_for_raw(record_type, raw, index),
    )
    approval = approvals.get(record_id)
    errors = list(inherited_errors)
    warnings = list(inherited_warnings)
    correction_errors: list[str] = []
    corrections = parse_corrections(approval, MUTABLE_FIELDS[record_type], correction_errors)
    errors.extend(correction_errors)
    effective_raw = {**raw, **corrections}
    if not isinstance(source, dict):
        errors.append(f"{record_type} entry must be an object")

    row = base_record(provenance, item, record_id)
    flag = parse_bool(
        effective_raw.get("requires_manual_review"),
        "requires_manual_review",
        errors,
        warnings,
    )
    row["model_requires_manual_review"] = flag

    if record_type == "node":
        row.update(
            {
                "node_id": parse_string(effective_raw.get("node_id"), "node_id", errors),
                "condition": parse_string(effective_raw.get("condition"), "condition", errors),
                "true_branch": parse_string(
                    effective_raw.get("true_branch"), "true_branch", errors, required=False
                ),
                "false_branch": parse_string(
                    effective_raw.get("false_branch"), "false_branch", errors, required=False
                ),
            }
        )
        variables = effective_raw.get("patient_variables")
        if not isinstance(variables, list) or not all(isinstance(value, str) for value in variables):
            errors.append("patient_variables must be an array of strings")
            variables = []
        row["patient_variables"] = "; ".join(normalize(value) for value in variables)
    elif record_type == "edge":
        row.update(
            {
                "from_node": parse_string(effective_raw.get("from_node"), "from_node", errors),
                "to_node": parse_string(effective_raw.get("to_node"), "to_node", errors),
                "edge_condition": parse_string(
                    effective_raw.get("edge_condition"), "edge_condition", errors, required=False
                ),
                "arrow_text": parse_string(
                    effective_raw.get("arrow_text"), "arrow_text", errors, required=False
                ),
            }
        )
    elif record_type == "action":
        row.update(
            {
                "drug_class": parse_string(
                    effective_raw.get("drug_class"), "drug_class", errors
                ),
                "action": parse_string(effective_raw.get("action"), "action", errors),
                "trigger": parse_string(effective_raw.get("trigger"), "trigger", errors),
                "strength": parse_string(
                    effective_raw.get("strength"), "strength", errors, required=False
                ),
                "dose_or_use_logic": parse_string(
                    effective_raw.get("dose_or_use_logic"),
                    "dose_or_use_logic",
                    errors,
                    required=False,
                ),
                "caution_or_contraindication": parse_string(
                    effective_raw.get("caution_or_contraindication"),
                    "caution_or_contraindication",
                    errors,
                    required=False,
                ),
            }
        )
    elif record_type == "footnote":
        row.update(
            {
                "symbol": parse_string(effective_raw.get("symbol"), "symbol", errors),
                "meaning": parse_string(effective_raw.get("meaning"), "meaning", errors),
                "applies_to": parse_string(
                    effective_raw.get("applies_to"), "applies_to", errors
                ),
            }
        )
    elif record_type == "symbol":
        normalize_symbol(effective_raw, row, errors, warnings, registry)

    evidence_text, evidence_bbox = evidence_fields(
        effective_raw,
        errors,
        warnings,
        normalize(provenance.get("evidence_bbox_space")),
    )
    row["evidence_text"] = evidence_text
    row["evidence_bbox"] = evidence_bbox

    # This is the exact object a reviewer approves.  It intentionally includes
    # correction-applied clinical content and immutable source provenance so
    # replacing a PDF/image or changing a correction invalidates approval even
    # when the model's asset-scoped record ID remains stable.
    effective_fingerprint = sha256_text(
        {
            "record_type": record_type,
            "record_id": record_id,
            "asset_id": provenance["asset_id"],
            "page_number": provenance["page_number"],
            "source_pdf": provenance["source_pdf"],
            "source_pdf_sha256": provenance["source_pdf_sha256"],
            "image_sha256": provenance["image_sha256"],
            "asset_type": provenance["asset_type"],
            "asset_bbox": provenance["asset_bbox"],
            "evidence_bbox_space": provenance["evidence_bbox_space"],
            "item_id": item["item_id"],
            "item_type": item["item_type"],
            "title": item["title"],
            "clinical_scope": item["clinical_scope"],
            "identity": identity_for_raw(record_type, raw, index),
            "effective": effective_raw,
        }
    )
    state, approval_errors, approved = approval_state(approval, effective_fingerprint)
    warnings.extend(approval_errors)
    row.update(state)
    row["content_sha256"] = effective_fingerprint
    row["effective_content_sha256"] = effective_fingerprint
    row["_errors"] = errors
    row["_warnings"] = warnings
    row["_record_type"] = record_type
    row["_approval_valid"] = approved
    finalize_record(row)
    return row


def normalize_symbol(
    raw: dict[str, Any],
    row: dict[str, Any],
    errors: list[str],
    warnings: list[str],
    registry: dict[str, dict[str, Any]],
) -> None:
    raw_symbol = parse_string(raw.get("raw_symbol"), "raw_symbol", errors)
    symbol_type = parse_string(raw.get("symbol_type"), "symbol_type", errors)
    ordinal_level = parse_string(raw.get("ordinal_level"), "ordinal_level", errors)
    direction = parse_string(raw.get("direction"), "direction", errors)
    role_value = raw.get("symbol_role", raw.get("usage_context", ""))
    role = parse_string(role_value, "symbol_role", errors, required=False).lower()
    is_ordinal = raw.get("is_ordinal")
    if is_ordinal is not None:
        ordinal_flag = parse_bool(is_ordinal, "is_ordinal", errors, warnings)
        explicit_role = "ordinal_scale" if ordinal_flag else "flowchart_prefix"
        if role and role != explicit_role:
            errors.append("symbol_role conflicts with is_ordinal")
        role = explicit_role

    glyph_is_plus = bool(re.fullmatch(r"\++", raw_symbol))
    glyph_is_dollar = bool(re.fullmatch(r"\$+", raw_symbol))
    if not role:
        if raw_symbol == "+":
            role = "unknown"
            errors.append(
                "single '+' is ambiguous; set symbol_role to ordinal_scale or a flowchart-prefix role"
            )
        elif glyph_is_plus or glyph_is_dollar:
            role = "ordinal_scale"
        else:
            role = "other"

    if role not in SYMBOL_ROLES:
        errors.append(f"symbol_role must be one of {sorted(SYMBOL_ROLES)}")
    if symbol_type not in SYMBOL_TYPES:
        errors.append(f"symbol_type must be one of {sorted(SYMBOL_TYPES)}")
    if ordinal_level not in ORDINAL_LEVELS:
        errors.append(f"ordinal_level must be one of {sorted(ORDINAL_LEVELS)}")
    if direction not in DIRECTIONS:
        errors.append(f"direction must be one of {sorted(DIRECTIONS)}")

    count = parse_int(raw.get("symbol_count"), "symbol_count", errors)
    if count is not None and count < 1:
        errors.append("symbol_count must be at least 1")

    flowchart_roles = {"flowchart_prefix", "presence_marker", "addition_prefix"}
    if role in flowchart_roles:
        if raw_symbol != "+":
            errors.append("flowchart-prefix symbols must use exactly one '+' glyph")
        if count != 1:
            errors.append("flowchart-prefix '+' must have symbol_count 1")
        if symbol_type not in {"presence_marker", "other"}:
            errors.append(
                "flowchart-prefix '+' must use symbol_type 'presence_marker' (legacy 'other' is accepted)"
            )
        if ordinal_level != "unknown":
            errors.append("flowchart-prefix '+' must use ordinal_level 'unknown'")
        if direction != "not_applicable":
            errors.append("flowchart-prefix '+' must use direction 'not_applicable'")
    elif role == "ordinal_scale":
        if not (glyph_is_plus or glyph_is_dollar):
            errors.append("ordinal_scale raw_symbol must contain only repeated '+' or '$' glyphs")
        expected_count = len(raw_symbol) if glyph_is_plus or glyph_is_dollar else None
        if count is not None and expected_count is not None and count != expected_count:
            errors.append(
                f"symbol_count {count} does not match glyph count {expected_count} in {raw_symbol!r}"
            )
        registry_row = registry.get(raw_symbol)
        if not registry_row:
            errors.append(f"ordinal symbol {raw_symbol!r} is not present in the symbol registry")
        else:
            if symbol_type != registry_row["symbol_type"]:
                errors.append(
                    f"symbol_type {symbol_type!r} does not match registry value "
                    f"{registry_row['symbol_type']!r}"
                )
            if count != registry_row["symbol_count"]:
                errors.append(
                    f"symbol_count {count!r} does not match registry value "
                    f"{registry_row['symbol_count']!r}"
                )
            if ordinal_level != registry_row["ordinal_level"]:
                errors.append(
                    f"ordinal_level {ordinal_level!r} does not match registry value "
                    f"{registry_row['ordinal_level']!r}"
                )
            registry_direction = registry_row["direction_default"]
            if registry_direction != "context_dependent" and direction != registry_direction:
                errors.append(
                    f"direction {direction!r} does not match registry value {registry_direction!r}"
                )
            elif registry_direction == "context_dependent" and direction != "context_dependent":
                if not normalize(raw.get("clinical_dimension")):
                    errors.append(
                        "context-dependent symbol direction requires clinical_dimension"
                    )
                warnings.append(
                    "context-dependent plus direction requires explicit human confirmation"
                )
            if registry_row["manual_review_required"]:
                warnings.append("symbol registry requires manual review")
    elif glyph_is_plus or glyph_is_dollar:
        warnings.append("glyph-like nonordinal symbol requires manual confirmation")

    row.update(
        {
            "row_label": parse_string(raw.get("row_label"), "row_label", errors),
            "clinical_dimension": parse_string(
                raw.get("clinical_dimension"), "clinical_dimension", errors, required=False
            ),
            "raw_symbol": raw_symbol,
            "symbol_type": symbol_type,
            "symbol_count": count if count is not None else "",
            "ordinal_level": ordinal_level,
            "direction": direction,
            "symbol_role": role,
            "interpretation": parse_string(
                raw.get("interpretation"), "interpretation", errors
            ),
        }
    )


def finalize_record(row: dict[str, Any]) -> None:
    errors = list(dict.fromkeys(row.get("_errors", [])))
    warnings = list(dict.fromkeys(row.get("_warnings", [])))
    valid = not errors
    approved = bool(row.get("_approval_valid"))
    row["validation_status"] = "valid" if valid else "invalid"
    row["validation_errors"] = compact_messages(errors)
    row["validation_warnings"] = compact_messages(warnings)
    row["release_eligible"] = valid and approved
    row["requires_manual_review"] = not row["release_eligible"]


def add_record_error(row: dict[str, Any], message: str) -> None:
    row.setdefault("_errors", []).append(message)
    finalize_record(row)


def validate_item_graph(records: list[dict[str, Any]]) -> None:
    nodes = [row for row in records if row["_record_type"] == "node"]
    edges = [row for row in records if row["_record_type"] == "edge"]
    node_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for node in nodes:
        node_groups[node.get("node_id", "")].append(node)
    for node_id, duplicates in node_groups.items():
        if node_id and len(duplicates) > 1:
            for row in duplicates:
                add_record_error(row, f"duplicate node_id {node_id!r} within item")

    node_ids = {node_id for node_id, group in node_groups.items() if node_id and len(group) == 1}
    for edge in edges:
        for field in ("from_node", "to_node"):
            endpoint = edge.get(field, "")
            if endpoint and endpoint not in node_ids:
                add_record_error(
                    edge,
                    f"{field} endpoint {endpoint!r} does not resolve to a unique node in this item",
                )

    natural_keys = {
        "edge": ("from_node", "to_node", "edge_condition", "arrow_text"),
        "action": (
            "drug_class",
            "action",
            "trigger",
            "dose_or_use_logic",
            "caution_or_contraindication",
        ),
        "footnote": ("symbol", "meaning", "applies_to"),
        "symbol": (
            "row_label",
            "clinical_dimension",
            "raw_symbol",
            "symbol_role",
        ),
    }
    for record_type, fields in natural_keys.items():
        groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
        for row in records:
            if row["_record_type"] == record_type:
                groups[tuple(row.get(field, "") for field in fields)].append(row)
        for key, duplicates in groups.items():
            if len(duplicates) > 1:
                for row in duplicates:
                    add_record_error(row, f"duplicate {record_type} record within item: {key!r}")


SEMANTIC_DUPLICATE_FIELDS = {
    "node": (
        "condition",
        "patient_variables",
        "true_branch",
        "false_branch",
        "evidence_text",
    ),
    "edge": (
        "from_node",
        "to_node",
        "edge_condition",
        "arrow_text",
        "evidence_text",
    ),
    "action": (
        "drug_class",
        "action",
        "trigger",
        "strength",
        "dose_or_use_logic",
        "caution_or_contraindication",
        "evidence_text",
    ),
    "footnote": ("symbol", "meaning", "applies_to", "evidence_text"),
    "symbol": (
        "row_label",
        "clinical_dimension",
        "raw_symbol",
        "symbol_type",
        "symbol_count",
        "ordinal_level",
        "direction",
        "symbol_role",
        "interpretation",
        "evidence_text",
    ),
}


def validate_cross_asset_semantic_duplicates(records: list[dict[str, Any]]) -> None:
    """Fail closed on exact normalized duplicates emitted by overlapping assets.

    This deliberately detects only exact same-page clinical/evidence content;
    fuzzy reconciliation remains a human-review task.
    """

    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        record_type = row.get("_record_type", "")
        fields = SEMANTIC_DUPLICATE_FIELDS.get(record_type)
        if not fields:
            continue
        key = (
            normalize(row.get("source_pdf_sha256")).lower(),
            row.get("page_number", ""),
            record_type,
            *(normalize(row.get(field)).casefold() for field in fields),
        )
        groups[key].append(row)

    for duplicates in groups.values():
        assets = sorted({normalize(row.get("asset_id")) for row in duplicates})
        if len(assets) < 2:
            continue
        message = (
            "exact same-page semantic duplicate appears in multiple assets: "
            + ", ".join(assets)
        )
        for row in duplicates:
            add_record_error(row, message)


def validate_manifest_duplicates(manifest: pd.DataFrame) -> dict[int, list[str]]:
    errors: dict[int, list[str]] = defaultdict(list)
    if "asset_id" in manifest:
        values = manifest["asset_id"].map(normalize)
        for asset_id, count in Counter(value for value in values if value).items():
            if count > 1:
                for index in manifest.index[values == asset_id]:
                    errors[int(index)].append(f"manifest asset_id {asset_id!r} is duplicated")
    pairs: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, row in manifest.iterrows():
        pairs[(normalize(row.get("page_number")), normalize(row.get("image_path")))].append(
            int(index)
        )
    for pair, indices in pairs.items():
        if pair[0] and pair[1] and len(indices) > 1:
            for index in indices:
                errors[index].append(
                    f"manifest page/image provenance pair {pair!r} is duplicated"
                )
    return errors


def build_retrieval_record(row: dict[str, Any]) -> dict[str, Any]:
    record_type = row["_record_type"]
    text_parts: dict[str, list[str]] = {
        "node": [
            row.get("title", ""),
            "Decision node",
            row.get("node_id", ""),
            row.get("condition", ""),
            row.get("patient_variables", ""),
            row.get("true_branch", ""),
            row.get("false_branch", ""),
        ],
        "edge": [
            row.get("title", ""),
            "Recommendation edge",
            row.get("from_node", ""),
            "to",
            row.get("to_node", ""),
            row.get("edge_condition", ""),
            row.get("arrow_text", ""),
        ],
        "action": [
            row.get("title", ""),
            row.get("drug_class", ""),
            row.get("action", ""),
            row.get("trigger", ""),
            row.get("strength", ""),
            row.get("dose_or_use_logic", ""),
            row.get("caution_or_contraindication", ""),
        ],
        "footnote": [
            row.get("title", ""),
            "Footnote",
            row.get("symbol", ""),
            row.get("meaning", ""),
            row.get("applies_to", ""),
        ],
        "symbol": [
            row.get("title", ""),
            row.get("row_label", ""),
            row.get("clinical_dimension", ""),
            row.get("raw_symbol", ""),
            row.get("symbol_role", ""),
            row.get("ordinal_level", ""),
            row.get("direction", ""),
            row.get("interpretation", ""),
        ],
    }
    topic = {
        "node": "visual_logic; decision_node",
        "edge": "visual_logic; recommendation_edge",
        "action": "visual_logic; drug_action",
        "footnote": "visual_logic; footnote",
        "symbol": "visual_logic; visual_symbol",
    }[record_type]
    return {
        "chunk_id": f"visual_{row['record_id']}",
        "record_id": row["record_id"],
        "source_pdf": row.get("source_pdf", ""),
        "source_pdf_sha256": row.get("source_pdf_sha256", ""),
        "image_sha256": row.get("image_sha256", ""),
        "asset_id": row.get("asset_id", ""),
        "asset_type": row.get("asset_type", ""),
        "asset_bbox": row.get("asset_bbox", ""),
        "evidence_bbox_space": row.get("evidence_bbox_space", ""),
        "page_number": row.get("page_number", ""),
        "section_title": row.get("title", ""),
        "content_type": f"visual_{record_type}",
        "table_or_figure_id": row.get("title", ""),
        "type2_relevance_score": 3,
        "drug_class_tags": row.get("drug_class", ""),
        "topic_tags": topic,
        "retrieval_text": compact_messages(text_parts[record_type]),
        "evidence_text": row.get("evidence_text", ""),
        "evidence_bbox": row.get("evidence_bbox", ""),
        "image_path": row.get("image_path", ""),
        "source_json": row.get("source_json", ""),
        "extraction_status": row.get("extraction_status", ""),
        "validation_status": row.get("validation_status", ""),
        "review_status": row.get("review_status", ""),
        "reviewer": row.get("reviewer", ""),
        "reviewed_at": row.get("reviewed_at", ""),
        "human_approved": bool(row.get("release_eligible", False)),
        "approval_status": row.get("review_status", ""),
        "release_status": "released" if row.get("release_eligible", False) else "unreleased",
        "approved_by": row.get("reviewer", "") if row.get("release_eligible", False) else "",
        "approved_at": row.get("reviewed_at", "") if row.get("release_eligible", False) else "",
        "content_sha256": row.get("content_sha256", ""),
        "effective_content_sha256": row.get("effective_content_sha256", ""),
        "requires_manual_review": row.get("requires_manual_review", True),
    }


def approval_template_row(row: dict[str, Any]) -> dict[str, Any]:
    local_id = {
        "node": row.get("node_id", ""),
        "edge": f"{row.get('from_node', '')}->{row.get('to_node', '')}",
        "action": f"{row.get('drug_class', '')}:{row.get('action', '')}",
        "footnote": row.get("symbol", ""),
        "symbol": f"{row.get('row_label', '')}:{row.get('raw_symbol', '')}",
    }[row["_record_type"]]
    return {
        "record_id": row["record_id"],
        "asset_id": row.get("asset_id", ""),
        "page_number": row.get("page_number", ""),
        "record_type": row["_record_type"],
        "item_id": row.get("item_id", ""),
        "local_id": local_id,
        "content_sha256": row.get("content_sha256", ""),
        "review_status": row.get("review_status", "pending"),
        "reviewer": row.get("reviewer", ""),
        "reviewed_at": row.get("reviewed_at", ""),
        "corrections_json": row.get("corrections_json", ""),
        "review_notes": row.get("review_notes", ""),
    }


def review_row(
    row: dict[str, Any],
    extraction_warnings: list[str],
) -> dict[str, Any]:
    record_type = row["_record_type"]
    local_id = approval_template_row(row)["local_id"]
    reasons: list[str] = []
    if row.get("model_requires_manual_review"):
        reasons.append("model marked this record requires_manual_review")
    if row.get("validation_errors"):
        reasons.append(row["validation_errors"])
    if row.get("validation_warnings"):
        reasons.append(row["validation_warnings"])
    reasons.extend(extraction_warnings)
    if row.get("review_status") != "approved":
        reasons.append("current human approval is required before release")
    elif not row.get("release_eligible"):
        reasons.append("approval exists but validation/release requirements are not satisfied")
    result = {
        "record_id": row["record_id"],
        **{field: row.get(field, "") for field in PROVENANCE_FIELDS},
        "record_type": record_type,
        "item_id": row.get("item_id", ""),
        "local_id": local_id,
        "title": row.get("title", ""),
        "validation_status": row.get("validation_status", ""),
        "review_status": row.get("review_status", ""),
        "reviewer": row.get("reviewer", ""),
        "reviewed_at": row.get("reviewed_at", ""),
        "requires_manual_review": row.get("requires_manual_review", True),
        "review_reason": compact_messages(reasons),
        "validation_errors": row.get("validation_errors", ""),
        "validation_warnings": row.get("validation_warnings", ""),
        "extraction_warnings": compact_messages(extraction_warnings),
        "content_sha256": row.get("content_sha256", ""),
        "effective_content_sha256": row.get("effective_content_sha256", ""),
        "corrections_json": row.get("corrections_json", ""),
        "review_notes": row.get("review_notes", ""),
        "release_eligible": row.get("release_eligible", False),
    }
    return result


def asset_review_row(
    provenance: dict[str, Any],
    errors: list[str],
    warnings: list[str],
    *,
    item_id: str = "",
    title: str = "",
) -> dict[str, Any]:
    record_id = stable_record_id(
        provenance.get("asset_id", "invalid_asset"),
        int(provenance.get("page_number") or 0),
        item_id or "asset",
        "asset",
        item_id or provenance.get("asset_id", "invalid_asset"),
    )
    return {
        "record_id": record_id,
        **{field: provenance.get(field, "") for field in PROVENANCE_FIELDS},
        "record_type": "item" if item_id else "asset",
        "item_id": item_id,
        "local_id": item_id or provenance.get("asset_id", ""),
        "title": title,
        "validation_status": "invalid" if errors else "valid",
        "review_status": "pending",
        "reviewer": "",
        "reviewed_at": "",
        "requires_manual_review": True,
        "review_reason": compact_messages(errors + warnings + ["human review required"]),
        "validation_errors": compact_messages(errors),
        "validation_warnings": compact_messages(warnings),
        "extraction_warnings": compact_messages(provenance.get("extraction_warnings", [])),
        "content_sha256": "",
        "effective_content_sha256": "",
        "corrections_json": "",
        "review_notes": "",
        "release_eligible": False,
    }


def flatten_payload(
    payload: dict[str, Any],
    manifest_row: pd.Series,
    *,
    raw_dir: Path | None = None,
    approvals: dict[str, dict[str, str]] | None = None,
    registry: dict[str, dict[str, Any]] | None = None,
    inherited_errors: list[str] | None = None,
    inherited_warnings: list[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Validate one page/asset payload and return normalized candidate rows."""

    raw_dir = raw_dir or Path(".")
    approvals = approvals or {}
    registry = registry or {}
    provenance, provenance_errors, provenance_warnings = provenance_for_payload(
        payload, manifest_row, raw_dir
    )
    provenance_errors.extend(inherited_errors or [])
    provenance_warnings.extend(inherited_warnings or [])
    output: dict[str, list[dict[str, Any]]] = {
        "nodes": [],
        "edges": [],
        "actions": [],
        "footnotes": [],
        "ordinal_symbols": [],
        "candidate_retrieval": [],
        "release_retrieval": [],
        "review": [],
        "approvals": [],
    }

    visual_items_value = payload.get("visual_items")
    if not isinstance(visual_items_value, list):
        provenance_errors.append("visual_items must be an array")
        visual_items: list[Any] = []
    else:
        visual_items = visual_items_value
    if not visual_items:
        provenance_errors.append("visual_items must contain at least one item")

    item_parse_errors: list[str] = []
    items = [
        parsed
        for index, item in enumerate(visual_items)
        if (parsed := item_metadata(item, index, item_parse_errors)) is not None
    ]
    item_ids = Counter(item["item_id"] for item in items if item["item_id"])
    for item_id, count in item_ids.items():
        if count > 1:
            item_parse_errors.append(f"duplicate item_id {item_id!r} within asset")

    output["review"].append(
        asset_review_row(
            provenance,
            provenance_errors + item_parse_errors,
            provenance_warnings,
            title=normalize(manifest_row.get("figure_ids"))
            or normalize(manifest_row.get("table_ids")),
        )
    )

    mapping = {
        "decision_nodes": ("node", "nodes"),
        "recommendation_edges": ("edge", "edges"),
        "drug_actions": ("action", "actions"),
        "symbols_or_footnotes": ("footnote", "footnotes"),
        "ordinal_symbols": ("symbol", "ordinal_symbols"),
    }
    all_records: list[dict[str, Any]] = []
    for item in items:
        item_errors = list(provenance_errors) + list(item.get("_errors", []))
        if item_ids.get(item["item_id"], 0) > 1:
            item_errors.append(f"duplicate item_id {item['item_id']!r} within asset")
        item_records: list[dict[str, Any]] = []
        for source_field, (record_type, output_key) in mapping.items():
            for index, source in enumerate(item[source_field]):
                record = normalize_child(
                    record_type,
                    source,
                    index,
                    item,
                    provenance,
                    approvals,
                    registry,
                    item_errors,
                    provenance_warnings,
                )
                output[output_key].append(record)
                item_records.append(record)
        validate_item_graph(item_records)
        all_records.extend(item_records)
        if not item_records or item.get("_errors"):
            output["review"].append(
                asset_review_row(
                    provenance,
                    list(item.get("_errors", []))
                    + (["visual item contains no child records"] if not item_records else []),
                    provenance_warnings,
                    item_id=item["item_id"],
                    title=item["title"],
                )
            )

    record_ids = Counter(row["record_id"] for row in all_records)
    for record in all_records:
        if record_ids[record["record_id"]] > 1:
            add_record_error(record, "stable record_id collision or exact duplicate record")

    for record in all_records:
        output["approvals"].append(approval_template_row(record))
        output["review"].append(
            review_row(record, list(provenance.get("extraction_warnings", [])))
        )
        if record["validation_status"] == "valid":
            retrieval = build_retrieval_record(record)
            output["candidate_retrieval"].append(retrieval)
            if record["release_eligible"]:
                output["release_retrieval"].append(retrieval)
    return output


def write_frame(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    """Always emit a parseable CSV, including when ``rows`` is empty."""

    frame = pd.DataFrame.from_records(rows, columns=columns)
    frame.to_csv(path, index=False)


def merge_approval_rows(
    rows: list[dict[str, Any]],
    prior: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for row in rows:
        old = prior.get(row["record_id"])
        if old and old.get("content_sha256") == row["content_sha256"]:
            for field in (
                "review_status",
                "reviewer",
                "reviewed_at",
                "corrections_json",
                "review_notes",
            ):
                row[field] = old.get(field, "")
        elif old:
            note = compact_messages(
                [old.get("review_notes", ""), "Content changed; prior review was invalidated."]
            )
            row.update(
                {
                    "review_status": "pending",
                    "reviewer": "",
                    "reviewed_at": "",
                    # Keep the correction visible for the required second-pass
                    # review, but never carry approval metadata across a changed
                    # effective fingerprint.
                    "corrections_json": old.get("corrections_json", ""),
                    "review_notes": note,
                }
            )
        merged.append(row)
    return sorted(merged, key=lambda value: value["record_id"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", default="outputs/visual_logic_raw")
    parser.add_argument("--output-dir", default="outputs/visual_logic_structured")
    parser.add_argument(
        "--symbol-registry",
        default=str(Path(__file__).with_name("guideline_symbol_registry.csv")),
    )
    parser.add_argument(
        "--approval-csv",
        default=None,
        help=(
            "Human approval CSV. Defaults to visual_review_approvals.csv in the "
            "output directory and is updated in place."
        ),
    )
    parser.add_argument(
        "--require-valid-candidates",
        action="store_true",
        help="Exit nonzero unless at least one structurally valid candidate is produced.",
    )
    parser.add_argument(
        "--require-released-records",
        action="store_true",
        help="Exit nonzero unless at least one currently approved record is released.",
    )
    args = parser.parse_args()

    raw_dir = Path(args.raw_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    approval_path = (
        Path(args.approval_csv)
        if args.approval_csv
        else output_dir / "visual_review_approvals.csv"
    )

    manifest = load_manifest(raw_dir)
    registry, registry_errors = load_symbol_registry(Path(args.symbol_registry))
    approvals, approval_file_errors = load_approvals(approval_path)
    manifest_errors = validate_manifest_duplicates(manifest)

    combined: dict[str, list[dict[str, Any]]] = {
        "nodes": [],
        "edges": [],
        "actions": [],
        "footnotes": [],
        "ordinal_symbols": [],
        "candidate_retrieval": [],
        "release_retrieval": [],
        "review": [],
        "approvals": [],
    }

    for index, manifest_row in manifest.iterrows():
        json_path = resolve_input_path(manifest_row.get("visual_logic_json"), raw_dir)
        row_errors = list(registry_errors) + list(approval_file_errors)
        row_errors.extend(manifest_errors.get(int(index), []))
        try:
            payload = read_json(json_path)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            page = parse_int(manifest_row.get("page_number"), "manifest page_number", []) or 0
            provenance = {
                "asset_id": normalize(manifest_row.get("asset_id")) or f"invalid_page_{page:03d}",
                "page_number": page,
                "source_pdf": normalize(manifest_row.get("source_pdf")),
                "source_pdf_sha256": normalize(manifest_row.get("source_pdf_sha256")),
                "image_sha256": normalize(manifest_row.get("image_sha256")),
                "asset_type": normalize(manifest_row.get("asset_type")),
                "asset_bbox": "",
                "evidence_bbox_space": normalize(
                    manifest_row.get("evidence_bbox_space")
                ),
                "image_path": normalize(manifest_row.get("image_path")),
                "source_json": normalize(manifest_row.get("visual_logic_json")),
                "extraction_status": normalize(manifest_row.get("status")),
                "extraction_warnings": [],
            }
            combined["review"].append(
                asset_review_row(
                    provenance,
                    row_errors + [f"could not read visual logic JSON: {exc}"],
                    [],
                )
            )
            continue
        flattened = flatten_payload(
            payload,
            manifest_row,
            raw_dir=raw_dir,
            approvals=approvals,
            registry=registry,
            inherited_errors=row_errors,
        )
        for key, rows in flattened.items():
            combined[key].extend(rows)

    all_children = (
        combined["nodes"]
        + combined["edges"]
        + combined["actions"]
        + combined["footnotes"]
        + combined["ordinal_symbols"]
    )
    validate_cross_asset_semantic_duplicates(all_children)

    # Cross-asset validation happens only after every manifest row is loaded,
    # so rebuild the derived review/retrieval views from the final row state.
    combined["candidate_retrieval"] = [
        build_retrieval_record(row)
        for row in all_children
        if row["validation_status"] == "valid"
    ]
    combined["release_retrieval"] = [
        build_retrieval_record(row)
        for row in all_children
        if row["validation_status"] == "valid" and row["release_eligible"]
    ]
    prior_child_reviews = {
        row["record_id"]: row
        for row in combined["review"]
        if row.get("record_type") not in {"asset", "item"}
    }
    asset_review_rows = [
        row for row in combined["review"] if row.get("record_type") in {"asset", "item"}
    ]
    combined["review"] = asset_review_rows + [
        review_row(
            row,
            [prior_child_reviews.get(row["record_id"], {}).get("extraction_warnings", "")],
        )
        for row in all_children
    ]

    approval_rows = merge_approval_rows(combined["approvals"], approvals)
    write_frame(approval_path, approval_rows, APPROVAL_FIELDS)

    write_frame(output_dir / "visual_decision_nodes.csv", combined["nodes"], NODE_FIELDS)
    write_frame(output_dir / "visual_recommendation_edges.csv", combined["edges"], EDGE_FIELDS)
    write_frame(output_dir / "visual_drug_actions.csv", combined["actions"], ACTION_FIELDS)
    write_frame(output_dir / "visual_symbols_footnotes.csv", combined["footnotes"], FOOTNOTE_FIELDS)
    write_frame(
        output_dir / "visual_ordinal_symbols.csv",
        combined["ordinal_symbols"],
        SYMBOL_FIELDS,
    )
    write_frame(
        output_dir / "visual_candidate_retrieval_records.csv",
        combined["candidate_retrieval"],
        RETRIEVAL_FIELDS,
    )
    # Backward-compatible downstream filename is release-gated by design.
    write_frame(
        output_dir / "visual_retrieval_records.csv",
        combined["release_retrieval"],
        RETRIEVAL_FIELDS,
    )
    write_frame(
        output_dir / "visual_release_records.csv",
        combined["release_retrieval"],
        RETRIEVAL_FIELDS,
    )
    write_frame(
        output_dir / "visual_manual_review_queue.csv",
        combined["review"],
        REVIEW_FIELDS,
    )

    summary = {
        "n_visual_assets": int(len(manifest)),
        "n_decision_nodes": int(len(combined["nodes"])),
        "n_recommendation_edges": int(len(combined["edges"])),
        "n_drug_actions": int(len(combined["actions"])),
        "n_symbols_or_footnotes": int(len(combined["footnotes"])),
        "n_ordinal_symbols": int(len(combined["ordinal_symbols"])),
        "n_valid_candidate_records": int(len(combined["candidate_retrieval"])),
        "n_invalid_candidate_records": int(
            sum(row.get("validation_status") == "invalid" for row in all_children)
        ),
        "n_human_approved_release_records": int(len(combined["release_retrieval"])),
        "n_manual_review_items": int(len(combined["review"])),
        "release_gate": "human_approval_required",
        "approval_csv": str(approval_path),
        "symbol_registry": str(args.symbol_registry),
    }
    (output_dir / "visual_logic_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )

    print(json.dumps(summary, indent=2))
    print(f"Wrote validated visual logic outputs to {output_dir}")
    if args.require_valid_candidates and not combined["candidate_retrieval"]:
        parser.error("release gate failed: no valid visual candidates were produced")
    if args.require_released_records and not combined["release_retrieval"]:
        parser.error("release gate failed: no human-approved visual records were released")


if __name__ == "__main__":
    main()
