"""Non-destructive visual review preparation and fail-closed v1 release gate.

This module never awards a human approval. Step 07 is imported for its pure
normalization functions; its CLI must not be called on the team's approval CSV.
AI notes are observations, not corrections applied to the scientific source.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PIPELINE_DIR = Path(__file__).resolve().parent
# Identity-only anchor for the explicitly scoped ADA v1 source/candidate cohort.
# The public code contains no clinical content or full ID list. The controlled
# review artifact supplies the list, whose hash must match this independent pin.
ORIGIN_SOURCE_SHA256 = "7c2913f79b61bc60f8f51327bb73b80391988fd277116b29e57df4242b2a2404"
ORIGIN_RECORD_COUNT = 623
ORIGIN_IDS_SHA256 = "4f46c7243d0d15e910df7d362c6c41edb67274608df462a435c4ea5024ad98c7"
AI_STATUSES = {"not_reviewed", "reviewed_no_change", "reviewed_with_findings", "uncertain"}
AI_FIELDS = {"record_id", "ai_review_status", "checked_sources", "findings", "suggested_corrections", "uncertainty"}
FINAL_DISPOSITIONS = {"approved", "rejected", "merged"}
HUMAN_LEDGER_FIELDS = {
    "final_disposition", "human_reviewer", "human_reviewed_at", "reviewed_content_sha256",
    "human_notes", "merge_target", "dependency_ids", "dependency_reviewed",
}
CHILD_KEYS = ("nodes", "edges", "actions", "footnotes", "ordinal_symbols")
AUDIT_ONLY_FIELDS = {
    "review_status", "reviewer", "reviewed_at", "corrections_json", "review_notes",
    "validation_status", "validation_errors", "validation_warnings", "release_eligible",
    "requires_manual_review", "_errors", "_warnings", "_approval_valid",
    "v1_group_id", "v1_dependency_ids", "v1_human_reviewer", "v1_human_reviewed_at", "v1_reviewed_content_sha256",
}

REVIEW_GUIDE = """# v1 review contract

Preparation does not approve a record. Existing Step 07 approval is necessary
but insufficient for v1. AI observations belong only in ai_* / findings fields;
they never change final_disposition or the source content.

For every record a person must set final_disposition to approved, rejected, or
merged; supply human_reviewer, timezone-aware human_reviewed_at, human_notes,
and reviewed_content_sha256 matching record_fingerprint(record). The separate
step07_content_sha256 is the original Step 07 approval fingerprint. Do not
substitute one for the other. For approved records, set dependency_reviewed to
true and dependency_ids to an explicit list (possibly empty after inspection).
Each edge must include both endpoint records in that list. Merge dispositions
must identify one approved merge_target. Never use AI's name as a human signer.

For each page-level group, a person must inspect all tiles against the complete
PDF page, resolve omissions/footnotes/conditions, set group_status=complete,
human_confirmed=true, human_reviewer and human_reviewed_at, and bind the review
to reviewed_group_sha256=group_fingerprint(group, records). Keep all original
expected_record_ids in group_reviews.json; added candidates must be present in
both the ledger and groups too. Unreadable evidence remains pending.

Corrections are not applied by editing this ledger. Use a new raw/correction
working copy, run Step 07 validation again into a new review version, and obtain
fresh human approvals for the resulting content. The group and record hashes
must also be reconfirmed. No existing team approval file is overwritten.

Practical order: (1) copy working_approval_template.csv to a new correction
workspace; (2) edit corrections_json there, not the team original; (3) run
Step 07 on that copy into a new validation directory to compute new hashes;
(4) a person reviews those hashes and sets Step 07 reviewer/time/status;
(5) run Step 07 again on that copy; (6) prepare_review into a NEW review
directory using that copy; (7) edit only the human columns in review_ledger.csv;
(8) import_human_ledger_csv writes a NEW JSONL, without signing or overwriting;
(9) a person confirms group_reviews.json against the complete source page;
(10) run release-check/build with the explicitly selected reviewed ledger.
Pending rows may remain in a draft import, but release-check will reject them.
CSV edits cannot change AI notes, candidate content, or source hashes. The
importer never fills a missing reviewer, time, content hash, or disposition.

For the designated ADA source, every future review version must retain the
original 623 candidate IDs and their final dispositions. The controlled origin
list is pinned by source SHA-256, count, and sorted-ID SHA-256 in the code.
The initial exact cohort is recognized automatically. To prepare a version
with additional candidates, pass origin_manifest_path pointing to the previous
anchored review_manifest.json (or group_reviews.json). Additional candidates
must be reviewed too. Removed/replaced original records must retain a rejected
or merged historical record rather than disappearing. This is a safety barrier,
not an automated migration/correction engine for changing record identities.

Source inventories freeze hashes, not permissions to distribute the materials.
PDF/image original paths are retained for audit. The release packager creates
a separate portable path map without changing those historical fields.
"""


def _module(filename: str):
    spec = importlib.util.spec_from_file_location(f"kb_v1_{filename[:2]}", PIPELINE_DIR / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _json_default(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=_json_default, allow_nan=False)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows = []
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: expected an object")
            rows.append(row)
    return rows


def _csv_value(value: Any) -> str:
    return canonical_json(value) if isinstance(value, (list, dict)) or value is None else str(value)


def import_human_ledger_csv(review_dir: str | Path, csv_path: str | Path, output_path: str | Path) -> dict[str, Any]:
    """Import explicitly human-entered columns into a NEW JSONL audit file.

    This is format validation, not verification of a person's identity. Source,
    AI and computed fields are immutable; no missing human value is synthesized.
    """
    review_dir, csv_path, output_path = map(Path, (review_dir, csv_path, output_path))
    if output_path.exists():
        raise FileExistsError(f"Refusing to overwrite a ledger: {output_path}")
    original = load_jsonl(review_dir / "review_ledger.jsonl")
    original_map = _index(original, "record_id", "original ledger")
    records = _index(load_jsonl(review_dir / "canonical_records.jsonl"), "record_id", "canonical records")
    if set(records) != set(original_map):
        raise ValueError("Canonical and original ledger inventories differ")
    expected_fields = set(original[0]) if original else set()
    if not expected_fields:
        raise ValueError("Cannot import an empty ledger")
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)) or set(reader.fieldnames) != expected_fields:
            raise ValueError("CSV columns must exactly match the prepared review_ledger.csv")
        incoming = list(reader)
    if any(None in row or any(value is None for value in row.values()) for row in incoming):
        raise ValueError("Malformed CSV row: too many or missing cells")
    incoming_map = _index(incoming, "record_id", "human CSV")
    if set(incoming_map) != set(original_map):
        raise ValueError("Human CSV must contain every original record_id exactly once, with no additions")
    imported = []
    for old in original:
        record_id = old["record_id"]
        row = incoming_map[record_id]
        for key in expected_fields - HUMAN_LEDGER_FIELDS:
            if row[key] != _csv_value(old[key]):
                raise ValueError(f"{record_id}: immutable field changed: {key}")
        human = {key: row[key] for key in HUMAN_LEDGER_FIELDS}
        disposition = human["final_disposition"]
        if disposition not in FINAL_DISPOSITIONS | {"pending"}:
            raise ValueError(f"{record_id}: unexpected final_disposition: {disposition!r}")
        boolean = human["dependency_reviewed"].strip().lower()
        if boolean not in {"true", "false"}:
            raise ValueError(f"{record_id}: dependency_reviewed must be explicit true/false")
        human["dependency_reviewed"] = boolean == "true"
        try:
            dependencies = json.loads(human["dependency_ids"])
        except json.JSONDecodeError as exc:
            raise ValueError(f"{record_id}: dependency_ids must be a JSON array or null") from exc
        if dependencies is not None and (not isinstance(dependencies, list) or any(not isinstance(value, str) for value in dependencies)):
            raise ValueError(f"{record_id}: dependency_ids must contain record ID strings")
        if dependencies is not None:
            if len(dependencies) != len(set(dependencies)) or record_id in dependencies or set(dependencies) - set(records):
                raise ValueError(f"{record_id}: unknown, duplicate, or self dependency")
        human["dependency_ids"] = dependencies
        if disposition in FINAL_DISPOSITIONS:
            if not human["human_reviewer"].strip() or not _valid_timestamp(human["human_reviewed_at"]):
                raise ValueError(f"{record_id}: final disposition needs human reviewer and timezone-aware time")
            if not human["human_notes"].strip():
                raise ValueError(f"{record_id}: final disposition needs human rationale")
            if human["reviewed_content_sha256"] != record_fingerprint(records[record_id]):
                raise ValueError(f"{record_id}: final disposition needs the current reviewed content hash")
        if disposition == "approved" and (human["dependency_reviewed"] is not True or dependencies is None):
            raise ValueError(f"{record_id}: approval needs explicitly reviewed dependency list")
        if disposition == "merged" and (human["merge_target"] not in records or human["merge_target"] == record_id):
            raise ValueError(f"{record_id}: merge needs another known target")
        if disposition != "merged" and human["merge_target"]:
            raise ValueError(f"{record_id}: merge_target is only allowed for merged disposition")
        imported.append({**old, **human})
    # Exclusive creation also prevents races with an existing reviewer's file.
    with output_path.open("x", encoding="utf-8") as handle:
        handle.write("".join(canonical_json(row) + "\n" for row in imported))
    return {"status": "SUCCESS", "n_records": len(imported), "dispositions": dict(Counter(row["final_disposition"] for row in imported)),
            "output_path": str(output_path), "notice": "Human entries imported, not independently authenticated or release-approved; run release-check."}


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=_json_default, allow_nan=False) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(canonical_json(row) + "\n" for row in rows), encoding="utf-8")


def record_fingerprint(record: dict[str, Any]) -> str:
    """Extra v1 binding to actual canonical content, not editable stored hashes."""
    content = {key: value for key, value in record.items() if key not in AUDIT_ONLY_FIELDS}
    return hashlib.sha256(canonical_json(content).encode("utf-8")).hexdigest()


def _origin_list_hash(ids: list[str]) -> str:
    return hashlib.sha256(canonical_json(sorted(ids)).encode("utf-8")).hexdigest()


def _validate_origin_anchor(records: list[dict[str, Any]], document: dict[str, Any]) -> dict[str, Any]:
    scoped_ids = {row["record_id"] for row in records if row.get("source_pdf_sha256", "").lower() == ORIGIN_SOURCE_SHA256}
    if not scoped_ids and document.get("origin_source_pdf_sha256") != ORIGIN_SOURCE_SHA256:
        return {}
    ids = document.get("origin_record_ids")
    if (document.get("origin_source_pdf_sha256") != ORIGIN_SOURCE_SHA256
            or document.get("origin_record_count") != ORIGIN_RECORD_COUNT
            or document.get("origin_record_ids_sha256") != ORIGIN_IDS_SHA256
            or not isinstance(ids, list)
            or any(not isinstance(identifier, str) or not identifier for identifier in ids)
            or len(ids) != ORIGIN_RECORD_COUNT or len(set(ids)) != ORIGIN_RECORD_COUNT
            or _origin_list_hash(ids) != ORIGIN_IDS_SHA256):
        raise ValueError("Initial ADA candidate origin anchor is missing or does not match the pinned source/count/ID hash; original candidates cannot be discarded by creating a new review version")
    missing = set(ids) - scoped_ids
    if missing:
        raise ValueError(f"Initial ADA candidate cohort is incomplete: {len(missing)} original record IDs are missing or moved to a different source; retain their rejected/merged dispositions")
    return {"origin_source_pdf_sha256": ORIGIN_SOURCE_SHA256, "origin_record_count": ORIGIN_RECORD_COUNT,
            "origin_record_ids_sha256": ORIGIN_IDS_SHA256, "origin_record_ids": sorted(ids)}


def _prepare_origin_anchor(records: list[dict[str, Any]], prior_document: dict[str, Any] | None = None) -> dict[str, Any]:
    scoped_ids = sorted(row["record_id"] for row in records if row.get("source_pdf_sha256", "").lower() == ORIGIN_SOURCE_SHA256)
    if not scoped_ids:
        return {}
    if prior_document is not None:
        return _validate_origin_anchor(records, prior_document)
    if len(scoped_ids) != ORIGIN_RECORD_COUNT or _origin_list_hash(scoped_ids) != ORIGIN_IDS_SHA256:
        raise ValueError("This is not the complete original ADA candidate cohort. For additional records supply origin_manifest_path from an anchored prior review, while retaining every original candidate ID")
    document = {"origin_source_pdf_sha256": ORIGIN_SOURCE_SHA256, "origin_record_count": ORIGIN_RECORD_COUNT,
                "origin_record_ids_sha256": ORIGIN_IDS_SHA256, "origin_record_ids": scoped_ids}
    return _validate_origin_anchor(records, document)


def group_fingerprint(group: dict[str, Any], records: list[dict[str, Any]]) -> str:
    lookup = {row["record_id"]: record_fingerprint(row) for row in records}
    ids = group.get("record_ids", [])
    return hashlib.sha256(canonical_json({
        "group_id": group.get("group_id"),
        "records": {record_id: lookup[record_id] for record_id in sorted(ids)},
    }).encode("utf-8")).hexdigest()


def to_retrieval_record(record: dict[str, Any]) -> dict[str, Any]:
    result = _module("07_validate_visual_logic_outputs.py").build_retrieval_record(record)
    for key in ("v1_group_id", "v1_dependency_ids", "v1_human_reviewer", "v1_human_reviewed_at", "v1_reviewed_content_sha256"):
        if key in record:
            result[key] = record[key]
    return result


def _index(rows: list[dict[str, Any]], key: str, label: str) -> dict[str, dict[str, Any]]:
    result = {}
    for row in rows:
        value = row.get(key)
        if not isinstance(value, str) or not value.strip() or value in result:
            raise ValueError(f"{label}: missing or duplicate {key}: {value!r}")
        result[value] = row
    return result


def _valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.tzinfo is not None and parsed.utcoffset() is not None
    except ValueError:
        return False


def _ai_notes(path: Path | None, record_ids: set[str], source_hashes: dict[str, str]) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    if path.suffix == ".jsonl":
        rows = load_jsonl(path)
    else:
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            rows = [{"record_id": key, **row} for key, row in value.items()]
        else:
            rows = value
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("AI notes must be a list or record_id-to-object mapping")
    notes = _index(rows, "record_id", "AI notes")
    if set(notes) - record_ids:
        raise ValueError("AI notes reference unknown record IDs")
    for record_id, row in notes.items():
        if set(row) - AI_FIELDS:
            raise ValueError(f"AI notes contain non-AI fields for {record_id}: {sorted(set(row) - AI_FIELDS)}")
        status = row.get("ai_review_status", "not_reviewed")
        if status not in AI_STATUSES:
            raise ValueError(f"Invalid ai_review_status: {status}")
        checks = row.get("checked_sources", [])
        if not isinstance(checks, list) or (status != "not_reviewed" and not checks):
            raise ValueError(f"Reviewed AI note requires checked_sources: {record_id}")
        for check in checks:
            if not isinstance(check, dict) or not check.get("method"):
                raise ValueError(f"Invalid checked source for {record_id}")
            source_path = str(Path(str(check.get("path", ""))).resolve())
            if source_hashes.get(source_path) != check.get("sha256"):
                raise ValueError(f"AI checked source hash/path mismatch: {record_id}")
        if not isinstance(row.get("findings", []), list) or not isinstance(row.get("suggested_corrections", {}), dict):
            raise ValueError(f"AI findings/corrections have invalid types: {record_id}")
    return notes


def prepare_review(
    raw_dir: str | Path,
    approval_csv: str | Path,
    registry_path: str | Path,
    output_dir: str | Path,
    notes_path: str | Path | None = None,
    origin_manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    """Freeze inputs and revalidate into a new directory, without human writes.

    The output must not already exist. Malformed/missing JSON is a hard error;
    unreadable assets cannot silently disappear from the candidate inventory.
    No visual reasoning is performed by this function; absent AI notes are
    explicitly marked not_reviewed.
    """
    raw_dir, approval_csv, registry_path, output_dir = map(Path, (raw_dir, approval_csv, registry_path, output_dir))
    raw_dir, approval_csv, registry_path, output_dir = (p.resolve() for p in (raw_dir, approval_csv, registry_path, output_dir))
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite review directory: {output_dir}")
    validator = _module("07_validate_visual_logic_outputs.py")
    manifest = validator.load_manifest(raw_dir)
    registry, registry_errors = validator.load_symbol_registry(registry_path)
    approvals, approval_errors = validator.load_approvals(approval_csv)
    if not approval_csv.is_file():
        raise FileNotFoundError(approval_csv)
    manifest_errors = validator.validate_manifest_duplicates(manifest)
    combined = {key: [] for key in (*CHILD_KEYS, "candidate_retrieval", "release_retrieval", "review", "approvals")}
    sources: dict[str, dict[str, Any]] = {}

    def inventory(path: Path, role: str, freeze: bool = False) -> None:
        path = path.resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Missing {role}: {path}")
        key = str(path)
        if key in sources:
            return
        entry = {"role": role, "original_path": key, "sha256": sha256_file(path), "bytes": path.stat().st_size}
        if freeze:
            entry["frozen_path"] = f"frozen_inputs/{role}/{entry['sha256'][:12]}_{path.name}"
        sources[key] = entry

    inventory(approval_csv, "approval", True)
    inventory(registry_path, "registry", True)
    inventory(raw_dir / "visual_logic_manifest.csv", "manifest", True)
    inventory(PIPELINE_DIR / "07_validate_visual_logic_outputs.py", "validator_code", True)
    inventory(PIPELINE_DIR / "08_build_enhanced_guideline_kb.py", "release_code", True)
    for index, manifest_row in manifest.iterrows():
        path = validator.resolve_input_path(manifest_row.get("visual_logic_json"), raw_dir)
        inventory(path, "raw_json", True)
        payload = validator.read_json(path)
        for field, role in (("source_pdf", "source_pdf"), ("image_path", "image")):
            inventory(validator.resolve_input_path(manifest_row.get(field), raw_dir), role)
        tile_path = validator.resolve_input_path(manifest_row.get("image_path"), raw_dir)
        overview_path = tile_path.parent / f"ada_page_{int(manifest_row['page_number']):03d}.png"
        if overview_path.is_file():
            inventory(overview_path, "page_overview")
        flattened = validator.flatten_payload(
            payload, manifest_row, raw_dir=raw_dir, approvals=approvals, registry=registry,
            inherited_errors=list(registry_errors) + list(approval_errors) + manifest_errors.get(int(index), []),
        )
        for key, rows in flattened.items():
            combined[key].extend(rows)
            if key in CHILD_KEYS:
                for child in rows:
                    child["_validation_source"] = {
                        "raw_path": str(path.resolve()), "raw_sha256": sha256_file(path),
                        "manifest_row": manifest_row.to_dict(),
                        "registry_path": str(registry_path), "registry_sha256": sha256_file(registry_path),
                        "inherited_errors": list(registry_errors) + list(approval_errors) + manifest_errors.get(int(index), []),
                    }
    records = [row for key in CHILD_KEYS for row in combined[key]]
    if not records:
        raise ValueError("No visual candidates were produced")
    _index(records, "record_id", "canonical records")
    prior_origin = json.loads(Path(origin_manifest_path).read_text(encoding="utf-8")) if origin_manifest_path is not None else None
    if prior_origin is not None and not isinstance(prior_origin, dict):
        raise ValueError("Origin manifest must be a JSON object")
    origin = _prepare_origin_anchor(records, prior_origin)
    if origin_manifest_path is not None:
        inventory(Path(origin_manifest_path), "origin_manifest", True)
    validator.validate_cross_asset_semantic_duplicates(records)
    combined["candidate_retrieval"] = [validator.build_retrieval_record(row) for row in records if row["validation_status"] == "valid"]
    combined["release_retrieval"] = [validator.build_retrieval_record(row) for row in records if row["validation_status"] == "valid" and row["release_eligible"]]
    prior_reviews = {row["record_id"]: row for row in combined["review"] if row.get("record_type") not in {"asset", "item"}}
    combined["review"] = [row for row in combined["review"] if row.get("record_type") in {"asset", "item"}] + [
        validator.review_row(row, [prior_reviews.get(row["record_id"], {}).get("extraction_warnings", "")]) for row in records
    ]
    # Existing tables alongside the selected approval are evidence, not input
    # to this recomputation. Freeze them separately without guessing unrelated runs.
    for path in sorted(approval_csv.parent.glob("visual_*.csv")):
        if path != approval_csv:
            inventory(path, "prior_validation", True)
    for path in sorted(approval_csv.parent.glob("visual_*summary.json")):
        inventory(path, "prior_validation", True)
    ai = _ai_notes(Path(notes_path) if notes_path is not None else None, {row["record_id"] for row in records}, {path: row["sha256"] for path, row in sources.items()})
    if notes_path is not None:
        inventory(Path(notes_path), "ai_notes", True)
    ledger, groups_by_id = [], {}
    for row in records:
        record_id = row["record_id"]
        group_id = f"{row.get('source_pdf_sha256', '')}:page:{row.get('page_number', '')}"
        groups_by_id.setdefault(group_id, {
            "group_id": group_id, "page_number": row.get("page_number"), "source_pdf": row.get("source_pdf"),
            "record_ids": [], "group_status": "pending", "human_confirmed": False,
            "human_reviewer": "", "human_reviewed_at": "", "reviewed_group_sha256": "", "notes": "",
        })["record_ids"].append(record_id)
        note = ai.get(record_id, {})
        ledger.append({
            "record_id": record_id, "group_id": group_id, "page_number": row.get("page_number"),
            "asset_id": row.get("asset_id"), "record_type": row.get("_record_type"),
            "source_pdf": row.get("source_pdf"), "image_path": row.get("image_path"), "source_json": row.get("source_json"),
            "step07_content_sha256": row["content_sha256"], "current_v1_content_sha256": record_fingerprint(row),
            "validation_status": row.get("validation_status"), "validation_errors": row.get("validation_errors"),
            "existing_review_status": row.get("review_status"),
            "ai_review_status": note.get("ai_review_status", "not_reviewed"),
            "checked_sources": note.get("checked_sources", []), "findings": note.get("findings", []),
            "suggested_corrections": note.get("suggested_corrections", {}), "uncertainty": note.get("uncertainty", ""),
            "final_disposition": "pending", "human_reviewer": "", "human_reviewed_at": "",
            "reviewed_content_sha256": "", "human_notes": "", "merge_target": "",
            "dependency_ids": None, "dependency_reviewed": False,
        })
    expected_ids = sorted(row["record_id"] for row in records)
    group_document = {"schema_version": 1, "expected_record_ids": expected_ids, "groups": list(groups_by_id.values()), **origin}
    for group in group_document["groups"]:
        group["current_group_sha256"] = group_fingerprint(group, records)
    summary = {
        "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "review_candidate_only", "n_visual_assets": len(manifest), "n_records": len(records),
        "n_pages": len({row.get("page_number") for row in records}),
        "record_types": dict(Counter(row["_record_type"] for row in records)),
        "validation_counts": dict(Counter(row["validation_status"] for row in records)),
        "ai_review_counts": dict(Counter(row["ai_review_status"] for row in ledger)),
        "human_disposition_counts": {"pending": len(records)},
        "n_step07_release_candidates": len(combined["release_retrieval"]),
        "n_v1_released_records": 0, "expected_record_ids": expected_ids,
        "source_inventory": "source_inventory.json", "validation_errors": registry_errors + approval_errors,
        "notice": "Technical validation and existing approvals do not constitute v1 scientific or group review.",
        **origin,
    }
    output_dir.mkdir(parents=True)
    for entry in sources.values():
        if entry.get("frozen_path"):
            target = output_dir / entry["frozen_path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(entry["original_path"], target)
            if sha256_file(target) != entry["sha256"]:
                raise ValueError(f"Input changed during snapshot: {entry['original_path']}")
    _write_json(output_dir / "source_inventory.json", {"schema_version": 1, "files": list(sources.values())})
    _write_jsonl(output_dir / "canonical_records.jsonl", records)
    _write_jsonl(output_dir / "review_ledger.jsonl", ledger)
    _write_json(output_dir / "group_reviews.json", group_document)
    _write_json(output_dir / "review_manifest.json", summary)
    with (output_dir / "review_ledger.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(ledger[0]))
        writer.writeheader()
        for row in ledger:
            writer.writerow({key: _csv_value(value) for key, value in row.items()})
    validation_dir = output_dir / "validation"
    validation_dir.mkdir()
    exports = [
        ("visual_decision_nodes.csv", "nodes", validator.NODE_FIELDS),
        ("visual_recommendation_edges.csv", "edges", validator.EDGE_FIELDS),
        ("visual_drug_actions.csv", "actions", validator.ACTION_FIELDS),
        ("visual_symbols_footnotes.csv", "footnotes", validator.FOOTNOTE_FIELDS),
        ("visual_ordinal_symbols.csv", "ordinal_symbols", validator.SYMBOL_FIELDS),
        ("visual_candidate_retrieval_records.csv", "candidate_retrieval", validator.RETRIEVAL_FIELDS),
        ("step07_release_candidates_NOT_V1.csv", "release_retrieval", validator.RETRIEVAL_FIELDS),
        ("visual_manual_review_queue.csv", "review", validator.REVIEW_FIELDS),
    ]
    for filename, key, columns in exports:
        validator.write_frame(validation_dir / filename, combined[key], columns)
    validator.write_frame(validation_dir / "working_approval_template.csv", validator.merge_approval_rows(combined["approvals"], approvals), validator.APPROVAL_FIELDS)
    lines = ["# Visual review candidate ledger", "", "No v1 human approvals are generated by this tool.", "", f"Records: {len(records)}; pages: {summary['n_pages']}; assets: {len(manifest)}.", ""]
    for row, entry in zip(records, ledger):
        lines.extend([
            f"## {row['record_id']}", "", f"Page {row.get('page_number')} · {row.get('title', '')} · {row['_record_type']}", "",
            f"[Source PDF](<{row.get('source_pdf', '')}#page={row.get('page_number', '')}>) · [Tile](<{row.get('image_path', '')}>) · [Raw JSON](<{row.get('source_json', '')}>)", "",
            f"Technical status: {row['validation_status']}. AI review: {entry['ai_review_status']}. Human disposition: pending.", "",
            "```json", json.dumps({key: value for key, value in row.items() if key not in AUDIT_ONLY_FIELDS}, ensure_ascii=False, indent=2, default=_json_default), "```", "",
            f"Findings: {canonical_json(entry['findings'])}", f"Suggested corrections (not applied): {canonical_json(entry['suggested_corrections'])}", f"Uncertainty: {entry['uncertainty']}", "",
        ])
    (output_dir / "review_ledger.md").write_text("\n".join(lines), encoding="utf-8")
    (output_dir / "REVIEW_GUIDE.md").write_text(REVIEW_GUIDE, encoding="utf-8")
    # Verify that read-only inputs have not changed while validation ran.
    for entry in sources.values():
        if sha256_file(Path(entry["original_path"])) != entry["sha256"]:
            raise ValueError(f"Input changed during preparation: {entry['original_path']}")
    return summary


def verify_review_inputs(review_dir: str | Path) -> dict[str, Any]:
    """Replay the frozen raw inventory independently of editable review tables.

    This detects accidental omission, including an entire asset removed from
    both ledger and group documents. SHA-256 is integrity evidence, not a
    cryptographic human signature or protection against rewriting every input.
    """
    import pandas as pd

    root = Path(review_dir).resolve()
    inventory = json.loads((root / "source_inventory.json").read_text(encoding="utf-8"))
    review_manifest = json.loads((root / "review_manifest.json").read_text(encoding="utf-8"))
    files = inventory.get("files")
    if not isinstance(files, list):
        raise ValueError("Invalid source inventory")
    indexed = _index(files, "original_path", "source inventory")
    frozen: dict[str, Path] = {}
    for original, entry in indexed.items():
        relative = entry.get("frozen_path")
        if relative is None:
            continue
        path = root / relative
        if Path(relative).is_absolute() or not path.resolve().is_relative_to(root) or path.is_symlink():
            raise ValueError(f"Unsafe frozen input path: {relative}")
        if not path.is_file() or sha256_file(path) != entry.get("sha256"):
            raise ValueError(f"Frozen input missing or changed: {relative}")
        frozen[original] = path

    def one(role):
        candidates = [entry for entry in files if entry.get("role") == role]
        if len(candidates) != 1 or candidates[0]["original_path"] not in frozen:
            raise ValueError(f"Review inventory needs exactly one frozen {role}")
        return candidates[0]

    manifest_entry, registry_entry = one("manifest"), one("registry")
    manifest = pd.read_csv(frozen[manifest_entry["original_path"]], keep_default_na=False)
    validator = _module("07_validate_visual_logic_outputs.py")
    registry, registry_errors = validator.load_symbol_registry(frozen[registry_entry["original_path"]])
    base_dir = Path(manifest_entry["original_path"]).parent
    ids, seen_paths = [], set()
    for _, manifest_row in manifest.iterrows():
        original = str(validator.resolve_input_path(manifest_row.get("visual_logic_json"), base_dir).resolve())
        if original in seen_paths:
            raise ValueError("Frozen manifest has duplicate raw JSON asset paths")
        seen_paths.add(original)
        entry = indexed.get(original, {})
        if entry.get("role") != "raw_json" or original not in frozen:
            raise ValueError(f"Frozen manifest references an unfrozen raw JSON: {original}")
        flattened = validator.flatten_payload(
            validator.read_json(frozen[original]), manifest_row, raw_dir=base_dir,
            approvals={}, registry=registry, inherited_errors=registry_errors,
        )
        ids.extend(row["record_id"] for key in CHILD_KEYS for row in flattened[key])
    if seen_paths != {entry["original_path"] for entry in files if entry.get("role") == "raw_json"}:
        raise ValueError("Frozen manifest and raw JSON inventory differ")
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("Frozen raw inventory has no records or duplicate record IDs")
    expected = review_manifest.get("expected_record_ids")
    if not isinstance(expected, list) or len(expected) != len(set(expected)) or set(expected) != set(ids):
        raise ValueError("review_manifest expected IDs do not match frozen raw candidates")
    canonical = _index(load_jsonl(root / "canonical_records.jsonl"), "record_id", "canonical inventory")
    _validate_origin_anchor(list(canonical.values()), review_manifest)
    if set(canonical) != set(ids):
        raise ValueError("Canonical records omit/add IDs relative to frozen raw candidates")
    if review_manifest.get("n_records") != len(ids) or review_manifest.get("n_visual_assets") != len(manifest):
        raise ValueError("Review summary counts do not match frozen raw inventory")
    return {"status": "SUCCESS", "n_assets": len(manifest), "n_records": len(ids), "expected_record_ids": sorted(ids),
            "n_frozen_inputs_verified": len(frozen)}


def assert_review_audit(
    records: list[dict[str, Any]],
    ledger: list[dict[str, Any]],
    group_reviews: dict[str, Any],
) -> list[dict[str, Any]]:
    """Stdlib-only human/content/dependency checks, usable by offline consumers.

    This function is intentionally strict. It cannot prove that a claimed human
    signature is authentic; it ensures that AI fields cannot serve as one, and
    that neither pending rows nor graph context can silently be omitted.
    """
    record_map = _index(records, "record_id", "records")
    ledger_map = _index(ledger, "record_id", "ledger")
    if not isinstance(group_reviews, dict) or not isinstance(group_reviews.get("groups"), list):
        raise ValueError("group_reviews must contain groups and expected_record_ids")
    groups = _index(group_reviews["groups"], "group_id", "groups")
    _validate_origin_anchor(records, group_reviews)
    expected = group_reviews.get("expected_record_ids")
    if not isinstance(expected, list) or not expected or len(expected) != len(set(expected)):
        raise ValueError("Missing or duplicated original expected_record_ids")
    errors = []
    if set(record_map) != set(ledger_map):
        errors.append("canonical records and ledger IDs differ")
    if set(expected) - set(record_map):
        errors.append("original candidates are missing from canonical records")
    membership: dict[str, list[str]] = {}
    for group_id, group in groups.items():
        ids = group.get("record_ids")
        if not isinstance(ids, list) or not ids or len(ids) != len(set(ids)):
            errors.append(f"{group_id}: invalid record_ids")
            continue
        for record_id in ids:
            membership.setdefault(record_id, []).append(group_id)
        if group.get("human_confirmed") is not True or group.get("group_status") != "complete":
            errors.append(f"{group_id}: group not explicitly human-confirmed complete")
        if not str(group.get("human_reviewer", "")).strip() or not _valid_timestamp(group.get("human_reviewed_at")):
            errors.append(f"{group_id}: missing human group signer/time")
        if set(ids) - set(record_map):
            errors.append(f"{group_id}: group contains unknown records")
        elif group.get("reviewed_group_sha256") != group_fingerprint(group, records):
            errors.append(f"{group_id}: stale or missing group content fingerprint")
    if set(membership) != set(record_map) or any(len(ids) != 1 for ids in membership.values()):
        errors.append("every candidate must belong to exactly one reviewed group")
    approved_ids = {record_id for record_id, row in ledger_map.items() if row.get("final_disposition") == "approved"}
    released = []
    for record_id, record in record_map.items():
        row = ledger_map.get(record_id, {})
        disposition = row.get("final_disposition")
        if disposition not in FINAL_DISPOSITIONS:
            errors.append(f"{record_id}: no final human disposition")
        if not str(row.get("human_reviewer", "")).strip() or not _valid_timestamp(row.get("human_reviewed_at")):
            errors.append(f"{record_id}: missing human signer/time")
        if not str(row.get("human_notes", "")).strip():
            errors.append(f"{record_id}: human disposition rationale required")
        if row.get("reviewed_content_sha256") != record_fingerprint(record):
            errors.append(f"{record_id}: stale or missing v1 content fingerprint")
        if row.get("group_id") not in membership.get(record_id, []):
            errors.append(f"{record_id}: ledger group mismatch")
        if disposition == "merged":
            target = row.get("merge_target")
            if target == record_id or target not in approved_ids:
                errors.append(f"{record_id}: merge_target must be another approved record")
        if disposition != "approved":
            continue
        dependencies = row.get("dependency_ids")
        if row.get("dependency_reviewed") is not True or not isinstance(dependencies, list) or any(not isinstance(value, str) for value in dependencies):
            errors.append(f"{record_id}: dependencies not explicitly human-reviewed")
            dependencies = []
        if len(dependencies) != len(set(dependencies)) or record_id in dependencies:
            errors.append(f"{record_id}: duplicate/self dependency")
        for dependency in dependencies:
            if dependency not in approved_ids:
                errors.append(f"{record_id}: dependency {dependency} is not released")
        if record.get("_record_type") == "edge":
            for endpoint in ("from_node", "to_node"):
                matching = [dep for dep in dependencies if dep in record_map and record_map[dep].get("_record_type") == "node" and record_map[dep].get("node_id") == record.get(endpoint)]
                if len(matching) != 1:
                    errors.append(f"{record_id}: {endpoint} lacks a unique explicitly mapped node dependency")
        released.append({**record, "v1_group_id": row.get("group_id"), "v1_dependency_ids": dependencies,
                         "v1_human_reviewer": row.get("human_reviewer"), "v1_human_reviewed_at": row.get("human_reviewed_at"),
                         "v1_reviewed_content_sha256": row.get("reviewed_content_sha256")})
    if not released:
        errors.append("No approved visual evidence: formal v1 cannot be text-only")
    if errors:
        raise ValueError("v1 release blocked:\n" + "\n".join(dict.fromkeys(errors)))
    return released


def _revalidate_canonical(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Recreate Step 07 from hashed raw sources, never trust cached validity."""
    import pandas as pd

    validator = _module("07_validate_visual_logic_outputs.py")
    approvals = {row["record_id"]: validator.approval_template_row(row) for row in records}
    inputs = {}
    for row in records:
        source = row.get("_validation_source")
        if not isinstance(source, dict):
            raise ValueError(f"{row['record_id']}: missing Step 07 validation source; prepare a NEW review version")
        raw_path, registry_path = Path(source["raw_path"]), Path(source["registry_path"])
        for path, key in ((raw_path, "raw_sha256"), (registry_path, "registry_sha256")):
            if not path.is_file() or sha256_file(path) != source.get(key):
                raise ValueError(f"Step 07 input changed or disappeared: {path}")
        source_key = str(raw_path.resolve())
        if source_key in inputs and inputs[source_key] != source:
            raise ValueError(f"Inconsistent Step 07 source mapping: {source_key}")
        inputs[source_key] = source
    rebuilt = []
    for source in inputs.values():
        path = Path(source["raw_path"])
        registry, errors = validator.load_symbol_registry(Path(source["registry_path"]))
        flattened = validator.flatten_payload(
            validator.read_json(path), pd.Series(source["manifest_row"]), raw_dir=path.parent,
            approvals=approvals, registry=registry, inherited_errors=list(dict.fromkeys(errors + source.get("inherited_errors", []))),
        )
        for key in CHILD_KEYS:
            for row in flattened[key]:
                row["_validation_source"] = source
                rebuilt.append(row)
    validator.validate_cross_asset_semantic_duplicates(rebuilt)
    rebuilt_map = _index(rebuilt, "record_id", "revalidated Step 07 records")
    if set(rebuilt_map) != {row["record_id"] for row in records}:
        raise ValueError("Step 07 raw-source record IDs differ from canonical candidate inventory")
    for row in records:
        if record_fingerprint(row) != record_fingerprint(rebuilt_map[row["record_id"]]):
            raise ValueError(f"{row['record_id']}: canonical content differs from freshly normalized Step 07 source")
    return rebuilt_map


def assert_release_ready(records, ledger, group_reviews):
    """Maintainer gate: complete audit plus newly executed Step 07/08 checks."""
    import pandas as pd

    approved = assert_review_audit(records, ledger, group_reviews)
    rebuilt = _revalidate_canonical(records)
    validator = _module("07_validate_visual_logic_outputs.py")
    builder = _module("08_build_enhanced_guideline_kb.py")
    errors = []
    for record in approved:
        fresh = rebuilt[record["record_id"]]
        if fresh.get("validation_status") != "valid" or fresh.get("release_eligible") is not True or fresh.get("_approval_valid") is not True:
            errors.append(f"{record['record_id']}: Step 07 release gate failed")
        try:
            retrieval = validator.build_retrieval_record(fresh)
            if not bool(builder.visual_release_mask(pd.DataFrame([retrieval])).iloc[0]):
                errors.append(f"{record['record_id']}: Step 08 release gate failed")
        except (KeyError, ValueError, TypeError) as exc:
            errors.append(f"{record['record_id']}: malformed Step 08 release record: {exc}")
    if errors:
        raise ValueError("v1 release blocked:\n" + "\n".join(errors))
    return approved
