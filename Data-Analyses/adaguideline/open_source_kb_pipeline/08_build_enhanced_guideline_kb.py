#!/usr/bin/env python3
"""Combine text/table/page KB records with visual guideline logic records."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def read_if_exists(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def load_existing_kb(kb_dir: Path) -> pd.DataFrame:
    candidates = [
        kb_dir / "full_guideline_text_chunks.csv",
        kb_dir / "full_guideline_structured_tables.csv",
        kb_dir / "full_guideline_figure_pages.csv",
        kb_dir / "type2_text_chunks.csv",
        kb_dir / "type2_structured_tables.csv",
        kb_dir / "type2_figure_pages.csv",
    ]
    frames = [read_if_exists(path) for path in candidates]
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        raise FileNotFoundError(f"No guideline KB CSV files found in {kb_dir}")
    return pd.concat(frames, ignore_index=True)


def load_visual_kb(visual_dir: Path) -> pd.DataFrame:
    visual_records = read_if_exists(visual_dir / "visual_retrieval_records.csv")
    if visual_records.empty:
        return pd.DataFrame()
    if "image_path" not in visual_records.columns:
        visual_records["image_path"] = ""
    if "source_json" not in visual_records.columns:
        visual_records["source_json"] = ""
    if "extraction_status" not in visual_records.columns:
        visual_records["extraction_status"] = ""
    if "requires_manual_review" not in visual_records.columns:
        visual_records["requires_manual_review"] = True
    return visual_records


def harmonize_columns(frame: pd.DataFrame) -> pd.DataFrame:
    required = [
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
    ]
    for column in required:
        if column not in frame.columns:
            frame[column] = ""
    frame = frame[required].copy()
    frame["retrieval_text"] = frame["retrieval_text"].fillna("").astype(str)
    frame = frame[frame["retrieval_text"].str.len() > 0].reset_index(drop=True)
    return frame


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kb-dir", default="outputs/full_guideline_kb")
    parser.add_argument("--visual-dir", default="outputs/visual_logic_structured")
    parser.add_argument("--output-dir", default="outputs/enhanced_guideline_kb")
    args = parser.parse_args()

    kb_dir = Path(args.kb_dir)
    visual_dir = Path(args.visual_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    existing = harmonize_columns(load_existing_kb(kb_dir))
    visual = harmonize_columns(load_visual_kb(visual_dir))
    enhanced = pd.concat([existing, visual], ignore_index=True)
    enhanced = enhanced.drop_duplicates(subset=["chunk_id"], keep="last")

    output_path = output_dir / "enhanced_guideline_kb_records.csv"
    enhanced.to_csv(output_path, index=False)

    manifest = {
        "description": (
            "Enhanced ADA guideline KB combining text, table/page records, "
            "and extracted visual guideline logic."
        ),
        "source_kb_dir": str(kb_dir),
        "source_visual_dir": str(visual_dir),
        "n_existing_records": int(len(existing)),
        "n_visual_records": int(len(visual)),
        "n_enhanced_records": int(len(enhanced)),
        "manual_review_records": int(
            enhanced["requires_manual_review"].astype(str).str.lower().eq("true").sum()
        ),
    }
    (output_dir / "enhanced_guideline_kb_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )

    print(f"Wrote enhanced KB records: {len(enhanced)}")
    print(f"Wrote enhanced KB to {output_path}")


if __name__ == "__main__":
    main()
