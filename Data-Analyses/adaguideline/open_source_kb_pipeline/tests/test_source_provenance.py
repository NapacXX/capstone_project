"""Focused tests for source-PDF provenance in the text KB pipeline."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd


PIPELINE_DIR = Path(__file__).resolve().parents[1]


def load_script(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, PIPELINE_DIR / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


extractor = load_script("extract_guideline_content", "01_extract_guideline_content.py")
builder = load_script("build_type2_kb", "02_build_type2_kb.py")


class SourceProvenanceTests(unittest.TestCase):
    def test_sha256_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "guideline.pdf"
            path.write_bytes(b"guideline-bytes")
            self.assertEqual(
                extractor.sha256_file(path),
                hashlib.sha256(b"guideline-bytes").hexdigest(),
            )

    def test_source_metadata_resolves_pdf_and_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf = root / "guideline.pdf"
            pdf.write_bytes(b"source-pdf")
            raw = root / "raw"
            raw.mkdir()
            expected = hashlib.sha256(b"source-pdf").hexdigest()
            (raw / "document.json").write_text(
                json.dumps(
                    {
                        "source_pdf": str(pdf),
                        "source_pdf_sha256": expected,
                    }
                ),
                encoding="utf-8",
            )

            path, digest = builder.source_metadata(raw)

            self.assertEqual(path, str(pdf.resolve()))
            self.assertEqual(digest, expected)

    def test_source_metadata_fails_when_recorded_pdf_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory)
            (raw / "document.json").write_text(
                json.dumps(
                    {
                        "source_pdf": str(raw / "missing.pdf"),
                        "source_pdf_sha256": "0" * 64,
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(FileNotFoundError, "no longer exists"):
                builder.source_metadata(raw)

    def test_source_metadata_requires_recorded_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf = root / "guideline.pdf"
            pdf.write_bytes(b"source-pdf")
            raw = root / "raw"
            raw.mkdir()
            (raw / "document.json").write_text(
                json.dumps({"source_pdf": str(pdf)}), encoding="utf-8"
            )

            with self.assertRaisesRegex(ValueError, "source_pdf_sha256"):
                builder.source_metadata(raw)

    def test_source_metadata_detects_pdf_drift_after_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf = root / "guideline.pdf"
            pdf.write_bytes(b"original")
            raw = root / "raw"
            raw.mkdir()
            (raw / "document.json").write_text(
                json.dumps(
                    {
                        "source_pdf": str(pdf),
                        "source_pdf_sha256": hashlib.sha256(b"original").hexdigest(),
                    }
                ),
                encoding="utf-8",
            )
            pdf.write_bytes(b"mutated")

            with self.assertRaisesRegex(ValueError, "no longer matches"):
                builder.source_metadata(raw)

    def test_normalize_treats_pandas_missing_values_as_empty(self) -> None:
        self.assertEqual(builder.normalize(pd.NA), "")
        self.assertEqual(builder.normalize(float("nan")), "")

    def test_empty_filtered_outputs_retain_parseable_schemas(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory)
            pd.DataFrame(
                [
                    {
                        "block_id": "block_1",
                        "page_number": 1,
                        "section_title": "Excluded",
                        "content_type": "paragraph",
                        "text": "Pregnancy and type 1 diabetes guidance.",
                    }
                ]
            ).to_csv(raw / "text_blocks.csv", index=False)
            pd.DataFrame(
                [
                    {
                        "table_id": "table_1",
                        "page_number": 1,
                        "table_or_figure_id": "Table 1",
                        "raw_table_text": "Pregnancy and type 1 diabetes guidance.",
                        "extraction_method": "test",
                    }
                ]
            ).to_csv(raw / "tables.csv", index=False)
            pd.DataFrame(
                [
                    {
                        "page_number": 1,
                        "figure_ids": "",
                        "page_text_preview": "General introduction.",
                    }
                ]
            ).to_csv(raw / "page_manifest.csv", index=False)

            outputs = [
                (
                    builder.build_text_chunks(raw, scope="type2"),
                    builder.TEXT_CHUNK_COLUMNS,
                ),
                (
                    builder.build_structured_tables(raw, scope="type2"),
                    builder.STRUCTURED_TABLE_COLUMNS,
                ),
                (
                    builder.build_figure_pages(raw, scope="type2"),
                    builder.FIGURE_PAGE_COLUMNS,
                ),
            ]
            for index, (frame, expected_columns) in enumerate(outputs):
                with self.subTest(index=index):
                    self.assertTrue(frame.empty)
                    self.assertEqual(frame.columns.tolist(), expected_columns)
                    csv_path = raw / f"empty_{index}.csv"
                    frame.to_csv(csv_path, index=False)
                    parsed = pd.read_csv(csv_path)
                    self.assertTrue(parsed.empty)
                    self.assertEqual(parsed.columns.tolist(), expected_columns)


if __name__ == "__main__":
    unittest.main()
