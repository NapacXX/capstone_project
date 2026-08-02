#!/usr/bin/env python3
"""Build a local FAISS index from ADA guideline KB retrieval text."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def load_kb(kb_dir: Path) -> pd.DataFrame:
    files = [
        ("enhanced", "enhanced_guideline_kb_records.csv"),
        ("text", "full_guideline_text_chunks.csv"),
        ("table", "full_guideline_structured_tables.csv"),
        ("figure_or_page", "full_guideline_figure_pages.csv"),
        ("text", "type2_text_chunks.csv"),
        ("table", "type2_structured_tables.csv"),
        ("figure_or_page", "type2_figure_pages.csv"),
    ]
    frames = []
    for default_type, filename in files:
        path = kb_dir / filename
        if not path.exists():
            continue
        frame = pd.read_csv(path)
        if frame.empty:
            continue
        if "content_type" not in frame.columns:
            frame["content_type"] = default_type
        frames.append(frame)
    if not frames:
        raise ValueError(f"No KB rows found in {kb_dir}")
    corpus = pd.concat(frames, ignore_index=True)
    corpus["retrieval_text"] = corpus["retrieval_text"].fillna("").astype(str)
    corpus = corpus[corpus["retrieval_text"].str.len() > 0].reset_index(drop=True)
    return corpus


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
        import faiss
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
            local_files_only=args.local_files_only,
        )
    except TypeError:
        model = SentenceTransformer(args.embedding_model)
    embeddings = model.encode(
        corpus["retrieval_text"].tolist(),
        batch_size=32,
        show_progress_bar=True,
        normalize_embeddings=True,
    )
    embeddings = np.asarray(embeddings, dtype="float32")

    index = faiss.IndexFlatIP(embeddings.shape[1])
    index.add(embeddings)
    faiss.write_index(index, str(output_dir / "faiss.index"))

    metadata_path = output_dir / "chunk_metadata.parquet"
    try:
        corpus.to_parquet(metadata_path, index=False)
    except Exception:
        metadata_path = output_dir / "chunk_metadata.csv"
        corpus.to_csv(metadata_path, index=False)

    (output_dir / "embedding_model.txt").write_text(args.embedding_model)

    print(f"Indexed {len(corpus)} KB rows.")
    print(f"Wrote FAISS index to {output_dir / 'faiss.index'}")
    print(f"Wrote metadata to {metadata_path}")


if __name__ == "__main__":
    main()
