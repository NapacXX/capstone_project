"""Tests for page-level traceability in the source extraction stage."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


PIPELINE_DIR = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "extract_guideline_content_under_test",
    PIPELINE_DIR / "01_extract_guideline_content.py",
)
assert SPEC and SPEC.loader
extractor = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = extractor
SPEC.loader.exec_module(extractor)


class ExtractGuidelineContentTests(unittest.TestCase):
    def test_page_text_blocks_retain_pdf_page_numbers(self) -> None:
        blocks = extractor.split_page_text_blocks(
            [(3, "First paragraph.\n\nSecond paragraph."), (4, "Third paragraph.")]
        )

        self.assertEqual([block.page_number for block in blocks], [3, 3, 4])
        self.assertEqual(
            [block.block_id for block in blocks],
            ["p003_001", "p003_002", "p004_001"],
        )

    def test_table_detection_preserves_source_page(self) -> None:
        blocks = extractor.split_page_text_blocks(
            [(9, "Table 9.2—Features of glucose-lowering medications")]
        )

        tables = extractor.detect_tables_from_blocks(blocks)

        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0].page_number, 9)
        self.assertEqual(tables[0].table_or_figure_id, "Table 9.2")
        self.assertEqual(tables[0].extraction_method, "page_text_pattern")

    def test_in_text_table_reference_is_not_a_table_block(self) -> None:
        blocks = extractor.split_page_text_blocks(
            [(8, "Choose therapy using Figure 9.4 and Table 9.2.")]
        )

        self.assertEqual(extractor.detect_tables_from_blocks(blocks), [])

    def test_page_manifest_records_caption_ids_not_bare_references(self) -> None:
        manifest = extractor.build_page_manifest(
            [
                (8, "Choose therapy using Figure 9.4 and Table 9.2."),
                (9, "Figure 9.4—Use of glucose-lowering medications"),
                (11, "Table 9.2—Continued"),
            ]
        )

        self.assertEqual(manifest[0].figure_ids, "")
        self.assertEqual(manifest[0].table_ids, "")
        self.assertEqual(manifest[1].figure_ids, "Figure 9.4")
        self.assertEqual(manifest[2].table_ids, "Table 9.2")


if __name__ == "__main__":
    unittest.main()
