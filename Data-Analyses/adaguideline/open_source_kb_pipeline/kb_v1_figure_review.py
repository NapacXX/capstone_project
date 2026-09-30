"""Offline, stdlib-only review of whole-figure reconstructions.

This is a separate review workflow, not a replacement for the existing 623-row
release gate and not an input automatically accepted by the KB builder.
Source blocks are unverified transcriptions until a person reviews them.
Hashes detect accidental changes; they do not authenticate a reviewer's identity.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DOCUMENT_SCHEMA = "ada-figure-reconstruction-v1"
DOCUMENT_SCHEMA_EN = "ada-figure-reconstruction-v2"
SCOPE_SCHEMA = "ada-figure-scope-v1"
SUPPLEMENT_SCOPE_SCHEMA = "ada-figure-supplement-scope-v1"
PACKAGE_SCHEMA = "ada-figure-review-package-v1"
BLOCK_KINDS = {"heading", "node", "comparison", "footnote", "caption", "abbreviations"}
PATH_KINDS = {"decision_path", "comparison", "supporting_context"}
# Fixed identity/page scope for this project's explicitly selected ADA cohort.
# This is not the entire chapter; its boundaries cannot be shrunk by creating a
# new scope file. Other PDF hashes remain available for synthetic/general use.
ADA_SOURCE_SHA256 = "7c2913f79b61bc60f8f51327bb73b80391988fd277116b29e57df4242b2a2404"
ADA_FIGURE_PAGES = {
    "ada2026-ch9-figure-9-1": [2],
    "ada2026-ch9-figure-9-2": [6],
    "ada2026-ch9-figure-9-3": [8],
    "ada2026-ch9-figure-9-4": [9],
    "ada2026-ch9-table-9-2": [11, 12, 13, 14],
    "ada2026-ch9-figure-9-5": [16],
    "ada2026-ch9-table-9-3": [21],
}
# A separately versioned, review-only addition; never redefine the original
# seven-group cohort or imply that its approvals also cover these tables.
ADA_SUPPLEMENT_PAGES = {
    "ada2026-ch9-table-9-1": [3, 4],
    "ada2026-ch9-table-9-4": [22],
}
SUPPLEMENT_SCOPE_ID = "ada2026-ch9-supplement-tables-9-1-9-4-v1"
SUPPLEMENT_PARENT = {
    "scope_id": "ada2026-ch9-existing-visual-cohort-7-groups-v1",
    "scope_manifest_sha256": "504fae0f4f1846c702cfca7cafdb51ae90ddd53e792dcf4d803cc5a5ea81593f",
    "relationship": "supplement_only_not_replacement",
}
SUPPLEMENT_LINEAGE_HASHES = {
    "canonical_records_sha256": "d796a62929d7b40975fc457de7914bade1d0f8967f202077071f16ddcc95185a",
    "latest_human_ledger_sha256": "2950852caf4843f2e3e4f59953ce61b96a23de5d9b6ab95a9b995c4c29b0ab44",
}
DECISION_STATUSES = {"pending", "corrections_required", "rejected", "approved"}
DOCUMENT_FIELDS = {
    "schema_version", "figure_id", "title", "revision", "source_pdf", "source_pdf_sha256",
    "page_numbers", "printed_pages", "image_path", "image_sha256", "description_zh",
    "generation_note", "source_blocks", "paths", "symbols", "open_questions", "review_checklist",
}
DOCUMENT_FIELDS_EN = (DOCUMENT_FIELDS - {"description_zh"}) | {"language", "description_en"}
# A leakage check, NOT general English-language identification. Ranges cover
# Han (including both supplementary ideographic planes), CJK radicals/compatibility forms,
# kana and Hangul. Latin/Greek letters, clinical symbols and source paths remain
# permitted. Other non-English languages are not certified or excluded here.
CJK_TEXT_RE = re.compile(
    "[\u1100-\u11ff\u2e80-\u303f\u3040-\u30ff\u3130-\u318f\u31f0-\u31ff"
    "\u3400-\u4dbf\u4e00-\u9fff\ua960-\ua97f\uac00-\ud7af\ud7b0-\ud7ff"
    "\uf900-\ufaff\uff66-\uff9f\U00020000-\U0003ffff]"
)
DECISION_FIELDS = {
    "figure_id", "status", "reviewer", "reviewed_at", "reviewed_content_sha256",
    "checklist_confirmations", "notes",
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def content_fingerprint(document: dict[str, Any]) -> str:
    """Bind the entire document, including transcriptions, AI text and questions."""
    return hashlib.sha256(canonical_json(document).encode("utf-8")).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: str | Path) -> dict[str, Any]:
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON field: {key}")
            result[key] = value
        return result

    value = json.loads(Path(path).read_text(encoding="utf-8-sig"), object_pairs_hook=unique_keys)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def _fields(value: Any, required: set[str], label: str) -> None:
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError(f"{label}: fields must be exactly {sorted(required)}")


def _text(value: Any, label: str, allow_empty: bool = False) -> None:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError(f"{label}: expected {'possibly empty ' if allow_empty else 'nonempty '}string")


def _strings(value: Any, label: str, nonempty: bool = True, unique: bool = True) -> None:
    if not isinstance(value, list) or (nonempty and not value):
        raise ValueError(f"{label}: expected {'nonempty ' if nonempty else ''}list")
    for item in value:
        _text(item, label)
    if unique and len(set(value)) != len(value):
        raise ValueError(f"{label}: duplicate entries")


def _validate_english_strings(document: dict[str, Any]) -> None:
    """Reject CJK leakage in v2 content, without claiming language detection."""
    def walk(value: Any, location: str) -> None:
        if isinstance(value, str):
            if CJK_TEXT_RE.search(value):
                raise ValueError(f"English v2 content contains CJK/Han text at {location}; this is a leakage check, not general language certification")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{location}[{index}]")
        elif isinstance(value, dict):
            for key, item in value.items():
                walk(item, f"{location}.{key}")

    for key, value in document.items():
        if key not in {"source_pdf", "image_path"}:
            walk(value, key)


def _hash(value: Any, label: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"{label}: expected lowercase SHA-256")


def _pages(value: Any, label: str) -> None:
    if (not isinstance(value, list) or not value or any(type(page) is not int or page < 1 for page in value)
            or value != sorted(set(value))):
        raise ValueError(f"{label}: expected sorted unique positive integer pages")


def _index(rows: Any, key: str, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(rows, list):
        raise ValueError(f"{label}: expected list")
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"{label}: expected objects")
        _text(row.get(key), f"{label}.{key}")
        if row[key] in result:
            raise ValueError(f"{label}: duplicate {key}: {row[key]}")
        result[row[key]] = row
    return result


def validate_scope(scope: dict[str, Any]) -> None:
    supplemental = isinstance(scope, dict) and scope.get("schema_version") == SUPPLEMENT_SCOPE_SCHEMA
    fields = {"schema_version", "scope_id", "source_pdf_sha256", "figures", "legacy_lineage", "scope_limitations"}
    if supplemental:
        fields |= {"parent_scope", "source_page_images"}
    _fields(scope, fields, "scope")
    if scope["schema_version"] not in {SCOPE_SCHEMA, SUPPLEMENT_SCOPE_SCHEMA}:
        raise ValueError("Unsupported scope schema")
    _text(scope["scope_id"], "scope_id")
    _hash(scope["source_pdf_sha256"], "scope.source_pdf_sha256")
    figures = _index(scope["figures"], "figure_id", "scope.figures")
    if not figures:
        raise ValueError("Scope must contain at least one figure")
    for row in figures.values():
        _fields(row, {"figure_id", "title", "page_numbers"}, "scope figure")
        _text(row["title"], "scope figure title")
        _pages(row["page_numbers"], "scope figure pages")
    if supplemental:
        if (scope["scope_id"] != SUPPLEMENT_SCOPE_ID
                or scope["source_pdf_sha256"] != ADA_SOURCE_SHA256
                or scope["parent_scope"] != SUPPLEMENT_PARENT):
            raise ValueError("Supplement must retain the exact ADA source and immutable original parent scope; it is not a replacement")
        declared_pages = {key: row["page_numbers"] for key, row in figures.items()}
        if declared_pages != ADA_SUPPLEMENT_PAGES:
            raise ValueError("Supplement requires exactly Table 9.1 pages 3-4 and Table 9.4 page 22")
        rows = scope["source_page_images"]
        if not isinstance(rows, list):
            raise ValueError("Supplement source_page_images must be a list")
        expected = {(figure, page) for figure, pages in ADA_SUPPLEMENT_PAGES.items() for page in pages}
        seen = set()
        for row in rows:
            _fields(row, {"figure_id", "page_number", "image_path", "image_sha256"}, "supplement source image")
            _text(row["figure_id"], "supplement image figure_id")
            if type(row["page_number"]) is not int:
                raise ValueError("Supplement source page must be an integer")
            identity = (row["figure_id"], row["page_number"])
            if identity in seen or identity not in expected:
                raise ValueError("Supplement source image page is duplicate or outside its table")
            seen.add(identity)
            _text(row["image_path"], "supplement image path")
            _hash(row["image_sha256"], "supplement image hash")
        if seen != expected:
            raise ValueError("Supplement source images must cover all three declared pages")
    elif scope["source_pdf_sha256"] == ADA_SOURCE_SHA256:
        declared_pages = {key: row["page_numbers"] for key, row in figures.items()}
        if declared_pages != ADA_FIGURE_PAGES:
            raise ValueError("Fixed ADA scope requires the original seven figure/table IDs and exact page groups; a smaller or reassigned scope cannot be declared complete")
    lineage = scope["legacy_lineage"]
    _fields(lineage, {"canonical_records_sha256", "record_count", "latest_human_ledger_sha256", "note"}, "legacy_lineage")
    for field in ("canonical_records_sha256", "latest_human_ledger_sha256"):
        _hash(lineage[field], f"legacy_lineage.{field}")
        if supplemental and lineage[field] != SUPPLEMENT_LINEAGE_HASHES[field]:
            raise ValueError("Supplement must retain the unchanged historical 623-record lineage")
    if type(lineage["record_count"]) is not int or lineage["record_count"] != 623:
        raise ValueError("Legacy lineage must preserve the original 623-record scope")
    _text(lineage["note"], "legacy_lineage.note")
    _strings(scope["scope_limitations"], "scope_limitations", nonempty=False)


def validate_document(document: dict[str, Any], scope: dict[str, Any]) -> None:
    validate_scope(scope)
    if not isinstance(document, dict) or document.get("schema_version") not in (DOCUMENT_SCHEMA, DOCUMENT_SCHEMA_EN):
        raise ValueError("Unsupported figure document schema")
    is_english = document["schema_version"] == DOCUMENT_SCHEMA_EN
    _fields(document, DOCUMENT_FIELDS_EN if is_english else DOCUMENT_FIELDS, "figure document")
    if is_english and document["language"] != "en":
        raise ValueError("English v2 document requires language='en'")
    description_key = "description_en" if is_english else "description_zh"
    logic_key = "logic_text_en" if is_english else "logic_text_zh"
    meaning_key = "meaning_en" if is_english else "meaning_zh"
    for key in ("figure_id", "title", "revision", "source_pdf", "image_path", description_key, "generation_note"):
        _text(document[key], key)
    for key in ("source_pdf_sha256", "image_sha256"):
        _hash(document[key], key)
    _pages(document["page_numbers"], "document.page_numbers")
    _strings(document["printed_pages"], "printed_pages")
    if len(document["printed_pages"]) != len(document["page_numbers"]):
        raise ValueError("printed_pages must match the number of PDF pages")
    selected = next((row for row in scope["figures"] if row["figure_id"] == document["figure_id"]), None)
    if selected is None:
        raise ValueError("Figure is not in the selected scope")
    if document["page_numbers"] != selected["page_numbers"] or document["title"] != selected["title"]:
        raise ValueError("Figure title/pages do not match scope")
    if document["source_pdf_sha256"] != scope["source_pdf_sha256"]:
        raise ValueError("Figure source PDF hash does not match scope")
    if scope["schema_version"] == SUPPLEMENT_SCOPE_SCHEMA:
        if not is_english:
            raise ValueError("Supplemental table review requires an English v2 document")
        primary = _supplement_images(document, scope)[0]
        if (document["image_path"] != primary["image_path"]
                or document["image_sha256"] != primary["image_sha256"]):
            raise ValueError("Supplement primary image must match its first scoped source page")
    blocks = _index(document["source_blocks"], "id", "source_blocks")
    if not blocks:
        raise ValueError("Source transcription blocks are required")
    for block in blocks.values():
        _fields(block, {"id", "kind", "region", "text"}, "source block")
        _text(block["kind"], "source block kind")
        if block["kind"] not in BLOCK_KINDS:
            raise ValueError(f"Unknown source block kind: {block['kind']}")
        _text(block["region"], "source block region")
        _text(block["text"], "source block text")
    paths = _index(document["paths"], "path_id", "paths")
    if not paths:
        raise ValueError("At least one source-bound path is required; isolated facts cannot be published")
    path_references = set()
    for row in paths.values():
        _fields(row, {"path_id", "title", "kind", "source_block_ids", "footnote_ids", logic_key, "critical_checks"}, "path")
        for key in ("title", "kind", logic_key):
            _text(row[key], f"path.{key}")
        if row["kind"] not in PATH_KINDS:
            raise ValueError(f"Unknown path kind: {row['kind']}; expected {sorted(PATH_KINDS)}")
        _strings(row["source_block_ids"], "path.source_block_ids")
        _strings(row["footnote_ids"], "path.footnote_ids", nonempty=False)
        _strings(row["critical_checks"], "path.critical_checks")
        if set(row["source_block_ids"] + row["footnote_ids"]) - set(blocks):
            raise ValueError(f"{row['path_id']}: missing source block reference")
        path_references.update(row["source_block_ids"] + row["footnote_ids"])
        if any(blocks[key]["kind"] != "footnote" for key in row["footnote_ids"]):
            raise ValueError(f"{row['path_id']}: footnote reference must name a footnote block")
        if not any(blocks[key]["kind"] in {"heading", "node", "comparison"} for key in row["source_block_ids"]):
            raise ValueError(f"{row['path_id']}: path needs a substantive source block, not only a footnote/caption")
    unreferenced = sorted(key for key, block in blocks.items()
                          if block["kind"] in {"node", "comparison", "footnote"} and key not in path_references)
    if unreferenced:
        raise ValueError(f"Substantive source blocks are missing from all paths: {', '.join(unreferenced)}; symbol references alone do not establish path context")
    symbols = _index(document["symbols"], "symbol", "symbols")
    for row in symbols.values():
        _fields(row, {"symbol", "kind", meaning_key, "source_block_ids"}, "symbol")
        _text(row["kind"], "symbol.kind")
        _text(row[meaning_key], f"symbol.{meaning_key}")
        _strings(row["source_block_ids"], "symbol.source_block_ids")
        if set(row["source_block_ids"]) - set(blocks):
            raise ValueError("Symbol has missing source block reference")
    questions = _index(document["open_questions"], "question_id", "open_questions")
    for row in questions.values():
        _fields(row, {"question_id", "severity", "text"}, "question")
        _text(row["severity"], "question.severity")
        if row["severity"] not in {"blocking", "nonblocking"}:
            raise ValueError("Question severity must be blocking or nonblocking")
        _text(row["text"], "question.text")
    _strings(document["review_checklist"], "review_checklist")
    if is_english:
        _validate_english_strings(document)


def _supplement_images(document: dict[str, Any], scope: dict[str, Any]) -> list[dict[str, Any]]:
    if scope["schema_version"] != SUPPLEMENT_SCOPE_SCHEMA:
        return []
    return sorted((row for row in scope["source_page_images"] if row["figure_id"] == document["figure_id"]),
                  key=lambda row: row["page_number"])


def _resolve_source(value: str, base: Path) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else base / path).resolve(strict=True)


def _safe_member(root: Path, value: Any) -> Path:
    _text(value, "package relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ValueError("Unsafe package path")
    # macOS may spell the enclosing temporary directory through /var ->
    # /private/var. Only links inside the review package are forbidden.
    root = root.resolve()
    target = root / path
    part = target
    while part != root:
        if part.is_symlink():
            raise ValueError("Symlinks are not allowed in review package paths")
        part = part.parent
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("Package path escapes review directory")
    return target


def _source_quote(text: str) -> str:
    """Show transcription literally, with explicit per-line Markdown breaks."""
    quoted = []
    for line in text.splitlines():
        # Protect source glyphs (notably # and *) from being interpreted as
        # headings, emphasis, lists, links or HTML inside the block quote.
        escaped = re.sub(r"([\\`*_\[\]<>#+|~])", lambda match: "\\" + match.group(0), line)
        escaped = re.sub(r"^(\s*)(-)", lambda match: match.group(1) + "\\-", escaped)
        escaped = re.sub(r"^(\s*\d{1,9})([.)])(?=\s)", lambda match: match.group(1) + "\\" + match.group(2), escaped)
        quoted.append("> " + escaped + "  " if escaped else ">")
    return "\n".join(quoted)


def _review_markdown(document: dict[str, Any], mapping: dict[str, str]) -> str:
    lines = [f"# {document['title']} — 图表级审核候选", "",
             "尚未人工批准。以下源文是待核转录，不保证准确；中文描述、路径和符号解释均为AI整理。",
             "本包只供完整图表审核，不能单独发布某条路径，也不替代原623条审核门槛。", "",
             f"图表ID：{document['figure_id']}；修订：{document['revision']}",
             f"PDF页：{document['page_numbers']}；印刷页：{document['printed_pages']}",
             f"[源PDF](<{mapping['source_pdf']}#page={document['page_numbers'][0]}>) · [整图](<{mapping['image']}>)", "",
             "## AI整理说明", "", document["description_zh"], "", document["generation_note"], "",
             "## 待核原文转录（不是已批准原文）", ""]
    for row in document["source_blocks"]:
        lines += [f"### {row['id']} · {row['kind']} · {row['region']}", "", _source_quote(row["text"]), ""]
    lines += ["## AI整理的路径（逐条核对条件与完整依赖）", ""]
    for row in document["paths"]:
        lines += [f"### {row['path_id']} · {row['title']} · {row['kind']}", "", row["logic_text_zh"], "",
                  f"源文块：{', '.join(row['source_block_ids'])}", f"脚注块：{', '.join(row['footnote_ids']) or '未列出；需人工核对是否真的没有'}", "",
                  "关键核对项：", "", *[f"- {item}" for item in row["critical_checks"]], ""]
    lines += ["## 符号与关系", ""]
    for row in document["symbols"]:
        lines += [f"- {row['symbol']} · {row['kind']}：{row['meaning_zh']}；源文块：{', '.join(row['source_block_ids'])}"]
    lines += ["", "## 待解决问题", ""]
    lines += [f"- {row['question_id']} [{row['severity']}]：{row['text']}" for row in document["open_questions"]]
    if not document["open_questions"]:
        lines += ["未列出问题不等于已证明无错误，仍须人工核对。"]
    lines += ["", "## 人工确认清单（尚未填写）", "", *[f"- [ ] {item}" for item in document["review_checklist"]], "",
              "复制 human_decision_template.json 到独立工作文件后填写；不可修改被冻结的文档来沿用旧批准。",
              "批准必须绑定当前完整内容指纹，填写本人、带时区时间、备注，确认所有清单项且无blocking问题。",
              "内容修订后必须建立新审核包并重新确认。机器不验证真实身份或临床正确性。", ""]
    return "\n".join(lines)


def _review_markdown_en(document: dict[str, Any], mapping: dict[str, str]) -> str:
    """English v2 rendering; the frozen v1 renderer above stays unchanged."""
    lines = [f"# {document['title']} — Whole-figure review candidate", "",
             "Not human-approved. Source blocks below are unverified transcriptions, not guaranteed accurate source text. The description, paths and symbol explanations are AI-organized.",
             "This package supports whole-figure review only. Individual paths cannot be released separately, and this workflow does not replace the original 623-record approval gate.", "",
             f"Figure ID: {document['figure_id']}; revision: {document['revision']}; language: {document['language']}",
             f"PDF pages: {document['page_numbers']}; printed pages: {document['printed_pages']}",
             f"[Source PDF](<{mapping['source_pdf']}#page={document['page_numbers'][0]}>) · [Figure image](<{mapping['image']}>)", "",
             "## AI-organized description", "", document["description_en"], "", document["generation_note"], "",
             "## Source transcription for verification (not approved source text)", ""]
    for row in document["source_blocks"]:
        lines += [f"### {row['id']} · {row['kind']} · {row['region']}", "", _source_quote(row["text"]), ""]
    lines += ["## AI-organized paths (verify all conditions and dependencies)", ""]
    for row in document["paths"]:
        lines += [f"### {row['path_id']} · {row['title']} · {row['kind']}", "", row["logic_text_en"], "",
                  f"Source blocks: {', '.join(row['source_block_ids'])}",
                  f"Footnote blocks: {', '.join(row['footnote_ids']) or 'None listed; a person must verify that no footnotes are required'}", "",
                  "Critical checks:", "", *[f"- {item}" for item in row["critical_checks"]], ""]
    lines += ["## Symbols and relationships", ""]
    for row in document["symbols"]:
        lines += [f"- {row['symbol']} · {row['kind']}: {row['meaning_en']}; source blocks: {', '.join(row['source_block_ids'])}"]
    lines += ["", "## Open questions", ""]
    lines += [f"- {row['question_id']} [{row['severity']}]: {row['text']}" for row in document["open_questions"]]
    if not document["open_questions"]:
        lines += ["No listed questions does not establish error-free content; human verification remains required."]
    lines += ["", "## Human review checklist (not yet completed)", "", *[f"- [ ] {item}" for item in document["review_checklist"]], "",
              "Copy human_decision_template.json to a separate working file. Do not edit the frozen document to reuse an earlier approval.",
              "Approval must bind the current full content fingerprint, identify the human reviewer and a timezone-aware review time, include notes, confirm every checklist item, and have no unresolved blocking questions.",
              "Any revision, including a language/schema change, requires a new review package and explicit confirmation. The software does not authenticate identity or establish clinical correctness.",
              "The v2 CJK/Han check prevents text leakage only; it is not general English-language certification. Unicode clinical symbols and original source paths are preserved.", ""]
    return "\n".join(lines)


def _render_review_markdown(document: dict[str, Any], mapping: dict[str, str],
                            scope: dict[str, Any] | None = None,
                            source_pages: list[dict[str, Any]] | None = None) -> str:
    if document["schema_version"] == DOCUMENT_SCHEMA_EN:
        rendered = _review_markdown_en(document, mapping)
    else:
        rendered = _review_markdown(document, mapping)
    if scope is None or scope["schema_version"] != SUPPLEMENT_SCOPE_SCHEMA:
        return rendered
    lines = ["# Supplemental table review — not integrated or approved", "",
             "This independently versioned supplement covers Table 9.1 and Table 9.4 only. It does not replace the approved seven-group scope, reuse its approvals, approve any legacy extraction, or release a nine-group knowledge base.", "",
             "All source pages for this table must be reviewed together:", ""]
    for row in source_pages or []:
        lines.append(f"- [PDF page {row['page_number']} image](<{row['relative_path']}>)")
    lines += ["", "The original scope and 623-record lineage are preserved as historical provenance only. Formal integration requires a separately reviewed release contract after explicit human approval.", "", rendered]
    return "\n".join(lines)


def prepare_figure_review(document_path: str | Path, scope_path: str | Path, output_dir: str | Path) -> dict[str, Any]:
    document_path, scope_path, output_dir = map(Path, (document_path, scope_path, output_dir))
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite review directory: {output_dir}")
    document, scope = _read_json(document_path), _read_json(scope_path)
    validate_document(document, scope)
    pdf = _resolve_source(document["source_pdf"], document_path.resolve().parent)
    image = _resolve_source(document["image_path"], document_path.resolve().parent)
    for path, field in ((pdf, "source_pdf_sha256"), (image, "image_sha256")):
        if not path.is_file() or sha256_file(path) != document[field]:
            raise ValueError(f"Source file hash mismatch: {field}")
    original_hashes = {str(path): sha256_file(path) for path in (document_path, scope_path, pdf, image)}
    image_suffix = image.suffix.lower() if image.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"} else ".image"
    source_relatives = {"source_pdf": "sources/source.pdf", "image": f"sources/source_image{image_suffix}"}
    source_pages, extra_copies = [], []
    for row in _supplement_images(document, scope):
        page_image = _resolve_source(row["image_path"], document_path.resolve().parent)
        if not page_image.is_file() or sha256_file(page_image) != row["image_sha256"]:
            raise ValueError(f"Supplement source page image hash mismatch: page {row['page_number']}")
        original_hashes[str(page_image)] = row["image_sha256"]
        if row["page_number"] == document["page_numbers"][0]:
            relative = source_relatives["image"]
        else:
            suffix = page_image.suffix.lower() if page_image.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"} else ".image"
            relative = f"sources/source_page_{row['page_number']}{suffix}"
            extra_copies.append((page_image, relative, f"source_page_{row['page_number']}"))
        source_pages.append({"page_number": row["page_number"], "original_path": row["image_path"],
                             "relative_path": relative, "sha256": row["image_sha256"]})
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "sources").mkdir()
    copies = [(document_path, "document.json", "document"), (scope_path, "scope_manifest.json", "scope"),
              (pdf, source_relatives["source_pdf"], "source_pdf"), (image, source_relatives["image"], "image"), *extra_copies]
    inventory = []
    for source, relative, role in copies:
        shutil.copyfile(source, output_dir / relative)
        digest = sha256_file(output_dir / relative)
        if digest != original_hashes[str(source)]:
            raise ValueError("Input changed during preparation; incomplete output must not be used")
        inventory.append({"role": role, "path": relative, "sha256": digest})
    mapping = {"source_pdf": {"original_path": document["source_pdf"], "relative_path": source_relatives["source_pdf"], "sha256": document["source_pdf_sha256"]},
               "image": {"original_path": document["image_path"], "relative_path": source_relatives["image"], "sha256": document["image_sha256"]}}
    if source_pages:
        mapping["source_pages"] = source_pages
    _write_json(output_dir / "source_path_map.json", mapping)
    decision = {"figure_id": document["figure_id"], "status": "pending", "reviewer": "", "reviewed_at": "",
                "reviewed_content_sha256": "", "checklist_confirmations": {item: False for item in document["review_checklist"]}, "notes": ""}
    _write_json(output_dir / "human_decision_template.json", decision)
    with (output_dir / "REVIEW.md").open("x", encoding="utf-8") as handle:
        handle.write(_render_review_markdown(document, source_relatives, scope, source_pages))
    for filename, role in (("source_path_map.json", "source_map"), ("human_decision_template.json", "decision_template"), ("REVIEW.md", "review_guide")):
        inventory.append({"role": role, "path": filename, "sha256": sha256_file(output_dir / filename)})
    manifest = {"schema_version": PACKAGE_SCHEMA, "status": "unreviewed_figure_candidate", "figure_id": document["figure_id"],
                "created_at": datetime.now(timezone.utc).isoformat(), "content_sha256": content_fingerprint(document),
                "scope_sha256": content_fingerprint(scope), "files": inventory,
                "notice": "No approvals generated. Not integrated with or substituted for the legacy 623-record KB release gate."}
    if source_pages:
        manifest["notice"] = "Supplemental table review only. No approvals generated, no existing chart or legacy decision changed, and no formal KB release performed."
    _write_json(output_dir / "review_manifest.json", manifest)
    for path, digest in original_hashes.items():
        if sha256_file(path) != digest:
            raise ValueError("Original input changed during preparation")
    return verify_figure_review(output_dir)


def verify_figure_review(review_dir: str | Path) -> dict[str, Any]:
    root = Path(review_dir)
    manifest = _read_json(root / "review_manifest.json")
    if manifest.get("schema_version") != PACKAGE_SCHEMA or manifest.get("status") != "unreviewed_figure_candidate":
        raise ValueError("Unsupported review package or status")
    entries = _index(manifest.get("files"), "role", "manifest files")
    required = {"document", "scope", "source_pdf", "image", "source_map", "decision_template", "review_guide"}
    if not required.issubset(entries):
        raise ValueError("Review package inventory is incomplete")
    files = {}
    seen_paths = set()
    for role, row in entries.items():
        _fields(row, {"role", "path", "sha256"}, "manifest file")
        _hash(row["sha256"], "manifest file hash")
        path = _safe_member(root, row["path"])
        if path in seen_paths:
            raise ValueError("Review package inventory repeats a path")
        seen_paths.add(path)
        if not path.is_file() or sha256_file(path) != row["sha256"]:
            raise ValueError(f"Frozen file missing or changed: {role}")
        files[role] = path
    # Fixed artifact names are part of the interface; paths cannot redirect
    # consumers to a different document than the one verified here.
    for role, filename in (("document", "document.json"), ("scope", "scope_manifest.json"), ("source_map", "source_path_map.json"), ("decision_template", "human_decision_template.json"), ("review_guide", "REVIEW.md")):
        if entries[role]["path"] != filename:
            raise ValueError("Unexpected fixed artifact path")
    document, scope = _read_json(files["document"]), _read_json(files["scope"])
    validate_document(document, scope)
    page_images = _supplement_images(document, scope)
    required |= {f"source_page_{row['page_number']}" for row in page_images[1:]}
    if set(entries) != required:
        raise ValueError("Review package inventory has unexpected or missing source-page roles")
    if manifest.get("figure_id") != document["figure_id"] or manifest.get("content_sha256") != content_fingerprint(document):
        raise ValueError("Document content fingerprint mismatch")
    if manifest.get("scope_sha256") != content_fingerprint(scope):
        raise ValueError("Scope content fingerprint mismatch")
    mapping = _read_json(files["source_map"])
    _fields(mapping, {"source_pdf", "image"} | ({"source_pages"} if page_images else set()), "source map")
    for role, source_field, hash_field in (("source_pdf", "source_pdf", "source_pdf_sha256"), ("image", "image_path", "image_sha256")):
        expected = {"original_path": document[source_field], "relative_path": entries[role]["path"], "sha256": document[hash_field]}
        if mapping[role] != expected or entries[role]["sha256"] != document[hash_field]:
            raise ValueError(f"Source map/hash mismatch: {role}")
    source_pages = []
    for index, row in enumerate(page_images):
        role = "image" if index == 0 else f"source_page_{row['page_number']}"
        source_pages.append({"page_number": row["page_number"], "original_path": row["image_path"],
                             "relative_path": entries[role]["path"], "sha256": row["image_sha256"]})
        if entries[role]["sha256"] != row["image_sha256"]:
            raise ValueError(f"Supplement source page image/hash mismatch: {row['page_number']}")
    if page_images and mapping["source_pages"] != source_pages:
        raise ValueError("Supplement source-page map must preserve every table page in order")
    template = _read_json(files["decision_template"])
    expected_template = {"figure_id": document["figure_id"], "status": "pending", "reviewer": "", "reviewed_at": "",
                         "reviewed_content_sha256": "", "checklist_confirmations": {key: False for key in document["review_checklist"]}, "notes": ""}
    if template != expected_template:
        raise ValueError("Frozen decision template must remain unsigned and pending")
    if files["review_guide"].read_text(encoding="utf-8") != _render_review_markdown(document, {role: entries[role]["path"] for role in ("source_pdf", "image")}, scope, source_pages):
        raise ValueError("Review guide does not represent the complete frozen document")
    result = {"status": "VERIFIED_CANDIDATE", "figure_id": document["figure_id"], "revision": document["revision"],
            "content_sha256": manifest["content_sha256"], "scope_sha256": manifest["scope_sha256"],
            "n_source_blocks": len(document["source_blocks"]), "n_paths": len(document["paths"]),
            "n_symbols": len(document["symbols"]), "n_blocking_questions": sum(row["severity"] == "blocking" for row in document["open_questions"]),
            "n_frozen_files_verified": len(files), "human_approval_verified": False}
    if page_images:
        result.update(scope_kind="supplement_only", n_source_page_images=len(page_images), kb_release_performed=False)
    return result


def _timestamp(value: str) -> bool:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


def validate_decision(document: dict[str, Any], decision: dict[str, Any]) -> None:
    _fields(decision, DECISION_FIELDS, "human decision (AI fields cannot replace human fields)")
    for key in DECISION_FIELDS - {"checklist_confirmations"}:
        _text(decision[key], f"decision.{key}", allow_empty=True)
    if decision["figure_id"] != document["figure_id"]:
        raise ValueError("Human decision names another figure")
    if decision["status"] not in DECISION_STATUSES:
        raise ValueError("Unexpected human decision status")
    confirmations = decision["checklist_confirmations"]
    if not isinstance(confirmations, dict) or set(confirmations) != set(document["review_checklist"]):
        raise ValueError("Checklist must exactly match the current document")
    if any(type(value) is not bool for value in confirmations.values()):
        raise ValueError("Checklist confirmations must be explicit JSON booleans")
    digest = content_fingerprint(document)
    if decision["reviewed_content_sha256"] and decision["reviewed_content_sha256"] != digest:
        raise ValueError("Stale human decision content fingerprint")
    if decision["reviewed_at"] and not _timestamp(decision["reviewed_at"]):
        raise ValueError("Human review time must be timezone-aware ISO-8601")
    if decision["status"] != "pending":
        if not decision["reviewer"].strip() or not _timestamp(decision["reviewed_at"]) or not decision["notes"].strip():
            raise ValueError("Non-pending human decision requires reviewer, timezone-aware time and notes")
        if decision["reviewed_content_sha256"] != digest:
            raise ValueError("Human decision must bind the current full content fingerprint")
    if decision["status"] == "approved":
        if not all(confirmations.values()):
            raise ValueError("Approval requires every checklist confirmation to be true")
        if any(row["severity"] == "blocking" for row in document["open_questions"]):
            raise ValueError("Approval blocked by unresolved blocking questions; prepare a corrected revision")


def import_figure_decision(review_dir: str | Path, decision_path: str | Path, output_path: str | Path) -> dict[str, Any]:
    output_path = Path(output_path)
    if output_path.exists():
        raise FileExistsError(f"Refusing to overwrite a human decision: {output_path}")
    verification = verify_figure_review(review_dir)
    document = _read_json(Path(review_dir) / "document.json")
    if content_fingerprint(document) != verification["content_sha256"]:
        raise ValueError("Figure document changed after package verification; retry from an unchanged review package")
    decision = _read_json(decision_path)
    validate_decision(document, decision)
    _write_json(output_path, decision)
    return {"status": "IMPORTED", "figure_id": document["figure_id"], "decision_status": decision["status"],
            "content_sha256": verification["content_sha256"], "output_path": str(output_path),
            "notice": "Explicit human fields validated, not identity-authenticated. No legacy record approval or KB release performed."}


def check_figure_scope(scope_path: str | Path, review_dirs: list[str | Path], decision_paths: list[str | Path]) -> dict[str, Any]:
    scope = _read_json(scope_path)
    validate_scope(scope)
    scope_digest = content_fingerprint(scope)
    expected = {row["figure_id"] for row in scope["figures"]}
    errors, reviews, decisions = [], {}, {}
    for directory in review_dirs:
        result = verify_figure_review(directory)
        figure_id = result["figure_id"]
        if figure_id in reviews:
            raise ValueError(f"Multiple review versions supplied for {figure_id}; select one explicitly")
        if result["scope_sha256"] != scope_digest or figure_id not in expected:
            raise ValueError("Review package does not match the selected full scope")
        document = _read_json(Path(directory) / "document.json")
        if content_fingerprint(document) != result["content_sha256"]:
            raise ValueError("Figure document changed after package verification; retry from an unchanged review package")
        reviews[figure_id] = document
    for path in decision_paths:
        row = _read_json(path)
        figure_id = row.get("figure_id")
        if not isinstance(figure_id, str) or figure_id not in expected:
            raise ValueError("Decision names an unknown figure")
        if figure_id in decisions:
            raise ValueError(f"Multiple decisions supplied for {figure_id}; select one explicitly")
        decisions[figure_id] = row
    approved = []
    for figure_id in sorted(expected):
        if figure_id not in reviews:
            errors.append(f"{figure_id}: missing figure review package")
            continue
        if figure_id not in decisions:
            errors.append(f"{figure_id}: missing human decision")
            continue
        try:
            validate_decision(reviews[figure_id], decisions[figure_id])
        except ValueError as exc:
            errors.append(f"{figure_id}: {exc}")
            continue
        if decisions[figure_id]["status"] != "approved":
            errors.append(f"{figure_id}: human decision is {decisions[figure_id]['status']}, not approved")
        else:
            approved.append(figure_id)
    result = {"ready": not errors, "status": "FIGURE_REVIEW_READY" if not errors else "BLOCKED",
            "scope_id": scope["scope_id"], "n_expected_figures": len(expected), "n_review_packages": len(reviews),
            "n_approved_figures": len(approved), "approved_figure_ids": approved, "errors": errors,
            "notice": "This status covers whole-figure review only; it never waives the original 623-record gate or declares the KB released."}
    if scope["schema_version"] == SUPPLEMENT_SCOPE_SCHEMA:
        result.update(scope_kind="supplement_only", kb_release_performed=False,
                      status="SUPPLEMENT_REVIEW_READY" if not errors else "BLOCKED",
                      notice="This status covers only the two-table supplement. It does not reuse parent approvals, approve legacy records, replace the original seven-group scope, or release a nine-group KB. A separate approved release integration is required.")
    return result
