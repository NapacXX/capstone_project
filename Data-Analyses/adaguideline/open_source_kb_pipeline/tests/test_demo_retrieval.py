"""Unit tests for fail-fast demo retrieval contracts."""

from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


PIPELINE_DIR = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "demo_retrieval", PIPELINE_DIR / "04_demo_retrieval.py"
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Unable to import 04_demo_retrieval.py")
demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(demo)


class FakeIndex:
    def __init__(self, ntotal: int, dimension: int) -> None:
        self.ntotal = ntotal
        self.d = dimension


class DemoRetrievalTests(unittest.TestCase):
    def test_default_case_path_matches_tracked_directory_case(self) -> None:
        self.assertEqual(
            demo.DEFAULT_RAW_DATA,
            "../../../data/Raw/final_results_capstone_data_ver2.csv",
        )

    def test_safe_native_thread_defaults_are_set(self) -> None:
        self.assertEqual(os.environ.get("TOKENIZERS_PARALLELISM"), "false")
        if demo.sys.platform == "darwin":
            self.assertEqual(os.environ.get("OMP_NUM_THREADS"), "1")
            self.assertEqual(os.environ.get("MKL_NUM_THREADS"), "1")
            self.assertEqual(os.environ.get("VECLIB_MAXIMUM_THREADS"), "1")

    def test_distinct_cases_are_selected_before_limit(self) -> None:
        raw = pd.DataFrame(
            [
                {"case_id": "case-1", "hba1c_percent": 8.1},
                {"case_id": "case-1", "hba1c_percent": 8.2},
                {"case_id": "case-2", "hba1c_percent": 9.0},
            ]
        )

        cases = demo.prepare_demo_cases(raw, 2)

        self.assertEqual(cases["case_id"].tolist(), ["case-1", "case-2"])
        self.assertTrue(cases["retrieval_query"].str.len().gt(0).all())

    def test_case_input_contract_is_fail_fast(self) -> None:
        with self.assertRaisesRegex(ValueError, "positive"):
            demo.prepare_demo_cases(pd.DataFrame({"case_id": ["a"]}), 0)
        with self.assertRaisesRegex(ValueError, "required column"):
            demo.prepare_demo_cases(pd.DataFrame({"other": ["a"]}), 1)
        with self.assertRaisesRegex(ValueError, "missing case_id"):
            demo.prepare_demo_cases(pd.DataFrame({"case_id": [None]}), 1)

    def test_top_k_is_positive_and_clamped_to_index(self) -> None:
        self.assertEqual(demo.effective_top_k(8, 3), 3)
        self.assertEqual(demo.effective_top_k(2, 3), 2)
        with self.assertRaisesRegex(ValueError, "top-k"):
            demo.effective_top_k(0, 3)
        with self.assertRaisesRegex(ValueError, "index is empty"):
            demo.effective_top_k(1, 0)

    def test_vector_contract_rejects_count_and_dimension_mismatches(self) -> None:
        metadata = pd.DataFrame({"chunk_id": ["one", "two"]})
        embeddings = np.zeros((1, 3), dtype="float32")
        with self.assertRaisesRegex(ValueError, "row-count mismatch"):
            demo.validate_vector_contract(FakeIndex(1, 3), metadata, embeddings)
        with self.assertRaisesRegex(ValueError, "dimension mismatch"):
            demo.validate_vector_contract(FakeIndex(2, 4), metadata, embeddings)

    def test_vector_contract_accepts_consistent_artifacts(self) -> None:
        metadata = pd.DataFrame({"chunk_id": ["one", "two"]})
        embeddings = np.zeros((1, 3), dtype="float32")
        demo.validate_vector_contract(FakeIndex(2, 3), metadata, embeddings)

    def test_headerless_metadata_csv_has_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "chunk_metadata.csv"
            path.write_text("", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "metadata is empty"):
                demo.read_metadata(Path(temp))


if __name__ == "__main__":
    unittest.main()
