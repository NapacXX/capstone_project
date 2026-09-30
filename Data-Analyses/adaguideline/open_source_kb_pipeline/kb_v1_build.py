"""Build an immutable, portable ADA KB v1 snapshot without downloading models.

The normal entry point is fail-closed: human-reviewed visual evidence is required.
``candidate=True`` produces a text/table-only development bundle, visibly marked
``review_candidate``; it is not a clinical approval or a formal release.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
MODEL_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
MODEL_DIMENSION = 384
MODEL_MAX_LENGTH = 256
# Content pins for the exact offline snapshot, not merely its directory name.
# The safetensors hash is also its Hugging Face LFS object identifier. The other
# pins freeze the tokenizer, pooling and module configuration used with it.
MODEL_FILE_SHA256 = {
    "model.safetensors": "53aa51172d142c89d9012cce15ae4d6cc0ca6895895114379cacb4fab128d9db",
    "tokenizer_config.json": "acb92769e8195aabd29b7b2137a9e6d6e25c476a4f15aa4355c233426c61576b",
    "special_tokens_map.json": "303df45a03609e4ead04bc3dc1536d0ab19b5358db685b6f3da123d05ec200e3",
    "config.json": "953f9c0d463486b10a6871cc2fd59f223b2c70184f49815e7efbcab5d8908b41",
    "config_sentence_transformers.json": "061ca9d39661d6c6d6de5ba27f79a1cd5770ea247f8d46412a68a498dc5ac9f3",
    "tokenizer.json": "be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037",
    "README.md": "dcd602d2fd35c203a247304a06fec6654a12f7941b739f9221a064fe8dc3b7f0",
    "sentence_bert_config.json": "fc1993fde0a95c24ec6c022539d41cf6e2f7c9721e5415d6fb6897472a9cd4b7",
    "vocab.txt": "07eced375cec144d27c900241f3e339478dec958f92fddbc551f295c992038a3",
    "modules.json": "84e40c8e006c9b1d6c122e02cba9b02458120b5fb0c87b746c41e0207cf642cf",
    "1_Pooling/config.json": "4be450dde3b0273bb9787637cfbd28fe04a7ba6ab9d36ac48e92b11e350ffc23",
}
SOURCE_TITLE = (
    "ADA Standards of Care in Diabetes—2026, Chapter 9: "
    "Pharmacologic Approaches to Glycemic Treatment"
)
# PDF-page numbers for the figures in this specific ADA 2026 chapter. This is
# page-level provenance, not a claim that every text chunk is figure content.
KNOWN_FIGURE_PAGES = frozenset({2, 6, 8, 9, 16})
FIGURE_PAGE_TEXT_NOTE = (
    "This PDF page contains a figure and may also contain ordinary prose. "
    "This record is PDF-extracted text, not human-approved visual evidence. "
    "Arrow directions, branches, spatial relationships, and symbol/footnote "
    "associations are not guaranteed to be preserved or verified in this text; "
    "consult the original page."
)
BASE_FILES = (
    ("full_guideline_text_chunks.csv", "text"),
    ("full_guideline_structured_tables.csv", "table"),
)
REVIEW_FILES = (
    "canonical_records.jsonl", "review_ledger.jsonl", "group_reviews.json",
    "source_inventory.json", "review_manifest.json",
)
SUPPLEMENTAL_LICENSE = Path(__file__).with_name("licenses") / "MiniLM-APACHE-2.0.txt"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"Missing CSV header: {path}")
        return list(reader)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"Expected JSON object at {path}:{number}")
        rows.append(row)
    return rows


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _page(value: Any) -> int:
    text = str(value).strip()
    if not re.fullmatch(r"[1-9][0-9]*", text):
        raise ValueError(f"Invalid PDF page number: {value!r}")
    return int(text)


def _load_base_records(kb_dir: Path, source_hash: str) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    records: list[dict[str, Any]] = []
    for filename, content_type in BASE_FILES:
        for row in _read_csv(kb_dir / filename):
            chunk_id = row.get("chunk_id", "").strip()
            source_text = row.get("retrieval_text", "")
            if not chunk_id or not source_text.strip():
                raise ValueError(f"Missing chunk ID or source text in {filename}")
            if row.get("source_pdf_sha256", "").strip().lower() != source_hash:
                raise ValueError(f"Source PDF fingerprint mismatch for {chunk_id}")
            if row.get("content_type", "") != content_type:
                raise ValueError(f"Unexpected content type for {chunk_id}")
            # Preserve original paths and all metadata; use a separate portable
            # source registry rather than rewriting the historical provenance.
            record: dict[str, Any] = dict(row)
            record.update({
                "record_id": f"{source_hash}:{chunk_id}",
                "source_id": source_hash,
                "page_number": _page(row.get("page_number")),
                "source_text": source_text,
                "retrieval_text": source_text,
                "review_status": "not_individually_reviewed",
                "release_status": "review_candidate",
                "dependency_ids": [],
                "source_file": filename,
            })
            if content_type == "text" and record["page_number"] in KNOWN_FIGURE_PAGES:
                record["source_layout_status"] = "unverified_pdf_figure_page_text"
                record["source_layout_note"] = FIGURE_PAGE_TEXT_NOTE
            records.append(record)
    if not records:
        raise ValueError("Base KB contains no text or table records")
    ids = [row["record_id"] for row in records]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate base record IDs")
    table_groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        if record["content_type"] != "table":
            continue
        table_label = record.get("table_or_figure_id", "").strip()
        if not table_label:
            raise ValueError(f"Table record lacks table ID: {record['record_id']}")
        group_id = f"{source_hash}:table:{table_label.lower().replace(' ', '-')}"
        record["evidence_group_id"] = group_id
        record["structure_status"] = "unverified_pdf_text_table"
        record["source_text_scope"] = "Page-level PDF text containing a table; not verified row/column records"
        table_groups.setdefault(group_id, []).append(record)
    for group in table_groups.values():
        group_ids = [record["record_id"] for record in group]
        # This builder targets the named ADA 2026 chapter, whose Table 9.2
        # occupies PDF pages 11–14. Do not release a partial continuation group.
        if group[0]["table_or_figure_id"].strip().lower() == "table 9.2" and {record["page_number"] for record in group} != {11, 12, 13, 14}:
            raise ValueError("Table 9.2 requires all continuation pages 11–14, including final footnotes")
        for record in group:
            record["dependency_ids"] = [identifier for identifier in group_ids if identifier != record["record_id"]]
    preview_path = kb_dir / "full_guideline_figure_pages.csv"
    previews = _read_csv(preview_path) if preview_path.exists() else []
    return records, previews


# Remove only complete, explicit administrative sentences. Partial sentences
# crossing an old chunk boundary remain untouched, rather than guessing where
# clinical prose starts or deleting a mixed paragraph wholesale.
ADMIN_SENTENCES = (
    r"©\s*20\d{2}\s+by the American Diabetes Association\.",
    r"Readers may use this work for educational, noncommercial purposes if properly cited and unaltered\.",
    r"The version of record may be linked at https://diabetesjournals\.org/care,\s*but ADA permission is required to post this work on any third-party site or platform\.",
    r"This publication and its contents may not be reproduced, distributed, or used for text or data mining, machine learning, or similar technologies without prior written permission\.",
    r"Requests to reuse, adapt, or distribute this work may be sent to permissions@diabetes\s*\.org\.",
    r"More information is available at https://\s*diabetesjournals\.org/journals/pages/license\s*\.",
)


def clean_retrieval_text(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Clean a derivative only, retaining every original record and removed span.

The bibliography boundary requires a heading followed by numbered references 1
and 2 in the same source block; an ordinary mention of references never qualifies.
"""
    log: list[dict[str, Any]] = []
    boundaries: dict[str, tuple[int, str, str]] = {}
    heading = re.compile(r"\bReferences\s+1\.\s+")
    administrative_openings: set[str] = set()
    for record in records:
        text = record["source_text"]
        if (record["page_number"] == 1 and record["content_type"] == "text"
                and record["record_id"].endswith("text_p001_001_01")
                and text.startswith("9. PHARMACOLOGIC APPROACHES TO GLYCEMIC TREATMENT")
                and "*A complete list of members" in text and "Suggested citation:" in text
                and "© 2025 by the American Diabetes Association." in text
                and "similar technologies without prior written permission." in text
                and text.endswith("More information is available at https:// di")
                and "PHARMACOLOGIC THERAPY FOR" not in text):
            administrative_openings.add(record["source_id"])
    for record in records:
        source_text = record["source_text"]
        match = heading.search(source_text)
        if match and re.search(r"\s2\.\s+[A-Z]", source_text[match.end():]):
            key = record["source_id"]
            candidate = (record["page_number"], record["record_id"], source_text[match.start():])
            if key in boundaries and boundaries[key][0] != candidate[0]:
                raise ValueError("Ambiguous multiple bibliography boundaries; manual review required")
            boundaries[key] = candidate

    # Existing records may overlap. On the boundary page, use the numeric chunk
    # suffix only for text blocks from that same block. Table records receive no
    # guessed same-page cutoff; their own explicit heading is required.
    for record in records:
        text = record["source_text"]
        original = text
        if record["source_id"] in administrative_openings and record["page_number"] == 1:
            if record["record_id"].endswith("text_p001_001_01"):
                log.append({"record_id": record["record_id"], "reason": "exact_title_author_citation_administrative_opening", "removed_text": text, "start_in_source_text": 0})
                text = ""
            elif record["record_id"].endswith("text_p001_001_02") and text.startswith("hnologies without prior written permission."):
                end = text.find("The American Diabetes Association (ADA)")
                if end > 0 and "diabetesjournals.org/journals/pages/license" in text[:end]:
                    log.append({"record_id": record["record_id"], "reason": "exact_continued_administrative_prefix_confirmed_by_previous_chunk", "removed_text": text[:end], "start_in_source_text": 0})
                    text = text[end:]
        boundary = boundaries.get(record["source_id"])
        if boundary:
            boundary_page, boundary_id, _ = boundary
            own_heading = heading.search(text)
            cutoff: int | None = None
            if record["page_number"] > boundary_page:
                cutoff = 0
            elif record["page_number"] == boundary_page:
                if own_heading:
                    cutoff = own_heading.start()
                elif record["content_type"] == "text":
                    current = re.fullmatch(r"(.+)_([0-9]+)", record["record_id"])
                    anchor = re.fullmatch(r"(.+)_([0-9]+)", boundary_id)
                    if current and anchor and current[1] == anchor[1] and int(current[2]) > int(anchor[2]):
                        cutoff = 0
            if cutoff is not None:
                removed = text[cutoff:]
                if removed:
                    log.append({"record_id": record["record_id"], "reason": "bibliography_after_explicit_heading", "removed_text": removed, "start_in_source_text": cutoff})
                text = text[:cutoff]
        for pattern in ADMIN_SENTENCES:
            def remove(match: re.Match[str]) -> str:
                log.append({"record_id": record["record_id"], "reason": "complete_administrative_sentence", "removed_text": match.group(0)})
                return " "
            text = re.sub(pattern, remove, text)
        # Do not normalize or rewrite clinical phrasing beyond whitespace around
        # a removed span; source_text remains byte-for-byte the input CSV value.
        record["retrieval_text"] = text.strip()
        record["indexable"] = bool(text.strip())
        record["cleaning_applied"] = original != text.strip()
    return log


def deduplicate_text_covered_by_tables(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Retain redundant text as audit evidence, indexing its complete table source.

    This is exact, case-sensitive containment after removing whitespace only,
    never semantic/fuzzy deduplication. The entire original text must occur on
    the same PDF page in a retained table and in that table's retrieval text.
    Short snippets and partially overlapping/mixed clinical chunks stay indexed.
    """
    tables: dict[tuple[str, int], list[dict[str, Any]]] = {}
    normalized_tables: dict[str, tuple[str, str]] = {}
    for row in records:
        if row["content_type"] != "table" or not row.get("indexable", True) or not row["retrieval_text"].strip():
            continue
        tables.setdefault((row["source_id"], row["page_number"]), []).append(row)
        normalized_tables[row["record_id"]] = (
            re.sub(r"\s+", "", row["source_text"]),
            re.sub(r"\s+", "", row["retrieval_text"]),
        )
    log = []
    for row in records:
        if row["content_type"] != "text" or not row.get("indexable", True) or not row["retrieval_text"].strip():
            continue
        source_text = row["source_text"]
        normalized = re.sub(r"\s+", "", source_text)
        if len(normalized) < 120 or len(source_text.split()) < 12:
            continue
        for table in tables.get((row["source_id"], row["page_number"]), []):
            table_source, table_retrieval = normalized_tables[table["record_id"]]
            if normalized not in table_source or normalized not in table_retrieval:
                continue
            log.append({
                "record_id": row["record_id"],
                "reason": "duplicate_text_fully_covered_by_table",
                "retained_table_id": table["record_id"],
                "source_text_sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
                "retained_table_source_text_sha256": hashlib.sha256(table["source_text"].encode("utf-8")).hexdigest(),
                "removed_text": row["retrieval_text"],
                "normalization": "whitespace_removal_only_case_sensitive",
            })
            row["retrieval_text"] = ""
            row["indexable"] = False
            row["cleaning_applied"] = True
            row["retained_table_id"] = table["record_id"]
            break
    return log


def _load_approved_visuals(review_dir: Path | None, ledger_path: Path | None = None) -> list[dict[str, Any]]:
    if review_dir is None:
        raise ValueError("Formal release requires review_dir and at least one approved visual record; use candidate=True only for a text-only development snapshot")
    import kb_v1_review

    kb_v1_review.verify_review_inputs(review_dir)
    records = _read_jsonl(review_dir / "canonical_records.jsonl")
    selected_ledger = Path(ledger_path) if ledger_path is not None else review_dir / "review_ledger.jsonl"
    if selected_ledger.suffix.lower() != ".jsonl":
        raise ValueError("Human ledger must be an explicitly imported JSONL file, not an edited CSV")
    ledger = _read_jsonl(selected_ledger)
    groups = json.loads((review_dir / "group_reviews.json").read_text(encoding="utf-8"))
    review_manifest = json.loads((review_dir / "review_manifest.json").read_text(encoding="utf-8"))
    expected = review_manifest.get("expected_record_ids")
    if (not isinstance(expected, list) or not expected
            or any(not isinstance(identifier, str) or not identifier for identifier in expected)
            or len(expected) != len(set(expected))):
        raise ValueError("Review manifest lacks a unique original candidate inventory")
    if not isinstance(groups, dict) or set(groups.get("expected_record_ids", [])) != set(expected):
        raise ValueError("Group inventory differs from the frozen review manifest candidate inventory")
    approved = kb_v1_review.assert_release_ready(records, ledger, groups)
    if not approved:
        raise ValueError("Formal release requires at least one approved visual record")
    result = []
    for original in approved:
        retrieval = kb_v1_review.to_retrieval_record(original)
        row = dict(original)
        row.update(retrieval)
        row.update({
            "record_id": original["record_id"],
            "source_id": original["source_pdf_sha256"].lower(),
            "source_text": original.get("evidence_text", ""),
            "page_number": _page(original.get("page_number")),
            "dependency_ids": original.get("v1_dependency_ids", []),
            "review_status": "approved",
            "release_status": "released",
            "indexable": True,
        })
        if not str(row.get("content_type", "")).startswith("visual") or not row.get("retrieval_text") or not row["source_text"]:
            raise ValueError(f"Approved visual lacks source evidence or retrieval representation: {row['record_id']}")
        result.append(row)
    return result


def _load_embedding_runtime(model_path: Path):
    # Set before importing the native tokenizer/BLAS runtimes. Respect explicit
    # operator choices, and avoid excess native worker creation on local Macs.
    for name, value in {"TOKENIZERS_PARALLELISM": "false", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}.items():
        os.environ.setdefault(name, value)
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(str(model_path), device="cpu", local_files_only=True, trust_remote_code=False)


def _validate_model_path(model_path: Path) -> None:
    if not model_path.is_dir() or model_path.name != MODEL_REVISION:
        raise ValueError(f"Use the local {MODEL_ID} snapshot directory named {MODEL_REVISION}; no model download is performed")
    if not (model_path / "config.json").is_file():
        raise ValueError("Local MiniLM snapshot is missing config.json")
    if not (model_path / "README.md").is_file():
        raise ValueError("Local MiniLM snapshot is missing its model card README.md")
    has_license = any(path.is_file() and path.name.lower() in {"license", "license.txt", "license.md"} for path in model_path.iterdir())
    if not has_license and not SUPPLEMENTAL_LICENSE.is_file():
        raise ValueError("MiniLM snapshot has no LICENSE and the verified supplemental licenses/MiniLM-APACHE-2.0.txt is missing")
    _validate_model_content(model_path)


def _validate_model_content(model_path: Path) -> None:
    for relative, expected in MODEL_FILE_SHA256.items():
        path = model_path / relative
        if not path.is_file() or sha256_file(path) != expected:
            raise ValueError(f"Pinned MiniLM model content mismatch: {relative}; directory name alone does not establish the revision")


def _copy_verified(source: Path, destination: Path, expected_hash: str) -> None:
    if not source.is_file() or sha256_file(source) != expected_hash:
        raise ValueError(f"Source asset missing or fingerprint changed: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    if sha256_file(destination) != expected_hash:
        raise ValueError(f"Copied asset failed integrity check: {destination}")


def _copy_sources(source_pdf: Path, source_hash: str, visuals: list[dict[str, Any]], output: Path) -> dict[str, Any]:
    pdf_relative = f"sources/{source_hash}.pdf"
    _copy_verified(source_pdf, output / pdf_relative, source_hash)
    source = {"title": SOURCE_TITLE, "year": 2026, "pdf_sha256": source_hash, "relative_path": pdf_relative, "images": []}
    copied_images = set()
    for record in visuals:
        if record["source_id"] != source_hash:
            raise ValueError("Visual record belongs to a different PDF; v1 accepts one source PDF")
        image_path = Path(record.get("image_path", ""))
        image_hash = str(record.get("image_sha256", "")).lower()
        if not re.fullmatch(r"[0-9a-f]{64}", image_hash):
            raise ValueError(f"Missing image fingerprint for visual {record['record_id']}")
        suffix = image_path.suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise ValueError(f"Unsupported visual source image format: {image_path}")
        relative = f"sources/images/{image_hash}{suffix}"
        _copy_verified(image_path, output / relative, image_hash)
        if image_hash not in copied_images:
            source["images"].append({"relative_path": relative, "sha256": image_hash, "page_number": record["page_number"]})
            copied_images.add(image_hash)
    return {source_hash: source}


def _validate_output_layout(output: Path, input_dirs: list[Path], input_files: list[Path]) -> None:
    """Reject overlapping source/destination trees before creating anything."""
    destination = output.resolve()
    for input_dir in input_dirs:
        source = input_dir.resolve()
        if destination == source or destination in source.parents or source in destination.parents:
            raise ValueError(f"Output directory overlaps an input directory: {input_dir}")
    for input_file in input_files:
        source = input_file.resolve()
        if source == destination or destination in source.parents:
            raise ValueError(f"Output directory contains an input asset: {input_file}")


def supersede_chart_base_records(records, charts, chart_only_pages=None):
    """Retain originals; suppress only reviewed-table carriers or exact spans.

    Never remove a mixed page wholesale. Source-hash-bound pages were
    visually checked as chart/table-only; their flat text is audit-only.
    Unmatched ordinary text on mixed pages remains searchable.
    """
    log = []
    chart_by_label = {row["table_or_figure_id"].lower(): row for row in charts}
    from kb_v1_chart_release import CHART_ONLY_PAGES, ADA_SOURCE_SHA256
    chart_only_pages = CHART_ONLY_PAGES if chart_only_pages is None else chart_only_pages
    by_figure = {row["figure_id"]: row for row in charts}
    for row in records:
        chart = chart_by_label.get(str(row.get("table_or_figure_id", "")).strip().lower())
        if (row["content_type"] == "text" and row["source_id"] == ADA_SOURCE_SHA256
                and row["page_number"] in chart_only_pages):
            chart = by_figure.get(chart_only_pages[row["page_number"]])
            if chart is None:
                raise ValueError("Cannot suppress chart-only page without its approved full parent")
            reason = "verified_chart_only_page_flat_text_superseded_by_complete_approved_chart"
        elif row["content_type"] == "table" and chart is not None:
            if row["page_number"] not in chart["page_numbers"]:
                raise ValueError("Superseded table source page differs from approved chart")
            if not any(r["content_type"] == "text" and r["page_number"] == row["page_number"] for r in records):
                raise ValueError("Cannot supersede a table-page carrier without same-page prose fallback")
            reason = "unverified_table_page_carrier_superseded_by_approved_whole_table"
        elif row["content_type"] == "text" and row.get("indexable", True):
            # Whitespace only; no fuzzy inference of clinical equivalence.
            text = re.sub(r"\s+", "", row["retrieval_text"])
            chart = next((c for c in charts if row["page_number"] in c["page_numbers"]
                          and len(text) >= 120 and text in re.sub(r"\s+", "", c["source_text"])), None)
            if chart is None:
                continue
            reason = "exact_text_span_covered_by_approved_whole_chart"
        else:
            continue
        log.append({"record_id": row["record_id"], "reason": reason,
                    "replacement_record_id": chart["record_id"],
                    "removed_retrieval_text": row["retrieval_text"],
                    "source_text_preserved": True})
        row.update(indexable=False, retrieval_text="", cleaning_applied=True,
                   superseded_by_chart=chart["record_id"])
    return log


def build_bundle(kb_dir, source_pdf, model_path, output_dir, review_dir=None, candidate=False, ledger_path=None, chart_workspace=None, supplement_workspace=None) -> dict[str, Any]:
    """Create a new snapshot; never overwrite, download, or approve evidence.

    A failed build retains ``.incomplete`` and never leaves a valid manifest.
    Model loading is isolated in ``_load_embedding_runtime`` for offline tests.
    """
    kb_dir, source_pdf, model_path, output = map(Path, (kb_dir, source_pdf, model_path, output_dir))
    review_dir = Path(review_dir) if review_dir is not None else None
    ledger_path = Path(ledger_path) if ledger_path is not None else None
    chart_workspace = Path(chart_workspace) if chart_workspace is not None else None
    supplement_workspace = Path(supplement_workspace) if supplement_workspace is not None else None
    if supplement_workspace is not None and chart_workspace is None:
        raise ValueError("Supplement build requires the original approved chart workspace")
    if chart_workspace is not None and (candidate or review_dir is not None or ledger_path is not None):
        raise ValueError("Whole-chart build cannot mix candidate mode or legacy review/ledger inputs")
    _validate_output_layout(
        output, [kb_dir, model_path] + ([review_dir] if review_dir is not None else []) + ([chart_workspace] if chart_workspace is not None else []) + ([supplement_workspace] if supplement_workspace is not None else []),
        [source_pdf] + ([ledger_path] if ledger_path is not None else []),
    )
    output.mkdir(parents=True, exist_ok=False)
    marker = output / ".incomplete"
    marker.write_text("Build in progress; not a release.\n", encoding="utf-8")
    try:
        if candidate and ledger_path is not None:
            raise ValueError("An explicit human ledger applies only to a formal build, not a text-only review candidate")
        source_hash = sha256_file(source_pdf)
        records, previews = _load_base_records(kb_dir, source_hash)
        cleaning_log = clean_retrieval_text(records)
        chart_release = None
        chart_replacement_log = []
        if chart_workspace is not None:
            from kb_v1_chart_release import load_chart_release
            chart_release = load_chart_release(chart_workspace, supplement_workspace=supplement_workspace)
            if chart_release["scope"]["source_pdf_sha256"] != source_hash:
                raise ValueError("Whole-chart review belongs to another source PDF")
            chart_replacement_log = supersede_chart_base_records(records, chart_release["records"], chart_release.get("chart_only_pages"))
            if supplement_workspace is not None:
                from kb_v1_chart_text import apply_supplement_text_policy
                chart_replacement_log.extend(apply_supplement_text_policy(records))
            cleaning_log.extend(chart_replacement_log)
        deduplication_log = deduplicate_text_covered_by_tables(records)
        cleaning_log.extend(deduplication_log)
        # Candidate builds deliberately do not even read unapproved visual input.
        review_input_hashes = {}
        if review_dir is not None and not candidate:
            for filename in REVIEW_FILES:
                path = ledger_path if filename == "review_ledger.jsonl" and ledger_path is not None else review_dir / filename
                review_input_hashes[filename] = sha256_file(path)
        visuals = chart_release["records"] if chart_release is not None else ([] if candidate else _load_approved_visuals(review_dir, ledger_path))
        status = "review_candidate" if candidate else "released"
        for record in records:
            record["release_status"] = status
        records.extend(visuals)
        ids = [record["record_id"] for record in records]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate IDs across base and visual records")
        _validate_model_path(model_path)
        model = _load_embedding_runtime(model_path)
        max_length = int(model.max_seq_length)
        if max_length != MODEL_MAX_LENGTH:
            raise ValueError(f"Expected MiniLM effective sequence limit {MODEL_MAX_LENGTH}, got {max_length}")
        from kb_v1_runtime import token_windows

        units = []
        for record in records:
            if not record["retrieval_text"].strip():
                continue
            windows = token_windows(record["retrieval_text"], model.tokenizer, max_length, overlap=32)
            if not windows:
                raise ValueError(f"Tokenization yielded no units for {record['record_id']}")
            for number, window in enumerate(windows, 1):
                text = window["text"]
                if not isinstance(text, str) or not text.strip():
                    raise ValueError("token_windows must return nonempty text windows")
                units.append({**window, "unit_id": f"{record['record_id']}::unit-{number:04d}", "record_id": record["record_id"], "text": text})
        if not units:
            raise ValueError("No indexable text remains")
        import numpy as np

        embeddings = np.asarray(model.encode([row["text"] for row in units], batch_size=32, show_progress_bar=False, normalize_embeddings=True), dtype="float32")
        if embeddings.shape != (len(units), MODEL_DIMENSION) or not np.isfinite(embeddings).all():
            raise ValueError("Embedding count/dimension or finite-value check failed")
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        if (norms <= 0).any():
            raise ValueError("Zero embedding vectors are not permitted")
        embeddings = np.asarray(embeddings / norms, dtype="float32")
        _write_jsonl(output / "records.jsonl", records)
        _write_jsonl(output / "units.jsonl", units)
        np.save(output / "embeddings.npy", embeddings, allow_pickle=False)
        if chart_release is None:
            source_registry = _copy_sources(source_pdf, source_hash, visuals, output)
        else:
            # Resolve assets through the frozen review packages; historical
            # machine-specific strings inside approved content stay untouched.
            source_registry = _copy_sources(source_pdf, source_hash, [], output)
            for chart in chart_release["charts"]:
                mapping = json.loads((chart["review_dir"] / "source_path_map.json").read_text(encoding="utf-8"))
                asset = mapping["image"]
                path = chart["review_dir"] / asset["relative_path"]
                relative = f"sources/images/{asset['sha256']}{path.suffix.lower()}"
                _copy_verified(path, output / relative, asset["sha256"])
                source_registry[source_hash]["images"].append({
                    "relative_path": relative, "sha256": asset["sha256"],
                    "page_number": chart["document"]["page_numbers"][0],
                    "figure_id": chart["document"]["figure_id"],
                    "coverage": "First page image only; complete page_numbers are available in the frozen PDF",
                })
            for page in chart_release["table_source_pages"]:
                relative = f"sources/images/{page['sha256']}.png"
                _copy_verified(chart_workspace / page["relative_image_path"], output / relative, page["sha256"])
                if page["sha256"] not in {image["sha256"] for image in source_registry[source_hash]["images"]}:
                    source_registry[source_hash]["images"].append({
                        "relative_path": relative, "sha256": page["sha256"],
                        "page_number": page["pdf_page"], "printed_page": page["printed_page"],
                        "figure_id": "ada2026-ch9-table-9-2",
                        "coverage": "Supplemental page image; complete frozen PDF is authoritative",
                    })
            for page in chart_release.get("supplement_source_pages", []):
                relative = f"sources/images/{page['sha256']}.png"
                _copy_verified(page["image_path"], output / relative, page["sha256"])
                if page["sha256"] not in {image["sha256"] for image in source_registry[source_hash]["images"]}:
                    source_registry[source_hash]["images"].append({
                        "relative_path": relative, "sha256": page["sha256"],
                        "page_number": page["pdf_page"], "printed_page": page["printed_page"],
                        "figure_id": page["figure_id"],
                        "coverage": "Supplemental page image; complete frozen PDF is authoritative",
                    })
        _write_json(output / "sources.json", source_registry)
        _write_jsonl(output / "audit" / "page_previews_not_indexed.jsonl", previews)
        _write_jsonl(output / "audit" / "cleaning_log.jsonl", cleaning_log)
        (output / "audit" / "base_inputs").mkdir(parents=True)
        for filename, _ in BASE_FILES:
            shutil.copyfile(kb_dir / filename, output / "audit" / "base_inputs" / filename)
        if review_dir is not None and not candidate:
            audit_review = output / "audit" / "review"
            audit_review.mkdir()
            for filename in REVIEW_FILES:
                source = ledger_path if filename == "review_ledger.jsonl" and ledger_path is not None else review_dir / filename
                _copy_verified(source, audit_review / filename, review_input_hashes[filename])
            frozen = review_dir / "frozen_inputs"
            if frozen.is_dir():
                shutil.copytree(frozen, audit_review / "frozen_inputs", symlinks=False)
        chart_audit = None
        if chart_workspace is not None:
            from kb_v1_chart_release import copy_chart_audit
            chart_audit = copy_chart_audit(chart_workspace, output, supplement_workspace=supplement_workspace)
            _write_jsonl(output / "audit" / "chart_replacement_log.jsonl", chart_replacement_log)
        # Hugging Face snapshots use symlinks into the user's cache. Dereference
        # these so a recipient does not need the publisher's filesystem/cache.
        shutil.copytree(model_path, output / "model", symlinks=False)
        _validate_model_content(output / "model")
        if not any(path.is_file() and path.name.lower() in {"license", "license.txt", "license.md"} for path in (output / "model").iterdir()):
            shutil.copyfile(SUPPLEMENTAL_LICENSE, output / "model" / "LICENSE")
        if any(path.is_symlink() for path in output.rglob("*")):
            raise ValueError("Portable bundle must not contain symlinks")
        _write_json(output / "audit" / "build_summary.json", {
            "source_scope": SOURCE_TITLE,
            "text_table_records": len(records) - len(visuals),
            "visual_records": len(visuals),
            "page_previews_not_indexed": len(previews),
            "non_indexable_records_retained": sum(not row.get("indexable", True) for row in records),
            "duplicate_text_records_covered_by_tables": len(deduplication_log),
            "cleaning_events": len(cleaning_log),
            "base_records_individually_clinically_reviewed": False,
            "candidate_visual_policy": "excluded_all" if candidate else "approved_groups_only",
            "visual_representation": "whole_chart_v1" if chart_workspace is not None else "legacy_record_v1",
            "chart_base_replacements": len(chart_replacement_log),
        })
        files = {path.relative_to(output).as_posix(): sha256_file(path) for path in sorted(output.rglob("*")) if path.is_file() and path != marker}
        version_seed = json.dumps({"files": files, "status": status, "revision": MODEL_REVISION}, sort_keys=True).encode()
        manifest = {
            "schema_version": "ada-kb-v1", "status": status,
            "kb_version": "ada2026-ch9-" + hashlib.sha256(version_seed).hexdigest()[:16],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "record_count": len(records), "unit_count": len(units),
            "visual_record_count": len(visuals),
            "embedding": {"model_id": MODEL_ID, "revision": MODEL_REVISION, "dimension": MODEL_DIMENSION, "normalize": True, "max_seq_length": max_length, "model_dir": "model", "core_file_sha256": MODEL_FILE_SHA256},
            "vector_order": "units.jsonl", "files": files,
            "scope": SOURCE_TITLE,
            "clinical_validation": "Not a clinical performance validation; text/table records are not individually approved.",
        }
        if chart_audit is not None:
            manifest["visual_representation"] = "whole_chart_v1"
            manifest["release_mode"] = "whole_chart"
            manifest["chart_audit"] = chart_audit
            manifest["release_purpose"] = "Controlled research evidence retrieval, not a clinical decision system"
        # The manifest is written last. A failed build cannot be mistaken for a
        # release simply because some output files are already present.
        _write_json(output / ".manifest.tmp", manifest)
        marker.unlink()
        (output / ".manifest.tmp").replace(output / "manifest.json")
        return manifest
    except Exception as exc:
        marker.write_text(f"INCOMPLETE: {type(exc).__name__}: {exc}\n", encoding="utf-8")
        if (output / "manifest.json").exists():
            (output / "manifest.json").unlink()
        raise
