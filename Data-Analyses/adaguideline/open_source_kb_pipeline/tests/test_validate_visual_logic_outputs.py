"""Focused tests for strict visual validation and human release gating."""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd


PIPELINE_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = PIPELINE_DIR / "07_validate_visual_logic_outputs.py"
SPEC = importlib.util.spec_from_file_location("validate_visual_logic", MODULE_PATH)
assert SPEC and SPEC.loader
validator = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = validator
SPEC.loader.exec_module(validator)

ENHANCED_MODULE_PATH = PIPELINE_DIR / "08_build_enhanced_guideline_kb.py"
ENHANCED_SPEC = importlib.util.spec_from_file_location(
    "build_enhanced_guideline_kb", ENHANCED_MODULE_PATH
)
assert ENHANCED_SPEC and ENHANCED_SPEC.loader
enhanced_builder = importlib.util.module_from_spec(ENHANCED_SPEC)
sys.modules[ENHANCED_SPEC.name] = enhanced_builder
ENHANCED_SPEC.loader.exec_module(enhanced_builder)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def registry_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    levels = ["low", "moderate", "high", "very_high", "highest"]
    for family, glyph, symbol_type, direction in (
        ("plus", "+", "relative_advantage", "context_dependent"),
        ("dollar", "$", "relative_cost", "more_is_worse"),
    ):
        for count, level in enumerate(levels, start=1):
            rows.append(
                {
                    "symbol_family": family,
                    "raw_symbol": glyph * count,
                    "symbol_type": symbol_type,
                    "symbol_count": count,
                    "ordinal_level": level,
                    "direction_default": direction,
                    "meaning": f"{level} {family}",
                    "scoring_use": "context only",
                    "manual_review_required": "true",
                }
            )
    return rows


def node(node_id: str, **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "node_id": node_id,
        "condition": f"Condition {node_id}",
        "patient_variables": ["egfr"],
        "true_branch": "",
        "false_branch": "",
        "evidence_text": f"Visible {node_id}",
        "evidence_bbox": [1, 2, 30, 40],
        "requires_manual_review": False,
    }
    value.update(overrides)
    return value


def edge(source: str, target: str, **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "from_node": source,
        "to_node": target,
        "edge_condition": "yes",
        "arrow_text": "Proceed",
        "evidence_text": "Visible arrow",
        "evidence_bbox": [10, 10, 50, 50],
        "requires_manual_review": False,
    }
    value.update(overrides)
    return value


def action(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "drug_class": "SGLT2 inhibitor",
        "action": "recommend",
        "trigger": "heart failure",
        "strength": "preferred",
        "dose_or_use_logic": "if eligible",
        "caution_or_contraindication": "volume depletion",
        "evidence_text": "Recommend SGLT2 inhibitor",
        "evidence_bbox": [5, 6, 70, 80],
        "requires_manual_review": False,
    }
    value.update(overrides)
    return value


def ordinal_symbol(raw_symbol: str = "$$", **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "row_label": "SGLT2 inhibitor",
        "clinical_dimension": "cost",
        "raw_symbol": raw_symbol,
        "symbol_type": "relative_cost",
        "symbol_count": len(raw_symbol),
        "ordinal_level": "moderate",
        "direction": "more_is_worse",
        "symbol_role": "ordinal_scale",
        "interpretation": "Moderate relative cost",
        "evidence_text": raw_symbol,
        "evidence_bbox": [1, 1, 20, 20],
        "requires_manual_review": True,
    }
    value.update(overrides)
    return value


def payload(asset_id: str = "page_009_tile_r02_c03") -> dict[str, object]:
    return {
        "asset_id": asset_id,
        "page_number": 9,
        "asset_type": "tile",
        "source_pdf": "guideline.pdf",
        "source_pdf_sha256": "a" * 64,
        "image_sha256": "b" * 64,
        "evidence_bbox_space": "normalized_0_1000",
        "visual_items": [
            {
                "item_id": f"{asset_id}__structure__figure_9_4",
                "item_type": "flowchart",
                "title": "Figure 9.4",
                "clinical_scope": "Type 2 diabetes treatment",
                "symbols_or_footnotes": [],
                "ordinal_symbols": [ordinal_symbol()],
                "decision_nodes": [node("n1"), node("n2")],
                "recommendation_edges": [edge("n1", "n2")],
                "drug_actions": [action()],
                "retrieval_summary": "Decision flow",
            }
        ],
        "extraction_warnings": [],
    }


class Fixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.raw = root / "raw"
        self.output = root / "structured"
        self.raw.mkdir()
        self.output.mkdir()
        self.image = root / "asset.png"
        self.pdf = root / "guideline.pdf"
        self.image.write_bytes(b"image-bytes")
        self.pdf.write_bytes(b"pdf-bytes")
        self.registry = root / "registry.csv"
        pd.DataFrame(registry_rows()).to_csv(self.registry, index=False)
        self.asset_id = "page_009_tile_r02_c03"
        self.json_path = self.raw / "logic.json"
        self.manifest_path = self.raw / "visual_logic_manifest.csv"

    def write(self, value: dict[str, object], **manifest_overrides: object) -> None:
        value = dict(value)
        value.update(
            {
                "source_pdf": str(self.pdf),
                "source_pdf_sha256": digest(self.pdf.read_bytes()),
                "image_sha256": digest(self.image.read_bytes()),
                "evidence_bbox_space": "normalized_0_1000",
            }
        )
        self.json_path.write_text(json.dumps(value), encoding="utf-8")
        row: dict[str, object] = {
            "asset_id": self.asset_id,
            "page_number": 9,
            "asset_type": "tile",
            "image_path": str(self.image),
            "image_sha256": digest(self.image.read_bytes()),
            "evidence_bbox_space": "normalized_0_1000",
            "source_pdf": str(self.pdf),
            "source_pdf_sha256": digest(self.pdf.read_bytes()),
            "bbox_x0": 100,
            "bbox_y0": 200,
            "bbox_x1": 500,
            "bbox_y1": 800,
            "visual_logic_json": str(self.json_path),
            "status": "extracted_unvalidated",
            "figure_ids": "Figure 9.4",
            "table_ids": "",
        }
        row.update(manifest_overrides)
        pd.DataFrame([row]).to_csv(self.manifest_path, index=False)

    def run(
        self,
        approval_csv: Path | None = None,
        *extra_args: str,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        command = [
            sys.executable,
            str(MODULE_PATH),
            "--raw-dir",
            str(self.raw),
            "--output-dir",
            str(self.output),
            "--symbol-registry",
            str(self.registry),
        ]
        if approval_csv:
            command.extend(["--approval-csv", str(approval_csv)])
        command.extend(extra_args)
        return subprocess.run(command, text=True, capture_output=True, check=check)


class VisualValidationTests(unittest.TestCase):
    def test_valid_candidates_are_stable_scoped_and_not_released_without_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            fixture.write(payload())
            fixture.run()
            candidates = pd.read_csv(fixture.output / "visual_candidate_retrieval_records.csv")
            released = pd.read_csv(fixture.output / "visual_retrieval_records.csv")
            approvals = pd.read_csv(fixture.output / "visual_review_approvals.csv")
            self.assertEqual(len(candidates), 5)
            self.assertEqual(len(released), 0)
            self.assertEqual(len(approvals), 5)
            self.assertTrue(candidates["record_id"].str.startswith("p009-page-009-tile-r02-c03::").all())
            first_ids = candidates["record_id"].tolist()
            fixture.run()
            second = pd.read_csv(fixture.output / "visual_candidate_retrieval_records.csv")
            self.assertEqual(first_ids, second["record_id"].tolist())

    def test_empty_model_title_uses_manifest_identifier_without_mutating_raw_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            value = payload()
            value["visual_items"][0]["title"] = ""  # type: ignore[index]
            fixture.write(value, table_ids="Table 9.2")
            fixture.run()

            candidates = pd.read_csv(
                fixture.output / "visual_candidate_retrieval_records.csv"
            )
            nodes = pd.read_csv(fixture.output / "visual_decision_nodes.csv")
            raw = json.loads(fixture.json_path.read_text(encoding="utf-8"))
            self.assertEqual(len(candidates), 5)
            self.assertTrue(
                (candidates["table_or_figure_id"] == "Figure 9.4; Table 9.2").all()
            )
            self.assertTrue((nodes["validation_status"] == "valid").all())
            self.assertTrue(
                nodes["validation_warnings"]
                .fillna("")
                .str.contains("title derived from manifest figure_ids/table_ids")
                .all()
            )
            self.assertEqual(raw["visual_items"][0]["title"], "")

    def test_empty_title_without_manifest_identifier_remains_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            value = payload()
            value["visual_items"][0]["title"] = ""  # type: ignore[index]
            fixture.write(value, figure_ids="", table_ids="")
            fixture.run()

            candidates = pd.read_csv(
                fixture.output / "visual_candidate_retrieval_records.csv"
            )
            nodes = pd.read_csv(fixture.output / "visual_decision_nodes.csv")
            self.assertTrue(candidates.empty)
            self.assertTrue(
                nodes["validation_errors"].str.contains("title is required").all()
            )

    def test_nonempty_evidence_bbox_requires_declared_normalized_space(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            value = payload()
            value["evidence_bbox_space"] = "pixels"
            fixture.write(value, evidence_bbox_space="pixels")
            fixture.run()
            candidates = pd.read_csv(
                fixture.output / "visual_candidate_retrieval_records.csv"
            )
            review = pd.read_csv(fixture.output / "visual_manual_review_queue.csv")
            self.assertTrue(candidates.empty)
            self.assertTrue(
                review["validation_errors"]
                .fillna("")
                .str.contains("evidence_bbox_space must be 'normalized_0_1000'")
                .any()
            )

    def test_normalized_evidence_bbox_rejects_coordinates_above_1000(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            value = payload()
            value["visual_items"][0]["drug_actions"][0]["evidence_bbox"] = [1, 2, 1100, 1200]  # type: ignore[index]
            fixture.write(value)
            fixture.run()
            actions = pd.read_csv(fixture.output / "visual_drug_actions.csv")
            self.assertEqual(actions.iloc[0]["validation_status"], "invalid")
            self.assertIn(
                "normalized range 0-1000",
                actions.iloc[0]["validation_errors"],
            )

    def test_human_approval_releases_only_the_approved_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            fixture.write(payload())
            fixture.run()
            approval_path = fixture.output / "visual_review_approvals.csv"
            approvals = pd.read_csv(approval_path, dtype=str, keep_default_na=False)
            approvals.loc[0, ["review_status", "reviewer", "reviewed_at"]] = [
                "approved",
                "clinician@example.org",
                "2026-08-13T10:30:00+00:00",
            ]
            approvals.to_csv(approval_path, index=False)
            fixture.run()
            released = pd.read_csv(fixture.output / "visual_release_records.csv")
            self.assertEqual(len(released), 1)
            self.assertEqual(released.loc[0, "review_status"], "approved")
            self.assertEqual(released.loc[0, "reviewer"], "clinician@example.org")
            self.assertEqual(released.loc[0, "source_pdf"], str(fixture.pdf))
            self.assertTrue(bool(released.loc[0, "human_approved"]))
            self.assertEqual(released.loc[0, "approval_status"], "approved")
            self.assertEqual(released.loc[0, "release_status"], "released")
            self.assertFalse(bool(released.loc[0, "requires_manual_review"]))

            # Exercise the real downstream gate, not merely the release CSV.
            downstream, excluded = enhanced_builder.load_visual_kb(fixture.output)
            self.assertEqual(len(downstream), 1)
            self.assertEqual(excluded, 4)

    def test_stale_approval_is_invalidated_when_source_record_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            value = payload()
            fixture.write(value)
            fixture.run()
            approval_path = fixture.output / "visual_review_approvals.csv"
            approvals = pd.read_csv(approval_path, dtype=str, keep_default_na=False)
            approvals.loc[:, "review_status"] = "approved"
            approvals.loc[:, "reviewer"] = "reviewer"
            approvals.loc[:, "reviewed_at"] = "2026-08-13T10:30:00Z"
            approvals.to_csv(approval_path, index=False)
            value["visual_items"][0]["drug_actions"][0]["trigger"] = "heart failure or CKD"  # type: ignore[index]
            fixture.write(value)
            fixture.run()
            updated = pd.read_csv(approval_path, dtype=str, keep_default_na=False)
            action_row = updated[updated.record_type == "action"].iloc[0]
            self.assertEqual(action_row.review_status, "pending")
            self.assertIn("Content changed", action_row.review_notes)

    def test_page_asset_hash_and_file_provenance_mismatches_invalidate_all_children(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            value = payload(asset_id="wrong-asset")
            value["page_number"] = 10
            fixture.write(value, image_sha256="0" * 64)
            fixture.run()
            nodes = pd.read_csv(fixture.output / "visual_decision_nodes.csv")
            self.assertTrue((nodes.validation_status == "invalid").all())
            errors = " ".join(nodes.validation_errors.tolist())
            self.assertIn("does not match manifest asset_id", errors)
            self.assertIn("does not match manifest page_number", errors)
            self.assertIn("image_sha256 does not match", errors)

    def test_source_pdf_provenance_must_match_manifest_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            value = payload()
            fixture.write(value)
            on_disk = json.loads(fixture.json_path.read_text(encoding="utf-8"))
            on_disk["source_pdf"] = "a-different-guideline.pdf"
            fixture.json_path.write_text(json.dumps(on_disk), encoding="utf-8")
            fixture.run()
            nodes = pd.read_csv(fixture.output / "visual_decision_nodes.csv")
            self.assertTrue((nodes.validation_status == "invalid").all())
            self.assertIn(
                "payload source_pdf", " ".join(nodes.validation_errors.tolist())
            )

    def test_missing_source_pdf_invalidates_every_child(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            fixture.write(payload())
            fixture.pdf.unlink()
            fixture.run()
            nodes = pd.read_csv(fixture.output / "visual_decision_nodes.csv")
            self.assertTrue((nodes.validation_status == "invalid").all())
            self.assertIn(
                "source_pdf does not exist or is not a file",
                " ".join(nodes.validation_errors.tolist()),
            )

    def test_replacing_source_and_updating_declared_hashes_invalidates_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            value = payload()
            fixture.write(value)
            fixture.run()
            approval_path = fixture.output / "visual_review_approvals.csv"
            approvals = pd.read_csv(approval_path, dtype=str, keep_default_na=False)
            approvals.loc[:, "review_status"] = "approved"
            approvals.loc[:, "reviewer"] = "reviewer"
            approvals.loc[:, "reviewed_at"] = "2026-08-13T10:30:00Z"
            approvals.to_csv(approval_path, index=False)

            # A replacement that updates both manifest and payload hashes still
            # represents a different source and therefore needs fresh review.
            fixture.pdf.write_bytes(b"replacement-pdf-bytes")
            fixture.write(value)
            fixture.run()
            updated = pd.read_csv(approval_path, dtype=str, keep_default_na=False)
            self.assertTrue((updated.review_status == "pending").all())
            self.assertTrue((updated.reviewer == "").all())

    def test_false_string_is_not_treated_as_true_and_is_structurally_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            value = payload()
            value["visual_items"][0]["decision_nodes"][0]["requires_manual_review"] = "false"  # type: ignore[index]
            fixture.write(value)
            fixture.run()
            nodes = pd.read_csv(fixture.output / "visual_decision_nodes.csv")
            row = nodes[nodes.node_id == "n1"].iloc[0]
            self.assertFalse(bool(row.model_requires_manual_review))
            self.assertEqual(row.validation_status, "valid")
            self.assertIn("coerced to boolean", row.validation_warnings)

    def test_duplicate_nodes_and_unresolved_graph_endpoints_are_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            value = payload()
            item = value["visual_items"][0]  # type: ignore[index]
            item["decision_nodes"] = [node("same"), node("same")]
            item["recommendation_edges"] = [edge("same", "missing")]
            fixture.write(value)
            fixture.run()
            nodes = pd.read_csv(fixture.output / "visual_decision_nodes.csv")
            edges = pd.read_csv(fixture.output / "visual_recommendation_edges.csv")
            self.assertTrue(nodes.validation_errors.str.contains("duplicate node_id").all())
            self.assertIn("does not resolve", edges.loc[0, "validation_errors"])

    def test_registry_enforces_glyph_count_type_level_and_direction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            value = payload()
            item = value["visual_items"][0]  # type: ignore[index]
            item["ordinal_symbols"] = [
                ordinal_symbol(
                    "$$$",
                    symbol_count=2,
                    symbol_type="relative_advantage",
                    ordinal_level="moderate",
                    direction="more_is_better",
                )
            ]
            fixture.write(value)
            fixture.run()
            symbols = pd.read_csv(fixture.output / "visual_ordinal_symbols.csv")
            errors = symbols.loc[0, "validation_errors"]
            self.assertIn("does not match glyph count", errors)
            self.assertIn("does not match registry value", errors)
            self.assertEqual(symbols.loc[0, "validation_status"], "invalid")

    def test_single_plus_presence_marker_is_not_registry_ordinal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            value = payload()
            item = value["visual_items"][0]  # type: ignore[index]
            item["ordinal_symbols"] = [
                ordinal_symbol(
                    "+",
                    row_label="+HF",
                    clinical_dimension="",
                    symbol_type="presence_marker",
                    symbol_count=1,
                    ordinal_level="unknown",
                    direction="not_applicable",
                    symbol_role="presence_marker",
                    interpretation="Heart failure present",
                )
            ]
            fixture.write(value)
            fixture.run()
            symbols = pd.read_csv(fixture.output / "visual_ordinal_symbols.csv")
            self.assertEqual(symbols.loc[0, "validation_status"], "valid")
            self.assertNotIn("symbol registry", str(symbols.loc[0, "validation_errors"]))

    def test_ambiguous_single_plus_requires_explicit_role(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            value = payload()
            symbol = ordinal_symbol("+", symbol_count=1, ordinal_level="low")
            symbol.pop("symbol_role")
            value["visual_items"][0]["ordinal_symbols"] = [symbol]  # type: ignore[index]
            fixture.write(value)
            fixture.run()
            symbols = pd.read_csv(fixture.output / "visual_ordinal_symbols.csv")
            self.assertIn("single '+' is ambiguous", symbols.loc[0, "validation_errors"])

    def test_unknown_evidence_bbox_is_valid_but_forced_to_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            value = payload()
            value["visual_items"][0]["drug_actions"][0]["evidence_bbox"] = []  # type: ignore[index]
            fixture.write(value)
            fixture.run()
            actions = pd.read_csv(fixture.output / "visual_drug_actions.csv")
            self.assertEqual(actions.loc[0, "validation_status"], "valid")
            self.assertIn("unknown", actions.loc[0, "validation_warnings"])
            self.assertTrue(bool(actions.loc[0, "requires_manual_review"]))

    def test_warnings_and_child_review_flags_propagate_to_review_queue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            value = payload()
            value["extraction_warnings"] = ["arrow crosses tile boundary"]
            value["visual_items"][0]["drug_actions"][0]["requires_manual_review"] = True  # type: ignore[index]
            fixture.write(value)
            fixture.run()
            queue = pd.read_csv(fixture.output / "visual_manual_review_queue.csv")
            action_rows = queue[queue.record_type == "action"]
            self.assertEqual(len(action_rows), 1)
            reason = action_rows.iloc[0].review_reason
            self.assertIn("model marked", reason)
            self.assertIn("arrow crosses tile boundary", reason)

    def test_empty_categories_are_headerful_and_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            value = payload()
            item = value["visual_items"][0]  # type: ignore[index]
            for field in (
                "decision_nodes",
                "recommendation_edges",
                "drug_actions",
                "symbols_or_footnotes",
                "ordinal_symbols",
            ):
                item[field] = []
            fixture.write(value)
            fixture.run()
            expected = {
                "visual_decision_nodes.csv": "node_id",
                "visual_recommendation_edges.csv": "from_node",
                "visual_drug_actions.csv": "drug_class",
                "visual_symbols_footnotes.csv": "symbol",
                "visual_ordinal_symbols.csv": "raw_symbol",
                "visual_retrieval_records.csv": "chunk_id",
            }
            for filename, column in expected.items():
                with self.subTest(filename=filename):
                    frame = pd.read_csv(fixture.output / filename)
                    self.assertEqual(len(frame), 0)
                    self.assertIn(column, frame.columns)

    def test_exact_same_page_semantic_duplicate_across_assets_is_invalid(self) -> None:
        def duplicate(asset_id: str) -> dict[str, object]:
            row: dict[str, object] = {
                "record_id": asset_id,
                "asset_id": asset_id,
                "page_number": 9,
                "source_pdf_sha256": "a" * 64,
                "_record_type": "action",
                "_errors": [],
                "_warnings": [],
                "_approval_valid": True,
                "drug_class": "SGLT2 inhibitor",
                "action": "recommend",
                "trigger": "heart failure",
                "strength": "preferred",
                "dose_or_use_logic": "if eligible",
                "caution_or_contraindication": "volume depletion",
                "evidence_text": "Recommend SGLT2 inhibitor",
            }
            validator.finalize_record(row)
            return row

        records = [duplicate("tile-left"), duplicate("tile-right")]
        validator.validate_cross_asset_semantic_duplicates(records)
        self.assertTrue(all(row["validation_status"] == "invalid" for row in records))
        self.assertTrue(all(not row["release_eligible"] for row in records))
        self.assertIn("multiple assets", str(records[0]["validation_errors"]))

    def test_cli_release_and_candidate_requirements_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            fixture.write(payload())
            valid = fixture.run(None, "--require-valid-candidates")
            self.assertEqual(valid.returncode, 0)
            unreleased = fixture.run(
                None, "--require-released-records", check=False
            )
            self.assertNotEqual(unreleased.returncode, 0)
            self.assertIn("no human-approved visual records", unreleased.stderr)
            fixture.write(payload(), image_sha256="0" * 64)
            invalid = fixture.run(None, "--require-valid-candidates", check=False)
            self.assertNotEqual(invalid.returncode, 0)
            self.assertIn("no valid visual candidates", invalid.stderr)

    def test_correction_requires_second_review_of_the_effective_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            value = payload()
            fixture.write(value)
            fixture.run()
            approval_path = fixture.output / "visual_review_approvals.csv"
            approvals = pd.read_csv(approval_path, dtype=str, keep_default_na=False)
            action_index = approvals.index[approvals.record_type == "action"][0]
            approvals.loc[action_index, "review_status"] = "approved"
            approvals.loc[action_index, "reviewer"] = "clinical-reviewer"
            approvals.loc[action_index, "reviewed_at"] = "2026-08-13T10:30:00Z"
            approvals.loc[action_index, "corrections_json"] = json.dumps(
                {"trigger": "heart failure with preserved or reduced ejection fraction"}
            )
            approvals.to_csv(approval_path, index=False, quoting=csv.QUOTE_MINIMAL)
            fixture.run()
            actions = pd.read_csv(fixture.output / "visual_drug_actions.csv")
            released = pd.read_csv(fixture.output / "visual_release_records.csv")
            self.assertEqual(
                actions.loc[0, "trigger"],
                "heart failure with preserved or reduced ejection fraction",
            )
            self.assertEqual(len(released), 0)

            # The correction is preserved, but its new effective fingerprint is
            # pending until a reviewer explicitly approves that exact content.
            updated = pd.read_csv(approval_path, dtype=str, keep_default_na=False)
            action_index = updated.index[updated.record_type == "action"][0]
            self.assertEqual(updated.loc[action_index, "review_status"], "pending")
            self.assertEqual(updated.loc[action_index, "reviewer"], "")
            self.assertIn("preserved or reduced", updated.loc[action_index, "corrections_json"])
            self.assertNotEqual(
                updated.loc[action_index, "content_sha256"],
                approvals.loc[action_index, "content_sha256"],
            )

            updated.loc[action_index, ["review_status", "reviewer", "reviewed_at"]] = [
                "approved",
                "clinical-reviewer",
                "2026-08-13T10:45:00Z",
            ]
            updated.to_csv(approval_path, index=False, quoting=csv.QUOTE_MINIMAL)
            fixture.run()
            released = pd.read_csv(fixture.output / "visual_release_records.csv")
            self.assertEqual(len(released), 1)

    def test_editing_a_correction_after_approval_invalidates_and_preserves_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            fixture.write(payload())
            fixture.run()
            approval_path = fixture.output / "visual_review_approvals.csv"
            approvals = pd.read_csv(approval_path, dtype=str, keep_default_na=False)
            action_index = approvals.index[approvals.record_type == "action"][0]
            approvals.loc[action_index, "corrections_json"] = json.dumps(
                {"trigger": "reviewed correction one"}
            )
            approvals.to_csv(approval_path, index=False)
            fixture.run()

            approvals = pd.read_csv(approval_path, dtype=str, keep_default_na=False)
            action_index = approvals.index[approvals.record_type == "action"][0]
            approvals.loc[action_index, ["review_status", "reviewer", "reviewed_at"]] = [
                "approved",
                "clinical-reviewer",
                "2026-08-13T10:45:00Z",
            ]
            approvals.to_csv(approval_path, index=False)
            fixture.run()
            self.assertEqual(
                len(pd.read_csv(fixture.output / "visual_release_records.csv")), 1
            )

            approvals = pd.read_csv(approval_path, dtype=str, keep_default_na=False)
            action_index = approvals.index[approvals.record_type == "action"][0]
            approvals.loc[action_index, "corrections_json"] = json.dumps(
                {"trigger": "tampered correction two"}
            )
            approvals.to_csv(approval_path, index=False)
            fixture.run()
            updated = pd.read_csv(approval_path, dtype=str, keep_default_na=False)
            action = updated[updated.record_type == "action"].iloc[0]
            self.assertEqual(action.review_status, "pending")
            self.assertEqual(action.reviewer, "")
            self.assertIn("tampered correction two", action.corrections_json)
            self.assertEqual(
                len(pd.read_csv(fixture.output / "visual_release_records.csv")), 0
            )

    def test_all_copies_of_a_duplicate_approval_id_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            approval_path = Path(directory) / "approvals.csv"
            duplicate = {
                "record_id": "duplicate-record",
                "asset_id": "asset",
                "page_number": "9",
                "record_type": "node",
                "item_id": "item",
                "local_id": "n1",
                "content_sha256": "a" * 64,
                "review_status": "approved",
                "reviewer": "reviewer",
                "reviewed_at": "2026-08-13T10:30:00Z",
                "corrections_json": "",
                "review_notes": "",
            }
            pd.DataFrame([duplicate, duplicate, duplicate]).to_csv(
                approval_path, index=False
            )
            approvals, errors = validator.load_approvals(approval_path)
            self.assertEqual(approvals, {})
            self.assertEqual(len(errors), 1)
            self.assertIn("is duplicated", errors[0])


if __name__ == "__main__":
    unittest.main()
