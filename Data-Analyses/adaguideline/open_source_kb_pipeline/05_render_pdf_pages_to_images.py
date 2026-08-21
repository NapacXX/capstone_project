#!/usr/bin/env python3
"""Render guideline pages into provenance-tracked visual extraction assets.

Each selected PDF page produces a whole-page overview and, by default,
overlapping high-resolution tiles.  The overview preserves context while the
tiles make dense flowcharts and small symbols legible to local vision models.
All geometry is recorded in PDF points (72 points per inch).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


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

MANIFEST_FIELDS = [
    # Legacy columns retained for the current visual-extraction consumer.
    "page_number",
    "image_path",
    "dpi",
    "figure_ids",
    "table_ids",
    "selection_reason",
    "page_text_preview",
    # Asset identity, geometry, and immutable provenance.
    "asset_id",
    "asset_type",
    "parent_page_number",
    "tile_row",
    "tile_column",
    "bbox_x0",
    "bbox_y0",
    "bbox_x1",
    "bbox_y1",
    "page_width_points",
    "page_height_points",
    "overlap_fraction",
    "source_pdf",
    "source_pdf_sha256",
    "image_sha256",
    # Deprecated alias retained for manifests produced by the first tiled version.
    "asset_sha256",
]


def normalize(text: object) -> str:
    if text is None:
        return ""
    try:
        if bool(pd.isna(text)):
            return ""
    except (TypeError, ValueError):
        pass
    return re.sub(r"\s+", " ", str(text)).strip()


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_source_pdf(pdf_path: Path, raw_dir: Path) -> tuple[Path, str]:
    """Verify that rendering uses the exact PDF extracted by step 01."""
    pdf_path = pdf_path.expanduser().resolve()
    document_path = raw_dir / "document.json"
    if not document_path.is_file():
        raise FileNotFoundError(
            f"Missing extraction metadata: {document_path}. "
            "Run 01_extract_guideline_content.py first."
        )

    payload = json.loads(document_path.read_text(encoding="utf-8"))
    recorded_pdf = normalize(payload.get("source_pdf"))
    if not recorded_pdf:
        raise ValueError(f"source_pdf is missing from {document_path}")
    recorded_path = Path(recorded_pdf).expanduser().resolve()
    if recorded_path != pdf_path:
        raise ValueError(
            "--pdf does not match the source PDF recorded by step 01: "
            f"expected {recorded_path}, got {pdf_path}"
        )

    recorded_sha256 = normalize(payload.get("source_pdf_sha256")).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", recorded_sha256):
        raise ValueError(
            f"source_pdf_sha256 is missing or invalid in {document_path}"
        )
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    actual_sha256 = sha256_file(pdf_path)
    if actual_sha256 != recorded_sha256:
        raise ValueError(
            "Source PDF SHA-256 no longer matches step 01 extraction metadata: "
            f"expected {recorded_sha256}, got {actual_sha256}"
        )
    return pdf_path, recorded_sha256


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _text_column(pages: pd.DataFrame, name: str) -> pd.Series:
    """Return a normalized text column, including for older manifests."""
    if name not in pages.columns:
        return pd.Series("", index=pages.index, dtype="string")
    return pages[name].fillna("").astype(str)


def select_pages(
    page_manifest: Path,
    target_patterns: list[str],
    render_all: bool,
) -> pd.DataFrame:
    pages = pd.read_csv(page_manifest)
    if "page_number" not in pages.columns:
        raise ValueError(f"Page manifest has no page_number column: {page_manifest}")

    has_label_columns = "figure_ids" in pages.columns or "table_ids" in pages.columns
    for column in ("figure_ids", "table_ids", "page_text_preview"):
        pages[column] = _text_column(pages, column)
    if has_label_columns:
        # Current Step 01 records caption-defined IDs. Do not reintroduce
        # false positives by matching ordinary references in the preview.
        pages["selection_text"] = (
            pages["figure_ids"] + " " + pages["table_ids"]
        ).str.lower()
    else:
        # Compatibility fallback for manifests predating explicit ID columns.
        pages["selection_text"] = pages["page_text_preview"].str.lower()

    if render_all:
        selected = pages.copy()
        selected["selection_reason"] = "render_all"
        return selected

    if not target_patterns:
        raise ValueError("At least one target pattern is required unless --render-all is used.")
    pattern = "|".join(re.escape(term.lower()) for term in target_patterns)
    selected = pages[
        pages["selection_text"].str.contains(pattern, regex=True, na=False)
    ].copy()
    selected["selection_reason"] = "target_figure_or_table"
    return selected


def axis_starts(length: float, tile_length: float, overlap_fraction: float) -> list[float]:
    """Return starts that cover an axis and anchor the last tile to its end."""
    if length <= 0 or tile_length <= 0:
        raise ValueError("Page and tile dimensions must be positive.")
    if not 0 <= overlap_fraction < 1:
        raise ValueError("overlap_fraction must be in [0, 1).")
    if tile_length >= length:
        return [0.0]

    stride = tile_length * (1 - overlap_fraction)
    starts = [0.0]
    final_start = length - tile_length
    while starts[-1] + stride < final_start:
        starts.append(starts[-1] + stride)
    if final_start - starts[-1] > 1e-6:
        starts.append(final_start)
    return starts


def tile_bboxes(
    page_width: float,
    page_height: float,
    tile_width: float,
    tile_height: float,
    overlap_fraction: float,
) -> Iterable[tuple[int, int, tuple[float, float, float, float]]]:
    x_starts = axis_starts(page_width, tile_width, overlap_fraction)
    y_starts = axis_starts(page_height, tile_height, overlap_fraction)
    for tile_row, y0 in enumerate(y_starts, start=1):
        for tile_column, x0 in enumerate(x_starts, start=1):
            yield (
                tile_row,
                tile_column,
                (
                    x0,
                    y0,
                    min(x0 + tile_width, page_width),
                    min(y0 + tile_height, page_height),
                ),
            )


def _asset_record(
    *,
    row: pd.Series,
    page_number: int,
    image_path: Path,
    dpi: int,
    asset_id: str,
    asset_type: str,
    tile_row: int | str,
    tile_column: int | str,
    bbox: tuple[float, float, float, float],
    page_width: float,
    page_height: float,
    overlap_fraction: float,
    pdf_path: Path,
    pdf_sha256: str,
) -> dict[str, Any]:
    x0, y0, x1, y1 = bbox
    image_sha256 = sha256_file(image_path)
    return {
        "page_number": page_number,
        "image_path": str(image_path.resolve()),
        "dpi": dpi,
        "figure_ids": normalize(row.get("figure_ids")),
        "table_ids": normalize(row.get("table_ids")),
        "selection_reason": normalize(row.get("selection_reason")),
        "page_text_preview": normalize(row.get("page_text_preview")),
        "asset_id": asset_id,
        "asset_type": asset_type,
        "parent_page_number": page_number,
        "tile_row": tile_row,
        "tile_column": tile_column,
        "bbox_x0": round(x0, 3),
        "bbox_y0": round(y0, 3),
        "bbox_x1": round(x1, 3),
        "bbox_y1": round(y1, 3),
        "page_width_points": round(page_width, 3),
        "page_height_points": round(page_height, 3),
        "overlap_fraction": overlap_fraction if asset_type == "tile" else 0,
        "source_pdf": str(pdf_path),
        "source_pdf_sha256": pdf_sha256,
        "image_sha256": image_sha256,
        "asset_sha256": image_sha256,
    }


def render_pages(
    pdf_path: Path,
    selected_pages: pd.DataFrame,
    image_dir: Path,
    dpi: int,
    *,
    tile_dpi: int = 320,
    tile_width_points: float = 360,
    tile_height_points: float = 360,
    tile_overlap: float = 0.20,
    asset_mode: str = "both",
) -> list[dict[str, Any]]:
    if asset_mode not in {"overview", "tiles", "both"}:
        raise ValueError("asset_mode must be overview, tiles, or both.")
    try:
        import pymupdf as fitz
    except ImportError:
        try:
            import fitz
        except ImportError as exc:
            raise ImportError(
                "PyMuPDF is required for page image rendering. "
                "Install it with: pip install pymupdf"
            ) from exc

    if dpi <= 0 or tile_dpi <= 0:
        raise ValueError("DPI values must be positive.")
    # Validate even when a one-page/small-page fixture would produce one tile.
    axis_starts(1.0, 1.0, tile_overlap)

    pdf_path = pdf_path.expanduser().resolve()
    image_dir.mkdir(parents=True, exist_ok=True)
    pdf_sha256 = sha256_file(pdf_path)
    document = fitz.open(pdf_path)
    records: list[dict[str, Any]] = []

    try:
        for _, row in selected_pages.iterrows():
            page_number = int(row["page_number"])
            if not 1 <= page_number <= len(document):
                raise ValueError(
                    f"Manifest page {page_number} is outside PDF page range 1-{len(document)}."
                )
            page = document[page_number - 1]
            page_width = float(page.rect.width)
            page_height = float(page.rect.height)
            full_bbox = (0.0, 0.0, page_width, page_height)

            if asset_mode in {"overview", "both"}:
                # Preserve the legacy filename for the overview asset.
                overview_path = image_dir / f"ada_page_{page_number:03d}.png"
                overview_matrix = fitz.Matrix(dpi / 72, dpi / 72)
                page.get_pixmap(matrix=overview_matrix, alpha=False).save(str(overview_path))
                records.append(
                    _asset_record(
                        row=row,
                        page_number=page_number,
                        image_path=overview_path,
                        dpi=dpi,
                        asset_id=f"page_{page_number:03d}_overview",
                        asset_type="page_overview",
                        tile_row="",
                        tile_column="",
                        bbox=full_bbox,
                        page_width=page_width,
                        page_height=page_height,
                        overlap_fraction=tile_overlap,
                        pdf_path=pdf_path,
                        pdf_sha256=pdf_sha256,
                    )
                )

            if asset_mode == "overview":
                continue

            tile_matrix = fitz.Matrix(tile_dpi / 72, tile_dpi / 72)
            for tile_row, tile_column, bbox in tile_bboxes(
                page_width,
                page_height,
                tile_width_points,
                tile_height_points,
                tile_overlap,
            ):
                asset_id = f"page_{page_number:03d}_tile_r{tile_row:02d}_c{tile_column:02d}"
                tile_path = image_dir / f"ada_{asset_id}.png"
                clip = fitz.Rect(*bbox)
                page.get_pixmap(matrix=tile_matrix, clip=clip, alpha=False).save(str(tile_path))
                records.append(
                    _asset_record(
                        row=row,
                        page_number=page_number,
                        image_path=tile_path,
                        dpi=tile_dpi,
                        asset_id=asset_id,
                        asset_type="tile",
                        tile_row=tile_row,
                        tile_column=tile_column,
                        bbox=bbox,
                        page_width=page_width,
                        page_height=page_height,
                        overlap_fraction=tile_overlap,
                        pdf_path=pdf_path,
                        pdf_sha256=pdf_sha256,
                    )
                )
    finally:
        document.close()

    return records


def positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def overlap_fraction(value: str) -> float:
    parsed = float(value)
    if not 0 <= parsed < 1:
        raise argparse.ArgumentTypeError("overlap must be in [0, 1)")
    return parsed


def resolve_asset_mode(
    requested_mode: str | None,
    *,
    render_all: bool,
    no_tiles: bool = False,
) -> str:
    """Resolve safe defaults while retaining the legacy ``--no-tiles`` flag."""
    if requested_mode and no_tiles:
        raise ValueError("Use either --asset-mode or --no-tiles, not both.")
    if no_tiles:
        return "overview"
    if requested_mode:
        return requested_mode
    return "overview" if render_all else "both"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--pdf",
        required=True,
        help="Path to the source guideline PDF (required; no contributor-specific default).",
    )
    parser.add_argument("--raw-dir", default="outputs/raw_extracted")
    parser.add_argument("--output-dir", default="outputs/page_images")
    parser.add_argument("--dpi", type=int, default=220, help="Whole-page overview DPI.")
    parser.add_argument("--tile-dpi", type=int, default=320)
    parser.add_argument("--tile-width-points", type=positive_float, default=360.0)
    parser.add_argument("--tile-height-points", type=positive_float, default=360.0)
    parser.add_argument("--tile-overlap", type=overlap_fraction, default=0.20)
    parser.add_argument(
        "--asset-mode",
        choices=["overview", "tiles", "both"],
        default=None,
        help=(
            "Assets to render. Default: both for targeted pages; overview for "
            "--render-all. Use both for dense flowcharts."
        ),
    )
    parser.add_argument(
        "--no-tiles",
        action="store_true",
        help="Deprecated compatibility alias for --asset-mode overview.",
    )
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

    pdf_path, _source_pdf_sha256 = validate_source_pdf(pdf_path, raw_dir)
    if not manifest_path.exists():
        raise FileNotFoundError(
            "Missing page manifest. Run 01_extract_guideline_content.py first."
        )

    target_patterns = TARGET_PATTERNS + (args.target_patterns or [])
    selected_pages = select_pages(manifest_path, target_patterns, args.render_all)
    if selected_pages.empty:
        raise ValueError(
            "No pages matched the requested targets; check page_manifest.csv or use --render-all."
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        asset_mode = resolve_asset_mode(
            args.asset_mode,
            render_all=args.render_all,
            no_tiles=args.no_tiles,
        )
    except ValueError as exc:
        parser.error(str(exc))

    records = render_pages(
        pdf_path,
        selected_pages,
        output_dir,
        args.dpi,
        tile_dpi=args.tile_dpi,
        tile_width_points=args.tile_width_points,
        tile_height_points=args.tile_height_points,
        tile_overlap=args.tile_overlap,
        asset_mode=asset_mode,
    )
    manifest_out = output_dir / "page_image_manifest.csv"
    write_csv(manifest_out, records, MANIFEST_FIELDS)

    overview_count = sum(row["asset_type"] == "page_overview" for row in records)
    tile_count = sum(row["asset_type"] == "tile" for row in records)
    print(
        f"Rendered {len(records)} assets ({overview_count} overviews, "
        f"{tile_count} tiles) to {output_dir}"
    )
    print(f"Wrote page image manifest to {manifest_out}")


if __name__ == "__main__":
    main()
