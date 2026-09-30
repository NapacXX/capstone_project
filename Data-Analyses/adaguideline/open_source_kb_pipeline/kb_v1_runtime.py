"""Offline, portable consumer for versioned ADA evidence bundles.

Only NumPy is imported eagerly. SentenceTransformer is loaded from the bundle
when a query actually runs. Vector row i corresponds to units.jsonl line i;
both artifacts are covered by the manifest. Similarity is not clinical validity.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

import numpy as np


SCHEMA_VERSION = "ada-kb-v1"
STRUCTURED_FIELDS = (
    ("hba1c_percent", "HbA1c"),
    ("egfr_ml_min_1_73m2", "eGFR"),
    ("uacr_mg_g", "UACR"),
    ("bmi_kg_m2", "BMI"),
    ("chronic_kidney_disease", "CKD"),
    ("stage_ckd", "CKD stage"),
    ("heart_failure_present", "heart failure"),
    ("hf_type", "HF type"),
    ("coronary_artery_disease", "CAD"),
    ("mi_history", "MI history"),
    ("stroke_tia_history", "stroke/TIA"),
    ("masld_present", "MASLD"),
    ("mash_present", "MASH"),
    ("current_dm_drugs", "baseline diabetes drugs"),
    ("financial_limitations", "financial limitations"),
    ("preference_simple_regimen", "simple regimen preference"),
    ("reluctant_injections", "reluctant injections"),
)
REQUIRED_FILES = {"records.jsonl", "units.jsonl", "embeddings.npy", "sources.json"}
MISSING_VALUES = {"", "nan", "null", "none", "na", "n/a"}
VISUAL_CLINICAL_FIELDS = (
    "_record_type", "title", "node_id", "condition", "patient_variables",
    "true_branch", "false_branch", "from_node", "to_node", "edge_condition",
    "arrow_text", "drug_class", "action", "trigger", "strength",
    "dose_or_use_logic", "caution_or_contraindication", "symbol", "meaning",
    "applies_to", "row_label", "clinical_dimension", "raw_symbol", "symbol_role",
    "ordinal_level", "direction", "interpretation", "symbol_count",
    "evidence_bbox", "evidence_bbox_space", "asset_id", "asset_type", "asset_bbox",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def safe_bundle_path(bundle_dir: Path, relative: str) -> Path:
    """Reject traversal, Windows absolute paths, and nonportable symlink files."""
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError(f"Unsafe bundle path: {relative!r}")
    logical = PurePosixPath(relative)
    if (
        logical.is_absolute()
        or logical.as_posix() != relative
        or any(part in {"", ".", ".."} or ":" in part for part in logical.parts)
    ):
        raise ValueError(f"Unsafe bundle path: {relative!r}")
    root = Path(bundle_dir).resolve(strict=True)
    if (root / ".incomplete").exists():
        raise ValueError("Bundle is marked incomplete")
    path = root.joinpath(*logical.parts)
    current = root
    for part in logical.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"Bundle paths must be materialized, not symlinks: {relative}")
    try:
        path.resolve(strict=True).relative_to(root)
    except (ValueError, FileNotFoundError) as exc:
        raise ValueError(f"Missing or escaping bundle path: {relative}") from exc
    return path


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Malformed UTF-8 JSON: {path.name}") from exc


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                raise ValueError(f"Blank JSONL row: {path.name}:{number}")
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Malformed JSONL: {path.name}:{number}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"JSONL row must be an object: {path.name}:{number}")
            records.append(row)
    return records


def _unique_rows(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    result = {}
    for row in rows:
        identifier = row.get(key)
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError(f"Missing {key}")
        if identifier in result:
            raise ValueError(f"Duplicate {key}: {identifier}")
        result[identifier] = row
    return result


def is_visual_record(record: dict[str, Any]) -> bool:
    return bool(record.get("is_visual")) or str(record.get("content_type", "")).startswith("visual")


def _check_visual_release(record: dict[str, Any]) -> None:
    identifier = record["record_id"]
    if (
        record.get("review_status") != "approved"
        or record.get("release_status") != "released"
        or record.get("human_approved") is not True
    ):
        raise ValueError(f"Unreleased visual record cannot be indexed: {identifier}")
    reviewer = record.get("approved_by") or record.get("reviewer")
    reviewed_at = record.get("approved_at") or record.get("reviewed_at")
    fingerprint = record.get("effective_content_sha256") or record.get("content_sha256")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ValueError(f"Visual record lacks human reviewer: {identifier}")
    try:
        stamp = datetime.fromisoformat(str(reviewed_at).replace("Z", "+00:00"))
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("Timezone required")
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Visual record lacks timezone-aware approval time: {identifier}") from exc
    if not isinstance(fingerprint, str) or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None:
        raise ValueError(f"Visual record lacks approved content fingerprint: {identifier}")


def expected_visual_retrieval_text(row: dict[str, Any]) -> str:
    """Stdlib projection matching Step 07's build_retrieval_record exactly.

    This duplication deliberately avoids pandas in consumer verification. A
    parity test and real-corpus check bind it to the maintainer's projection.
    """
    fields = {
        "node": ("title", "=Decision node", "node_id", "condition", "patient_variables", "true_branch", "false_branch"),
        "edge": ("title", "=Recommendation edge", "from_node", "=to", "to_node", "edge_condition", "arrow_text"),
        "action": ("title", "drug_class", "action", "trigger", "strength", "dose_or_use_logic", "caution_or_contraindication"),
        "footnote": ("title", "=Footnote", "symbol", "meaning", "applies_to"),
        "symbol": ("title", "row_label", "clinical_dimension", "raw_symbol", "symbol_role", "ordinal_level", "direction", "interpretation"),
    }
    record_type = row.get("_record_type")
    if record_type not in fields:
        raise ValueError(f"Unsupported canonical visual record type: {record_type}")
    parts = []
    for key in fields[record_type]:
        value = key[1:] if key.startswith("=") else row.get(key, "")
        if value is None or (isinstance(value, float) and np.isnan(value)):
            continue
        normalized = re.sub(r"\s+", " ", str(value)).strip()
        if normalized and normalized not in parts:
            parts.append(normalized)
    return "; ".join(parts)


def _verify_review_audit(root: Path, files: dict[str, str], records: list[dict[str, Any]]) -> None:
    required = {"audit/review/canonical_records.jsonl", "audit/review/review_ledger.jsonl", "audit/review/group_reviews.json", "audit/review/review_manifest.json"}
    if not required.issubset(files):
        raise ValueError("Released visual evidence requires the complete hash-bound review ledger and groups")
    # Import the trusted companion shipped with this consumer, never a path
    # supplied by the data manifest. Its audit function uses the standard library.
    specification = importlib.util.spec_from_file_location("kb_v1_consumer_review", Path(__file__).with_name("kb_v1_review.py"))
    if specification is None or specification.loader is None:
        raise ValueError("Missing consumer review verifier")
    reviewer = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(reviewer)
    canonical = read_jsonl(root / "audit" / "review" / "canonical_records.jsonl")
    ledger = read_jsonl(root / "audit" / "review" / "review_ledger.jsonl")
    groups = _json(root / "audit" / "review" / "group_reviews.json")
    review_manifest = _json(root / "audit" / "review" / "review_manifest.json")
    expected = review_manifest.get("expected_record_ids") if isinstance(review_manifest, dict) else None
    if not isinstance(expected, list) or not expected or any(not isinstance(value, str) or not value for value in expected) or len(expected) != len(set(expected)):
        raise ValueError("Review manifest lacks a unique original candidate inventory")
    if not isinstance(groups, dict) or set(groups.get("expected_record_ids", [])) != set(expected):
        raise ValueError("Group expected IDs differ from the frozen review manifest")
    approved = reviewer.assert_review_audit(canonical, ledger, groups)
    approved_map = _unique_rows(approved, "record_id")
    published = {record["record_id"]: record for record in records if is_visual_record(record)}
    if set(published) != set(approved_map):
        raise ValueError("Indexed visual records differ from the approved canonical set")
    for identifier, record in published.items():
        original = approved_map[identifier]
        if record.get("v1_reviewed_content_sha256") != reviewer.record_fingerprint(original):
            raise ValueError(f"Published visual does not match its v1 approval fingerprint: {identifier}")
        if record["dependency_ids"] != original["v1_dependency_ids"]:
            raise ValueError(f"Published visual dependencies differ from reviewed dependencies: {identifier}")
        if record["source_id"] != original["source_pdf_sha256"] or record["page_number"] != original["page_number"]:
            raise ValueError(f"Published visual source differs from reviewed source: {identifier}")
        if record["source_text"] != original.get("evidence_text"):
            raise ValueError(f"Published visual source text differs from reviewed evidence: {identifier}")
        if record["content_type"] != "visual_" + original["_record_type"]:
            raise ValueError(f"Published visual type differs from reviewed evidence: {identifier}")
        if record["retrieval_text"] != expected_visual_retrieval_text(original):
            raise ValueError(f"Published visual retrieval text differs from reviewed evidence: {identifier}")
        for field in VISUAL_CLINICAL_FIELDS:
            if field in original and record.get(field) != original[field]:
                raise ValueError(f"Published visual clinical field {field} differs from reviewed evidence: {identifier}")
        if original.get("image_sha256") and record.get("image_sha256") != original["image_sha256"]:
            raise ValueError(f"Published visual image differs from reviewed image: {identifier}")


def verify_bundle(bundle_dir: str | Path, allow_candidate: bool = False) -> dict[str, Any]:
    """Verify hashes, relative provenance, index contracts, and release metadata.

    This integrity check is not an independent clinical review or a digital
    signature. Release builders must also reconcile the human review ledger.
    Candidate mode never permits unapproved visual records in the index.
    """
    root = Path(bundle_dir).resolve(strict=True)
    manifest = _json(safe_bundle_path(root, "manifest.json"))
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"Unsupported bundle schema; expected {SCHEMA_VERSION}")
    if manifest.get("status") not in {"released", "review_candidate"}:
        raise ValueError("Bundle status must be released or review_candidate")
    if manifest["status"] != "released" and not allow_candidate:
        raise ValueError("Review candidate is NOT a formal v1 release; explicitly allow_candidate to inspect")
    if not isinstance(manifest.get("kb_version"), str) or not manifest["kb_version"].strip():
        raise ValueError("Missing kb_version")
    expected_records = _positive_int(manifest.get("record_count"), "record_count")
    expected_units = _positive_int(manifest.get("unit_count"), "unit_count")
    files = manifest.get("files")
    if not isinstance(files, dict) or not REQUIRED_FILES.issubset(files):
        raise ValueError("Manifest files must cover records, units, embeddings, and sources")
    for relative, expected_hash in files.items():
        if not isinstance(expected_hash, str) or re.fullmatch(r"[0-9a-f]{64}", expected_hash) is None:
            raise ValueError(f"Invalid SHA-256 for {relative}")
        path = safe_bundle_path(root, relative)
        if not path.is_file() or sha256_file(path) != expected_hash:
            raise ValueError(f"Checksum mismatch or non-file: {relative}")

    embedding = manifest.get("embedding")
    if not isinstance(embedding, dict) or embedding.get("normalize") is not True:
        raise ValueError("Bundle requires normalized embeddings")
    dimension = _positive_int(embedding.get("dimension"), "embedding.dimension")
    _positive_int(embedding.get("max_seq_length"), "embedding.max_seq_length")
    for field in ("model_id", "revision", "model_dir"):
        if not isinstance(embedding.get(field), str) or not embedding[field].strip():
            raise ValueError(f"Missing embedding.{field}")
    model_dir = safe_bundle_path(root, embedding["model_dir"])
    if not model_dir.is_dir():
        raise ValueError("Embedding model_dir must be a directory")
    model_files = []
    for path in model_dir.rglob("*"):
        relative = path.relative_to(root).as_posix()
        safe_bundle_path(root, relative)
        if path.is_file():
            model_files.append(relative)
            if relative not in files:
                raise ValueError(f"Model file not covered by manifest: {relative}")
    if not model_files:
        raise ValueError("Embedding model directory is empty")
    if manifest.get("vector_order", "units.jsonl") != "units.jsonl":
        raise ValueError("Unsupported vector order; rows must follow units.jsonl")

    sources = _json(root / "sources.json")
    if not isinstance(sources, dict) or not sources:
        raise ValueError("sources.json must contain source objects")
    for source_id, source in sources.items():
        if not isinstance(source, dict) or not source.get("title") or not source.get("year"):
            raise ValueError(f"Missing source title/year: {source_id}")
        relative = source.get("relative_path")
        safe_bundle_path(root, relative)
        if relative not in files or files[relative] != source.get("pdf_sha256"):
            raise ValueError(f"Source PDF is not hash-bound to manifest: {source_id}")
        images = source.get("images", [])
        if not isinstance(images, list):
            raise ValueError(f"Source images must be a list: {source_id}")
        for image in images:
            if not isinstance(image, dict):
                raise ValueError(f"Invalid source image: {source_id}")
            image_path = image.get("relative_path")
            safe_bundle_path(root, image_path)
            if image_path not in files or files[image_path] != image.get("sha256"):
                raise ValueError(f"Source image is not hash-bound to manifest: {source_id}")

    records = read_jsonl(root / "records.jsonl")
    by_record = _unique_rows(records, "record_id")
    units = read_jsonl(root / "units.jsonl")
    _unique_rows(units, "unit_id")
    if len(records) != expected_records or len(units) != expected_units:
        raise ValueError("Manifest record/unit counts do not match JSONL contents")
    visual_count = 0
    for record in records:
        for key in ("content_type", "source_id", "source_text", "review_status", "release_status"):
            if not isinstance(record.get(key), str) or not record[key].strip():
                raise ValueError(f"Record {record['record_id']} lacks {key}")
        if not isinstance(record.get("retrieval_text"), str):
            raise ValueError(f"Record {record['record_id']} lacks retrieval_text")
        if record.get("indexable", True) is not False and not record["retrieval_text"].strip():
            raise ValueError(f"Indexed record has empty retrieval_text: {record['record_id']}")
        if record["source_id"] not in sources:
            raise ValueError(f"Unknown source_id: {record['source_id']}")
        if record.get("image_sha256") and record["image_sha256"] not in {entry["sha256"] for entry in sources[record["source_id"]].get("images", [])}:
            raise ValueError(f"Record image is absent from portable source registry: {record['record_id']}")
        _positive_int(record.get("page_number"), "record.page_number")
        dependencies = record.get("dependency_ids")
        if not isinstance(dependencies, list) or any(not isinstance(value, str) for value in dependencies):
            raise ValueError(f"Invalid dependency_ids: {record['record_id']}")
        for dependency in dependencies:
            if dependency not in by_record:
                raise ValueError(f"Unresolved evidence dependency: {record['record_id']} -> {dependency}")
        if is_visual_record(record):
            if record.get("indexable", True) is False:
                raise ValueError("Released visual records cannot be hidden as unindexed audit records")
            visual_count += 1
            _check_visual_release(record)
    if manifest["status"] == "released" and visual_count == 0:
        raise ValueError("Formal v1 release requires at least one released visual record")
    if "visual_record_count" in manifest and manifest["visual_record_count"] != visual_count:
        raise ValueError("Manifest visual record count differs from indexed records")
    if visual_count:
        charts = [r for r in records if r["content_type"] == "visual_whole_chart"]
        if charts or manifest.get("visual_representation") == "whole_chart_v1":
            if manifest["status"] != "released" or manifest.get("visual_representation") != "whole_chart_v1" or len(charts) != visual_count:
                raise ValueError("Whole-chart release must be explicit and cannot mix legacy visual records")
            from kb_v1_chart_release import verify_chart_audit
            verify_chart_audit(root, files, records)
            for chart in charts:
                images = [image for image in sources[chart["source_id"]].get("images", [])
                          if image.get("figure_id") == chart["figure_id"]]
                if {image.get("page_number") for image in images} != set(chart["page_numbers"]):
                    raise ValueError("Whole-chart page images do not cover the approved page range")
                if not any(image["sha256"] == chart["image_sha256"] and image["page_number"] == chart["page_number"] for image in images):
                    raise ValueError("Whole-chart first-page image differs from approved source")
        else:
            _verify_review_audit(root, files, records)
    indexed_records = set()
    for unit in units:
        if unit.get("record_id") not in by_record:
            raise ValueError(f"Unknown record in unit: {unit.get('unit_id')}")
        if by_record[unit["record_id"]].get("indexable", True) is False:
            raise ValueError("audit-only records must not be indexed")
        if not isinstance(unit.get("text"), str) or not unit["text"].strip():
            raise ValueError(f"Empty retrieval unit: {unit['unit_id']}")
        parent_text = by_record[unit["record_id"]]["retrieval_text"]
        if unit["text"] not in parent_text:
            raise ValueError(f"Retrieval unit is not a span of its parent: {unit['unit_id']}")
        if "start_char" in unit or "end_char" in unit:
            start, end = unit.get("start_char"), unit.get("end_char")
            if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int) or not isinstance(end, int) or not (0 <= start < end <= len(parent_text)) or parent_text[start:end] != unit["text"]:
                raise ValueError(f"Retrieval unit offsets do not match its parent: {unit['unit_id']}")
        indexed_records.add(unit["record_id"])
    expected_indexed = {identifier for identifier, record in by_record.items() if record.get("indexable", True) is not False}
    if indexed_records != expected_indexed:
        raise ValueError("Every indexable record must have units; audit-only records must not be indexed")
    vectors = np.load(root / "embeddings.npy", allow_pickle=False)
    if vectors.dtype != np.dtype("float32") or vectors.shape != (expected_units, dimension):
        raise ValueError("Embeddings must be float32 with manifest (unit_count, dimension) shape")
    if not np.isfinite(vectors).all():
        raise ValueError("Embeddings contain non-finite values")
    if not np.allclose(np.linalg.norm(vectors, axis=1), 1.0, rtol=1e-3, atol=1e-3):
        raise ValueError("Embedding rows must have unit norm")
    return manifest


def _text(value: Any) -> str:
    if value is None:
        return ""
    result = str(value).strip()
    return "" if result.lower() in MISSING_VALUES else result


def build_case_query(row: dict[str, Any], input_mode: str = "text") -> str:
    if input_mode == "text":
        query = _text(row.get("vignette_text"))
        if not query:
            raise ValueError("Text input requires non-empty vignette_text")
        return query
    if input_mode != "structured":
        raise ValueError("input_mode must be text or structured")
    features = [f"{label} {_text(row.get(field))}" for field, label in STRUCTURED_FIELDS if _text(row.get(field))]
    if not features:
        raise ValueError("Structured case contains no recognized non-empty clinical fields")
    return "Type 2 diabetes pharmacotherapy guideline context. " + "; ".join(features)


def prepare_cases(rows: Iterable[dict[str, Any]], input_mode: str = "text") -> list[dict[str, Any]]:
    """Deduplicate equal cases; do not silently discard conflicting case content."""
    result = []
    by_id = {}
    for row_number, row in enumerate(rows, 2):
        identifier = _text(row.get("case_id"))
        if not identifier:
            raise ValueError(f"Missing case_id at data row {row_number}")
        query = build_case_query(row, input_mode)
        canonical = " ".join(query.split())
        if identifier in by_id:
            if canonical != by_id[identifier]:
                raise ValueError(f"Conflicting content for case_id {identifier}")
            continue
        by_id[identifier] = canonical
        clinical_input = (
            {"vignette_text": query}
            if input_mode == "text"
            else {field: _text(row.get(field)) for field, _ in STRUCTURED_FIELDS if _text(row.get(field))}
        )
        result.append({"case_id": identifier, "retrieval_query": query, "case_input": clinical_input})
    if not result:
        raise ValueError("No cases supplied")
    return result


def load_cases(cases_path: str | Path, input_mode: str = "text") -> list[dict[str, Any]]:
    with Path(cases_path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "case_id" not in reader.fieldnames:
            raise ValueError("Cases CSV requires case_id column")
        if len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError("Cases CSV contains duplicate column names")
        if input_mode == "text" and "vignette_text" not in reader.fieldnames:
            raise ValueError("Text input requires vignette_text column")
        rows = []
        for row in reader:
            if None in row:
                raise ValueError("Malformed CSV row: more values than column names")
            rows.append(row)
    return prepare_cases(rows, input_mode)


def _token_count(text: str, tokenizer: Any) -> int:
    return len(tokenizer(text, add_special_tokens=True, truncation=False)["input_ids"])


def token_windows(text: str, tokenizer: Any, max_length: int, overlap: int = 32) -> list[dict[str, Any]]:
    """Create bounded windows using original character offsets, never decoding IDs.

    Every original character is retained by at least one window, including
    whitespace, punctuation, and clinical symbols. Token counts include special
    tokens and are rechecked after slicing to handle wordpiece boundaries.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Cannot window empty text")
    _positive_int(max_length, "max_length")
    if not isinstance(overlap, int) or isinstance(overlap, bool) or overlap < 0:
        raise ValueError("overlap must be a non-negative integer")
    special_count = tokenizer.num_special_tokens_to_add(pair=False)
    capacity = max_length - special_count
    if capacity < 1:
        raise ValueError("max_length leaves no space after special tokens")
    effective_overlap = min(overlap, capacity - 1)
    try:
        encoded = tokenizer(text, add_special_tokens=False, truncation=False, return_offsets_mapping=True)
        offsets = [tuple(pair) for pair in encoded["offset_mapping"]]
    except (KeyError, TypeError, NotImplementedError) as exc:
        raise ValueError("A fast tokenizer with character offsets is required") from exc
    if not offsets or any(len(pair) != 2 or pair[1] <= pair[0] for pair in offsets):
        raise ValueError("Tokenizer returned invalid or empty character offsets")
    if any(start < 0 or end > len(text) for start, end in offsets):
        raise ValueError("Tokenizer offsets escape source text")
    if any(offsets[index][0] < offsets[index - 1][0] for index in range(1, len(offsets))):
        raise ValueError("Tokenizer offsets are not ordered")
    windows = []
    start_token = 0
    while start_token < len(offsets):
        end_token = min(start_token + capacity, len(offsets))
        start_char = 0 if start_token == 0 else offsets[start_token][0]
        while True:
            end_char = offsets[end_token][0] if end_token < len(offsets) else len(text)
            piece = text[start_char:end_char]
            count = _token_count(piece, tokenizer)
            if count <= max_length:
                break
            end_token -= 1
            if end_token <= start_token:
                raise ValueError("One offset-aligned token cannot fit the configured model limit")
        windows.append({"window_index": len(windows), "start_char": start_char, "end_char": end_char, "token_count": count, "text": piece})
        if end_token == len(offsets):
            break
        start_token = max(start_token + 1, end_token - effective_overlap)
    if windows[0]["start_char"] != 0 or windows[-1]["end_char"] != len(text):
        raise ValueError("Token windowing lost source boundaries")
    if any(right["start_char"] > left["end_char"] for left, right in zip(windows, windows[1:])):
        raise ValueError("Token windowing lost source characters")
    return windows


def rank_evidence(
    records: list[dict[str, Any]],
    units: list[dict[str, Any]],
    vectors: np.ndarray,
    query_vectors: np.ndarray,
    top_k: int = 8,
) -> list[dict[str, Any]]:
    """Exact cosine ranking over query windows; deterministic per-evidence dedup."""
    _positive_int(top_k, "top_k")
    by_record = _unique_rows(records, "record_id")
    _unique_rows(units, "unit_id")
    vectors = np.asarray(vectors, dtype="float32")
    query_vectors = np.asarray(query_vectors, dtype="float32")
    if vectors.ndim != 2 or len(vectors) != len(units) or not len(vectors):
        raise ValueError("Vector/unit row count mismatch")
    if query_vectors.ndim != 2 or not len(query_vectors) or query_vectors.shape[1] != vectors.shape[1]:
        raise ValueError("Query vectors have incompatible dimensions")
    if not np.isfinite(vectors).all() or not np.isfinite(query_vectors).all():
        raise ValueError("Cannot rank non-finite vectors")
    if not np.allclose(np.linalg.norm(query_vectors, axis=1), 1, rtol=1e-3, atol=1e-3):
        raise ValueError("Query vectors must be normalized")
    similarities = query_vectors @ vectors.T
    best_windows = similarities.argmax(axis=0)
    scores = similarities.max(axis=0)
    ordering = sorted(range(len(units)), key=lambda i: (-float(scores[i]), units[i]["record_id"], units[i]["unit_id"]))
    seen_records, seen_groups, seen_source_text = set(), set(), set()
    hits = []
    for index in ordering:
        unit = units[index]
        if unit["record_id"] not in by_record:
            raise ValueError("Unit references missing evidence record")
        record = by_record[unit["record_id"]]
        group = record.get("evidence_group_id") or record["record_id"]
        exact_text = (record["source_id"], " ".join(record["source_text"].split()))
        if record["record_id"] in seen_records or group in seen_groups or exact_text in seen_source_text:
            continue
        seen_records.add(record["record_id"])
        seen_groups.add(group)
        seen_source_text.add(exact_text)
        hits.append({**record, "rank": len(hits) + 1, "similarity_score": float(scores[index]), "matched_unit_id": unit["unit_id"], "matched_unit_text": unit["text"], "matched_query_window": int(best_windows[index])})
        if len(hits) == top_k:
            break
    return hits


def load_embedding_model(bundle_dir: Path, manifest: dict[str, Any]) -> Any:
    if sys.platform == "darwin":
        for variable in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
            os.environ.setdefault(variable, "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    from sentence_transformers import SentenceTransformer

    configuration = manifest["embedding"]
    model = SentenceTransformer(
        str(safe_bundle_path(bundle_dir, configuration["model_dir"])),
        device="cpu",
        local_files_only=True,
        trust_remote_code=False,
    )
    if model.get_sentence_embedding_dimension() != configuration["dimension"]:
        raise ValueError("Loaded model dimension differs from manifest")
    if model.max_seq_length != configuration["max_seq_length"]:
        raise ValueError("Loaded model sequence limit differs from manifest")
    return model


def _public_evidence(record: dict[str, Any]) -> dict[str, Any]:
    """Do not propagate machine-specific historical audit paths into API inputs."""
    fields = (
        "record_id", "content_type", "source_id", "page_number", "source_text",
        "retrieval_text", "review_status", "release_status", "dependency_ids",
        "structure_status", "source_text_scope", "cleaning_applied",
        "source_layout_status", "source_layout_note",
        "evidence_group_id", "table_or_figure_id", "section_title", "evidence_bbox",
        "evidence_bbox_space", "image_sha256", "asset_bbox", "asset_id", "rank",
        "similarity_score", "matched_unit_id", "matched_unit_text", "matched_query_window",
        "figure_id", "revision", "content_sha256", "page_numbers", "printed_pages",
        "source_blocks", "ai_description", "logic_paths", "symbols", "open_questions",
        "internal_dependencies", "use_policy", "approved_by", "approved_at",
        "human_approved", "audit_document", "source_images",
        "source_excerpt_char_range", "original_source_text_retained_in",
        "related_approved_chart", "supplement_text_policy",
    )
    result = {field: record[field] for field in fields if field in record}
    if record.get("supplement_text_policy") == "ada2026-p22-exact-excerpts-v1":
        # Full original chunks remain in the verified records file. The public
        # prose evidence is the explicitly located excerpt, not stale table
        # fragments that duplicate the separately approved whole-table parent.
        result["source_text"] = record["evidence_text"]
    return result


def _markdown(results: list[dict[str, Any]], bundle_dir: Path, output_dir: Path) -> str:
    lines = ["# ADA evidence retrieval", "", "Similarity scores are retrieval signals, not clinical correctness probabilities.", ""]
    if results[0]["bundle_status"] != "released":
        lines += ["> REVIEW CANDIDATE — NOT A FORMAL v1 RELEASE. Human review remains incomplete.", ""]
    for result in results:
        lines += [f"## Case {result['case_id']}", "", f"KB: {result['kb_version']}; query windows: {len(result['query_windows'])}", "", result["retrieval_query"], ""]
        if result.get("release_context"):
            lines += ["Current release context: " + result["release_context"]["historical_scope_note"], ""]
        for hit in result["evidence"]:
            source = hit["source"]
            pdf_path = bundle_dir.joinpath(*PurePosixPath(source["relative_path"]).parts)
            try:
                location = Path(os.path.relpath(pdf_path, output_dir)).as_posix()
            except ValueError:
                location = pdf_path.as_uri()
            lines += [f"### {hit['rank']}. {hit['record_id']}", "", f"Source: [{source['title']} ({source['year']})](<{location}#page={hit['page_number']}>) — PDF page {hit['page_number']}", "", f"Type: {hit['content_type']}; review: {hit['review_status']}; release: {hit['release_status']}; similarity: {hit['similarity_score']:.6f}", ""]
            if hit.get("table_or_figure_id"):
                lines += [f"Figure/table: {hit['table_or_figure_id']}", ""]
            if hit.get("structure_status"):
                lines += [f"Structure status: {hit['structure_status']}. {hit.get('source_text_scope', '')}", ""]
            if hit.get("source_layout_status"):
                lines += [f"> Source-layout warning: {hit['source_layout_status']}. {hit.get('source_layout_note', '')}", ""]
            if hit.get("supplement_text_policy"):
                lines += [f"Source excerpt: {hit['source_text_scope']} Original chunk: {hit['original_source_text_retained_in']}, record ID above; character range {hit['source_excerpt_char_range']}.", ""]
            if hit.get("content_type") == "visual_whole_chart":
                lines += [f"Complete reviewed chart: {hit.get('figure_id')}; revision {hit.get('revision')}; PDF pages {hit.get('page_numbers')}; printed pages {hit.get('printed_pages')}.",
                          "", f"Approval: {hit.get('approved_by')} / {hit.get('approved_at')}; content SHA-256: {hit.get('content_sha256')}.", "",
                          "Use restrictions (must accompany this evidence in downstream API workflows):", "",
                          "```json", json.dumps(hit.get("use_policy", {}), ensure_ascii=False, indent=2), "```", ""]
                for question in hit.get("open_questions", []):
                    lines += [f"> Unresolved source limitation {question['question_id']}: {question['text']}", ""]
                lines += ["#### Source transcription", "",
                          "Human-reviewed transcription of the complete chart; AI explanations follow separately. The matched retrieval window is a locator, not a standalone recommendation.", ""]
            lines += ["> " + line for line in hit["source_text"].splitlines()]
            lines.append("")
            if hit.get("content_type") == "visual_whole_chart":
                lines += ["#### AI reconstruction reviewed with the chart", "",
                          "This explanation and these relationships are AI-authored interpretations, not verbatim ADA statements or patient-specific instructions.", "",
                          hit.get("ai_description", ""), "", "Complete structured relationships, source-block locations, symbols and dependencies:", "",
                          "```json", json.dumps({field: hit.get(field, []) for field in ("source_blocks", "logic_paths", "symbols", "internal_dependencies")}, ensure_ascii=False, indent=2), "```", ""]
            if hit.get("dependency_evidence"):
                lines += ["Required context (included independently of top-k ranking):", ""]
                for dependency in hit["dependency_evidence"]:
                    if dependency.get("source_layout_status"):
                        lines += [f"Source-layout warning for {dependency['record_id']}: {dependency['source_layout_status']}. {dependency.get('source_layout_note', '')}", ""]
                    lines += [f"- {dependency['record_id']} (PDF page {dependency['page_number']}): {dependency['source_text']}"]
                lines.append("")
    return "\n".join(lines) + "\n"


def retrieve_cases(
    bundle_dir: str | Path,
    cases_path: str | Path,
    output_dir: str | Path,
    input_mode: str = "text",
    top_k: int = 8,
    allow_candidate: bool = False,
) -> dict[str, Any]:
    """Verify bundle, retrieve all cases locally, and write a NEW evidence folder."""
    _positive_int(top_k, "top_k")
    root = Path(bundle_dir).resolve(strict=True)
    destination = Path(output_dir).resolve()
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite output directory: {destination}")
    manifest = verify_bundle(root, allow_candidate=allow_candidate)
    cases = load_cases(cases_path, input_mode=input_mode)
    records = read_jsonl(root / "records.jsonl")
    by_record = _unique_rows(records, "record_id")
    units = read_jsonl(root / "units.jsonl")
    sources = _json(root / "sources.json")
    vectors = np.load(root / "embeddings.npy", allow_pickle=False)
    model = load_embedding_model(root, manifest)
    limit = manifest["embedding"]["max_seq_length"]
    for unit in units:
        if _token_count(unit["text"], model.tokenizer) > limit:
            raise ValueError(f"Indexed unit exceeds actual tokenizer limit: {unit['unit_id']}")
    results = []
    for case in cases:
        windows = token_windows(case["retrieval_query"], model.tokenizer, limit)
        query_vectors = np.asarray(model.encode([window["text"] for window in windows], normalize_embeddings=True, show_progress_bar=False), dtype="float32")
        hits = [_public_evidence(record) for record in rank_evidence(records, units, vectors, query_vectors, top_k=top_k)]
        for hit in hits:
            hit["source"] = sources[hit["source_id"]]
            if hit["content_type"] == "visual_whole_chart":
                hit["source_images"] = [image for image in hit["source"].get("images", [])
                                        if image.get("figure_id") == hit["figure_id"]]
            dependencies, seen = [], {hit["record_id"]}
            queue = list(hit["dependency_ids"])
            while queue:
                identifier = queue.pop(0)
                if identifier in seen:
                    continue
                seen.add(identifier)
                dependency = by_record[identifier]
                dependencies.append({**_public_evidence(dependency), "source": sources[dependency["source_id"]]})
                queue.extend(dependency["dependency_ids"])
            hit["dependency_evidence"] = dependencies
        response = {**case, "schema_version": SCHEMA_VERSION, "kb_version": manifest["kb_version"], "bundle_status": manifest["status"], "input_mode": input_mode, "query_windows": windows, "evidence": hits, "notice": "Research evidence retrieval only; similarities are not clinical correctness probabilities."}
        if manifest.get("chart_audit", {}).get("n_approved_charts") == 9:
            response["release_context"] = {
                "approved_chart_groups": 9,
                "historical_scope_note": "This release includes Figures 9.1-9.5 and Tables 9.1-9.4. Frozen Figure 9.5 question Q03 describes the historical seven-group review scope, not the coverage of this release. Table 9.4 is now separately approved and retrievable; this does not resolve the other cross-reference ambiguity or establish an automatic cross-chart clinical dependency.",
            }
        results.append(response)
    markdown = _markdown(results, root, destination)
    # All validation and inference precede writing. exist_ok=False also handles
    # races; partial I/O failures leave an explicitly new folder, never a prior run.
    destination.mkdir(parents=True, exist_ok=False)
    with (destination / "evidence.jsonl").open("x", encoding="utf-8", newline="\n") as handle:
        for result in results:
            handle.write(json.dumps(result, ensure_ascii=False, allow_nan=False) + "\n")
    (destination / "evidence.md").write_text(markdown, encoding="utf-8")
    stats = {"case_count": len(results), "evidence_count": sum(len(result["evidence"]) for result in results), "query_window_count": sum(len(result["query_windows"]) for result in results), "kb_version": manifest["kb_version"], "bundle_status": manifest["status"], "output_dir": str(destination)}
    (destination / "retrieval_summary.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return stats
