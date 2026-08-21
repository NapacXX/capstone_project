"""Focused tests for the review-only visual extraction stage."""

from __future__ import annotations

import importlib.util
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd
import requests


MODULE_PATH = Path(__file__).parents[1] / "06_extract_visual_guideline_logic.py"
SPEC = importlib.util.spec_from_file_location("extract_visual_logic", MODULE_PATH)
assert SPEC and SPEC.loader
extractor = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = extractor
SPEC.loader.exec_module(extractor)


def valid_payload(*, item_id: str = "node group") -> dict:
    return {
        "page_number": 999,
        "visual_items": [
            {
                "item_id": item_id,
                "item_type": "flowchart",
                "title": "Figure 9.4",
                "clinical_scope": "Type 2 diabetes",
                "symbols_or_footnotes": [],
                "ordinal_symbols": [],
                "decision_nodes": [],
                "recommendation_edges": [],
                "drug_actions": [],
                "retrieval_summary": "Visible flowchart logic.",
            }
        ],
        "extraction_warnings": [],
    }


def asset_row(image_path: Path | str = "asset.png") -> pd.Series:
    return pd.Series(
        {
            "asset_id": "page_009_tile_r02_c03",
            "page_number": 9,
            "asset_type": "tile",
            "image_path": str(image_path),
            "image_sha256": "a" * 64,
            "asset_sha256": "a" * 64,
            "evidence_bbox_space": "normalized_0_1000",
            "source_pdf": "/data/guideline.pdf",
            "source_pdf_sha256": "b" * 64,
            "bbox_x0": 120,
            "bbox_y0": 240,
            "bbox_x1": 480,
            "bbox_y1": 600,
            "figure_ids": "Figure 9.4",
            "table_ids": "",
            "page_text_preview": "Dense treatment flowchart",
        }
    )


class ExtractVisualLogicTest(unittest.TestCase):
    def test_default_model_is_direct_output_instruct_variant(self) -> None:
        self.assertEqual(extractor.DEFAULT_MODEL, "qwen3-vl:8b-instruct")

    def test_base_url_normalization(self) -> None:
        cases = {
            "http://localhost:11434": "http://localhost:11434",
            "http://localhost:11434/": "http://localhost:11434",
            "http://localhost:11434/api/generate": "http://localhost:11434",
            "http://localhost:11434/api/generate/": "http://localhost:11434",
            "https://ollama.example/prefix/api/chat": "https://ollama.example/prefix",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(extractor.ollama_base_url(raw), expected)

    def test_parse_model_json_rejects_non_object_roots(self) -> None:
        for raw in ("[]", '"text"', "null", "true"):
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, "root"):
                extractor.parse_model_json(raw)

    def test_thinking_only_response_is_not_accepted_as_guideline_evidence(self) -> None:
        with self.assertRaisesRegex(
            extractor.OllamaRequestError,
            "thinking field.*qwen3-vl:8b-instruct",
        ):
            extractor.final_response_text(
                {"response": "", "thinking": '{"page_number": 9}'}
            )

    def test_validate_response_shape_rejects_bad_nested_shapes_and_booleans(self) -> None:
        payload = valid_payload()
        payload["visual_items"][0]["decision_nodes"] = [
            {"node_id": "n1", "requires_manual_review": "false"},
            "not-an-object",
        ]
        payload["visual_items"][0]["recommendation_edges"] = "not-a-list"
        errors = extractor.validate_response_shape(payload)
        joined = "; ".join(errors)
        self.assertIn("recommendation_edges must be a list", joined)
        self.assertIn("requires_manual_review must be boolean", joined)
        self.assertIn("decision_nodes[1] must be an object", joined)

    def test_strict_schema_rejects_bool_page_number(self) -> None:
        payload = valid_payload()
        payload["page_number"] = True
        errors = extractor.validate_response_shape(payload)
        self.assertTrue(errors)
        self.assertIn("page_number", "; ".join(errors))

    def test_strict_schema_rejects_missing_required_item_field(self) -> None:
        payload = valid_payload()
        del payload["visual_items"][0]["retrieval_summary"]
        errors = extractor.validate_response_shape(payload)
        self.assertTrue(errors)
        self.assertIn("retrieval_summary", "; ".join(errors))

    def test_strict_schema_rejects_bad_enum_bbox_and_warning_type(self) -> None:
        payload = valid_payload()
        payload["extraction_warnings"] = [7]
        payload["visual_items"][0]["ordinal_symbols"] = [
            {
                "row_label": "Medication A",
                "clinical_dimension": "cost",
                "raw_symbol": "$$",
                "symbol_type": "not_a_symbol_type",
                "symbol_count": 2,
                "ordinal_level": "moderate",
                "direction": "more_is_worse",
                "interpretation": "relative cost",
                "evidence_text": "$$",
                "evidence_bbox": [1, 2, 3, 4, 5],
                "requires_manual_review": True,
            }
        ]
        errors = extractor.validate_response_shape(payload)
        joined = "; ".join(errors)
        self.assertIn("not_a_symbol_type", joined)
        self.assertIn("evidence_bbox", joined)
        self.assertIn("extraction_warnings", joined)
        self.assertIn("not of type 'string'", joined)

    def test_output_stems_abort_on_exact_duplicate_asset_ids(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate asset_id.*same-asset"):
            extractor.output_stems_for_asset_ids(["same-asset", "same-asset"])

    def test_output_stems_hash_sanitized_collisions_deterministically(self) -> None:
        asset_ids = ["page 1/a", "page_1/a", "ordinary"]
        first = extractor.output_stems_for_asset_ids(asset_ids)
        second = extractor.output_stems_for_asset_ids(asset_ids)
        self.assertEqual(first, second)
        self.assertEqual(len(first), len(set(first)))
        self.assertEqual(first[2], "ordinary")
        for asset_id, stem in zip(asset_ids[:2], first[:2]):
            expected_hash = hashlib.sha256(asset_id.encode("utf-8")).hexdigest()[:12]
            self.assertEqual(stem, f"page_1_a_{expected_hash}")

    def test_tiles_suppress_same_page_overview_unless_requested(self) -> None:
        manifest = pd.DataFrame(
            [
                {"page_number": 9, "asset_type": "page_overview", "asset_id": "overview"},
                {"page_number": 9, "asset_type": "tile", "asset_id": "tile"},
                {"page_number": 10, "asset_type": "page_overview", "asset_id": "only"},
            ]
        )
        selected = extractor.select_extraction_assets(
            manifest, include_overviews=False
        )
        self.assertEqual(selected["asset_id"].tolist(), ["tile", "only"])
        all_assets = extractor.select_extraction_assets(
            manifest, include_overviews=True
        )
        self.assertEqual(all_assets["asset_id"].tolist(), ["overview", "tile", "only"])

    def test_bbox_schema_allows_empty_or_exactly_four_coordinates(self) -> None:
        for bbox in ([], [1, 2, 3, 4]):
            with self.subTest(bbox=bbox):
                payload = valid_payload()
                payload["visual_items"][0]["decision_nodes"] = [
                    {
                        "node_id": "n1",
                        "condition": "visible condition",
                        "patient_variables": [],
                        "true_branch": "",
                        "false_branch": "",
                        "evidence_text": "visible condition",
                        "evidence_bbox": bbox,
                        "requires_manual_review": bbox == [],
                    }
                ]
                self.assertEqual(extractor.validate_response_shape(payload), [])
        for bbox in ([1], [1, 2], [1, 2, 3], [1, 2, 3, 4, 5]):
            with self.subTest(bbox=bbox):
                payload = valid_payload()
                payload["visual_items"][0]["decision_nodes"] = [
                    {
                        "node_id": "n1",
                        "condition": "visible condition",
                        "patient_variables": [],
                        "true_branch": "",
                        "false_branch": "",
                        "evidence_text": "visible condition",
                        "evidence_bbox": bbox,
                        "requires_manual_review": True,
                    }
                ]
                self.assertTrue(extractor.validate_response_shape(payload))

    def test_call_ollama_rejects_shape_and_overrides_model_page(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "asset.png"
            image.write_bytes(b"png")
            invalid = valid_payload()
            invalid["visual_items"] = "wrong"
            with patch.object(
                extractor,
                "request_json",
                return_value={"response": json.dumps(invalid)},
            ):
                with self.assertRaisesRegex(ValueError, "visual_items must be a list"):
                    extractor.call_ollama_vision(
                        image,
                        "vision",
                        "http://ollama",
                        9,
                        5,
                        "prompt",
                        "structure",
                        8192,
                        4096,
                    )

            with patch.object(
                extractor,
                "request_json",
                return_value={"response": json.dumps(valid_payload())},
            ):
                parsed, _, metrics = extractor.call_ollama_vision(
                    image,
                    "vision",
                    "http://ollama",
                    9,
                    5,
                    "prompt",
                    "structure",
                    8192,
                    4096,
                )
            self.assertEqual(parsed["page_number"], 9)
            self.assertEqual(metrics, {})

    def test_call_ollama_requests_direct_nonthinking_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "asset.png"
            image.write_bytes(b"png")
            with patch.object(
                extractor,
                "request_json",
                return_value={"response": json.dumps(valid_payload())},
            ) as request:
                extractor.call_ollama_vision(
                    image,
                    "qwen3-vl:8b-instruct",
                    "http://ollama",
                    9,
                    5,
                    "prompt",
                    "structure",
                    8192,
                    4096,
                )
        payload = request.call_args.kwargs["payload"]
        self.assertIs(payload["think"], False)
        self.assertIs(payload["stream"], False)
        self.assertEqual(payload["format"], extractor.response_schema_for_pass("structure"))
        self.assertEqual(payload["options"]["num_ctx"], 8192)
        self.assertEqual(payload["options"]["num_predict"], 4096)

    def test_call_ollama_rejects_length_truncation_before_json_parse(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "asset.png"
            image.write_bytes(b"png")
            with patch.object(
                extractor,
                "request_json",
                return_value={
                    "response": '{"page_number": 9,',
                    "done_reason": "length",
                    "eval_count": 4096,
                },
            ):
                with self.assertRaisesRegex(
                    extractor.OllamaRequestError,
                    "truncated the structure response.*4096",
                ):
                    extractor.call_ollama_vision(
                        image,
                        "vision",
                        "http://ollama",
                        9,
                        5,
                        "prompt",
                        "structure",
                        8192,
                        4096,
                    )

    def test_pass_schema_bounds_one_aggregate_item_and_relevant_children(self) -> None:
        structure = extractor.response_schema_for_pass("structure")
        visual_items = structure["properties"]["visual_items"]
        properties = visual_items["items"]["properties"]
        self.assertEqual(visual_items["maxItems"], 1)
        self.assertEqual(properties["decision_nodes"]["maxItems"], 16)
        self.assertEqual(properties["recommendation_edges"]["maxItems"], 20)
        self.assertEqual(properties["drug_actions"]["maxItems"], 0)
        self.assertEqual(properties["ordinal_symbols"]["maxItems"], 0)

    def test_request_error_preserves_http_status_and_body(self) -> None:
        response = Mock()
        response.ok = False
        response.status_code = 500
        response.text = '{"error":"unsupported mllama architecture"}'
        with patch.object(extractor.requests, "request", return_value=response):
            with self.assertRaises(extractor.OllamaRequestError) as caught:
                extractor.request_json("POST", "http://ollama/api/generate", timeout=5)
        message = str(caught.exception)
        self.assertIn("HTTP 500", message)
        self.assertIn("unsupported mllama architecture", message)

    def test_request_connection_error_is_wrapped(self) -> None:
        with patch.object(
            extractor.requests,
            "request",
            side_effect=requests.ConnectionError("connection refused"),
        ):
            with self.assertRaisesRegex(extractor.OllamaRequestError, "connection refused"):
                extractor.request_json("GET", "http://ollama/api/version", timeout=5)

    def test_preflight_missing_model_stops_before_show_or_probe(self) -> None:
        replies = [
            {"version": "0.12.0"},
            {"models": [{"name": "llava:7b", "digest": "abc"}]},
        ]
        with patch.object(extractor, "request_json", side_effect=replies) as request:
            with self.assertRaisesRegex(
                extractor.OllamaRequestError,
                "qwen3-vl:8b-instruct.*not installed",
            ):
                extractor.preflight_ollama(
                    "http://ollama", "qwen3-vl:8b-instruct", timeout=5, probe=True
                )
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[0].args[:2], ("GET", "http://ollama/api/version"))
        self.assertEqual(request.call_args_list[1].args[:2], ("GET", "http://ollama/api/tags"))

    def test_preflight_probe_requests_direct_nonthinking_output(self) -> None:
        replies = [
            {"version": "0.32.9"},
            {
                "models": [
                    {
                        "name": "qwen3-vl:8b-instruct",
                        "digest": "a" * 64,
                    }
                ]
            },
            {
                "details": {"family": "qwen3vl"},
                "model_info": {"general.architecture": "qwen3vl"},
            },
            {"response": '{"ok": true}'},
        ]
        with patch.object(extractor, "request_json", side_effect=replies) as request:
            runtime = extractor.preflight_ollama(
                "http://ollama",
                "qwen3-vl:8b-instruct",
                timeout=5,
                probe=True,
            )
        probe_payload = request.call_args_list[3].kwargs["payload"]
        self.assertIs(probe_payload["think"], False)
        self.assertIs(probe_payload["stream"], False)
        self.assertTrue(runtime["vision_probe_completed"])

    def test_asset_scoped_item_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "asset.png"
            image.write_bytes(b"png")
            row = asset_row(image)
            model_result = (
                valid_payload(item_id="node group"),
                "raw-json",
                {"done_reason": "stop", "eval_count": 10},
            )
            with patch.object(extractor, "call_ollama_vision", return_value=model_result):
                payload, status, error = extractor.extract_asset(
                    row,
                    model="vision",
                    base_url="http://ollama",
                    timeout=5,
                    prompt_mode="full",
                    passes=["structure"],
                    num_ctx=8192,
                    num_predict=4096,
                )
        self.assertEqual(status, "extracted_unvalidated")
        self.assertEqual(error, "")
        self.assertEqual(payload["raw_model_metrics"]["structure"]["eval_count"], 10)
        self.assertEqual(
            payload["visual_items"][0]["item_id"],
            "page_009_tile_r02_c03__structure__node_group",
        )

    def test_main_writes_distinct_asset_scoped_output_filenames(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "page_image_manifest.csv"
            output = root / "out"
            pd.DataFrame(
                [
                    {
                        **asset_row(root / "one.png").to_dict(),
                        "asset_id": "page_009_tile_r01_c01",
                    },
                    {
                        **asset_row(root / "two.png").to_dict(),
                        "asset_id": "page_009_tile_r01_c02",
                    },
                ]
            ).to_csv(manifest, index=False)
            result = subprocess.run(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    "--image-manifest",
                    str(manifest),
                    "--output-dir",
                    str(output),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((output / "visual_logic_page_009_tile_r01_c01.json").is_file())
            self.assertTrue((output / "visual_logic_page_009_tile_r01_c02.json").is_file())
            written = pd.read_csv(output / "visual_logic_manifest.csv")
            self.assertEqual(written["visual_logic_json"].nunique(), 2)

    def test_placeholder_preserves_step05_provenance(self) -> None:
        row = asset_row()
        payload = extractor.placeholder_payload(row, "test", "no model")
        self.assertEqual(payload["asset_id"], row["asset_id"])
        self.assertEqual(payload["page_number"], row["page_number"])
        self.assertEqual(payload["asset_type"], row["asset_type"])
        self.assertEqual(payload["source_pdf"], row["source_pdf"])
        self.assertEqual(payload["source_pdf_sha256"], row["source_pdf_sha256"])
        self.assertEqual(payload["image_sha256"], row["image_sha256"])
        self.assertEqual(payload["evidence_bbox_space"], "normalized_0_1000")

    def test_placeholder_supports_legacy_asset_sha256_fallback(self) -> None:
        row = asset_row().drop(labels=["image_sha256"])
        payload = extractor.placeholder_payload(row, "test", "no model")
        self.assertEqual(payload["image_sha256"], row["asset_sha256"])


if __name__ == "__main__":
    unittest.main()
