#!/usr/bin/env python3
"""Flatten and validate visual guideline logic JSON outputs.

This script converts page-level multimodal JSON into reviewable structured
tables for decision nodes, graph edges, drug actions, footnotes, and retrieval
records. It does not assume the vision model is always correct; review flags
are preserved for human audit.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import pandas as pd


def normalize(text: object) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def load_manifest(raw_dir: Path) -> pd.DataFrame:
    manifest_path = raw_dir / "visual_logic_manifest.csv"
    if not manifest_path.exists():
        raise FileNotFoundError(
            "Missing visual_logic_manifest.csv. "
            "Run 06_extract_visual_guideline_logic.py first."
        )
    return pd.read_csv(manifest_path)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def flatten_payload(
    payload: dict[str, Any],
    manifest_row: pd.Series,
) -> dict[str, list[dict[str, Any]]]:
    page_number = int(payload.get("page_number") or manifest_row.get("page_number"))
    source_json = str(manifest_row.get("visual_logic_json"))
    image_path = str(manifest_row.get("image_path"))
    status = normalize(manifest_row.get("status"))
    warnings = "; ".join(normalize(item) for item in as_list(payload.get("extraction_warnings")))

    rows = {
        "nodes": [],
        "edges": [],
        "actions": [],
        "footnotes": [],
        "ordinal_symbols": [],
        "retrieval": [],
        "review": [],
    }

    visual_items = as_list(payload.get("visual_items"))
    if not visual_items:
        rows["review"].append(
            {
                "page_number": page_number,
                "item_id": f"page_{page_number:03d}_json_parse_review",
                "title": normalize(manifest_row.get("figure_ids"))
                or normalize(manifest_row.get("table_ids"))
                or f"page {page_number}",
                "status": status or "json_parse_failed",
                "review_reason": warnings
                or "No structured visual_items were parsed from model output.",
                "image_path": image_path,
                "source_json": source_json,
            }
        )

    for item_index, item in enumerate(visual_items, start=1):
        item_id = normalize(item.get("item_id")) or f"page_{page_number:03d}_item_{item_index:02d}"
        item_type = normalize(item.get("item_type"))
        title = normalize(item.get("title"))
        clinical_scope = normalize(item.get("clinical_scope"))
        retrieval_summary = normalize(item.get("retrieval_summary"))

        rows["retrieval"].append(
            {
                "chunk_id": f"visual_{item_id}",
                "source_pdf": "",
                "page_number": page_number,
                "section_title": title,
                "content_type": "visual_logic",
                "table_or_figure_id": title,
                "type2_relevance_score": 3,
                "drug_class_tags": "",
                "topic_tags": "visual_logic; decision_flow",
                "retrieval_text": " ".join(
                    part
                    for part in [
                        title,
                        clinical_scope,
                        retrieval_summary,
                    ]
                    if part
                ),
                "image_path": image_path,
                "source_json": source_json,
                "extraction_status": status,
                "requires_manual_review": status != "extracted_with_ollama",
            }
        )

        for node in as_list(item.get("decision_nodes")):
            row = {
                "page_number": page_number,
                "item_id": item_id,
                "item_type": item_type,
                "title": title,
                "node_id": normalize(node.get("node_id")),
                "condition": normalize(node.get("condition")),
                "patient_variables": "; ".join(
                    normalize(value) for value in as_list(node.get("patient_variables"))
                ),
                "true_branch": normalize(node.get("true_branch")),
                "false_branch": normalize(node.get("false_branch")),
                "requires_manual_review": bool(node.get("requires_manual_review")),
                "image_path": image_path,
                "source_json": source_json,
            }
            rows["nodes"].append(row)

        for edge in as_list(item.get("recommendation_edges")):
            row = {
                "page_number": page_number,
                "item_id": item_id,
                "item_type": item_type,
                "title": title,
                "from_node": normalize(edge.get("from_node")),
                "to_node": normalize(edge.get("to_node")),
                "edge_condition": normalize(edge.get("edge_condition")),
                "arrow_text": normalize(edge.get("arrow_text")),
                "requires_manual_review": bool(edge.get("requires_manual_review")),
                "image_path": image_path,
                "source_json": source_json,
            }
            rows["edges"].append(row)

        for action in as_list(item.get("drug_actions")):
            row = {
                "page_number": page_number,
                "item_id": item_id,
                "item_type": item_type,
                "title": title,
                "drug_class": normalize(action.get("drug_class")),
                "action": normalize(action.get("action")),
                "trigger": normalize(action.get("trigger")),
                "strength": normalize(action.get("strength")),
                "dose_or_use_logic": normalize(action.get("dose_or_use_logic")),
                "caution_or_contraindication": normalize(
                    action.get("caution_or_contraindication")
                ),
                "requires_manual_review": bool(action.get("requires_manual_review")),
                "image_path": image_path,
                "source_json": source_json,
            }
            rows["actions"].append(row)

        for footnote in as_list(item.get("symbols_or_footnotes")):
            row = {
                "page_number": page_number,
                "item_id": item_id,
                "item_type": item_type,
                "title": title,
                "symbol": normalize(footnote.get("symbol")),
                "meaning": normalize(footnote.get("meaning")),
                "applies_to": normalize(footnote.get("applies_to")),
                "image_path": image_path,
                "source_json": source_json,
            }
            rows["footnotes"].append(row)

        for symbol in as_list(item.get("ordinal_symbols")):
            raw_symbol = normalize(symbol.get("raw_symbol"))
            row = {
                "page_number": page_number,
                "item_id": item_id,
                "item_type": item_type,
                "title": title,
                "row_label": normalize(symbol.get("row_label")),
                "clinical_dimension": normalize(symbol.get("clinical_dimension")),
                "raw_symbol": raw_symbol,
                "symbol_type": normalize(symbol.get("symbol_type")),
                "symbol_count": symbol.get("symbol_count"),
                "ordinal_level": normalize(symbol.get("ordinal_level")),
                "direction": normalize(symbol.get("direction")),
                "interpretation": normalize(symbol.get("interpretation")),
                "requires_manual_review": bool(symbol.get("requires_manual_review")),
                "image_path": image_path,
                "source_json": source_json,
            }
            rows["ordinal_symbols"].append(row)
            if raw_symbol:
                rows["retrieval"].append(
                    {
                        "chunk_id": f"visual_symbol_{item_id}_{len(rows['ordinal_symbols']):03d}",
                        "source_pdf": "",
                        "page_number": page_number,
                        "section_title": title,
                        "content_type": "visual_ordinal_symbol",
                        "table_or_figure_id": title,
                        "type2_relevance_score": 1,
                        "drug_class_tags": "",
                        "topic_tags": "visual_symbol; ordinal_scale",
                        "retrieval_text": " ".join(
                            part
                            for part in [
                                title,
                                row["row_label"],
                                row["clinical_dimension"],
                                row["raw_symbol"],
                                row["ordinal_level"],
                                row["direction"],
                                row["interpretation"],
                            ]
                            if part
                        ),
                        "image_path": image_path,
                        "source_json": source_json,
                        "extraction_status": status,
                        "requires_manual_review": row["requires_manual_review"],
                    }
                )

        if warnings or status != "extracted_with_ollama":
            rows["review"].append(
                {
                    "page_number": page_number,
                    "item_id": item_id,
                    "title": title,
                    "status": status,
                    "review_reason": warnings or "Vision extraction was not completed.",
                    "image_path": image_path,
                    "source_json": source_json,
                }
            )

    return rows


def write_frame(path: Path, rows: list[dict[str, Any]]) -> None:
    pd.DataFrame(rows).to_csv(path, index=False)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", default="outputs/visual_logic_raw")
    parser.add_argument("--output-dir", default="outputs/visual_logic_structured")
    args = parser.parse_args()

    raw_dir = Path(args.raw_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest = load_manifest(raw_dir)
    combined = {
        "nodes": [],
        "edges": [],
        "actions": [],
        "footnotes": [],
        "ordinal_symbols": [],
        "retrieval": [],
        "review": [],
    }

    for _, row in manifest.iterrows():
        payload = read_json(Path(str(row["visual_logic_json"])))
        flattened = flatten_payload(payload, row)
        for key, rows in flattened.items():
            combined[key].extend(rows)

    write_frame(output_dir / "visual_decision_nodes.csv", combined["nodes"])
    write_frame(output_dir / "visual_recommendation_edges.csv", combined["edges"])
    write_frame(output_dir / "visual_drug_actions.csv", combined["actions"])
    write_frame(output_dir / "visual_symbols_footnotes.csv", combined["footnotes"])
    write_frame(output_dir / "visual_ordinal_symbols.csv", combined["ordinal_symbols"])
    write_frame(output_dir / "visual_retrieval_records.csv", combined["retrieval"])
    write_frame(output_dir / "visual_manual_review_queue.csv", combined["review"])

    summary = {
        "n_visual_pages": int(len(manifest)),
        "n_decision_nodes": int(len(combined["nodes"])),
        "n_recommendation_edges": int(len(combined["edges"])),
        "n_drug_actions": int(len(combined["actions"])),
        "n_symbols_or_footnotes": int(len(combined["footnotes"])),
        "n_ordinal_symbols": int(len(combined["ordinal_symbols"])),
        "n_manual_review_items": int(len(combined["review"])),
    }
    (output_dir / "visual_logic_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )

    print(json.dumps(summary, indent=2))
    print(f"Wrote structured visual logic outputs to {output_dir}")


if __name__ == "__main__":
    main()
