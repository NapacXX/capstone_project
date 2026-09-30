"""Pinned, portable release contracts for approved ADA whole charts.

This is a new representation, not approval of the historical 623 extractions.
The consumer uses only the standard library and the stdlib figure verifier.
Approval hashes bind the recorded decision; they do not authenticate a person.
"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

from kb_v1_figure_review import (
    ADA_FIGURE_PAGES, ADA_SOURCE_SHA256, DOCUMENT_SCHEMA_EN,
    _read_json, canonical_json, check_figure_scope, content_fingerprint,
    sha256_file, validate_decision, verify_figure_review,
)


RELEASE_MODE = "whole_chart"
REPRESENTATION = "ada-whole-chart-release-v1"
REPRESENTATION_SUPPLEMENT = "ada-whole-chart-release-v2-with-table-supplement"
AUDIT_RELATIVE = "audit/whole_charts"
SOURCE_TITLE = "ADA Standards of Care in Diabetes—2026, Chapter 9: Pharmacologic Approaches to Glycemic Treatment"
# Source-bound layout check by the maintainer against the original page images:
# these pages contain the approved chart/table and its caption/footer only.
# Mixed prose/chart pages (including 8, 14 and 21) are deliberately excluded.
CHART_ONLY_PAGES = {
    9: "ada2026-ch9-figure-9-4", 11: "ada2026-ch9-table-9-2",
    12: "ada2026-ch9-table-9-2", 13: "ada2026-ch9-table-9-2",
    16: "ada2026-ch9-figure-9-5",
}
SCOPE_SHA256 = "504fae0f4f1846c702cfca7cafdb51ae90ddd53e792dcf4d803cc5a5ea81593f"
MIGRATION_SHA256 = "b4739f1d720f6875a5baf31c7496d05228511e18ac85950ee307be4756d572b3"
CANONICAL_SHA256 = "d796a62929d7b40975fc457de7914bade1d0f8967f202077071f16ddcc95185a"
LEDGER_SHA256 = "2950852caf4843f2e3e4f59953ce61b96a23de5d9b6ab95a9b995c4c29b0ab44"
ORIGIN_IDS_SHA256 = "4f46c7243d0d15e910df7d362c6c41edb67274608df462a435c4ea5024ad98c7"
TABLE_VIEW_SHA256 = "1fcba253ee08d1fad3c26cc970724868df96babad88605bba97ab99fc91e9c69"
SUPPLEMENT_SCOPE_SHA256 = "cc91308571d8433707139ded43ab8fc37474b95886a25c1b07bd7673dd1f1c26"
SUPPLEMENT_SELECTED = (
    ("table_9_1_draft_001_en", "3e306c3b2b6a0683b7d5825652ff7be6c170c2bd175ffff61b36eca8fe35df48", "d64e33f3232ef80629bf939cb4ef01a538539eb59a4a2828344883134337d18d"),
    ("table_9_4_draft_001_en", "3da461c8eec0e47b60c2e2eb94ff9770a75d62f5f3a10eb1f1a443f09e0afccb", "256dcb70a17ffad75fab66a1d412048a3249d9c97794ecfc310c2bb0c6cd291c"),
)
# Source-bound layout inspection: physical pages 3 and 4 contain Table 9.1
# plus its own title/footer only. Page 22 also contains recommendations 9.31a
# and 9.31b below Table 9.4 and MUST NOT receive whole-page suppression.
SUPPLEMENT_CHART_ONLY_PAGES = {3: "ada2026-ch9-table-9-1", 4: "ada2026-ch9-table-9-1"}
# Explicit version selection. A future revision needs a separate reviewed
# release contract; scanning for the newest file cannot silently replace it.
SELECTED = (
    ("figure_9_1_draft_001_en", "98ca410e99115e73c9b28dcdd67626050f782bf3520f3441b5c2f5c7e804fd83", "54bb001644baca9e5762a64acdde08402f80da8585339ccc1a1c0f0d1b61075a"),
    ("figure_9_2_draft_001_en", "0eae283df145d6d8a0953840ecdcde6662cbd263a1af578834bdb0d652578bc1", "2713c89c1b7b29af9ee1971e938736f8688596a32a4130e0e0e391bd01a7fa34"),
    ("figure_9_3_draft_001_en", "53dae4a5304435c725f1c7e79dcbfdd4ddde6d580c49e5910ba48d82f693154f", "901b6fcd7ce0eac7ba886b45da90f29e5c2b779f12a7252a8a756bb5d71b5eff"),
    ("figure_9_4_draft_002_en", "5abc5029ab0cc4359ca915e48643a966d507fe0113e48cbac9e10a4d897f707d", "3471528e83ffa1b69579af637ce752fcad3b4c2b4b4c76eb232557395c881216"),
    ("figure_9_5_draft_001_en", "7d83775d0677a1ef6221a65b2c9d2f4087f63baf8fcb01ee58a87a175348eb40", "a5e2e576cea3831c8757adceb15940728d7c86e6db011e77e8a8e9cc9ef91838"),
    ("table_9_2_draft_001_en", "bfe0dcec3715dd8412406481cc9eb0e750b6dc3b75b3d0a1b790946f4da1a064", "f2c2b2475d93582d4c5864b3f60a5c4e28e5929d2e1e37297a83e73d69b2645c"),
    ("table_9_3_draft_001_en", "38389feca3e019c7186c1b9ab5332ee130fba9546b249be934e35775667d79d4", "47872e951aeb8147c87659155e819b057edbac5286fe2be4f983afbdc0a64361"),
)
FIGURE_95_RESTRICTIONS = [
    "N12's trailing 'at the same total' remains unresolved: do not compute a dose from it or supply an inferred formula.",
    "N07 does not authorize simultaneous separate GLP-1 RA and dual GIP/GLP-1 RA classes; do not infer a combination regimen.",
    "The H01 Figure 9.3 cross-reference remains a source inconsistency, not a verified executable dependency; do not silently substitute Figure 9.4.",
]


def _safe(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError("Unsafe chart audit path")
    logical = PurePosixPath(relative)
    if (logical.is_absolute() or logical.as_posix() != relative
            or any(part in {".", ".."} or ":" in part for part in logical.parts)):
        raise ValueError("Unsafe chart audit path")
    root = root.resolve(strict=True)
    current = root
    for part in logical.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Chart audit paths must be materialized, not symlinks")
    if not current.resolve(strict=True).is_relative_to(root):
        raise ValueError("Chart audit path escapes bundle")
    return current


def _hash_check(path: Path, expected: str, label: str) -> None:
    if not path.is_file() or sha256_file(path) != expected:
        raise ValueError(f"Pinned chart release input changed or missing: {label}")


def _jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            raise ValueError(f"Blank historical JSONL row {number}")
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("Historical JSONL must contain objects")
        rows.append(value)
    return rows


def _validate_migration(scope: dict[str, Any], migration: dict[str, Any], canonical: Path, ledger: Path) -> None:
    _hash_check(canonical, CANONICAL_SHA256, "historical canonical records")
    _hash_check(ledger, LEDGER_SHA256, "historical human ledger")
    lineage = scope["legacy_lineage"]
    if (lineage["canonical_records_sha256"] != CANONICAL_SHA256
            or lineage["latest_human_ledger_sha256"] != LEDGER_SHA256
            or lineage["record_count"] != 623):
        raise ValueError("Historical lineage differs from the fixed original cohort")
    rows = _jsonl(canonical)
    ids = [row.get("record_id") for row in rows]
    if (len(ids) != 623 or any(not isinstance(x, str) or not x for x in ids)
            or len(set(ids)) != 623
            or hashlib.sha256(canonical_json(sorted(ids)).encode()).hexdigest() != ORIGIN_IDS_SHA256):
        raise ValueError("Migration does not retain all 623 independently anchored original IDs")
    page_groups = {page: figure for figure, pages in ADA_FIGURE_PAGES.items() for page in pages}
    expected = []
    for row in rows:
        page = row.get("page_number")
        if (type(page) is not int or page not in page_groups
                or row.get("source_pdf_sha256") != ADA_SOURCE_SHA256):
            raise ValueError("Historical source/page identity changed")
        expected.append({
            "legacy_record_id": row["record_id"], "page_number": page,
            "source_figure_id": page_groups[page],
            "mapping_basis": "source page membership only; not semantic equivalence",
            "migration_status": "source_group_identified; new representation not approved",
            "legacy_disposition_changed": False,
        })
    if (migration.get("scope_id") != scope["scope_id"]
            or migration.get("expected_groups") != 7
            or migration.get("expected_pages") != sorted(page_groups)
            or migration.get("legacy_record_count") != 623
            or migration.get("existing_image_tiles") != 60
            or migration.get("mapping") != expected
            or migration.get("counts_by_source_figure") != dict(Counter(x["source_figure_id"] for x in expected))):
        raise ValueError("Migration mapping must exactly retain historical page-based provenance, not clinical equivalence or legacy approval")


def project_chart(document: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    """Deterministic complete-parent projection; never change approved content."""
    validate_decision(document, decision)
    if decision["status"] != "approved" or document["schema_version"] != DOCUMENT_SCHEMA_EN:
        raise ValueError("Whole-chart release requires explicit approval of the English document")
    blocks = copy.deepcopy(document["source_blocks"])
    paths = copy.deepcopy(document["paths"])
    policy = {
        "purpose": "source_evidence_only",
        "clinical_execution_allowed": False,
        "automatic_dose_calculation_allowed": False,
        "automated_prescribing_allowed": False,
        "whole_parent_required": True,
        "external_model_enforcement": False,
        "unresolved_source_question_ids": [q["question_id"] for q in document["open_questions"]],
        "figure_9_5_restrictions": copy.deepcopy(FIGURE_95_RESTRICTIONS) if document["figure_id"] == "ada2026-ch9-figure-9-5" else [],
        "notice": "The retriever preserves and validates this policy and returns the complete parent; it does not control an external generation model or establish clinical correctness.",
    }
    if document["figure_id"] in {"ada2026-ch9-table-9-1", "ada2026-ch9-table-9-4"}:
        policy.update(
            automatic_unit_conversion_allowed=False,
            source_limitation_texts=[q["text"] for q in document["open_questions"]],
        )
    if document["figure_id"] == "ada2026-ch9-table-9-1":
        policy.update(
            automatic_percentage_normalization_allowed=False,
            unreviewed_source_wording_correction_allowed=False,
        )
    if document["figure_id"] == "ada2026-ch9-table-9-4":
        policy.update(
            automatic_pricing_calculation_allowed=False,
            current_price_claims_allowed=False,
            price_based_treatment_selection_allowed=False,
            historical_price_date="2025-07-15",
            price_basis="Source table title: per 1,000 units; conflicting footnote reference retained, not converted to monthly cost.",
        )
    retrieval = [document["title"], "SOURCE TRANSCRIPTION (human-reviewed; source wording follows)"]
    for block in blocks:
        retrieval += [f"[{block['id']}; {block['kind']}; {block['region']}]", block["text"]]
    retrieval += ["AI-ORGANIZED PATHS (human-reviewed; not verbatim guideline text)"]
    for path in paths:
        retrieval += [f"[{path['path_id']}] {path['title']}", path["logic_text_en"],
                      "Required source blocks: " + ", ".join(path["source_block_ids"]),
                      "Required footnotes: " + (", ".join(path["footnote_ids"]) or "none listed"),
                      "Review qualifications: " + " | ".join(path["critical_checks"])]
    retrieval += ["UNRESOLVED SOURCE LIMITATIONS"]
    retrieval += [q["text"] for q in document["open_questions"]]
    figure_id = document["figure_id"]
    return {
        "record_id": figure_id + "::" + document["revision"],
        "content_type": "visual_whole_chart", "is_visual": True,
        "representation": REPRESENTATION, "source_id": document["source_pdf_sha256"],
        "source_pdf_sha256": document["source_pdf_sha256"],
        "source_pdf_relative": f"sources/{document['source_pdf_sha256']}.pdf",
        "image_sha256": document["image_sha256"], "page_number": document["page_numbers"][0],
        "page_numbers": list(document["page_numbers"]), "printed_pages": list(document["printed_pages"]),
        "figure_id": figure_id,
        "table_or_figure_id": ("Table " if "-table-" in figure_id else "Figure ") + figure_id.rsplit("-", 2)[-2] + "." + figure_id.rsplit("-", 1)[-1],
        "title": document["title"],
        "revision": document["revision"], "language": "en", "evidence_group_id": figure_id,
        "source_text": "\n\n".join(block["text"] for block in blocks),
        "source_text_scope": "Complete human-reviewed source transcription for this whole chart, not the entire page or chapter. Region labels and AI interpretation remain separately identified.",
        "source_blocks": blocks, "ai_description": document["description_en"],
        "logic_paths": paths, "symbols": copy.deepcopy(document["symbols"]),
        "open_questions": copy.deepcopy(document["open_questions"]),
        "generation_note": document["generation_note"], "approved_document": copy.deepcopy(document),
        "retrieval_text": "\n\n".join(retrieval), "indexable": True,
        "dependency_ids": [],
        "internal_dependencies": [{"path_id": path["path_id"], "source_block_ids": list(path["source_block_ids"]),
                                   "footnote_ids": list(path["footnote_ids"]), "critical_checks": list(path["critical_checks"])} for path in paths],
        "dependency_policy": "Every retrieval unit resolves to this complete whole-chart parent; no source block, footnote or path may be published as an independent approved record.",
        "use_policy": policy, "review_status": "approved", "release_status": "released",
        "human_approved": True, "approved_by": decision["reviewer"], "approved_at": decision["reviewed_at"],
        "content_sha256": content_fingerprint(document),
    }


def _load(root: Path, canonical: Path, ledger: Path) -> dict[str, Any]:
    root = root.resolve(strict=True)
    scope_path = _safe(root, "scope_manifest.json")
    migration_path = _safe(root, "migration_inventory.json")
    _hash_check(scope_path, SCOPE_SHA256, "scope manifest")
    _hash_check(migration_path, MIGRATION_SHA256, "historical migration inventory")
    scope, migration = _read_json(scope_path), _read_json(migration_path)
    _validate_migration(scope, migration, canonical, ledger)
    charts, records = [], []
    for name, fingerprint, decision_hash in SELECTED:
        review_dir = _safe(root, f"reviews/{name}")
        decision_path = _safe(root, f"decisions/{name}_approved_001.json")
        _hash_check(decision_path, decision_hash, f"recorded human approval {name}")
        verified = verify_figure_review(review_dir)
        document, decision = _read_json(review_dir / "document.json"), _read_json(decision_path)
        if verified["content_sha256"] != fingerprint or content_fingerprint(document) != fingerprint:
            raise ValueError("Selected approved chart version changed")
        validate_decision(document, decision)
        if decision["status"] != "approved":
            raise ValueError("All seven chart decisions must be approved")
        mapping = _read_json(review_dir / "source_path_map.json")
        charts.append({"name": name, "figure_id": document["figure_id"], "review_dir": review_dir,
                       "decision_path": decision_path, "document": document, "decision": decision,
                       "source_image": _safe(review_dir, mapping["image"]["relative_path"]),
                       "source_pdf": _safe(review_dir, mapping["source_pdf"]["relative_path"])})
        records.append(project_chart(document, decision))
    result = check_figure_scope(scope_path, [x["review_dir"] for x in charts], [x["decision_path"] for x in charts])
    if not result["ready"] or result["n_approved_figures"] != 7:
        raise ValueError("Whole-chart release requires complete seven-chart approval: " + str(result["errors"]))
    table_view_path = _safe(root, "TABLE_9_2_TABLE_VIEW.json")
    _hash_check(table_view_path, TABLE_VIEW_SHA256, "historical four-page table source mapping")
    table_view = _read_json(table_view_path)
    table_doc = next(x["document"] for x in charts if x["figure_id"] == "ada2026-ch9-table-9-2")
    source_pages = table_view["source_pages"]
    if ([x["pdf_page"] for x in source_pages] != [11, 12, 13, 14]
            or [x["printed_page"] for x in source_pages] != table_doc["printed_pages"]
            or table_view["canonical_content_sha256"] != content_fingerprint(table_doc)):
        raise ValueError("Table 9.2 must retain all four authoritative PDF page references")
    for row in source_pages:
        _hash_check(_safe(root, row["relative_image_path"]), row["sha256"], "Table 9.2 page image")
    return {"records": records, "scope": scope, "migration": migration, "charts": charts,
            "scope_check": result, "source_pdf": charts[0]["source_pdf"],
            "legacy_canonical": canonical, "legacy_ledger": ledger,
            "table_source_pages": source_pages, "workspace": root}


def _add_supplement(data: dict[str, Any], workspace: Path) -> dict[str, Any]:
    """Require both exact newly approved tables; never inherit parent approvals."""
    root = Path(workspace).resolve(strict=True)
    scope_path = _safe(root, "scope_manifest.json")
    parent_scope_path = _safe(root, "parent_scope_manifest.json")
    _hash_check(scope_path, SUPPLEMENT_SCOPE_SHA256, "two-table supplement scope")
    _hash_check(parent_scope_path, SCOPE_SHA256, "supplement parent scope")
    scope = _read_json(scope_path)
    if (scope["parent_scope"]["scope_id"] != data["scope"]["scope_id"]
            or scope["parent_scope"]["relationship"] != "supplement_only_not_replacement"):
        raise ValueError("Supplement must extend the unchanged original seven-chart cohort")
    charts, records, source_pages = [], [], []
    for name, fingerprint, decision_hash in SUPPLEMENT_SELECTED:
        review_dir = _safe(root, f"reviews/{name}")
        decision_path = _safe(root, f"decisions/{name}_approved_001.json")
        _hash_check(decision_path, decision_hash, f"recorded supplement approval {name}")
        verified = verify_figure_review(review_dir)
        document, decision = _read_json(review_dir / "document.json"), _read_json(decision_path)
        if verified["content_sha256"] != fingerprint or content_fingerprint(document) != fingerprint:
            raise ValueError("Selected approved supplement table version changed")
        validate_decision(document, decision)
        if decision["status"] != "approved":
            raise ValueError("Both supplement table decisions must be approved")
        mapping = _read_json(review_dir / "source_path_map.json")
        charts.append({"name": name, "figure_id": document["figure_id"], "review_dir": review_dir,
                       "decision_path": decision_path, "document": document, "decision": decision,
                       "source_image": _safe(review_dir, mapping["image"]["relative_path"]),
                       "source_pdf": _safe(review_dir, mapping["source_pdf"]["relative_path"]),
                       "is_supplement": True})
        for page, printed in zip(mapping["source_pages"], document["printed_pages"], strict=True):
            path = _safe(review_dir, page["relative_path"])
            _hash_check(path, page["sha256"], "supplement authoritative page image")
            source_pages.append({"figure_id": document["figure_id"], "pdf_page": page["page_number"],
                                 "printed_page": printed, "sha256": page["sha256"], "image_path": path,
                                 "relative_image_path": f"supplement/reviews/{name}/{page['relative_path']}"})
        records.append(project_chart(document, decision))
    result = check_figure_scope(scope_path, [x["review_dir"] for x in charts], [x["decision_path"] for x in charts])
    if not result["ready"] or result["n_approved_figures"] != 2:
        raise ValueError("Whole-chart supplement requires both complete approvals: " + str(result["errors"]))
    if [x["pdf_page"] for x in source_pages] != [3, 4, 22]:
        raise ValueError("Table supplement must preserve all three authoritative page images")
    data.update(supplement_workspace=root, supplement_scope=scope, supplement_scope_check=result,
                supplement_source_pages=source_pages,
                chart_only_pages={**CHART_ONLY_PAGES, **SUPPLEMENT_CHART_ONLY_PAGES})
    data["charts"] += charts
    data["records"] += records
    return data


def load_chart_release(workspace: Path, supplement_workspace: Path | None = None) -> dict[str, Any]:
    """Load original immutable workspace; no output files or approvals created."""
    root = Path(workspace).resolve(strict=True)
    canonical = root.parent / "review_candidate_v2" / "canonical_records.jsonl"
    ledger = root.parent / "human_review_changchang_pan" / "batch_0002" / "human_review_ledger.jsonl"
    data = _load(root, canonical, ledger)
    data.update(chart_only_pages=dict(CHART_ONLY_PAGES), supplement_source_pages=[])
    return _add_supplement(data, supplement_workspace) if supplement_workspace is not None else data


def _contract(data: dict[str, Any]) -> dict[str, Any]:
    result = {
        "schema_version": REPRESENTATION, "release_mode": RELEASE_MODE,
        "source_pdf_sha256": ADA_SOURCE_SHA256, "scope_id": data["scope"]["scope_id"],
        "n_approved_charts": 7, "n_source_pages": 10,
        "n_historical_candidate_ids": 623, "n_legacy_candidates_approved_by_migration": 0,
        "migration_policy": "Replace the extraction representation for this fixed seven-chart source cohort; retain every old candidate and disposition as historical audit evidence. Page-group lineage is not semantic equivalence, rejection, approval or a 623-record clinical review.",
        "historical_status_policy": "Frozen review templates, scope notes and migration inventory retain their original pending/pilot language. Only external pinned decisions determine current approval; historical files are not rewritten.",
        "chart_only_page_text_policy": {
            "source_pdf_sha256": ADA_SOURCE_SHA256,
            "page_to_approved_chart": {str(page): figure for page, figure in CHART_ONLY_PAGES.items()},
            "rule": "Preserve the old extracted text as audit-only, not independent searchable clinical evidence. These original pages contain the chart/table and its own caption/footer only. Mixed-prose pages are not subject to this page-level rule.",
        },
        "records": [{"record_id": x["record_id"], "figure_id": x["figure_id"], "revision": x["revision"],
                     "content_sha256": x["content_sha256"], "page_numbers": x["page_numbers"]} for x in data["records"]],
        "scope_check": data["scope_check"],
        "table_9_2_source_pages": [{"pdf_page": x["pdf_page"], "printed_page": x["printed_page"],
                                    "relative_image_path": f"{AUDIT_RELATIVE}/{x['relative_image_path']}",
                                    "sha256": x["sha256"],
                                    "pdf_locator": f"sources/{ADA_SOURCE_SHA256}.pdf#page={x['pdf_page']}"}
                                   for x in data["table_source_pages"]],
        "clinical_boundary": "Research evidence retrieval only. Full parent context and all source limitations must accompany a match. No diagnosis, generated treatment recommendation, dose computation or external-model enforcement is supplied.",
    }
    if "supplement_scope" in data:
        result.update(
            schema_version=REPRESENTATION_SUPPLEMENT,
            scope_id="ada2026-ch9-nine-approved-whole-charts-v1",
            parent_scope_id=data["scope"]["scope_id"],
            supplement_scope_id=data["supplement_scope"]["scope_id"],
            n_approved_charts=9, n_source_pages=13,
            n_supplement_charts=2, n_supplement_legacy_candidate_ids=0,
            supplement_scope_check=data["supplement_scope_check"],
            supplement_policy="Add the two exact independently approved whole tables to the unchanged seven-chart cohort. Neither table reuses the original 623 candidate IDs or inherits a parent approval.",
            supplement_source_pages=[{
                "figure_id": x["figure_id"], "pdf_page": x["pdf_page"], "printed_page": x["printed_page"],
                "relative_image_path": f"{AUDIT_RELATIVE}/{x['relative_image_path']}", "sha256": x["sha256"],
                "pdf_locator": f"sources/{ADA_SOURCE_SHA256}.pdf#page={x['pdf_page']}",
            } for x in data["supplement_source_pages"]],
        )
        result["chart_only_page_text_policy"]["page_to_approved_chart"] = {
            str(page): figure for page, figure in data["chart_only_pages"].items()}
        result["chart_only_page_text_policy"]["supplement_layout_proof"] = {
            "inspection": "Full source-page visual inspection: physical pages 3 and 4 contain Table 9.1 and its own page furniture only. Physical page 22 also contains prose recommendations 9.31a and 9.31b; it is not a chart-only page.",
            "source_page_images": [{"pdf_page": x["pdf_page"], "sha256": x["sha256"]}
                                   for x in data["supplement_source_pages"]],
        }
    return result


def _expected_source_registry(data: dict[str, Any]) -> dict[str, Any]:
    images = []
    for chart in data["charts"]:
        document, path = chart["document"], chart["source_image"]
        images.append({
            "relative_path": f"sources/images/{document['image_sha256']}{path.suffix.lower()}",
            "sha256": document["image_sha256"], "page_number": document["page_numbers"][0],
            "figure_id": document["figure_id"],
            "coverage": "First page image only; complete page_numbers are available in the frozen PDF",
        })
    for page in data["table_source_pages"]:
        if page["sha256"] not in {x["sha256"] for x in images}:
            images.append({
                "relative_path": f"sources/images/{page['sha256']}.png", "sha256": page["sha256"],
                "page_number": page["pdf_page"], "printed_page": page["printed_page"],
                "figure_id": "ada2026-ch9-table-9-2",
                "coverage": "Supplemental page image; complete frozen PDF is authoritative",
            })
    for page in data.get("supplement_source_pages", []):
        if page["sha256"] not in {x["sha256"] for x in images}:
            images.append({
                "relative_path": f"sources/images/{page['sha256']}.png", "sha256": page["sha256"],
                "page_number": page["pdf_page"], "printed_page": page["printed_page"],
                "figure_id": page["figure_id"],
                "coverage": "Supplemental page image; complete frozen PDF is authoritative",
            })
    return {ADA_SOURCE_SHA256: {"title": SOURCE_TITLE, "year": 2026, "pdf_sha256": ADA_SOURCE_SHA256,
                               "relative_path": f"sources/{ADA_SOURCE_SHA256}.pdf", "images": images}}


def copy_chart_audit(workspace: Path, output: Path, supplement_workspace: Path | None = None) -> dict[str, Any]:
    """Copy verified audit inputs into a new child of an existing/new bundle."""
    data = load_chart_release(workspace, supplement_workspace)
    root, output = data["workspace"], Path(output).resolve()
    if output == root or output.is_relative_to(root) or root.is_relative_to(output):
        raise ValueError("Bundle output must not overlap the chart review workspace")
    if supplement_workspace is not None:
        supplement = data["supplement_workspace"]
        if output == supplement or output.is_relative_to(supplement) or supplement.is_relative_to(output):
            raise ValueError("Bundle output must not overlap the supplement review workspace")
    destination = output / AUDIT_RELATIVE
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite chart audit: {destination}")
    inputs = [(root / name, name) for name in ("scope_manifest.json", "migration_inventory.json", "TABLE_9_2_TABLE_VIEW.json")]
    inputs += [(data["legacy_canonical"], "legacy/canonical_records.jsonl"), (data["legacy_ledger"], "legacy/human_review_ledger.jsonl")]
    if supplement_workspace is not None:
        inputs += [(data["supplement_workspace"] / name, f"supplement/{name}")
                   for name in ("scope_manifest.json", "parent_scope_manifest.json")]
    for chart in data["charts"]:
        prefix = "supplement/" if chart.get("is_supplement") else ""
        package = chart["review_dir"]
        manifest = _read_json(package / "review_manifest.json")
        inputs.append((package / "review_manifest.json", f"{prefix}reviews/{chart['name']}/review_manifest.json"))
        inputs += [(_safe(package, x["path"]), f"{prefix}reviews/{chart['name']}/{x['path']}") for x in manifest["files"]]
        inputs.append((chart["decision_path"], f"{prefix}decisions/{chart['decision_path'].name}"))
    inputs += [(_safe(root, x["relative_image_path"]), x["relative_image_path"]) for x in data["table_source_pages"]]
    snapshots = [(source, relative, sha256_file(source)) for source, relative in inputs]
    destination.mkdir(parents=True, exist_ok=False)
    for source, relative, digest in snapshots:
        _hash_check(source, digest, relative)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        _hash_check(target, digest, relative)
    copied = _load(destination, destination / "legacy/canonical_records.jsonl", destination / "legacy/human_review_ledger.jsonl")
    if supplement_workspace is not None:
        copied = _add_supplement(copied, destination / "supplement")
    if copied["records"] != data["records"]:
        raise ValueError("Chart release inputs changed while copying")
    contract = _contract(copied)
    with (destination / "release_contract.json").open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(contract, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    for source, relative, digest in snapshots:
        _hash_check(source, digest, relative)
    return {"release_mode": RELEASE_MODE, "representation": contract["schema_version"],
            "audit_path": f"{AUDIT_RELATIVE}/release_contract.json", "n_approved_charts": len(data["records"]),
            "n_historical_candidate_ids": 623, "legacy_candidates_auto_approved": 0}


def verify_chart_audit(bundle: Path, files: dict[str, str], records: list[dict[str, Any]]) -> dict[str, Any]:
    """Fail closed on changed approvals, projections, scope or lineage.

    ``files`` is the bundle manifest's relative-path -> SHA-256 inventory.
    The frozen source hashes are also independently pinned by this module.
    """
    bundle = Path(bundle).resolve(strict=True)
    root = _safe(bundle, AUDIT_RELATIVE)
    if not root.is_dir():
        raise ValueError("Missing whole-chart audit directory")
    inventory = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(bundle).as_posix()
        safe = _safe(bundle, relative)
        if safe.is_file():
            inventory.append(relative)
            if relative not in files or sha256_file(safe) != files[relative]:
                raise ValueError("Chart audit file missing from manifest or changed: " + relative)
    contract = _read_json(_safe(root, "release_contract.json"))
    if contract.get("schema_version") not in {REPRESENTATION, REPRESENTATION_SUPPLEMENT}:
        raise ValueError("Unsupported whole-chart release contract version")
    data = _load(root, _safe(root, "legacy/canonical_records.jsonl"), _safe(root, "legacy/human_review_ledger.jsonl"))
    if contract["schema_version"] == REPRESENTATION_SUPPLEMENT:
        data = _add_supplement(data, _safe(root, "supplement"))
    elif (root / "supplement").exists():
        raise ValueError("Seven-chart contract cannot silently include a supplement audit")
    if contract != _contract(data):
        raise ValueError("Chart release contract does not match pinned complete audit")
    # The public navigation registry must not relabel a valid image as another
    # page/chart, or direct a correct record to a substituted PDF. Audit copies
    # alone do not establish that the links returned to a teammate are correct.
    registry_path = _safe(bundle, "sources.json")
    if files.get("sources.json") != sha256_file(registry_path):
        raise ValueError("Whole-chart source registry must be hash-bound in the bundle")
    source_registry = _read_json(registry_path)
    expected_registry = _expected_source_registry(data)
    if source_registry != expected_registry:
        raise ValueError("Whole-chart source registry differs from approved PDF/image/page mapping")
    source = source_registry[ADA_SOURCE_SHA256]
    for asset in [{"relative_path": source["relative_path"], "sha256": source["pdf_sha256"]}, *source["images"]]:
        path = _safe(bundle, asset["relative_path"])
        if files.get(asset["relative_path"]) != asset["sha256"]:
            raise ValueError("Whole-chart public source asset is missing from the hash inventory")
        _hash_check(path, asset["sha256"], "public PDF/page image")
    visual = [x for x in records if x.get("is_visual") or str(x.get("content_type", "")).startswith("visual")]
    expected = {x["record_id"]: x for x in data["records"]}
    count = len(expected)
    if (len(visual) != count or len({x.get("record_id") for x in visual}) != count
            or {x.get("record_id") for x in visual} != set(expected)):
        label = "seven" if count == 7 else "nine"
        raise ValueError(f"Whole-chart release must contain exactly the {label} complete approved parents, not legacy or isolated visual records")
    for row in visual:
        if row != expected[row["record_id"]]:
            raise ValueError("Published whole-chart record differs from the exact approved projection: " + row["record_id"])
    for row in records:
        if (row.get("source_id") == ADA_SOURCE_SHA256 and row.get("content_type") == "text"
                and row.get("page_number") in data.get("chart_only_pages", CHART_ONLY_PAGES) and row.get("indexable", True) is not False):
            raise ValueError("Flattened text on a pinned chart-only source page must remain audit-only; retrieve the approved complete chart instead")
        if ("supplement_scope" in data and row.get("source_id") == ADA_SOURCE_SHA256
                and row.get("content_type") == "table"
                and str(row.get("table_or_figure_id", "")).strip().lower() in {"table 9.1", "table 9.4"}
                and row.get("indexable", True) is not False):
            raise ValueError("Unverified table carrier must not compete with its approved complete supplement table")
    if "supplement_scope" in data:
        from kb_v1_chart_text import verify_supplement_text_policy
        verify_supplement_text_policy(records)
    return {"status": "VERIFIED_WHOLE_CHART_RELEASE_AUDIT", "n_approved_charts": count,
            "n_source_pages": contract["n_source_pages"], "n_historical_candidate_ids": 623,
            "n_legacy_candidates_approved_by_migration": 0, "n_audit_files_verified": len(inventory)}
