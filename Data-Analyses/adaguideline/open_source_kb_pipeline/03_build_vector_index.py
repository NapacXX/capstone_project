#!/usr/bin/env python3
"""Build a local FAISS index from eligible ADA guideline KB records."""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# PyTorch, Apple Accelerate, and FAISS can initialize incompatible native
# thread pools in the same macOS process.  Conservative defaults prevent the
# observed encode hang/segfault; an explicitly configured environment wins.
if sys.platform == "darwin":
    for _thread_variable in (
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    ):
        os.environ.setdefault(_thread_variable, "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import numpy as np
import pandas as pd


DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

KB_FILES = [
    ("enhanced", "enhanced_guideline_kb_records.csv"),
    ("text", "full_guideline_text_chunks.csv"),
    ("table", "full_guideline_structured_tables.csv"),
    ("figure_or_page", "full_guideline_figure_pages.csv"),
    ("text", "type2_text_chunks.csv"),
    ("table", "type2_structured_tables.csv"),
    ("figure_or_page", "type2_figure_pages.csv"),
    ("text", "ada_text_chunks.csv"),
    ("medication_table", "ada_medication_table_structured.csv"),
    ("decision_rule", "ada_decision_rule_registry.csv"),
]

TRUE_VALUES = {"true", "1", "yes", "y"}
FALSE_VALUES = {"false", "0", "no", "n", ""}
EXPLICIT_FALSE_VALUES = FALSE_VALUES - {""}
HASH_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


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
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_") or "unknown"


def join_fields(row: pd.Series, fields: list[str]) -> str:
    return " ".join(
        part for part in (normalize_scalar(row.get(field)) for field in fields) if part
    )


def canonicalize_frame(
    frame: pd.DataFrame, source_path: Path, default_type: str
) -> pd.DataFrame:
    """Accept pipeline-generated and committed KB CSV schemas."""
    frame = frame.copy()
    filename = source_path.name

    if filename == "ada_text_chunks.csv":
        frame["retrieval_text"] = frame.get("summary_text", "")
        frame["content_type"] = (
            frame["source_type"] if "source_type" in frame else default_type
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
    elif "content_type" not in frame.columns:
        frame["content_type"] = default_type

    if "image_path" not in frame.columns and "page_image_path" in frame.columns:
        frame["image_path"] = frame["page_image_path"]
    frame["source_file"] = str(source_path)

    defaults = {
        "chunk_id": "",
        "retrieval_text": "",
        "content_type": default_type,
        "requires_manual_review": "",
        "human_approved": "",
        "approval_status": "",
        "release_status": "",
        "validation_status": "",
        "review_status": "",
        "reviewer": "",
        "reviewed_at": "",
        "approved_by": "",
        "approved_at": "",
        "content_sha256": "",
        "effective_content_sha256": "",
        "source_json": "",
        "extraction_status": "",
    }
    for column, default in defaults.items():
        if column not in frame.columns:
            frame[column] = default

    frame["chunk_id"] = frame["chunk_id"].map(normalize_scalar)
    missing_ids = frame["chunk_id"].eq("")
    if missing_ids.any():
        raise ValueError(
            f"Missing chunk_id in {source_path} at data rows "
            f"{(frame.index[missing_ids] + 2).tolist()}"
        )
    frame["retrieval_text"] = frame["retrieval_text"].map(normalize_scalar)
    return frame


def visual_record_mask(frame: pd.DataFrame) -> pd.Series:
    content_type = frame["content_type"].fillna("").astype(str).str.lower()
    source_json = frame["source_json"].fillna("").astype(str).str.strip()
    extraction_status = (
        frame["extraction_status"].fillna("").astype(str).str.strip()
    )
    return (
        content_type.str.startswith("visual")
        | source_json.ne("")
        | extraction_status.ne("")
    )


def visual_release_mask(frame: pd.DataFrame) -> pd.Series:
    """Accept only the canonical, internally consistent Step 07 release state."""
    human_approved = parse_boolean_series(
        frame["human_approved"], field="human_approved"
    )
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


def assert_unique_chunk_ids(frame: pd.DataFrame, *, context: str) -> None:
    duplicate_mask = frame["chunk_id"].duplicated(keep=False)
    if duplicate_mask.any():
        details = (
            frame.loc[duplicate_mask, ["chunk_id", "source_file"]]
            .fillna("")
            .drop_duplicates()
            .to_dict("records")
        )
        raise ValueError(f"Duplicate chunk_id values in {context}: {details}")


def filter_released_records(corpus: pd.DataFrame) -> pd.DataFrame:
    """Quarantine review-required rows and visual rows lacking release approval."""
    manual_review = parse_boolean_series(
        corpus["requires_manual_review"], field="requires_manual_review"
    )
    is_visual = visual_record_mask(corpus)
    visual_released = pd.Series(False, index=corpus.index, dtype=bool)
    if is_visual.any():
        visual_released.loc[is_visual] = visual_release_mask(corpus.loc[is_visual])
    include = ~manual_review & (~is_visual | visual_released)
    filtered = corpus.loc[include].reset_index(drop=True)

    surviving_visual = visual_record_mask(filtered)
    if surviving_visual.any() and not visual_release_mask(
        filtered.loc[surviving_visual]
    ).all():
        ids = filtered.loc[surviving_visual, "chunk_id"].astype(str).tolist()
        raise ValueError(
            "Fail-closed: unapproved visual records reached vector input: "
            f"{ids}"
        )
    return filtered


def choose_kb_files(kb_dir: Path) -> list[tuple[str, str]]:
    """Prefer an enhanced snapshot so component rows are not indexed twice."""
    enhanced = ("enhanced", "enhanced_guideline_kb_records.csv")
    if (kb_dir / enhanced[1]).exists():
        return [enhanced]
    return [entry for entry in KB_FILES if entry != enhanced]


def load_kb(kb_dir: Path) -> pd.DataFrame:
    frames = []
    for default_type, filename in choose_kb_files(kb_dir):
        path = kb_dir / filename
        if not path.exists():
            continue
        try:
            frame = pd.read_csv(path)
        except pd.errors.EmptyDataError:
            continue
        if frame.empty:
            continue
        frames.append(canonicalize_frame(frame, path, default_type))
    if not frames:
        raise ValueError(f"No KB rows found in {kb_dir}")

    corpus = pd.concat(frames, ignore_index=True, sort=False)
    assert_unique_chunk_ids(corpus, context="vector source corpus")
    corpus = filter_released_records(corpus)
    corpus = corpus[corpus["retrieval_text"].ne("")].reset_index(drop=True)
    if corpus.empty:
        raise ValueError(
            f"No eligible, review-free KB rows with retrieval text found in {kb_dir}"
        )
    assert_unique_chunk_ids(corpus, context="eligible vector corpus")
    return corpus


def write_metadata(corpus: pd.DataFrame, output_dir: Path) -> Path:
    """Write one authoritative metadata format and remove its stale sibling."""
    parquet_path = output_dir / "chunk_metadata.parquet"
    csv_path = output_dir / "chunk_metadata.csv"
    try:
        corpus.to_parquet(parquet_path, index=False)
        if csv_path.exists():
            csv_path.unlink()
        return parquet_path
    except Exception:
        # Readers prefer parquet, so a stale parquet must never shadow the CSV.
        if parquet_path.exists():
            parquet_path.unlink()
        corpus.to_csv(csv_path, index=False)
        return csv_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kb-dir", default="outputs/full_guideline_kb")
    parser.add_argument("--output-dir", default="outputs/vector_index")
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument(
        "--local-files-only",
        action="store_true",
        help="Use only locally cached embedding model files; do not try downloads.",
    )
    args = parser.parse_args()

    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise ImportError(
            "Install requirements.txt before building the vector index: "
            "pip install -r requirements.txt"
        ) from exc

    kb_dir = Path(args.kb_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    corpus = load_kb(kb_dir)
    try:
        model = SentenceTransformer(
            args.embedding_model,
            device="cpu",
            local_files_only=args.local_files_only,
        )
    except TypeError as exc:
        if args.local_files_only:
            raise RuntimeError(
                "Installed sentence-transformers does not support safe "
                "--local-files-only loading."
            ) from exc
        model = SentenceTransformer(args.embedding_model, device="cpu")
    embeddings = model.encode(
        corpus["retrieval_text"].tolist(),
        batch_size=32,
        show_progress_bar=True,
        normalize_embeddings=True,
    )
    embeddings = np.asarray(embeddings, dtype="float32")

    # Initialize FAISS/OpenMP after PyTorch inference.  The reverse order can
    # segfault in some macOS runtime combinations.
    try:
        import faiss
    except ImportError as exc:
        raise ImportError(
            "Install requirements.txt before building the vector index: "
            "pip install -r requirements.txt"
        ) from exc
    index = faiss.IndexFlatIP(embeddings.shape[1])
    index.add(embeddings)
    faiss.write_index(index, str(output_dir / "faiss.index"))

    metadata_path = write_metadata(corpus, output_dir)

    (output_dir / "embedding_model.txt").write_text(args.embedding_model)

    print(f"Indexed {len(corpus)} eligible KB rows.")
    print(f"Wrote FAISS index to {output_dir / 'faiss.index'}")
    print(f"Wrote metadata to {metadata_path}")


if __name__ == "__main__":
    main()
