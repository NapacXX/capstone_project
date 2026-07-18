#!/usr/bin/env python3
"""Build full-guideline and Type 2-focused KB layers from raw extraction."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pandas as pd


INCLUDE_TERMS = [
    "type 2 diabetes",
    "t2d",
    "pharmacologic",
    "metformin",
    "sglt2",
    "glp-1",
    "gip",
    "dpp-4",
    "insulin",
    "ckd",
    "egfr",
    "kidney",
    "albuminuria",
    "heart failure",
    "ascvd",
    "cardiovascular",
    "obesity",
    "mash",
    "masld",
    "hypoglycemia",
]

EXCLUDE_TERMS = [
    "type 1 diabetes",
    "gestational",
    "pregnancy",
    "pregnant",
    "children",
    "adolescents",
    "pediatric",
]

DRUG_CLASS_TERMS = {
    "metformin": ["metformin"],
    "SGLT2 inhibitor": ["sglt2", "empagliflozin", "dapagliflozin", "canagliflozin"],
    "GLP-1 RA": ["glp-1", "semaglutide", "liraglutide", "dulaglutide"],
    "dual GIP/GLP-1 RA": ["gip", "tirzepatide"],
    "DPP-4 inhibitor": ["dpp-4", "sitagliptin", "linagliptin", "saxagliptin"],
    "insulin": ["insulin", "glargine", "degludec", "lispro", "aspart"],
    "sulfonylurea": ["sulfonylurea", "glipizide", "glyburide", "glimepiride"],
    "TZD": ["thiazolidinedione", "pioglitazone"],
}


def normalize(text: object) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def score_type2_relevance(text: str) -> int:
    lower = text.lower()
    include_score = sum(1 for term in INCLUDE_TERMS if term in lower)
    exclude_score = sum(1 for term in EXCLUDE_TERMS if term in lower)
    return include_score - (2 * exclude_score)


def detect_drug_classes(text: str) -> str:
    lower = text.lower()
    classes = [
        drug_class
        for drug_class, terms in DRUG_CLASS_TERMS.items()
        if any(term in lower for term in terms)
    ]
    return "; ".join(classes)


def detect_topic_tags(text: str) -> str:
    lower = text.lower()
    tags = []
    for tag, terms in {
        "kidney": ["ckd", "egfr", "kidney", "albuminuria", "uacr"],
        "heart_failure": ["heart failure", "hfpef", "hfref"],
        "ascvd": ["ascvd", "cardiovascular", "myocardial infarction", "stroke"],
        "obesity_weight": ["obesity", "weight", "bmi"],
        "hypoglycemia": ["hypoglycemia"],
        "contraindication_caution": ["contraindicated", "not recommended", "caution"],
        "type2_pharmacotherapy": ["type 2 diabetes", "pharmacologic"],
    }.items():
        if any(term in lower for term in terms):
            tags.append(tag)
    return "; ".join(tags)


def chunk_text(text: str, chunk_size: int = 1400, overlap: int = 180) -> list[str]:
    text = normalize(text)
    if len(text) <= chunk_size:
        return [text] if text else []
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunks.append(text[start:end])
        if end == len(text):
            break
        start = max(0, end - overlap)
    return chunks


def build_text_chunks(raw_dir: Path, scope: str = "full") -> pd.DataFrame:
    text_blocks = pd.read_csv(raw_dir / "text_blocks.csv")
    rows = []
    for _, row in text_blocks.iterrows():
        text = normalize(row.get("text"))
        if not text:
            continue
        for chunk_index, chunk in enumerate(chunk_text(text), start=1):
            score = score_type2_relevance(chunk)
            if scope == "type2" and score <= 0:
                continue
            rows.append(
                {
                    "chunk_id": f"text_{row.get('block_id')}_{chunk_index:02d}",
                    "source_pdf": "",
                    "page_number": row.get("page_number"),
                    "section_title": row.get("section_title", ""),
                    "content_type": "text",
                    "table_or_figure_id": "",
                    "type2_relevance_score": score,
                    "drug_class_tags": detect_drug_classes(chunk),
                    "topic_tags": detect_topic_tags(chunk),
                    "retrieval_text": chunk,
                }
            )
    return pd.DataFrame(rows)


def build_structured_tables(raw_dir: Path, scope: str = "full") -> pd.DataFrame:
    tables = pd.read_csv(raw_dir / "tables.csv")
    rows = []
    for _, row in tables.iterrows():
        text = normalize(row.get("raw_table_text"))
        if not text:
            continue
        score = score_type2_relevance(text)
        table_id = normalize(row.get("table_or_figure_id")) or row.get("table_id")
        is_table92 = "table 9.2" in text.lower() or "features of medications" in text.lower()
        if scope == "type2" and score <= 0 and not is_table92:
            continue
        rows.append(
            {
                "chunk_id": f"table_{row.get('table_id')}",
                "source_pdf": "",
                "page_number": row.get("page_number"),
                "section_title": "",
                "content_type": "table",
                "table_or_figure_id": table_id,
                "type2_relevance_score": max(score, 1 if is_table92 else score),
                "drug_class_tags": detect_drug_classes(text),
                "topic_tags": detect_topic_tags(text),
                "retrieval_text": text,
                "extraction_method": row.get("extraction_method", ""),
            }
        )
    return pd.DataFrame(rows)


def build_figure_pages(raw_dir: Path, scope: str = "full") -> pd.DataFrame:
    pages = pd.read_csv(raw_dir / "page_manifest.csv")
    rows = []
    for _, row in pages.iterrows():
        preview = normalize(row.get("page_text_preview"))
        figure_ids = normalize(row.get("figure_ids"))
        score = score_type2_relevance(preview)
        is_figure94 = "9.4" in figure_ids or "figure 9.4" in preview.lower()
        is_pharm_flow = (
            "pharmacologic" in preview.lower()
            and "type 2 diabetes" in preview.lower()
            and ("approach" in preview.lower() or "treatment" in preview.lower())
        )
        if scope == "type2" and not (is_figure94 or is_pharm_flow or score >= 2):
            continue
        rows.append(
            {
                "chunk_id": f"page_{int(row.get('page_number')):03d}",
                "source_pdf": "",
                "page_number": row.get("page_number"),
                "section_title": "",
                "content_type": "figure_or_page",
                "table_or_figure_id": figure_ids,
                "type2_relevance_score": max(score, 3 if is_figure94 else score),
                "drug_class_tags": detect_drug_classes(preview),
                "topic_tags": detect_topic_tags(preview),
                "retrieval_text": preview,
                "page_image_path": "",
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", default="outputs/raw_extracted")
    parser.add_argument("--output-dir", default="outputs/full_guideline_kb")
    parser.add_argument(
        "--scope",
        choices=["full", "type2"],
        default="full",
        help="Use full to keep all guideline content; type2 filters to T2D.",
    )
    args = parser.parse_args()

    raw_dir = Path(args.raw_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    required = ["text_blocks.csv", "tables.csv", "page_manifest.csv"]
    missing = [name for name in required if not (raw_dir / name).exists()]
    if missing:
        raise FileNotFoundError(f"Missing raw extraction files: {missing}")

    text_chunks = build_text_chunks(raw_dir, scope=args.scope)
    structured_tables = build_structured_tables(raw_dir, scope=args.scope)
    figure_pages = build_figure_pages(raw_dir, scope=args.scope)

    prefix = "full_guideline" if args.scope == "full" else "type2"
    text_chunks.to_csv(output_dir / f"{prefix}_text_chunks.csv", index=False)
    structured_tables.to_csv(output_dir / f"{prefix}_structured_tables.csv", index=False)
    figure_pages.to_csv(output_dir / f"{prefix}_figure_pages.csv", index=False)

    manifest = {
        "description": (
            "Full ADA pharmacotherapy guideline KB with Type 2 relevance tags."
            if args.scope == "full"
            else "Type 2 Diabetes-focused ADA pharmacotherapy KB."
        ),
        "scope": args.scope,
        "raw_dir": str(raw_dir),
        "n_text_chunks": int(len(text_chunks)),
        "n_structured_table_rows": int(len(structured_tables)),
        "n_figure_or_page_records": int(len(figure_pages)),
        "include_terms": INCLUDE_TERMS,
        "exclude_or_deprioritize_terms": EXCLUDE_TERMS,
    }
    (output_dir / f"{prefix}_kb_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )

    print(f"Wrote {args.scope} text chunks: {len(text_chunks)}")
    print(f"Wrote structured table records: {len(structured_tables)}")
    print(f"Wrote figure/page records: {len(figure_pages)}")
    print(f"Wrote {args.scope} KB outputs to {output_dir}")


if __name__ == "__main__":
    main()
