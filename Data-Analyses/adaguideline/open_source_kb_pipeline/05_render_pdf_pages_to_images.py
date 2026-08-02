#!/usr/bin/env python3
"""Render ADA guideline pages to images for visual guideline extraction.

This step prepares figure/table pages for multimodal extraction. It uses
PyMuPDF locally and writes only generated artifacts under outputs/.
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path
from typing import Any

import pandas as pd


DEFAULT_PDF = (
    "/Users/jiayiwei/Documents/Capstone/materials/"
    "ada 2026 pharmacotherapy guidelines.pdf"
)

TARGET_PATTERNS = [
    "figure 9.1",
    "fig. 9.1",
    "fig 9.1",
    "figure 9.2",
    "fig. 9.2",
    "fig 9.2",
    "figure 9.3",
    "fig. 9.3",
    "fig 9.3",
    "figure 9.4",
    "fig. 9.4",
    "fig 9.4",
    "figure 9.5",
    "fig. 9.5",
    "fig 9.5",
    "table 9.2",
    "table 9.3",
]


def normalize(text: object) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def select_pages(
    page_manifest: Path,
    target_patterns: list[str],
    render_all: bool,
) -> pd.DataFrame:
    pages = pd.read_csv(page_manifest)
    pages["figure_ids"] = pages.get("figure_ids", "").fillna("").astype(str)
    pages["table_ids"] = pages.get("table_ids", "").fillna("").astype(str)
    pages["page_text_preview"] = pages.get("page_text_preview", "").fillna("").astype(str)
    pages["selection_text"] = (
        pages["figure_ids"] + " " + pages["table_ids"] + " " + pages["page_text_preview"]
    ).str.lower()

    if render_all:
        selected = pages.copy()
        selected["selection_reason"] = "render_all"
        return selected

    pattern = "|".join(re.escape(term.lower()) for term in target_patterns)
    selected = pages[pages["selection_text"].str.contains(pattern, regex=True)].copy()
    selected["selection_reason"] = "target_figure_or_table"
    return selected


def render_pages(
    pdf_path: Path,
    selected_pages: pd.DataFrame,
    image_dir: Path,
    dpi: int,
) -> list[dict[str, Any]]:
    try:
        import fitz
    except ImportError as exc:
        raise ImportError(
            "PyMuPDF is required for page image rendering. "
            "Install it with: pip install pymupdf"
        ) from exc

    image_dir.mkdir(parents=True, exist_ok=True)
    document = fitz.open(pdf_path)
    records: list[dict[str, Any]] = []
    zoom = dpi / 72
    matrix = fitz.Matrix(zoom, zoom)

    for _, row in selected_pages.iterrows():
        page_number = int(row["page_number"])
        page = document[page_number - 1]
        pixmap = page.get_pixmap(matrix=matrix, alpha=False)
        image_path = image_dir / f"ada_page_{page_number:03d}.png"
        pixmap.save(str(image_path))

        records.append(
            {
                "page_number": page_number,
                "image_path": str(image_path),
                "dpi": dpi,
                "figure_ids": normalize(row.get("figure_ids")),
                "table_ids": normalize(row.get("table_ids")),
                "selection_reason": normalize(row.get("selection_reason")),
                "page_text_preview": normalize(row.get("page_text_preview")),
            }
        )

    document.close()
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", default=DEFAULT_PDF)
    parser.add_argument("--raw-dir", default="outputs/raw_extracted")
    parser.add_argument("--output-dir", default="outputs/page_images")
    parser.add_argument("--dpi", type=int, default=220)
    parser.add_argument(
        "--render-all",
        action="store_true",
        help="Render every page instead of only target figure/table pages.",
    )
    parser.add_argument(
        "--target-pattern",
        action="append",
        dest="target_patterns",
        help="Additional case-insensitive target pattern, e.g. 'Figure 9.4'.",
    )
    args = parser.parse_args()

    pdf_path = Path(args.pdf).expanduser().resolve()
    raw_dir = Path(args.raw_dir)
    output_dir = Path(args.output_dir)
    manifest_path = raw_dir / "page_manifest.csv"

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    if not manifest_path.exists():
        raise FileNotFoundError(
            "Missing page manifest. Run 01_extract_guideline_content.py first."
        )

    target_patterns = TARGET_PATTERNS + (args.target_patterns or [])
    selected_pages = select_pages(manifest_path, target_patterns, args.render_all)
    output_dir.mkdir(parents=True, exist_ok=True)

    records = render_pages(pdf_path, selected_pages, output_dir, args.dpi)
    manifest_out = output_dir / "page_image_manifest.csv"
    write_csv(
        manifest_out,
        records,
        [
            "page_number",
            "image_path",
            "dpi",
            "figure_ids",
            "table_ids",
            "selection_reason",
            "page_text_preview",
        ],
    )

    print(f"Rendered {len(records)} page images to {output_dir}")
    print(f"Wrote page image manifest to {manifest_out}")


if __name__ == "__main__":
    main()
