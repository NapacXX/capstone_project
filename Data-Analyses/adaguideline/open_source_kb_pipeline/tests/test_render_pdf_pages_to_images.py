"""Focused tests for the visual-asset renderer (no PDF dependency required)."""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd


MODULE_PATH = Path(__file__).parents[1] / "05_render_pdf_pages_to_images.py"
SPEC = importlib.util.spec_from_file_location("render_pdf_pages", MODULE_PATH)
assert SPEC and SPEC.loader
renderer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = renderer
SPEC.loader.exec_module(renderer)


class RendererHelpersTest(unittest.TestCase):
    def test_sha256_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.pdf"
            path.write_bytes(b"synthetic-pdf-content")
            expected = hashlib.sha256(b"synthetic-pdf-content").hexdigest()
            self.assertEqual(renderer.sha256_file(path), expected)

    def test_validate_source_pdf_accepts_exact_step01_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "guideline.pdf"
            source.write_bytes(b"synthetic-pdf-content")
            raw = root / "raw"
            raw.mkdir()
            expected = hashlib.sha256(b"synthetic-pdf-content").hexdigest()
            (raw / "document.json").write_text(
                json.dumps(
                    {
                        "source_pdf": str(source.resolve()),
                        "source_pdf_sha256": expected,
                    }
                ),
                encoding="utf-8",
            )

            path, digest = renderer.validate_source_pdf(source, raw)

            self.assertEqual(path, source.resolve())
            self.assertEqual(digest, expected)

    def test_validate_source_pdf_rejects_different_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "guideline.pdf"
            other = root / "other.pdf"
            source.write_bytes(b"original")
            other.write_bytes(b"original")
            raw = root / "raw"
            raw.mkdir()
            (raw / "document.json").write_text(
                json.dumps(
                    {
                        "source_pdf": str(source),
                        "source_pdf_sha256": hashlib.sha256(b"original").hexdigest(),
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "does not match"):
                renderer.validate_source_pdf(other, raw)

    def test_validate_source_pdf_rejects_changed_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "guideline.pdf"
            source.write_bytes(b"original")
            raw = root / "raw"
            raw.mkdir()
            (raw / "document.json").write_text(
                json.dumps(
                    {
                        "source_pdf": str(source),
                        "source_pdf_sha256": hashlib.sha256(b"original").hexdigest(),
                    }
                ),
                encoding="utf-8",
            )
            source.write_bytes(b"changed")

            with self.assertRaisesRegex(ValueError, "no longer matches"):
                renderer.validate_source_pdf(source, raw)

    def test_validate_source_pdf_requires_recorded_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "guideline.pdf"
            source.write_bytes(b"original")
            raw = root / "raw"
            raw.mkdir()
            (raw / "document.json").write_text(
                json.dumps({"source_pdf": str(source)}), encoding="utf-8"
            )

            with self.assertRaisesRegex(ValueError, "source_pdf_sha256"):
                renderer.validate_source_pdf(source, raw)

    def test_normalize_treats_pandas_missing_values_as_empty(self) -> None:
        self.assertEqual(renderer.normalize(pd.NA), "")
        self.assertEqual(renderer.normalize(float("nan")), "")

    def test_axis_starts_cover_end_with_overlap(self) -> None:
        starts = renderer.axis_starts(1000, 400, 0.20)
        self.assertEqual(starts, [0.0, 320.0, 600])
        self.assertEqual(starts[-1] + 400, 1000)
        self.assertLess(starts[1], starts[0] + 400)

    def test_tile_bboxes_cover_page_and_identify_grid(self) -> None:
        boxes = list(renderer.tile_bboxes(700, 900, 400, 400, 0.25))
        self.assertEqual(boxes[0], (1, 1, (0.0, 0.0, 400.0, 400.0)))
        self.assertEqual(boxes[-1], (3, 2, (300, 500, 700, 900)))
        self.assertEqual({row for row, _, _ in boxes}, {1, 2, 3})
        self.assertEqual({column for _, column, _ in boxes}, {1, 2})

    def test_invalid_overlap_rejected(self) -> None:
        for value in (-0.1, 1.0, 2.0):
            with self.subTest(value=value), self.assertRaises(ValueError):
                renderer.axis_starts(100, 50, value)

    def test_asset_mode_values_are_explicit(self) -> None:
        # The PDF-dependent function validates before opening the document.
        with self.assertRaisesRegex(ValueError, "asset_mode"):
            renderer.render_pages(
                Path("does-not-matter.pdf"),
                pd.DataFrame(),
                Path("does-not-matter"),
                220,
                asset_mode="unexpected",
            )

    def test_asset_mode_defaults_targeted_to_both_and_render_all_to_overview(self) -> None:
        self.assertEqual(renderer.resolve_asset_mode(None, render_all=False), "both")
        self.assertEqual(renderer.resolve_asset_mode(None, render_all=True), "overview")
        self.assertEqual(
            renderer.resolve_asset_mode(None, render_all=False, no_tiles=True),
            "overview",
        )
        with self.assertRaises(ValueError):
            renderer.resolve_asset_mode("both", render_all=False, no_tiles=True)

    def test_select_pages_supports_legacy_missing_columns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "page_manifest.csv"
            pd.DataFrame(
                {"page_number": [1, 2], "page_text_preview": ["Figure 9.4", "Other"]}
            ).to_csv(manifest, index=False)
            selected = renderer.select_pages(manifest, ["figure 9.4"], False)
            self.assertEqual(selected["page_number"].tolist(), [1])
            self.assertEqual(selected.iloc[0]["figure_ids"], "")

    def test_select_pages_ignores_preview_references_when_ids_exist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "page_manifest.csv"
            pd.DataFrame(
                {
                    "page_number": [8, 9],
                    "figure_ids": ["", "Figure 9.4"],
                    "table_ids": ["", ""],
                    "page_text_preview": [
                        "Recommendations refer to Figure 9.4.",
                        "Figure caption appears later on this page.",
                    ],
                }
            ).to_csv(manifest, index=False)

            selected = renderer.select_pages(manifest, ["figure 9.4"], False)

            self.assertEqual(selected["page_number"].tolist(), [9])

    def test_manifest_columns_include_legacy_geometry_and_hashes(self) -> None:
        required = {
            "page_number",
            "image_path",
            "dpi",
            "asset_id",
            "asset_type",
            "parent_page_number",
            "bbox_x0",
            "bbox_y0",
            "bbox_x1",
            "bbox_y1",
            "source_pdf_sha256",
            "image_sha256",
            "asset_sha256",
        }
        self.assertTrue(required.issubset(renderer.MANIFEST_FIELDS))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "manifest.csv"
            renderer.write_csv(output, [], renderer.MANIFEST_FIELDS)
            with output.open(newline="", encoding="utf-8") as handle:
                self.assertEqual(set(next(csv.reader(handle))), set(renderer.MANIFEST_FIELDS))

    def test_render_pages_emits_overview_tiles_and_provenance(self) -> None:
        class FakePixmap:
            def __init__(self, payload: bytes) -> None:
                self.payload = payload

            def save(self, path: str) -> None:
                Path(path).write_bytes(self.payload)

        class FakePage:
            rect = types.SimpleNamespace(width=700, height=900)

            def get_pixmap(self, *, matrix, alpha, clip=None):
                del alpha
                payload = f"matrix={matrix};clip={clip}".encode()
                return FakePixmap(payload)

        class FakeDocument:
            def __init__(self) -> None:
                self.page = FakePage()
                self.closed = False

            def __len__(self) -> int:
                return 1

            def __getitem__(self, index: int) -> FakePage:
                self.assert_valid_index(index)
                return self.page

            @staticmethod
            def assert_valid_index(index: int) -> None:
                if index != 0:
                    raise IndexError(index)

            def close(self) -> None:
                self.closed = True

        fake_document = FakeDocument()
        fake_fitz = types.SimpleNamespace(
            open=lambda path: fake_document,
            Matrix=lambda x, y: (x, y),
            Rect=lambda *values: tuple(values),
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "guideline.pdf"
            source.write_bytes(b"synthetic guideline")
            selected = pd.DataFrame(
                [
                    {
                        "page_number": 1,
                        "figure_ids": "Figure 9.4",
                        "table_ids": "",
                        "selection_reason": "target_figure_or_table",
                        "page_text_preview": "dense flowchart",
                    }
                ]
            )
            with patch.dict(
                sys.modules,
                {"pymupdf": fake_fitz, "fitz": fake_fitz},
            ):
                records = renderer.render_pages(
                    source,
                    selected,
                    root / "images",
                    220,
                    tile_dpi=320,
                    tile_width_points=400,
                    tile_height_points=400,
                    tile_overlap=0.25,
                    asset_mode="both",
                )

            self.assertTrue(fake_document.closed)
            self.assertEqual(len(records), 7)  # one overview plus a 3x2 tile grid
            self.assertEqual(records[0]["asset_id"], "page_001_overview")
            self.assertEqual(records[0]["asset_type"], "page_overview")
            self.assertEqual(records[-1]["asset_id"], "page_001_tile_r03_c02")
            self.assertEqual(records[-1]["bbox_x1"], 700)
            self.assertEqual(records[-1]["bbox_y1"], 900)
            expected_source_hash = hashlib.sha256(b"synthetic guideline").hexdigest()
            self.assertTrue(
                all(record["source_pdf_sha256"] == expected_source_hash for record in records)
            )
            for record in records:
                image = Path(record["image_path"])
                self.assertTrue(image.is_file())
                self.assertEqual(record["image_sha256"], renderer.sha256_file(image))
                self.assertEqual(record["asset_sha256"], renderer.sha256_file(image))


if __name__ == "__main__":
    unittest.main()
