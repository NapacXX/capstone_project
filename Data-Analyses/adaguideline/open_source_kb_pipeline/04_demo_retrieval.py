#!/usr/bin/env python3
"""Demo retrieval of ADA guideline context for vignette patient features."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


DEFAULT_RAW_DATA = "../../../data/raw/final_results_capstone_data_ver2.csv"


def read_metadata(vector_dir: Path) -> pd.DataFrame:
    parquet_path = vector_dir / "chunk_metadata.parquet"
    csv_path = vector_dir / "chunk_metadata.csv"
    if parquet_path.exists():
        return pd.read_parquet(parquet_path)
    if csv_path.exists():
        return pd.read_csv(csv_path)
    raise FileNotFoundError("Missing chunk metadata in vector index directory.")


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
    args = parser.parse_args()

    try:
        import faiss
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

    raw_data = pd.read_csv(raw_path)
    demo_responses = raw_data.head(args.n_rows).copy()
    demo_cases = demo_responses.drop_duplicates("case_id").copy()
    demo_cases["retrieval_query"] = demo_cases.apply(build_case_query, axis=1)

    index = faiss.read_index(str(vector_dir / "faiss.index"))
    metadata = read_metadata(vector_dir)
    embedding_model = (vector_dir / "embedding_model.txt").read_text().strip()
    model = SentenceTransformer(embedding_model)

    query_embeddings = model.encode(
        demo_cases["retrieval_query"].tolist(),
        normalize_embeddings=True,
    )
    scores, indices = index.search(query_embeddings, args.top_k)

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
        f"Top-k retrieved per case: {args.top_k}",
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
