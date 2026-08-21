#!/usr/bin/env python3
"""Demo retrieval of ADA guideline context for vignette patient features."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Match the index builder's safe native-thread defaults.  Without these,
# PyTorch/Accelerate and FAISS can hang or segfault in the same macOS process.
# Users who deliberately tune these variables keep their explicit values.
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


DEFAULT_RAW_DATA = "../../../data/Raw/final_results_capstone_data_ver2.csv"


def read_metadata(vector_dir: Path) -> pd.DataFrame:
    parquet_path = vector_dir / "chunk_metadata.parquet"
    csv_path = vector_dir / "chunk_metadata.csv"
    if parquet_path.exists():
        metadata = pd.read_parquet(parquet_path)
        if metadata.empty:
            raise ValueError(f"Vector metadata is empty: {parquet_path}")
        return metadata
    if csv_path.exists():
        try:
            metadata = pd.read_csv(csv_path)
        except pd.errors.EmptyDataError as exc:
            raise ValueError(f"Vector metadata is empty: {csv_path}") from exc
        if metadata.empty:
            raise ValueError(f"Vector metadata is empty: {csv_path}")
        return metadata
    raise FileNotFoundError("Missing chunk metadata in vector index directory.")


def prepare_demo_cases(raw_data: pd.DataFrame, n_rows: int) -> pd.DataFrame:
    """Select distinct cases before applying the requested case limit."""
    if n_rows <= 0:
        raise ValueError("--n-rows must be a positive integer")
    if "case_id" not in raw_data.columns:
        raise ValueError("Raw data is missing required column: case_id")

    case_ids = raw_data["case_id"].astype("string").fillna("").str.strip()
    missing = case_ids.eq("")
    if missing.any():
        raise ValueError(
            "Raw data contains missing case_id values at data rows "
            f"{(raw_data.index[missing] + 2).tolist()}"
        )
    normalized = raw_data.copy()
    normalized["case_id"] = case_ids
    cases = normalized.drop_duplicates("case_id", keep="first").head(n_rows).copy()
    if cases.empty:
        raise ValueError("Raw data contains no demo cases")
    if cases["case_id"].duplicated().any():
        raise ValueError("Demo case_id values must be unique")
    cases["retrieval_query"] = cases.apply(build_case_query, axis=1)
    return cases


def effective_top_k(top_k: int, index_size: int) -> int:
    if top_k <= 0:
        raise ValueError("--top-k must be a positive integer")
    if index_size <= 0:
        raise ValueError("FAISS index is empty")
    return min(top_k, index_size)


def validate_vector_contract(
    index: object, metadata: pd.DataFrame, query_embeddings: np.ndarray
) -> None:
    index_size = int(getattr(index, "ntotal", -1))
    index_dimension = int(getattr(index, "d", -1))
    if index_size != len(metadata):
        raise ValueError(
            "FAISS/metadata row-count mismatch: "
            f"index has {index_size}, metadata has {len(metadata)}"
        )
    if query_embeddings.ndim != 2:
        raise ValueError(
            f"Query embeddings must be a 2-D matrix, got {query_embeddings.shape}"
        )
    if index_dimension != int(query_embeddings.shape[1]):
        raise ValueError(
            "FAISS/query embedding dimension mismatch: "
            f"index expects {index_dimension}, queries have {query_embeddings.shape[1]}"
        )


def clean_value(row: pd.Series, column: str) -> str:
    value = row.get(column, "")
    if pd.isna(value):
        return ""
    return str(value)


def build_case_query(row: pd.Series) -> str:
    features = [
        "Type 2 diabetes pharmacotherapy guideline context.",
        f"HbA1c {clean_value(row, 'hba1c_percent')}",
        f"eGFR {clean_value(row, 'egfr_ml_min_1_73m2')}",
        f"UACR {clean_value(row, 'uacr_mg_g')}",
        f"BMI {clean_value(row, 'bmi_kg_m2')}",
        f"CKD {clean_value(row, 'chronic_kidney_disease')}",
        f"CKD stage {clean_value(row, 'stage_ckd')}",
        f"heart failure {clean_value(row, 'heart_failure_present')}",
        f"HF type {clean_value(row, 'hf_type')}",
        f"CAD {clean_value(row, 'coronary_artery_disease')}",
        f"MI history {clean_value(row, 'mi_history')}",
        f"stroke/TIA {clean_value(row, 'stroke_tia_history')}",
        f"MASLD {clean_value(row, 'masld_present')}",
        f"MASH {clean_value(row, 'mash_present')}",
        f"baseline diabetes drugs {clean_value(row, 'current_dm_drugs')}",
        f"financial limitations {clean_value(row, 'financial_limitations')}",
        f"simple regimen preference {clean_value(row, 'preference_simple_regimen')}",
        f"reluctant injections {clean_value(row, 'reluctant_injections')}",
    ]
    return " ".join(part for part in features if part.strip())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-data", default=DEFAULT_RAW_DATA)
    parser.add_argument("--vector-dir", default="outputs/vector_index")
    parser.add_argument("--output-dir", default="outputs/demo_retrieval")
    parser.add_argument("--n-rows", type=int, default=6)
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument(
        "--local-files-only",
        action="store_true",
        help="Use only locally cached embedding model files; do not try downloads.",
    )
    args = parser.parse_args()
    if args.n_rows <= 0:
        raise ValueError("--n-rows must be a positive integer")
    if args.top_k <= 0:
        raise ValueError("--top-k must be a positive integer")

    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise ImportError(
            "Install requirements.txt before demo retrieval: "
            "pip install -r requirements.txt"
        ) from exc

    raw_path = Path(args.raw_data)
    vector_dir = Path(args.vector_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not raw_path.exists():
        raise FileNotFoundError(f"Raw data not found: {raw_path}")

    try:
        raw_data = pd.read_csv(raw_path)
    except pd.errors.EmptyDataError as exc:
        raise ValueError(f"Raw data is empty: {raw_path}") from exc
    demo_cases = prepare_demo_cases(raw_data, args.n_rows)
    metadata = read_metadata(vector_dir)
    embedding_model_path = vector_dir / "embedding_model.txt"
    if not embedding_model_path.is_file():
        raise FileNotFoundError(f"Missing embedding model manifest: {embedding_model_path}")
    embedding_model = embedding_model_path.read_text(encoding="utf-8").strip()
    if not embedding_model:
        raise ValueError(f"Embedding model manifest is empty: {embedding_model_path}")
    try:
        model = SentenceTransformer(
            embedding_model,
            device="cpu",
            local_files_only=args.local_files_only,
        )
    except TypeError as exc:
        if args.local_files_only:
            raise RuntimeError(
                "Installed sentence-transformers does not support safe "
                "--local-files-only loading."
            ) from exc
        model = SentenceTransformer(embedding_model, device="cpu")

    query_embeddings = model.encode(
        demo_cases["retrieval_query"].tolist(),
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    query_embeddings = np.asarray(query_embeddings, dtype="float32")

    # Import/read FAISS only after model inference.  Initializing FAISS/OpenMP
    # before PyTorch inference segfaults in some macOS runtimes.
    try:
        import faiss
    except ImportError as exc:
        raise ImportError(
            "Install requirements.txt before demo retrieval: "
            "pip install -r requirements.txt"
        ) from exc
    index_path = vector_dir / "faiss.index"
    if not index_path.is_file():
        raise FileNotFoundError(f"Missing FAISS index: {index_path}")
    index = faiss.read_index(str(index_path))
    validate_vector_contract(index, metadata, query_embeddings)
    retrieval_k = effective_top_k(args.top_k, int(index.ntotal))
    scores, indices = index.search(query_embeddings, retrieval_k)

    retrieved_rows = []
    for case_idx, (_, case_row) in enumerate(demo_cases.iterrows()):
        for rank, metadata_idx in enumerate(indices[case_idx], start=1):
            if metadata_idx < 0:
                continue
            kb_row = metadata.iloc[int(metadata_idx)].to_dict()
            retrieved_rows.append(
                {
                    "case_id": case_row["case_id"],
                    "rank": rank,
                    "similarity_score": float(scores[case_idx][rank - 1]),
                    "retrieval_query": case_row["retrieval_query"],
                    **kb_row,
                }
            )

    queries = demo_cases[["case_id", "retrieval_query"]]
    retrieved = pd.DataFrame(retrieved_rows)

    queries.to_csv(output_dir / "demo_case_queries.csv", index=False)
    retrieved.to_csv(
        output_dir / "demo_retrieved_guideline_context.csv", index=False
    )

    summary_lines = [
        "# ADA Guideline Retrieval Demo Summary",
        "",
        f"Demo cases: {len(demo_cases)}",
        f"Top-k requested per case: {args.top_k}",
        f"Top-k retrieved per case: {retrieval_k}",
        "",
    ]
    for case_id, group in retrieved.groupby("case_id"):
        summary_lines.append(f"## {case_id}")
        for _, row in group.head(5).iterrows():
            summary_lines.append(
                "- "
                f"rank {row['rank']}, score {row['similarity_score']:.3f}, "
                f"type {row.get('content_type', '')}, "
                f"page {row.get('page_number', '')}, "
                f"tags {row.get('topic_tags', '')}"
            )
        summary_lines.append("")
    (output_dir / "demo_retrieval_summary.md").write_text(
        "\n".join(summary_lines), encoding="utf-8"
    )

    print(f"Wrote demo queries and retrieved context to {output_dir}")


if __name__ == "__main__":
    main()
