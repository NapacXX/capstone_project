#!/usr/bin/env python3
"""Combine canonical text/table/rule KB rows with approved visual records.

Visual extraction is an untrusted staging input.  A visual row is released into
the enhanced KB only after an explicit human approval and release decision, and
only when it no longer requires manual review.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

import pandas as pd


PIPELINE_KB_FILES = [
    "full_guideline_text_chunks.csv",
    "full_guideline_structured_tables.csv",
    "full_guideline_figure_pages.csv",
    "type2_text_chunks.csv",
    "type2_structured_tables.csv",
    "type2_figure_pages.csv",
]

COMMITTED_KB_FILES = [
    "ada_text_chunks.csv",
    "ada_medication_table_structured.csv",
    "ada_decision_rule_registry.csv",
]

CORE_COLUMNS = [
    "chunk_id",
    "source_pdf",
    "page_number",
    "section_title",
    "content_type",
    "table_or_figure_id",
    "type2_relevance_score",
    "drug_class_tags",
    "topic_tags",
    "retrieval_text",
    "image_path",
    "source_json",
    "extraction_status",
    "requires_manual_review",
    "human_approved",
    "approval_status",
    "release_status",
    "validation_status",
    "review_status",
    "reviewer",
    "reviewed_at",
    "approved_by",
    "approved_at",
    "content_sha256",
    "effective_content_sha256",
    "source_file",
]

TRUE_VALUES = {"true", "1", "yes", "y"}
FALSE_VALUES = {"false", "0", "no", "n", ""}
EXPLICIT_FALSE_VALUES = FALSE_VALUES - {""}
HASH_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


def read_if_exists(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def normalize_scalar(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def parse_boolean_series(series: pd.Series, *, field: str) -> pd.Series:
    normalized = series.astype("string").fillna("").str.strip().str.lower()
    invalid = ~normalized.isin(TRUE_VALUES | FALSE_VALUES)
    if invalid.any():
        values = sorted(normalized[invalid].unique().tolist())
        raise ValueError(f"Invalid boolean values in {field}: {values}")
    return normalized.isin(TRUE_VALUES)


def slug(value: object) -> str:
    text = normalize_scalar(value).lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text or "unknown"


def join_fields(row: pd.Series, fields: list[str]) -> str:
    return " ".join(
        part for part in (normalize_scalar(row.get(field)) for field in fields) if part
    )


def canonicalize_frame(frame: pd.DataFrame, source_path: Path) -> pd.DataFrame:
    """Map generated and committed KB schemas to one retrieval schema.

    Source-specific columns are deliberately retained for audit and provenance.
    """
    if frame.empty:
        return frame.copy()

    frame = frame.copy()
    filename = source_path.name

    if filename == "ada_text_chunks.csv":
        frame["retrieval_text"] = frame.get("summary_text", "")
        frame["content_type"] = (
            frame["source_type"] if "source_type" in frame else "text"
        )
        frame["page_number"] = frame.get("source_page", "")
        frame["drug_class_tags"] = frame.get("drug_class", "")
        frame["topic_tags"] = frame.get("condition", "")
        frame["section_title"] = frame.get("condition", "")
    elif filename == "ada_medication_table_structured.csv":
        frame["chunk_id"] = frame.apply(
            lambda row: f"ADA2026_MEDICATION_{slug(row.get('drug_class'))}", axis=1
        )
        frame["retrieval_text"] = frame.apply(
            lambda row: join_fields(
                row,
                [
                    "drug_class",
                    "glycemic_efficacy",
                    "hypoglycemia_risk",
                    "weight_effect",
                    "mace_effect",
                    "heart_failure_effect",
                    "ckd_progression_effect",
                    "kidney_dosing_consideration",
                    "mash_effect",
                    "major_cautions",
                ],
            ),
            axis=1,
        )
        frame["content_type"] = "medication_table"
        frame["page_number"] = frame.get("source_page", "")
        frame["table_or_figure_id"] = frame.get("source_table", "")
        frame["drug_class_tags"] = frame.get("drug_class", "")
        frame["topic_tags"] = "medication properties; dosing; cautions"
        frame["section_title"] = frame.get("source_table", "")
    elif filename == "ada_decision_rule_registry.csv":
        if "rule_id" not in frame.columns:
            raise ValueError(f"Missing rule_id column in {source_path}")
        frame["chunk_id"] = frame["rule_id"]
        frame["retrieval_text"] = frame.apply(
            lambda row: join_fields(
                row,
                [
                    "trigger_logic",
                    "condition",
                    "drug_class",
                    "expected_action",
                    "expected_dose_label",
                    "rule_strength",
                    "exception_variables",
                    "reason_template",
                ],
            ),
            axis=1,
        )
        frame["content_type"] = "decision_rule"
        frame["page_number"] = frame.get("source_page", "")
        frame["table_or_figure_id"] = frame.get(
            "source_figure_or_recommendation", ""
        )
        frame["drug_class_tags"] = frame.get("drug_class", "")
        frame["topic_tags"] = frame.get("condition", "")
        frame["section_title"] = frame.get(
            "source_figure_or_recommendation", ""
        )

    if "image_path" not in frame.columns and "page_image_path" in frame.columns:
        frame["image_path"] = frame["page_image_path"]
    frame["source_file"] = str(source_path)

    for column in CORE_COLUMNS:
        if column not in frame.columns:
            frame[column] = ""

    frame["chunk_id"] = frame["chunk_id"].map(normalize_scalar)
    missing_ids = frame["chunk_id"].eq("")
    if missing_ids.any():
        raise ValueError(
            f"Missing chunk_id in {source_path} at data rows "
            f"{(frame.index[missing_ids] + 2).tolist()}"
        )

    frame["retrieval_text"] = frame["retrieval_text"].map(normalize_scalar)
    frame = frame[frame["retrieval_text"].ne("")].reset_index(drop=True)
    return frame


def assert_unique_chunk_ids(frame: pd.DataFrame, *, context: str) -> None:
    if frame.empty:
        return
    duplicate_mask = frame["chunk_id"].duplicated(keep=False)
    if duplicate_mask.any():
        details = (
            frame.loc[duplicate_mask, ["chunk_id", "source_file"]]
            .fillna("")
            .drop_duplicates()
            .to_dict("records")
        )
        raise ValueError(f"Duplicate chunk_id values in {context}: {details}")


def load_existing_kb(kb_dir: Path) -> pd.DataFrame:
    paths = [kb_dir / name for name in PIPELINE_KB_FILES + COMMITTED_KB_FILES]
    frames = []
    for path in paths:
        raw = read_if_exists(path)
        if not raw.empty:
            frames.append(canonicalize_frame(raw, path))
    if not frames:
        raise FileNotFoundError(f"No guideline KB CSV files found in {kb_dir}")
    combined = pd.concat(frames, ignore_index=True, sort=False)
    assert_unique_chunk_ids(combined, context="source knowledge base")
    return combined


def visual_release_mask(frame: pd.DataFrame) -> pd.Series:
    """Accept only the canonical, internally consistent Step 07 release state."""
    required = {
        "human_approved",
        "requires_manual_review",
        "validation_status",
        "review_status",
        "approval_status",
        "release_status",
        "reviewer",
        "approved_by",
        "reviewed_at",
        "approved_at",
        "content_sha256",
        "effective_content_sha256",
    }
    if not required.issubset(frame.columns):
        return pd.Series(False, index=frame.index, dtype=bool)
    human_approved = parse_boolean_series(
        frame["human_approved"], field="human_approved"
    )

    if "requires_manual_review" not in frame.columns:
        review_cleared = pd.Series(False, index=frame.index, dtype=bool)
    else:
        # Missing is not equivalent to a reviewer explicitly clearing the row.
        normalized_review = (
            frame["requires_manual_review"]
            .astype("string")
            .fillna("")
            .str.strip()
            .str.lower()
        )
        invalid = ~normalized_review.isin(TRUE_VALUES | FALSE_VALUES)
        if invalid.any():
            values = sorted(normalized_review[invalid].unique().tolist())
            raise ValueError(
                "Invalid boolean values in requires_manual_review: " f"{values}"
            )
        review_cleared = normalized_review.isin(EXPLICIT_FALSE_VALUES)

    def normalized(column: str) -> pd.Series:
        return frame[column].map(normalize_scalar).str.lower()

    reviewer = frame["reviewer"].map(normalize_scalar)
    approved_by = frame["approved_by"].map(normalize_scalar)

    def matching_aware_timestamps(row: pd.Series) -> bool:
        values = []
        for column in ("reviewed_at", "approved_at"):
            text = normalize_scalar(row.get(column))
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return False
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                return False
            values.append(parsed)
        return values[0] == values[1]

    timestamps_match = frame.apply(matching_aware_timestamps, axis=1)
    hashes_valid = frame["content_sha256"].map(
        lambda value: bool(HASH_PATTERN.fullmatch(normalize_scalar(value)))
    ) & frame["effective_content_sha256"].map(
        lambda value: bool(HASH_PATTERN.fullmatch(normalize_scalar(value)))
    )
    hashes_match = frame["content_sha256"].map(normalize_scalar).str.lower().eq(
        frame["effective_content_sha256"].map(normalize_scalar).str.lower()
    )
    return (
        human_approved
        & review_cleared
        & normalized("validation_status").eq("valid")
        & normalized("review_status").eq("approved")
        & normalized("approval_status").eq("approved")
        & normalized("release_status").eq("released")
        & reviewer.ne("")
        & reviewer.eq(approved_by)
        & timestamps_match
        & hashes_valid
        & hashes_match
    )


def load_visual_kb(visual_dir: Path) -> tuple[pd.DataFrame, int]:
    candidate_path = visual_dir / "visual_candidate_retrieval_records.csv"
    compatibility_path = visual_dir / "visual_retrieval_records.csv"
    path = candidate_path if candidate_path.exists() else compatibility_path
    visual_records = read_if_exists(path)
    if visual_records.empty:
        return pd.DataFrame(), 0

    eligible = visual_release_mask(visual_records)
    excluded_count = int((~eligible).sum())
    approved = canonicalize_frame(visual_records.loc[eligible].copy(), path)
    return approved, excluded_count


def harmonize_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Backwards-compatible name: add canonical fields without dropping audit data."""
    if frame.empty:
        return frame.copy()
    result = frame.copy()
    for column in CORE_COLUMNS:
        if column not in result.columns:
            result[column] = ""
    result["retrieval_text"] = result["retrieval_text"].map(normalize_scalar)
    return result[result["retrieval_text"].ne("")].reset_index(drop=True)


def visual_record_mask(frame: pd.DataFrame) -> pd.Series:
    empty = pd.Series("", index=frame.index, dtype="string")
    content_type = (
        frame.get("content_type", empty).fillna("").astype(str).str.lower()
    )
    source_json = frame.get("source_json", empty).fillna("").astype(str).str.strip()
    extraction_status = (
        frame.get("extraction_status", empty).fillna("").astype(str).str.strip()
    )
    return (
        content_type.str.startswith("visual")
        | source_json.ne("")
        | extraction_status.ne("")
    )


def assert_visual_rows_are_released(frame: pd.DataFrame) -> None:
    if frame.empty:
        return
    visual = frame[visual_record_mask(frame)]
    if visual.empty:
        return
    eligible = visual_release_mask(visual)
    if not eligible.all():
        ids = visual.loc[~eligible, "chunk_id"].astype(str).tolist()
        raise ValueError(
            "Fail-closed: unapproved or manual-review visual rows would be "
            f"included in the enhanced KB: {ids}"
        )


def build_enhanced_kb(
    kb_dir: Path, visual_dir: Path
) -> tuple[pd.DataFrame, dict[str, int]]:
    existing = harmonize_columns(load_existing_kb(kb_dir))
    visual, excluded_visual = load_visual_kb(visual_dir)
    visual = harmonize_columns(visual)
    enhanced = pd.concat([existing, visual], ignore_index=True, sort=False)
    assert_unique_chunk_ids(enhanced, context="enhanced knowledge base")
    assert_visual_rows_are_released(enhanced)
    return enhanced, {
        "n_existing_records": int(len(existing)),
        "n_visual_records": int(len(visual)),
        "n_visual_records_excluded_unapproved": excluded_visual,
        "n_enhanced_records": int(len(enhanced)),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kb-dir", default="outputs/full_guideline_kb")
    parser.add_argument("--visual-dir", default="outputs/visual_logic_structured")
    parser.add_argument("--output-dir", default="outputs/enhanced_guideline_kb")
    parser.add_argument(
        "--require-visual-records",
        action="store_true",
        help="Fail unless at least one canonical released visual record is included.",
    )
    args = parser.parse_args()

    kb_dir = Path(args.kb_dir)
    visual_dir = Path(args.visual_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    enhanced, counts = build_enhanced_kb(kb_dir, visual_dir)
    if args.require_visual_records and counts["n_visual_records"] == 0:
        raise ValueError(
            "No canonical released visual records were found; complete Step 07 "
            "review and release before requiring visual records."
        )
    output_path = output_dir / "enhanced_guideline_kb_records.csv"
    enhanced.to_csv(output_path, index=False)

    manual_review = parse_boolean_series(
        enhanced["requires_manual_review"], field="requires_manual_review"
    )
    manifest = {
        "description": (
            "Enhanced ADA guideline KB combining canonical text, table, rule, "
            "and explicitly released visual records."
        ),
        "source_kb_dir": str(kb_dir),
        "source_visual_dir": str(visual_dir),
        **counts,
        "manual_review_records": int(manual_review.sum()),
        "visual_release_policy": (
            "canonical Step 07 approval/release statuses, matching reviewer and "
            "timezone-aware timestamps, valid fingerprints, and "
            "requires_manual_review=false"
        ),
    }
    (output_dir / "enhanced_guideline_kb_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )

    print(f"Wrote enhanced KB records: {len(enhanced)}")
    print(
        "Excluded unapproved/manual-review visual records: "
        f"{counts['n_visual_records_excluded_unapproved']}"
    )
    print(f"Wrote enhanced KB to {output_path}")


if __name__ == "__main__":
    main()
