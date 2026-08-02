#!/usr/bin/env python3
"""Extract visual guideline logic from rendered ADA figure/table page images.

The preferred mode uses a local open-source vision model through Ollama, such
as llama3.2-vision. If Ollama is unavailable, the script still writes a review
queue with placeholder JSON so the rest of the workflow remains reproducible.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import re
from pathlib import Path
from typing import Any

import pandas as pd
import requests


DEFAULT_OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "llama3.2-vision"


VISUAL_EXTRACTION_PROMPT = """
You are extracting clinical guideline logic from an ADA pharmacotherapy
guideline page image. Focus on Type 2 diabetes medication selection,
contraindications, dose/use cautions, tables, flowcharts, arrows, footnotes,
and decision branches.

For plus signs and dollar signs in comparative figures, treat them as ordinal
relative indicators, not exact numeric values:
- +, ++, +++, ++++, +++++ indicate increasing relative advantage/strength.
- $, $$, $$$, $$$$, $$$$$ indicate increasing relative cost.
- More plus signs may be better or worse depending on the clinical dimension.
- For "greater flexibility" and "lower hypoglycemia risk", more is better.
- For "higher costs", more is worse.

Return only valid JSON with this schema:
{
  "page_number": null,
  "visual_items": [
    {
      "item_id": "",
      "item_type": "figure|table|flowchart|footnote|page",
      "title": "",
      "clinical_scope": "",
      "symbols_or_footnotes": [
        {
          "symbol": "",
          "meaning": "",
          "applies_to": ""
        }
      ],
      "ordinal_symbols": [
        {
          "row_label": "",
          "clinical_dimension": "",
          "raw_symbol": "",
          "symbol_type": "relative_advantage|relative_cost|other",
          "symbol_count": null,
          "ordinal_level": "low|moderate|high|very_high|highest|unknown",
          "direction": "more_is_better|more_is_worse|context_dependent",
          "interpretation": "",
          "requires_manual_review": false
        }
      ],
      "decision_nodes": [
        {
          "node_id": "",
          "condition": "",
          "patient_variables": [],
          "true_branch": "",
          "false_branch": "",
          "requires_manual_review": false
        }
      ],
      "recommendation_edges": [
        {
          "from_node": "",
          "to_node": "",
          "edge_condition": "",
          "arrow_text": "",
          "requires_manual_review": false
        }
      ],
      "drug_actions": [
        {
          "drug_class": "",
          "action": "",
          "trigger": "",
          "strength": "",
          "dose_or_use_logic": "",
          "caution_or_contraindication": "",
          "requires_manual_review": false
        }
      ],
      "retrieval_summary": ""
    }
  ],
  "extraction_warnings": []
}

Use concise wording. Extract every visible arrow as a recommendation edge.
Preserve uncertainty by setting requires_manual_review to true when an arrow,
symbol, abbreviation, footnote, row label, or condition is ambiguous.
Do not provide medical advice beyond extracting the page logic.
"""

SIMPLE_VISUAL_EXTRACTION_PROMPT = """
You are reading one ADA guideline figure/table page image. Extract only the
most visible clinical decision logic. Return valid compact JSON only.

Schema:
{
  "page_number": null,
  "visual_items": [
    {
      "item_id": "page_visual_logic",
      "item_type": "figure",
      "title": "",
      "clinical_scope": "",
      "symbols_or_footnotes": [],
      "ordinal_symbols": [],
      "decision_nodes": [
        {
          "node_id": "",
          "condition": "",
          "patient_variables": [],
          "true_branch": "",
          "false_branch": "",
          "requires_manual_review": true
        }
      ],
      "recommendation_edges": [
        {
          "from_node": "",
          "to_node": "",
          "edge_condition": "",
          "arrow_text": "",
          "requires_manual_review": true
        }
      ],
      "drug_actions": [
        {
          "drug_class": "",
          "action": "",
          "trigger": "",
          "strength": "",
          "dose_or_use_logic": "",
          "caution_or_contraindication": "",
          "requires_manual_review": true
        }
      ],
      "retrieval_summary": ""
    }
  ],
  "extraction_warnings": []
}

Rules:
- Keep lists short: at most 5 decision_nodes, 8 recommendation_edges, and 8
  drug_actions.
- Extract arrows as from_node -> to_node when visible.
- For + or $ symbols, use ordinal_symbols with raw_symbol, symbol_count,
  ordinal_level, direction, and requires_manual_review.
- If uncertain, keep the row but set requires_manual_review to true.
- Return JSON only. No markdown.
"""


def encode_image(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("utf-8")


def normalize(text: object) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def placeholder_payload(row: pd.Series, status: str, message: str) -> dict[str, Any]:
    page_number = int(row["page_number"])
    figure_ids = normalize(row.get("figure_ids"))
    table_ids = normalize(row.get("table_ids"))
    title = "; ".join(value for value in [figure_ids, table_ids] if value)
    return {
        "page_number": page_number,
        "visual_items": [
            {
                "item_id": f"page_{page_number:03d}_visual_review",
                "item_type": "page",
                "title": title,
                "clinical_scope": "ADA pharmacotherapy guideline visual content",
                "symbols_or_footnotes": [],
                "ordinal_symbols": [],
                "decision_nodes": [],
                "recommendation_edges": [],
                "drug_actions": [],
                "retrieval_summary": normalize(row.get("page_text_preview")),
            }
        ],
        "extraction_warnings": [f"{status}: {message}"],
    }


def raw_response_payload(
    row: pd.Series,
    raw_text: str,
    status: str,
    message: str,
) -> dict[str, Any]:
    payload = placeholder_payload(row, status, message)
    payload["raw_model_response"] = raw_text
    payload["extraction_warnings"].append(
        "Raw model response was saved because JSON parsing failed."
    )
    return payload


def parse_json_response(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def call_ollama_vision(
    image_path: Path,
    model: str,
    ollama_url: str,
    page_number: int,
    timeout: int,
    prompt_mode: str,
) -> dict[str, Any]:
    base_prompt = (
        SIMPLE_VISUAL_EXTRACTION_PROMPT
        if prompt_mode == "simple"
        else VISUAL_EXTRACTION_PROMPT
    )
    prompt = base_prompt + f"\nPage number: {page_number}\n"
    payload = {
        "model": model,
        "prompt": prompt,
        "images": [encode_image(image_path)],
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0,
        },
    }
    response = requests.post(ollama_url, json=payload, timeout=timeout)
    response.raise_for_status()
    raw = response.json()
    model_text = raw.get("response", "")
    try:
        parsed = parse_json_response(model_text)
    except json.JSONDecodeError as exc:
        return {
            "page_number": page_number,
            "visual_items": [],
            "raw_model_response": model_text,
            "extraction_warnings": [
                f"json_parse_failed: {exc}",
                "Raw model response was saved for manual review.",
            ],
        }
    parsed["page_number"] = parsed.get("page_number") or page_number
    parsed["raw_model_response"] = model_text
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image-manifest", default="outputs/page_images/page_image_manifest.csv")
    parser.add_argument("--output-dir", default="outputs/visual_logic_raw")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument(
        "--use-ollama",
        action="store_true",
        help="Call a local Ollama vision model. Without this, write review placeholders.",
    )
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument(
        "--prompt-mode",
        choices=["full", "simple"],
        default="full",
        help="Use simple for smaller vision models that struggle with strict JSON.",
    )
    args = parser.parse_args()

    manifest_path = Path(args.image_manifest)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not manifest_path.exists():
        raise FileNotFoundError(
            "Missing page image manifest. Run 05_render_pdf_pages_to_images.py first."
        )

    manifest = pd.read_csv(manifest_path)
    records: list[dict[str, Any]] = []

    for _, row in manifest.iterrows():
        page_number = int(row["page_number"])
        image_path = Path(str(row["image_path"]))
        out_path = output_dir / f"visual_logic_page_{page_number:03d}.json"

        status = "placeholder"
        error_message = ""
        if args.use_ollama:
            try:
                payload = call_ollama_vision(
                    image_path=image_path,
                    model=args.model,
                    ollama_url=args.ollama_url,
                    page_number=page_number,
                    timeout=args.timeout,
                    prompt_mode=args.prompt_mode,
                )
                status = (
                    "json_parse_failed"
                    if not payload.get("visual_items")
                    else "extracted_with_ollama"
                )
            except Exception as exc:  # pragma: no cover - depends on local server
                error_message = str(exc)
                payload = placeholder_payload(row, "ollama_failed", error_message)
                status = "needs_visual_model"
        else:
            payload = placeholder_payload(
                row,
                "ollama_not_used",
                "Run with --use-ollama after starting a local vision model.",
            )
            status = "needs_visual_model"

        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        records.append(
            {
                "page_number": page_number,
                "image_path": str(image_path),
                "visual_logic_json": str(out_path),
                "model": args.model if args.use_ollama else "",
                "status": status,
                "error_message": error_message,
                "figure_ids": normalize(row.get("figure_ids")),
                "table_ids": normalize(row.get("table_ids")),
            }
        )

    write_csv(
        output_dir / "visual_logic_manifest.csv",
        records,
        [
            "page_number",
            "image_path",
            "visual_logic_json",
            "model",
            "status",
            "error_message",
            "figure_ids",
            "table_ids",
        ],
    )

    print(f"Wrote visual logic JSON files for {len(records)} page images.")
    print(f"Wrote manifest to {output_dir / 'visual_logic_manifest.csv'}")


if __name__ == "__main__":
    main()
