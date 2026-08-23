#!/usr/bin/env python3
"""Extract review-only visual guideline logic from rendered image assets.

This stage deliberately does *not* approve clinical logic. It preflights the
local Ollama runtime once, requests schema-constrained JSON, preserves source
asset provenance, and writes model output with an ``extracted_unvalidated``
status. Step 07 performs deterministic validation and human release gating.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from jsonschema import Draft202012Validator


DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen3-vl:8b-instruct"
DEFAULT_PASSES = ["structure", "actions", "symbols"]
DEFAULT_NUM_CTX = 8192
DEFAULT_NUM_PREDICT = 4096
EVIDENCE_BBOX_SPACE = "normalized_0_1000"

# A valid one-pixel PNG. The probe forces the selected Ollama runner to load
# its vision projector before the pipeline starts an expensive page run.
VISION_PROBE_IMAGE = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "YAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)

ITEM_TYPES = ["figure", "table", "flowchart", "footnote", "page", "region"]
SYMBOL_TYPES = ["relative_advantage", "relative_cost", "presence_marker", "other"]
ORDINAL_LEVELS = ["low", "moderate", "high", "very_high", "highest", "unknown"]
DIRECTIONS = ["more_is_better", "more_is_worse", "context_dependent", "not_applicable"]


def _bbox_schema() -> dict[str, Any]:
    return {
        "type": "array",
        "items": {"type": "number", "minimum": 0, "maximum": 1000},
        # Keep the grammar simple for Ollama; local validation below rejects
        # partial coordinate arrays.
        "minItems": 0,
        "maxItems": 4,
        "description": (
            "[x0,y0,x1,y1] coordinates on a normalized 0-1000 image grid; "
            "[] if unknown"
        ),
    }


VISUAL_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["page_number", "visual_items", "extraction_warnings"],
    "properties": {
        "page_number": {"type": "integer"},
        "visual_items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "item_id",
                    "item_type",
                    "title",
                    "clinical_scope",
                    "symbols_or_footnotes",
                    "ordinal_symbols",
                    "decision_nodes",
                    "recommendation_edges",
                    "drug_actions",
                    "retrieval_summary",
                ],
                "properties": {
                    "item_id": {"type": "string"},
                    "item_type": {"type": "string", "enum": ITEM_TYPES},
                    "title": {"type": "string"},
                    "clinical_scope": {"type": "string"},
                    "symbols_or_footnotes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "symbol",
                                "meaning",
                                "applies_to",
                                "evidence_text",
                                "evidence_bbox",
                                "requires_manual_review",
                            ],
                            "properties": {
                                "symbol": {"type": "string"},
                                "meaning": {"type": "string"},
                                "applies_to": {"type": "string"},
                                "evidence_text": {"type": "string", "minLength": 1},
                                "evidence_bbox": _bbox_schema(),
                                "requires_manual_review": {"type": "boolean"},
                            },
                        },
                    },
                    "ordinal_symbols": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
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
                                "requires_manual_review",
                            ],
                            "properties": {
                                "row_label": {"type": "string"},
                                "clinical_dimension": {"type": "string"},
                                "raw_symbol": {"type": "string"},
                                "symbol_type": {"type": "string", "enum": SYMBOL_TYPES},
                                "symbol_count": {"type": "integer", "minimum": 0},
                                "ordinal_level": {"type": "string", "enum": ORDINAL_LEVELS},
                                "direction": {"type": "string", "enum": DIRECTIONS},
                                "symbol_role": {
                                    "type": "string",
                                    "enum": [
                                        "ordinal_scale",
                                        "presence_marker",
                                        "addition_prefix",
                                        "other",
                                        "unknown",
                                    ],
                                },
                                "interpretation": {"type": "string"},
                                "evidence_text": {"type": "string", "minLength": 1},
                                "evidence_bbox": _bbox_schema(),
                                "requires_manual_review": {"type": "boolean"},
                            },
                        },
                    },
                    "decision_nodes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "node_id",
                                "condition",
                                "patient_variables",
                                "true_branch",
                                "false_branch",
                                "evidence_text",
                                "evidence_bbox",
                                "requires_manual_review",
                            ],
                            "properties": {
                                "node_id": {"type": "string"},
                                "condition": {"type": "string"},
                                "patient_variables": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "true_branch": {"type": "string"},
                                "false_branch": {"type": "string"},
                                "evidence_text": {"type": "string", "minLength": 1},
                                "evidence_bbox": _bbox_schema(),
                                "requires_manual_review": {"type": "boolean"},
                            },
                        },
                    },
                    "recommendation_edges": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "from_node",
                                "to_node",
                                "edge_condition",
                                "arrow_text",
                                "evidence_text",
                                "evidence_bbox",
                                "requires_manual_review",
                            ],
                            "properties": {
                                "from_node": {"type": "string"},
                                "to_node": {"type": "string"},
                                "edge_condition": {"type": "string"},
                                "arrow_text": {"type": "string"},
                                "evidence_text": {"type": "string", "minLength": 1},
                                "evidence_bbox": _bbox_schema(),
                                "requires_manual_review": {"type": "boolean"},
                            },
                        },
                    },
                    "drug_actions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "drug_class",
                                "action",
                                "trigger",
                                "strength",
                                "dose_or_use_logic",
                                "caution_or_contraindication",
                                "evidence_text",
                                "evidence_bbox",
                                "requires_manual_review",
                            ],
                            "properties": {
                                "drug_class": {"type": "string"},
                                "action": {"type": "string"},
                                "trigger": {"type": "string"},
                                "strength": {"type": "string"},
                                "dose_or_use_logic": {"type": "string"},
                                "caution_or_contraindication": {"type": "string"},
                                "evidence_text": {"type": "string", "minLength": 1},
                                "evidence_bbox": _bbox_schema(),
                                "requires_manual_review": {"type": "boolean"},
                            },
                        },
                    },
                    "retrieval_summary": {"type": "string"},
                },
            },
        },
        "extraction_warnings": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
}


PASS_COLLECTION_LIMITS: dict[str, dict[str, int]] = {
    # Each 320-DPI tile is represented by one aggregate item. These bounds are
    # deliberately above the observed per-tile density while preventing a
    # model from repeating one full visual item per visible box until Ollama
    # exhausts its output window.
    "structure": {
        "symbols_or_footnotes": 0,
        "ordinal_symbols": 0,
        "decision_nodes": 16,
        "recommendation_edges": 20,
        "drug_actions": 0,
    },
    "actions": {
        "symbols_or_footnotes": 16,
        "ordinal_symbols": 0,
        "decision_nodes": 0,
        "recommendation_edges": 0,
        "drug_actions": 16,
    },
    "symbols": {
        "symbols_or_footnotes": 20,
        "ordinal_symbols": 24,
        "decision_nodes": 0,
        "recommendation_edges": 0,
        "drug_actions": 0,
    },
    "all": {
        "symbols_or_footnotes": 20,
        "ordinal_symbols": 24,
        "decision_nodes": 16,
        "recommendation_edges": 20,
        "drug_actions": 16,
    },
}


def response_schema_for_pass(pass_name: str) -> dict[str, Any]:
    """Return a bounded schema specialized to one extraction pass."""
    if pass_name not in PASS_COLLECTION_LIMITS:
        raise ValueError(f"Unsupported extraction pass: {pass_name}")
    schema = deepcopy(VISUAL_RESPONSE_SCHEMA)
    visual_items = schema["properties"]["visual_items"]
    visual_items["maxItems"] = 1
    item_properties = visual_items["items"]["properties"]
    for collection, limit in PASS_COLLECTION_LIMITS[pass_name].items():
        item_properties[collection]["maxItems"] = limit
    return schema

PASS_INSTRUCTIONS = {
    "structure": (
        "Extract all visible decision nodes and directed arrows. Use short, unique node IDs. "
        "Populate decision_nodes and recommendation_edges; leave ordinal_symbols and "
        "drug_actions empty unless essential to name a node."
    ),
    "actions": (
        "Extract medication classes, actions, triggers, cautions, dose/use logic, and modifying "
        "footnotes. Preserve an explicit recommendation-strength word only when that word is "
        "visible; otherwise use an empty strength string. Populate drug_actions and "
        "symbols_or_footnotes; leave ordinal_symbols empty."
    ),
    "symbols": (
        "Extract special symbols and their exact local evidence. A single leading '+' in a "
        "flowchart label such as '+HF' or '+CKD' uses symbol_type=presence_marker, "
        "symbol_role=presence_marker, symbol_count=1, ordinal_level=unknown, and "
        "direction=not_applicable; it is not an ordinal score. Only repeated '+' or '$' "
        "scales use symbol_role=ordinal_scale. Populate symbols_or_footnotes and "
        "ordinal_symbols; leave nodes, edges, and actions empty."
    ),
    "all": "Extract every visible node, arrow, medication action, symbol, and modifying footnote.",
}


class OllamaRequestError(RuntimeError):
    """An Ollama request failed with its response body preserved."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return re.sub(r"\s+", " ", str(value)).strip()


def safe_id(value: object) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "_", normalize(value)).strip("_.-")
    return clean or "visual_asset"


def encode_image(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def ollama_base_url(url: str) -> str:
    clean = url.rstrip("/")
    for suffix in ("/api/generate", "/api/chat"):
        if clean.endswith(suffix):
            return clean[: -len(suffix)]
    return clean


def request_json(
    method: str,
    url: str,
    *,
    timeout: int,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        response = requests.request(method, url, json=payload, timeout=timeout)
    except requests.RequestException as exc:
        raise OllamaRequestError(f"Could not reach Ollama at {url}: {exc}") from exc
    if not response.ok:
        body = normalize(response.text)[:2000]
        raise OllamaRequestError(
            f"Ollama {method} {url} returned HTTP {response.status_code}: {body or '<empty body>'}"
        )
    try:
        result = response.json()
    except ValueError as exc:
        raise OllamaRequestError(
            f"Ollama returned non-JSON content from {url}: {normalize(response.text)[:1000]}"
        ) from exc
    if not isinstance(result, dict):
        raise OllamaRequestError(f"Ollama returned {type(result).__name__}, expected an object")
    return result


def parse_model_json(raw_text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"model response was not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("model response root must be a JSON object")
    return parsed


def final_response_text(payload: dict[str, Any]) -> str:
    """Return final model content without treating a reasoning trace as evidence."""
    response_text = str(payload.get("response") or "").strip()
    if response_text:
        return response_text
    if str(payload.get("thinking") or "").strip():
        raise OllamaRequestError(
            "Ollama returned no final response and placed output in the thinking field. "
            "Use a direct-output vision model such as qwen3-vl:8b-instruct; reasoning "
            "traces are not accepted as extracted guideline evidence."
        )
    raise OllamaRequestError("Ollama returned an empty final response")


def validate_response_shape(
    payload: dict[str, Any], *, schema: dict[str, Any] = VISUAL_RESPONSE_SCHEMA
) -> list[str]:
    """Reject malformed model output locally before it enters the review queue."""
    errors: list[str] = []
    if not isinstance(payload.get("page_number"), int):
        errors.append("page_number must be an integer")
    if not isinstance(payload.get("visual_items"), list):
        errors.append("visual_items must be a list")
        return errors
    if not isinstance(payload.get("extraction_warnings"), list):
        errors.append("extraction_warnings must be a list")
    required_lists = [
        "symbols_or_footnotes",
        "ordinal_symbols",
        "decision_nodes",
        "recommendation_edges",
        "drug_actions",
    ]
    for index, item in enumerate(payload["visual_items"]):
        if not isinstance(item, dict):
            errors.append(f"visual_items[{index}] must be an object")
            continue
        if item.get("item_type") not in ITEM_TYPES:
            errors.append(f"visual_items[{index}].item_type is invalid")
        for field in required_lists:
            if not isinstance(item.get(field), list):
                errors.append(f"visual_items[{index}].{field} must be a list")
        for collection in required_lists:
            values = item.get(collection, [])
            if not isinstance(values, list):
                continue
            for child_index, child in enumerate(values):
                if not isinstance(child, dict):
                    errors.append(
                        f"visual_items[{index}].{collection}[{child_index}] must be an object"
                    )
                elif not isinstance(child.get("requires_manual_review"), bool):
                    errors.append(
                        f"visual_items[{index}].{collection}[{child_index}]."
                        "requires_manual_review must be boolean"
                    )
                if isinstance(child, dict):
                    bbox = child.get("evidence_bbox")
                    if isinstance(bbox, list) and len(bbox) not in {0, 4}:
                        errors.append(
                            f"visual_items[{index}].{collection}[{child_index}]."
                            "evidence_bbox must be empty or contain exactly four coordinates"
                        )

    # The quick checks above retain concise, stable messages for common model
    # failures. The schema pass enforces every required field, primitive type,
    # enum, array item, and additionalProperties constraint.
    for error in sorted(
        Draft202012Validator(schema).iter_errors(payload),
        key=lambda item: tuple(str(part) for part in item.absolute_path),
    ):
        location = ".".join(str(part) for part in error.absolute_path)
        message = f"{location or '$'}: {error.message}"
        if message not in errors:
            errors.append(message)
    return errors


def output_stems_for_asset_ids(asset_ids: list[str]) -> list[str]:
    """Create deterministic, non-overwriting filenames for manifest assets."""
    duplicates = sorted({asset_id for asset_id in asset_ids if asset_ids.count(asset_id) > 1})
    if duplicates:
        raise ValueError(f"Image manifest contains duplicate asset_id values: {duplicates}")

    bases = [safe_id(asset_id) for asset_id in asset_ids]
    collisions = {base for base in bases if bases.count(base) > 1}
    stems: list[str] = []
    for asset_id, base in zip(asset_ids, bases):
        if base in collisions:
            digest = hashlib.sha256(asset_id.encode("utf-8")).hexdigest()[:12]
            stems.append(f"{base}_{digest}")
        else:
            stems.append(base)
    if len(stems) != len(set(stems)):
        raise ValueError("Could not derive unique output filenames from image manifest asset IDs")
    return stems


def select_extraction_assets(
    manifest: pd.DataFrame, *, include_overviews: bool
) -> pd.DataFrame:
    """Prefer detail tiles and retain whole-page images for reviewer context."""
    if include_overviews or "asset_type" not in manifest.columns:
        return manifest.copy()
    asset_types = manifest["asset_type"].map(normalize).str.lower()
    pages_with_tiles = set(manifest.loc[asset_types.eq("tile"), "page_number"].tolist())
    overview_types = {"page", "page_image", "page_overview", "overview"}
    redundant_overview = manifest["page_number"].isin(pages_with_tiles) & asset_types.isin(
        overview_types
    )
    selected = manifest.loc[~redundant_overview].copy()
    if selected.empty:
        raise ValueError("No image assets remain after overview/tile selection")
    return selected


def installed_model_record(tags: dict[str, Any], model: str) -> dict[str, Any] | None:
    requested = model.casefold()
    requested_latest = f"{requested}:latest" if ":" not in requested else requested
    for record in tags.get("models", []):
        if not isinstance(record, dict):
            continue
        names = {normalize(record.get("name")).casefold(), normalize(record.get("model")).casefold()}
        if requested in names or requested_latest in names:
            return record
    return None


def preflight_ollama(base_url: str, model: str, timeout: int, probe: bool) -> dict[str, Any]:
    version_payload = request_json("GET", f"{base_url}/api/version", timeout=timeout)
    tags = request_json("GET", f"{base_url}/api/tags", timeout=timeout)
    model_record = installed_model_record(tags, model)
    if model_record is None:
        raise OllamaRequestError(
            f"Ollama model '{model}' is not installed. Run: ollama pull {model}"
        )
    show = request_json(
        "POST",
        f"{base_url}/api/show",
        timeout=timeout,
        payload={"model": model},
    )
    if probe:
        probe_schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["ok"],
            "properties": {"ok": {"type": "boolean"}},
        }
        raw = request_json(
            "POST",
            f"{base_url}/api/generate",
            timeout=timeout,
            payload={
                "model": model,
                "prompt": "This is a runtime vision probe. Return {\"ok\": true}.",
                "images": [VISION_PROBE_IMAGE],
                "stream": False,
                "think": False,
                "format": probe_schema,
                "options": {"temperature": 0},
            },
        )
        parsed_probe = parse_model_json(final_response_text(raw))
        if parsed_probe.get("ok") is not True:
            raise OllamaRequestError(
                f"Model '{model}' loaded but failed the schema/vision probe: {parsed_probe}"
            )
    model_info = show.get("model_info") if isinstance(show.get("model_info"), dict) else {}
    details = show.get("details") if isinstance(show.get("details"), dict) else {}
    return {
        "ollama_version": normalize(version_payload.get("version")),
        "model": model,
        "model_digest": normalize(model_record.get("digest")),
        "model_family": normalize(details.get("family")),
        "model_architecture": normalize(model_info.get("general.architecture")),
        "vision_probe_completed": probe,
    }


def asset_id_for_row(row: pd.Series) -> str:
    supplied = normalize(row.get("asset_id"))
    if supplied:
        return supplied
    page_number = int(row["page_number"])
    return f"page_{page_number:03d}_overview"


def image_hash_for_row(row: pd.Series) -> str:
    """Read the canonical image hash with support for early tiled manifests."""
    return normalize(row.get("image_sha256")) or normalize(row.get("asset_sha256"))


def manifest_item_title(row: pd.Series) -> str:
    """Return authoritative visual identifiers without inventing a clinical title."""
    identifiers = [
        normalize(row.get(field)) for field in ("figure_ids", "table_ids")
    ]
    return "; ".join(dict.fromkeys(value for value in identifiers if value))


def placeholder_payload(row: pd.Series, status: str, message: str) -> dict[str, Any]:
    page_number = int(row["page_number"])
    asset_id = asset_id_for_row(row)
    title = manifest_item_title(row)
    return {
        "asset_id": asset_id,
        "page_number": page_number,
        "asset_type": normalize(row.get("asset_type")) or "overview",
        "source_pdf": normalize(row.get("source_pdf")),
        "source_pdf_sha256": normalize(row.get("source_pdf_sha256")),
        "image_sha256": image_hash_for_row(row),
        "evidence_bbox_space": EVIDENCE_BBOX_SPACE,
        "visual_items": [
            {
                "item_id": f"{safe_id(asset_id)}__review",
                "item_type": "page",
                "title": title,
                "clinical_scope": "ADA pharmacotherapy guideline visual content",
                "symbols_or_footnotes": [],
                "ordinal_symbols": [],
                "decision_nodes": [],
                "recommendation_edges": [],
                "drug_actions": [],
                "retrieval_summary": normalize(row.get("page_text_preview")),
                "extraction_pass": "placeholder",
            }
        ],
        "extraction_passes": [],
        "extraction_warnings": [f"{status}: {message}"],
        "raw_model_responses": {},
        "raw_model_metrics": {},
    }


def build_prompt(row: pd.Series, pass_name: str, prompt_mode: str) -> str:
    asset_id = asset_id_for_row(row)
    role = normalize(row.get("asset_type")) or "overview"
    location = ""
    bbox_values = [normalize(row.get(name)) for name in ("bbox_x0", "bbox_y0", "bbox_x1", "bbox_y1")]
    if all(bbox_values):
        location = f" Source-page bounding box: {bbox_values}."
    compact = (
        "Use concise text and no inferred medical advice. "
        if prompt_mode == "simple"
        else (
            "Preserve exact thresholds, logical alternatives (AND/OR), priority language, arrow "
            "direction, medication-class distinctions, and uncertainty. Do not turn alternatives "
            "into separate mandatory recommendations. "
        )
    )
    return (
        "Extract clinical guideline evidence visible in this single image asset. "
        f"Asset ID: {asset_id}. PDF page: {int(row['page_number'])}. Asset type: {role}."
        f"{location} {compact}{PASS_INSTRUCTIONS[pass_name]} "
        "Return at most one aggregate visual item for this asset and pass; put every "
        "visible record of the requested types into that item's child arrays. "
        "Evidence bounding boxes use Qwen3-VL's normalized 0-1000 coordinate grid, not source "
        "pixels; use [] when uncertain. Every child record needs a nonempty evidence_text that "
        "quotes or closely transcribes visible local wording. For an arrow, evidence_text may "
        "briefly identify the visible source label, arrow, and target label. "
        "For a tile, do not invent off-tile nodes or arrow endpoints: retain visible evidence and "
        "set requires_manual_review=true for any boundary-crossing relationship. "
        "Return only data matching the supplied JSON Schema."
    )


def call_ollama_vision(
    image_path: Path,
    model: str,
    base_url: str,
    page_number: int,
    timeout: int,
    prompt: str,
    pass_name: str,
    num_ctx: int,
    num_predict: int,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    raw = request_json(
        "POST",
        f"{base_url}/api/generate",
        timeout=timeout,
        payload={
            "model": model,
            "prompt": prompt,
            "images": [encode_image(image_path)],
            "stream": False,
            "think": False,
            "format": response_schema_for_pass(pass_name),
            "options": {
                "temperature": 0,
                "seed": 0,
                "num_ctx": num_ctx,
                "num_predict": num_predict,
            },
        },
    )
    if normalize(raw.get("done_reason")).lower() == "length":
        raise OllamaRequestError(
            f"Ollama truncated the {pass_name} response at "
            f"{raw.get('eval_count', 'unknown')} generated tokens. Increase the "
            "configured context/output budget or reduce the per-pass schema bounds."
        )
    model_text = final_response_text(raw)
    parsed = parse_model_json(model_text)
    shape_errors = validate_response_shape(parsed, schema=response_schema_for_pass(pass_name))
    if shape_errors:
        raise ValueError("; ".join(shape_errors))
    # Model-provided provenance is never trusted.
    parsed["page_number"] = page_number
    metrics = {
        field: raw.get(field)
        for field in (
            "done_reason",
            "total_duration",
            "load_duration",
            "prompt_eval_count",
            "prompt_eval_duration",
            "eval_count",
            "eval_duration",
        )
        if raw.get(field) is not None
    }
    return parsed, model_text, metrics


def extract_asset(
    row: pd.Series,
    *,
    model: str,
    base_url: str,
    timeout: int,
    prompt_mode: str,
    passes: list[str],
    num_ctx: int,
    num_predict: int,
) -> tuple[dict[str, Any], str, str]:
    page_number = int(row["page_number"])
    asset_id = asset_id_for_row(row)
    image_path = Path(normalize(row.get("image_path")))
    if not image_path.exists():
        raise FileNotFoundError(f"Image asset does not exist: {image_path}")

    combined = placeholder_payload(row, "unvalidated", "Model output requires validation.")
    combined["visual_items"] = []
    combined["extraction_warnings"] = []
    combined["extraction_passes"] = passes
    combined["raw_model_responses"] = {}
    combined["raw_model_metrics"] = {}
    errors: list[str] = []

    for pass_name in passes:
        try:
            parsed, raw_text, metrics = call_ollama_vision(
                image_path=image_path,
                model=model,
                base_url=base_url,
                page_number=page_number,
                timeout=timeout,
                prompt=build_prompt(row, pass_name, prompt_mode),
                pass_name=pass_name,
                num_ctx=num_ctx,
                num_predict=num_predict,
            )
        except (OllamaRequestError, ValueError) as exc:
            errors.append(f"{pass_name}: {exc}")
            continue
        combined["raw_model_responses"][pass_name] = raw_text
        combined["raw_model_metrics"][pass_name] = metrics
        for item_index, item in enumerate(parsed.get("visual_items", []), start=1):
            raw_item_id = safe_id(item.get("item_id") or f"item_{item_index:02d}")
            item["item_id"] = f"{safe_id(asset_id)}__{pass_name}__{raw_item_id}"
            if not normalize(item.get("title")):
                fallback_title = manifest_item_title(row)
                if fallback_title:
                    item["title"] = fallback_title
                    combined["extraction_warnings"].append(
                        f"{pass_name}: visual item title derived from manifest "
                        "figure_ids/table_ids"
                    )
            item["extraction_pass"] = pass_name
            combined["visual_items"].append(item)
        for warning in parsed.get("extraction_warnings", []):
            combined["extraction_warnings"].append(f"{pass_name}: {normalize(warning)}")

    if errors:
        combined["extraction_warnings"].extend(errors)
    combined.update(
        {
            "asset_id": asset_id,
            "page_number": page_number,
            "asset_type": normalize(row.get("asset_type")) or "overview",
            "source_pdf": normalize(row.get("source_pdf")),
            "source_pdf_sha256": normalize(row.get("source_pdf_sha256")),
            "image_sha256": image_hash_for_row(row),
            "evidence_bbox_space": EVIDENCE_BBOX_SPACE,
        }
    )

    if not combined["visual_items"]:
        status = "model_output_invalid"
    elif errors:
        status = "partial_extraction"
    else:
        status = "extracted_unvalidated"
    return combined, status, "; ".join(errors)


MANIFEST_FIELDS = [
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image-manifest", default="outputs/page_images/page_image_manifest.csv")
    parser.add_argument("--output-dir", default="outputs/visual_logic_raw")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument(
        "--use-ollama",
        action="store_true",
        help="Run a preflighted local Ollama model. Outputs remain unvalidated.",
    )
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument(
        "--num-ctx",
        type=int,
        default=DEFAULT_NUM_CTX,
        help="Ollama context window per request. Default: 8192 tokens.",
    )
    parser.add_argument(
        "--num-predict",
        type=int,
        default=DEFAULT_NUM_PREDICT,
        help="Maximum generated tokens per extraction pass. Default: 4096.",
    )
    parser.add_argument(
        "--prompt-mode",
        choices=["full", "simple"],
        default="full",
        help="Simple shortens wording but retains the same strict schema and no result caps.",
    )
    parser.add_argument(
        "--extraction-pass",
        action="append",
        choices=["structure", "actions", "symbols", "all"],
        dest="passes",
        help="Repeat to select passes. Default: structure, actions, symbols.",
    )
    parser.add_argument(
        "--skip-vision-probe",
        action="store_true",
        help="Skip the one-pixel model-load probe (not recommended).",
    )
    parser.add_argument(
        "--include-overviews",
        action="store_true",
        help=(
            "Also extract whole-page overviews when tiles exist. By default, "
            "overviews remain reviewer references to avoid noisy duplicate extraction."
        ),
    )
    args = parser.parse_args()
    if args.num_ctx <= 0 or args.num_predict <= 0:
        raise ValueError("--num-ctx and --num-predict must be positive integers")
    if args.num_predict >= args.num_ctx:
        raise ValueError("--num-predict must be smaller than --num-ctx")

    manifest_path = Path(args.image_manifest)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if not manifest_path.exists():
        raise FileNotFoundError(
            "Missing page image manifest. Run 05_render_pdf_pages_to_images.py first."
        )

    manifest = pd.read_csv(manifest_path)
    required_manifest = {"page_number", "image_path"}
    missing = sorted(required_manifest - set(manifest.columns))
    if missing:
        raise ValueError(f"Image manifest is missing required columns: {missing}")
    all_asset_ids = [asset_id_for_row(row) for _, row in manifest.iterrows()]
    # Validate the full manifest before filtering so a hidden duplicate is not
    # ignored merely because it belongs to a reference-only overview.
    output_stems_for_asset_ids(all_asset_ids)
    manifest_asset_count = len(manifest)
    manifest = select_extraction_assets(
        manifest, include_overviews=args.include_overviews
    ).reset_index(drop=True)
    passes = args.passes or DEFAULT_PASSES
    if "all" in passes and len(passes) > 1:
        raise ValueError("Use extraction pass 'all' alone, not with specialized passes.")
    base_url = ollama_base_url(args.ollama_url)
    run_info: dict[str, Any] = {
        "started_at": utc_now(),
        "image_manifest": str(manifest_path),
        "model": args.model if args.use_ollama else "",
        "ollama_url": base_url if args.use_ollama else "",
        "extraction_passes": passes,
        "num_ctx": args.num_ctx,
        "num_predict": args.num_predict,
        "n_manifest_assets": manifest_asset_count,
        "asset_selection": (
            "tiles_and_overviews" if args.include_overviews else "tiles_preferred"
        ),
        "status": "starting",
    }
    runtime: dict[str, Any] = {}

    asset_ids = [asset_id_for_row(row) for _, row in manifest.iterrows()]
    output_stems = output_stems_for_asset_ids(asset_ids)

    if args.use_ollama:
        try:
            runtime = preflight_ollama(
                base_url,
                args.model,
                args.timeout,
                probe=not args.skip_vision_probe,
            )
        except Exception as exc:
            run_info.update({"status": "runtime_incompatible", "error": str(exc), "finished_at": utc_now()})
            (output_dir / "extraction_run.json").write_text(
                json.dumps(run_info, indent=2), encoding="utf-8"
            )
            write_csv(output_dir / "visual_logic_manifest.csv", [], MANIFEST_FIELDS)
            raise SystemExit(f"Ollama preflight failed; no assets were processed. {exc}") from exc

    records: list[dict[str, Any]] = []
    for row_position, (_, row) in enumerate(manifest.iterrows()):
        page_number = int(row["page_number"])
        asset_id = asset_ids[row_position]
        out_path = output_dir / f"visual_logic_{output_stems[row_position]}.json"
        error_message = ""
        if args.use_ollama:
            try:
                payload, status, error_message = extract_asset(
                    row,
                    model=args.model,
                    base_url=base_url,
                    timeout=args.timeout,
                    prompt_mode=args.prompt_mode,
                    passes=passes,
                    num_ctx=args.num_ctx,
                    num_predict=args.num_predict,
                )
            except Exception as exc:
                error_message = str(exc)
                payload = placeholder_payload(row, "asset_failed", error_message)
                status = "asset_failed"
        else:
            payload = placeholder_payload(
                row,
                "ollama_not_used",
                "Run with --use-ollama after starting a compatible local vision model.",
            )
            status = "needs_visual_model"

        payload["runtime"] = runtime
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        records.append(
            {
                "asset_id": asset_id,
                "page_number": page_number,
                "asset_type": normalize(row.get("asset_type")) or "overview",
                "image_path": normalize(row.get("image_path")),
                "image_sha256": image_hash_for_row(row),
                "evidence_bbox_space": EVIDENCE_BBOX_SPACE,
                "source_pdf": normalize(row.get("source_pdf")),
                "source_pdf_sha256": normalize(row.get("source_pdf_sha256")),
                "bbox_x0": normalize(row.get("bbox_x0")),
                "bbox_y0": normalize(row.get("bbox_y0")),
                "bbox_x1": normalize(row.get("bbox_x1")),
                "bbox_y1": normalize(row.get("bbox_y1")),
                "visual_logic_json": str(out_path),
                "model": args.model if args.use_ollama else "",
                "model_digest": runtime.get("model_digest", ""),
                "ollama_version": runtime.get("ollama_version", ""),
                "extraction_passes": ";".join(passes),
                "status": status,
                "error_message": error_message,
                "figure_ids": normalize(row.get("figure_ids")),
                "table_ids": normalize(row.get("table_ids")),
            }
        )
        print(
            f"[{row_position + 1}/{len(manifest)}] {asset_id}: {status}",
            flush=True,
        )

    write_csv(output_dir / "visual_logic_manifest.csv", records, MANIFEST_FIELDS)
    run_info.update(
        {
            **runtime,
            "status": "completed_unvalidated" if args.use_ollama else "placeholders_only",
            "n_assets": len(records),
            "status_counts": {
                str(key): int(value)
                for key, value in pd.Series(
                    [row["status"] for row in records], dtype="string"
                ).value_counts().items()
            },
            "finished_at": utc_now(),
        }
    )
    (output_dir / "extraction_run.json").write_text(
        json.dumps(run_info, indent=2), encoding="utf-8"
    )
    print(f"Wrote unvalidated visual logic JSON for {len(records)} image assets.")
    print(f"Wrote manifest to {output_dir / 'visual_logic_manifest.csv'}")
    print("No visual record is released until step 07 validation and human approval.")


if __name__ == "__main__":
    main()
