#!/usr/bin/env python3
"""Extract text, table-like blocks, and page metadata from the ADA PDF.

This script uses Docling when available and falls back to pypdf text
extraction. It intentionally writes raw intermediate files only under
outputs/ so generated guideline text can be kept out of Git if needed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


TABLE_CAPTION_PATTERN = re.compile(
    r"\b(Table\s+\d+(?:\.\d+)?)\s*[\u2013\u2014-]",
    re.IGNORECASE,
)
FIGURE_CAPTION_PATTERN = re.compile(
    r"\b(Fig(?:ure)?\.?\s+\d+(?:\.\d+)?)\s*[\u2013\u2014-]",
    re.IGNORECASE,
)


@dataclass
class TextBlock:
    block_id: str
    page_number: int | None
    section_title: str
    content_type: str
    text: str


@dataclass
class TableBlock:
    table_id: str
    page_number: int | None
    table_or_figure_id: str
    raw_table_text: str
    extraction_method: str


@dataclass
class PageRecord:
    page_number: int
    text_char_count: int
    table_ids: str
    figure_ids: str
    page_text_preview: str


def require_pdf(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def ensure_dirs(output_dir: Path) -> dict[str, Path]:
    raw_dir = output_dir / "raw_extracted"
    raw_dir.mkdir(parents=True, exist_ok=True)
    return {"raw": raw_dir}


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def safe_docling_extract(pdf_path: Path, raw_dir: Path) -> tuple[str, dict[str, Any]]:
    """Run Docling if installed and return markdown plus document json."""
    try:
        from docling.document_converter import DocumentConverter
    except ImportError:
        return "", {
            "extraction_method": "pypdf_fallback",
            "docling_available": False,
            "message": "Docling is not installed; used pypdf fallback.",
        }

    converter = DocumentConverter()
    result = converter.convert(str(pdf_path))
    document = result.document

    doc_json: dict[str, Any]
    if hasattr(document, "export_to_dict"):
        doc_json = document.export_to_dict()
    else:
        doc_json = {"document_repr": repr(document)}

    markdown = ""
    if hasattr(document, "export_to_markdown"):
        markdown = document.export_to_markdown()

    (raw_dir / "document_markdown.md").write_text(markdown, encoding="utf-8")
    return markdown, {
        "extraction_method": "docling",
        "docling_available": True,
        "document": doc_json,
    }


def extract_pages_with_pypdf(pdf_path: Path) -> list[tuple[int, str]]:
    try:
        from pypdf import PdfReader
    except ImportError:
        try:
            from PyPDF2 import PdfReader  # type: ignore[no-redef]
        except ImportError as exc:
            raise ImportError(
                "pypdf is required for page-level extraction. "
                "Install requirements.txt before running this script."
            ) from exc

    reader = PdfReader(str(pdf_path))
    pages: list[tuple[int, str]] = []
    for index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        pages.append((index, text))
    return pages


def split_markdown_blocks(markdown: str) -> list[TextBlock]:
    blocks: list[TextBlock] = []
    section = ""
    for i, chunk in enumerate(re.split(r"\n\s*\n", markdown), start=1):
        text = re.sub(r"\s+", " ", chunk).strip()
        if not text:
            continue
        if text.startswith("#"):
            section = re.sub(r"^#+\s*", "", text)
            content_type = "section_heading"
        elif "|" in text and "---" in text:
            content_type = "markdown_table"
        else:
            content_type = "text"
        blocks.append(
            TextBlock(
                block_id=f"md_{i:05d}",
                page_number=None,
                section_title=section,
                content_type=content_type,
                text=text,
            )
        )
    return blocks


def split_page_text_blocks(pages: list[tuple[int, str]]) -> list[TextBlock]:
    blocks: list[TextBlock] = []
    for page_number, page_text in pages:
        chunks = re.split(r"\n\s*\n", page_text)
        for i, chunk in enumerate(chunks, start=1):
            text = re.sub(r"\s+", " ", chunk).strip()
            if not text:
                continue
            blocks.append(
                TextBlock(
                    block_id=f"p{page_number:03d}_{i:03d}",
                    page_number=page_number,
                    section_title="",
                    content_type="text",
                    text=text,
                )
            )
    return blocks


def detect_tables_from_blocks(blocks: list[TextBlock]) -> list[TableBlock]:
    tables: list[TableBlock] = []
    for block in blocks:
        # A bare reference such as "see Table 9.2" does not make the page a
        # table asset.  Require a caption dash (including "—Continued") for
        # page-text extraction; Docling Markdown tables remain explicit.
        caption_match = TABLE_CAPTION_PATTERN.search(block.text)
        is_table = block.content_type == "markdown_table" or bool(caption_match)
        if not is_table:
            continue
        table_id = f"tbl_{len(tables) + 1:04d}"
        tables.append(
            TableBlock(
                table_id=table_id,
                page_number=block.page_number,
                table_or_figure_id=(
                    caption_match.group(1) if caption_match else ""
                ),
                raw_table_text=block.text,
                extraction_method=(
                    "docling_markdown" if block.content_type == "markdown_table"
                    else "page_text_pattern"
                ),
            )
        )
    return tables


def build_page_manifest(pages: list[tuple[int, str]]) -> list[PageRecord]:
    records: list[PageRecord] = []
    for page_number, text in pages:
        compact = re.sub(r"\s+", " ", text).strip()
        table_ids = sorted(
            {match.group(1).strip() for match in TABLE_CAPTION_PATTERN.finditer(text)}
        )
        figure_ids = sorted(
            {match.group(1).strip() for match in FIGURE_CAPTION_PATTERN.finditer(text)}
        )
        records.append(
            PageRecord(
                page_number=page_number,
                text_char_count=len(compact),
                table_ids="; ".join(table_ids),
                figure_ids="; ".join(figure_ids),
                page_text_preview=compact[:1000],
            )
        )
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--pdf",
        required=True,
        help="Path to the source guideline PDF (required; no contributor-specific default).",
    )
    parser.add_argument("--output-dir", default="outputs")
    args = parser.parse_args()

    pdf_path = Path(args.pdf).expanduser().resolve()
    output_dir = Path(args.output_dir)
    require_pdf(pdf_path)
    dirs = ensure_dirs(output_dir)
    raw_dir = dirs["raw"]

    _markdown, doc_payload = safe_docling_extract(pdf_path, raw_dir)
    pages = extract_pages_with_pypdf(pdf_path)

    # The page-level extraction is the canonical KB input even when Docling is
    # available.  Docling's flattened Markdown export does not retain reliable
    # page coordinates; using it here silently produced page_number=None for
    # every text block and broke source traceability.  The richer Docling
    # document remains available in document.json for future structured use.
    text_blocks = split_page_text_blocks(pages)

    tables = detect_tables_from_blocks(text_blocks)
    page_manifest = build_page_manifest(pages)

    doc_payload.update(
        {
            "source_pdf": str(pdf_path),
            "source_pdf_sha256": sha256_file(pdf_path),
            "n_pages": len(pages),
            "n_text_blocks": len(text_blocks),
            "n_tables_detected": len(tables),
        }
    )
    (raw_dir / "document.json").write_text(
        json.dumps(doc_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    write_csv(
        raw_dir / "text_blocks.csv",
        [asdict(row) for row in text_blocks],
        ["block_id", "page_number", "section_title", "content_type", "text"],
    )
    write_csv(
        raw_dir / "tables.csv",
        [asdict(row) for row in tables],
        [
            "table_id",
            "page_number",
            "table_or_figure_id",
            "raw_table_text",
            "extraction_method",
        ],
    )
    write_csv(
        raw_dir / "page_manifest.csv",
        [asdict(row) for row in page_manifest],
        [
            "page_number",
            "text_char_count",
            "table_ids",
            "figure_ids",
            "page_text_preview",
        ],
    )

    print(f"Extracted {len(text_blocks)} text blocks from {len(pages)} pages.")
    print(f"Detected {len(tables)} table-like blocks.")
    print(f"Wrote raw extraction outputs to {raw_dir}")


if __name__ == "__main__":
    main()
