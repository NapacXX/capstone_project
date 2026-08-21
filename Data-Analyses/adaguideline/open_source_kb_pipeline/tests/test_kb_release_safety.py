"""Safety tests for enhanced KB integration and vector-corpus loading."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd


PIPELINE_DIR = Path(__file__).resolve().parents[1]


def load_script(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, PIPELINE_DIR / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to import {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


enhanced_builder = load_script("enhanced_builder", "08_build_enhanced_guideline_kb.py")
vector_builder = load_script("vector_builder", "03_build_vector_index.py")


def write_csv(directory: Path, filename: str, rows: list[dict[str, object]]) -> None:
    pd.DataFrame(rows).to_csv(directory / filename, index=False)


def canonical_visual_fields(**overrides: object) -> dict[str, object]:
    fingerprint = "a" * 64
    fields: dict[str, object] = {
        "requires_manual_review": False,
        "human_approved": True,
        "validation_status": "valid",
        "review_status": "approved",
        "approval_status": "approved",
        "release_status": "released",
        "reviewer": "reviewer-a",
        "approved_by": "reviewer-a",
        "reviewed_at": "2026-08-13T10:30:00Z",
        "approved_at": "2026-08-13T10:30:00+00:00",
        "content_sha256": fingerprint,
        "effective_content_sha256": fingerprint,
    }
    fields.update(overrides)
    return fields


class EnhancedKnowledgeBaseTests(unittest.TestCase):
    def test_committed_kb_schemas_are_canonicalized_and_auditable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            kb_dir = Path(temp)
            write_csv(
                kb_dir,
                "ada_text_chunks.csv",
                [
                    {
                        "chunk_id": "REC_1",
                        "source_type": "recommendation",
                        "source_page": 8,
                        "condition": "CKD",
                        "drug_class": "SGLT2 inhibitor",
                        "summary_text": "Use an agent with kidney benefit.",
                    }
                ],
            )
            write_csv(
                kb_dir,
                "ada_medication_table_structured.csv",
                [
                    {
                        "drug_class": "metformin",
                        "source_table": "Table 9.2",
                        "source_page": 11,
                        "glycemic_efficacy": "high",
                        "kidney_dosing_consideration": "avoid below threshold",
                    }
                ],
            )
            write_csv(
                kb_dir,
                "ada_decision_rule_registry.csv",
                [
                    {
                        "rule_id": "RULE_1",
                        "source_figure_or_recommendation": "Figure 9.4",
                        "source_page": 8,
                        "trigger_logic": "ckd",
                        "condition": "CKD",
                        "drug_class": "SGLT2 inhibitor",
                        "expected_action": "recommend",
                        "reason_template": "CKD supports treatment.",
                    }
                ],
            )

            result = enhanced_builder.load_existing_kb(kb_dir)

            self.assertEqual(
                set(result["chunk_id"]),
                {"REC_1", "ADA2026_MEDICATION_metformin", "RULE_1"},
            )
            self.assertEqual(
                set(result["content_type"]),
                {"recommendation", "medication_table", "decision_rule"},
            )
            self.assertIn("source_file", result.columns)
            self.assertIn("reason_template", result.columns)
            self.assertTrue(result["retrieval_text"].str.len().gt(0).all())

    def test_only_explicitly_released_visual_rows_are_merged(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            kb_dir = root / "kb"
            visual_dir = root / "visual"
            kb_dir.mkdir()
            visual_dir.mkdir()
            write_csv(
                kb_dir,
                "full_guideline_text_chunks.csv",
                [
                    {
                        "chunk_id": "text_1",
                        "content_type": "text",
                        "retrieval_text": "Guideline text.",
                    }
                ],
            )
            write_csv(
                visual_dir,
                "visual_retrieval_records.csv",
                [
                    {
                        "chunk_id": "visual_released",
                        "content_type": "visual_logic",
                        "retrieval_text": "Human-verified branch.",
                        "source_json": "page.json",
                        **canonical_visual_fields(),
                    },
                    {
                        "chunk_id": "visual_unapproved",
                        "content_type": "visual_logic",
                        "retrieval_text": "Unreviewed branch.",
                        "requires_manual_review": False,
                        "human_approved": False,
                        "release_status": "draft",
                    },
                    {
                        "chunk_id": "visual_still_reviewing",
                        "content_type": "visual_logic",
                        "retrieval_text": "Ambiguous arrow.",
                        "requires_manual_review": True,
                        "human_approved": True,
                        "release_status": "released",
                    },
                ],
            )

            result, counts = enhanced_builder.build_enhanced_kb(kb_dir, visual_dir)

            self.assertEqual(set(result["chunk_id"]), {"text_1", "visual_released"})
            self.assertEqual(counts["n_visual_records"], 1)
            self.assertEqual(counts["n_visual_records_excluded_unapproved"], 2)
            released = result.set_index("chunk_id").loc["visual_released"]
            self.assertEqual(released["approved_by"], "reviewer-a")
            self.assertEqual(released["source_json"], "page.json")

    def test_duplicate_chunk_ids_abort_instead_of_overwriting(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            kb_dir = root / "kb"
            visual_dir = root / "visual"
            kb_dir.mkdir()
            visual_dir.mkdir()
            write_csv(
                kb_dir,
                "full_guideline_text_chunks.csv",
                [
                    {
                        "chunk_id": "duplicate",
                        "content_type": "text",
                        "retrieval_text": "Text version.",
                    }
                ],
            )
            write_csv(
                visual_dir,
                "visual_retrieval_records.csv",
                [
                    {
                        "chunk_id": "duplicate",
                        "content_type": "visual_logic",
                        "retrieval_text": "Visual version.",
                        **canonical_visual_fields(),
                    }
                ],
            )

            with self.assertRaisesRegex(ValueError, "Duplicate chunk_id"):
                enhanced_builder.build_enhanced_kb(kb_dir, visual_dir)

    def test_final_merge_guard_rejects_unsafe_visual_row(self) -> None:
        unsafe = pd.DataFrame(
            [
                {
                    "chunk_id": "unsafe_visual",
                    "content_type": "visual_logic",
                    "requires_manual_review": False,
                    "human_approved": False,
                    "release_status": "draft",
                }
            ]
        )
        with self.assertRaisesRegex(ValueError, "Fail-closed"):
            enhanced_builder.assert_visual_rows_are_released(unsafe)

    def test_visual_release_gate_rejects_every_inconsistent_contract_field(self) -> None:
        contradictions = {
            "validation_status": "invalid",
            "review_status": "rejected",
            "approval_status": "pending",
            "release_status": "unreleased",
            "reviewer": "",
            "approved_by": "someone-else",
            "reviewed_at": "2026-08-13T10:30:00",
            "approved_at": "2026-08-13T10:31:00Z",
            "content_sha256": "not-a-hash",
            "effective_content_sha256": "b" * 64,
        }
        for field, value in contradictions.items():
            with self.subTest(field=field):
                row = canonical_visual_fields(**{field: value})
                frame = pd.DataFrame([row])
                self.assertFalse(enhanced_builder.visual_release_mask(frame).iloc[0])
                self.assertFalse(vector_builder.visual_release_mask(frame).iloc[0])

    def test_candidate_file_is_preferred_and_counted_before_release_filter(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            visual_dir = Path(temp)
            write_csv(
                visual_dir,
                "visual_candidate_retrieval_records.csv",
                [
                    {
                        "chunk_id": "candidate_pending",
                        "content_type": "visual_logic",
                        "retrieval_text": "Pending candidate.",
                        **canonical_visual_fields(
                            human_approved=False,
                            review_status="pending",
                            approval_status="pending",
                            release_status="unreleased",
                        ),
                    }
                ],
            )
            write_csv(
                visual_dir,
                "visual_retrieval_records.csv",
                [
                    {
                        "chunk_id": "compatibility_release",
                        "content_type": "visual_logic",
                        "retrieval_text": "Should not shadow candidates.",
                        **canonical_visual_fields(),
                    }
                ],
            )

            visual, excluded = enhanced_builder.load_visual_kb(visual_dir)

            self.assertTrue(visual.empty)
            self.assertEqual(excluded, 1)

    def test_headerless_empty_csv_is_treated_as_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "empty.csv"
            path.write_text("", encoding="utf-8")
            self.assertTrue(enhanced_builder.read_if_exists(path).empty)


class VectorCorpusTests(unittest.TestCase):
    def test_metadata_csv_fallback_removes_stale_preferred_parquet(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp)
            stale = output_dir / "chunk_metadata.parquet"
            stale.write_text("stale", encoding="utf-8")
            corpus = pd.DataFrame({"chunk_id": ["one"]})

            with mock.patch.object(
                pd.DataFrame, "to_parquet", side_effect=RuntimeError("no engine")
            ):
                written = vector_builder.write_metadata(corpus, output_dir)

            self.assertEqual(written, output_dir / "chunk_metadata.csv")
            self.assertFalse(stale.exists())
            self.assertEqual(pd.read_csv(written)["chunk_id"].tolist(), ["one"])

    def test_metadata_parquet_success_removes_stale_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp)
            stale = output_dir / "chunk_metadata.csv"
            stale.write_text("stale", encoding="utf-8")
            corpus = pd.DataFrame({"chunk_id": ["one"]})

            def fake_to_parquet(path: Path, **_: object) -> None:
                Path(path).write_text("current", encoding="utf-8")

            with mock.patch.object(
                pd.DataFrame, "to_parquet", side_effect=fake_to_parquet
            ):
                written = vector_builder.write_metadata(corpus, output_dir)

            self.assertEqual(written, output_dir / "chunk_metadata.parquet")
            self.assertTrue(written.exists())
            self.assertFalse(stale.exists())

    def test_vector_loader_prefers_enhanced_snapshot_over_component_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            kb_dir = Path(temp)
            row = {
                "chunk_id": "same_logical_record",
                "content_type": "text",
                "retrieval_text": "Canonical snapshot text.",
            }
            write_csv(kb_dir, "enhanced_guideline_kb_records.csv", [row])
            write_csv(kb_dir, "full_guideline_text_chunks.csv", [row])

            corpus = vector_builder.load_kb(kb_dir)

            self.assertEqual(corpus["chunk_id"].tolist(), ["same_logical_record"])

    def test_vector_loader_filters_review_and_unapproved_visual_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            kb_dir = Path(temp)
            write_csv(
                kb_dir,
                "enhanced_guideline_kb_records.csv",
                [
                    {
                        "chunk_id": "text_ok",
                        "content_type": "text",
                        "retrieval_text": "Released guideline text.",
                        "requires_manual_review": False,
                    },
                    {
                        "chunk_id": "text_review",
                        "content_type": "text",
                        "retrieval_text": "Text awaiting review.",
                        "requires_manual_review": True,
                    },
                    {
                        "chunk_id": "visual_ok",
                        "content_type": "visual_logic",
                        "retrieval_text": "Verified visual edge.",
                        "source_json": "verified.json",
                        **canonical_visual_fields(),
                    },
                    {
                        "chunk_id": "visual_no",
                        "content_type": "visual_logic",
                        "retrieval_text": "Unverified visual edge.",
                        "requires_manual_review": False,
                        "human_approved": False,
                        "release_status": "draft",
                    },
                    {
                        "chunk_id": "visual_missing_review_decision",
                        "content_type": "visual_logic",
                        "retrieval_text": "No explicit review decision.",
                        "human_approved": True,
                        "release_status": "released",
                    },
                ],
            )

            corpus = vector_builder.load_kb(kb_dir)

            self.assertEqual(set(corpus["chunk_id"]), {"text_ok", "visual_ok"})
            self.assertIn("source_json", corpus.columns)
            self.assertIn("human_approved", corpus.columns)

    def test_vector_loader_skips_headerless_empty_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            kb_dir = Path(temp)
            (kb_dir / "full_guideline_structured_tables.csv").write_text(
                "", encoding="utf-8"
            )
            write_csv(
                kb_dir,
                "full_guideline_text_chunks.csv",
                [
                    {
                        "chunk_id": "text_ok",
                        "content_type": "text",
                        "retrieval_text": "Usable text.",
                    }
                ],
            )

            corpus = vector_builder.load_kb(kb_dir)

            self.assertEqual(corpus["chunk_id"].tolist(), ["text_ok"])

    def test_vector_loader_rejects_duplicates_before_filtering(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            kb_dir = Path(temp)
            write_csv(
                kb_dir,
                "enhanced_guideline_kb_records.csv",
                [
                    {
                        "chunk_id": "same_id",
                        "content_type": "text",
                        "retrieval_text": "First.",
                    },
                    {
                        "chunk_id": "same_id",
                        "content_type": "text",
                        "retrieval_text": "Second.",
                    },
                ],
            )

            with self.assertRaisesRegex(ValueError, "Duplicate chunk_id"):
                vector_builder.load_kb(kb_dir)

    def test_vector_loader_supports_committed_medication_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            kb_dir = Path(temp)
            write_csv(
                kb_dir,
                "ada_medication_table_structured.csv",
                [
                    {
                        "drug_class": "GLP-1 RA",
                        "source_table": "Table 9.2",
                        "glycemic_efficacy": "high",
                        "major_cautions": "GI effects",
                    }
                ],
            )

            corpus = vector_builder.load_kb(kb_dir)

            self.assertEqual(
                corpus.loc[0, "chunk_id"], "ADA2026_MEDICATION_glp_1_ra"
            )
            self.assertEqual(corpus.loc[0, "content_type"], "medication_table")
            self.assertIn("GI effects", corpus.loc[0, "retrieval_text"])


if __name__ == "__main__":
    unittest.main()
