#!/usr/bin/env python3
"""Assemble a LOCAL controlled review-candidate delivery, never a formal release.

Keeps historical paths inside the audit immutable. Separate navigation maps
point to materialized sources and work after copying to another computer.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
import zipfile
from pathlib import Path

PIPE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def jsonl(path):
    return [json.loads(s) for s in Path(path).read_text(encoding="utf-8").splitlines() if s.strip()]


def _unique(rows, label):
    ids = [row.get('record_id') for row in rows]
    if any(not isinstance(identifier, str) or not identifier for identifier in ids) or len(ids) != len(set(ids)):
        raise ValueError(f"Missing or duplicate {label} record IDs")
    return {row['record_id']: row for row in rows}


def _review_handoff(review, notes):
    """Bind pending ledger and AI observations to the frozen source inventory."""
    import kb_v1_review as helper
    verified = helper.verify_review_inputs(review)
    records = jsonl(review / 'canonical_records.jsonl')
    ledger = jsonl(review / 'review_ledger.jsonl')
    originals, entries = _unique(records, 'canonical'), _unique(ledger, 'ledger')
    expected = set(verified['expected_record_ids'])
    groups = json.loads((review / 'group_reviews.json').read_text(encoding='utf-8'))
    helper._validate_origin_anchor(records, groups)
    group_expected = groups.get('expected_record_ids', [])
    memberships = [identifier for group in groups.get('groups', []) for identifier in group.get('record_ids', [])]
    if (set(originals) != expected or set(entries) != expected or set(group_expected) != expected
            or len(group_expected) != len(expected) or len(memberships) != len(set(memberships)) or set(memberships) != expected):
        raise ValueError('Review ledger, group inventory and frozen candidate records do not match')
    if len(records) != helper.ORIGIN_RECORD_COUNT:
        raise ValueError('This initial handoff requires all 623 original candidates')
    if any(row.get('final_disposition') != 'pending' or any(row.get(key) for key in ('human_reviewer', 'human_reviewed_at', 'reviewed_content_sha256', 'human_notes', 'merge_target', 'dependency_reviewed')) for row in ledger):
        raise ValueError('Initial AI-review handoff must not claim human dispositions or signatures')
    if any(group.get('group_status') != 'pending' or group.get('human_confirmed') is not False or any(group.get(key) for key in ('human_reviewer', 'human_reviewed_at', 'reviewed_group_sha256')) for group in groups['groups']):
        raise ValueError('Initial AI-review groups must remain human-pending')
    inventory = json.loads((review / 'source_inventory.json').read_text(encoding='utf-8'))['files']
    source_hashes = {str(Path(row['original_path']).resolve()): row['sha256'] for row in inventory}
    frozen_notes = [row for row in inventory if row.get('role') == 'ai_notes']
    if len(frozen_notes) != 1 or not frozen_notes[0].get('frozen_path'):
        raise ValueError('Exactly one frozen AI-notes inventory is required')
    authoritative = helper._ai_notes(review / frozen_notes[0]['frozen_path'], expected, source_hashes)
    if set(authoritative) != expected or any(row.get('ai_review_status') == 'not_reviewed' for row in authoritative.values()):
        raise ValueError('Frozen AI notes do not cover all candidate records')
    defaults = {'record_id': '', 'ai_review_status': 'not_reviewed', 'checked_sources': [], 'findings': [], 'suggested_corrections': {}, 'uncertainty': ''}
    def projection(row):
        return {key: row.get(key, default) for key, default in defaults.items()}
    for identifier, row in entries.items():
        if projection(row) != projection(authoritative[identifier]):
            raise ValueError(f'Ledger AI fields differ from frozen AI notes: {identifier}')
        original = originals[identifier]
        if row.get('current_v1_content_sha256') != helper.record_fingerprint(original) or row.get('page_number') != original.get('page_number'):
            raise ValueError(f'Ledger content/page fingerprint differs from canonical: {identifier}')
    note_files = sorted(path for path in notes.iterdir() if path.suffix in {'.jsonl', '.md'})
    if any(path.is_symlink() or not path.is_file() for path in note_files):
        raise ValueError('AI notes must be materialized files, not symlinks/directories')
    coverage = set()
    for path in note_files:
        if path.suffix == '.jsonl':
            selected = helper._ai_notes(path, expected, source_hashes)
            for identifier, row in selected.items():
                if projection(row) != projection(authoritative[identifier]):
                    raise ValueError(f'Delivery AI note differs from frozen AI notes: {identifier}')
            coverage.update(selected)
    if coverage != expected:
        raise ValueError('Delivery AI-note files do not cover the frozen candidate inventory')
    return records, ledger, inventory, note_files


def _file_hashes(directory):
    paths = sorted(directory.rglob('*'))
    if any(path.is_symlink() for path in paths):
        raise ValueError(f'Delivery input must not contain symlinks: {directory}')
    return {path.relative_to(directory).as_posix(): sha(path) for path in paths if path.is_file()}


def _copy_file(source, target, expected):
    if not source.is_file() or sha(source) != expected:
        raise ValueError(f'Delivery source changed before copying: {source}')
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    if sha(target) != expected:
        raise ValueError(f'Delivery copy failed integrity verification: {target}')


def package_candidate(bundle, review, notes, output, make_zip=False, acceptance_dir=None):
    from kb_v1_runtime import verify_bundle
    bundle, review, notes, output = (Path(p).resolve() for p in (bundle, review, notes, output))
    acceptance = Path(acceptance_dir).resolve() if acceptance_dir is not None else None
    for source in (bundle, review, notes, *([acceptance] if acceptance is not None else []), PIPE):
        # PIPE is intentionally an ancestor for normal outputs; only forbid
        # output ancestry of PIPE (which would overwrite broad project data).
        if source == PIPE:
            if output == source or output in source.parents:
                raise ValueError("Delivery output cannot contain the pipeline source directory")
        elif output == source or output in source.parents or source in output.parents:
            raise ValueError("Delivery output and input directories must not overlap")
    if output.exists():
        raise FileExistsError(output)
    zip_path = output.with_suffix('.zip')
    if make_zip and zip_path == output:
        raise ValueError('Delivery directory must not itself end in .zip')
    if make_zip and (zip_path.exists() or zip_path.is_symlink()):
        raise FileExistsError(zip_path)
    manifest = verify_bundle(bundle, allow_candidate=True)
    if manifest["status"] != "review_candidate":
        raise ValueError("This utility packages review candidates only, not formal releases")
    records, ledger, source_inventory, note_files = _review_handoff(review, notes)
    tree_hashes = {directory: _file_hashes(directory) for directory in (bundle, review)}
    note_hashes = {path: sha(path) for path in note_files}
    acceptance_files = {}
    if acceptance is not None:
        report = json.loads((acceptance / "acceptance_results.json").read_text(encoding="utf-8"))
        if report.get("kb_version") != manifest["kb_version"] or report.get("status") != "SUCCESS":
            raise ValueError("Acceptance must succeed on this exact KB version before handoff")
        for name in ("acceptance_results.json", "unit_tests.stderr.txt", "unit_tests.stdout.txt", "faiss_worker.stderr.txt"):
            path = acceptance / name
            if path.is_symlink():
                raise ValueError('Acceptance files must not be symlinks')
            acceptance_files[path] = sha(path)
    source_entries = {str(Path(row['original_path']).resolve()): row for row in source_inventory}
    raw_links = {}
    for row in records:
        name = Path(row['source_json']).name
        matches = [entry for entry in source_inventory if entry.get('role') == 'raw_json' and Path(entry['original_path']).name == name and entry.get('frozen_path')]
        if len(matches) != 1:
            raise ValueError(f'Expected one frozen raw JSON source for candidate: {row["record_id"]}')
        raw_links[row['record_id']] = 'review/' + matches[0]['frozen_path']
    # A zero-candidate tile is still evidence for checking extraction omissions.
    # Copy the entire frozen image inventory, not only images referenced by rows.
    image_entries = {path: entry for path, entry in source_entries.items() if entry.get('role') == 'image'}
    image_pages = {str(Path(row['image_path']).resolve()): row['page_number'] for row in records}
    for path in image_entries:
        if path not in image_pages:
            page_match = re.match(r'^ada_page_([0-9]+)_tile_', Path(path).name)
            if not page_match:
                raise ValueError(f'Cannot assign inventory-only source tile to a page: {path}')
            image_pages[path] = int(page_match[1])
        if image_pages[path] not in {row['page_number'] for row in records}:
            raise ValueError('Inventory tile belongs to a page missing from review navigation')
    overview_entries = {}
    for page in sorted({r['page_number'] for r in records}):
        matches = [row for row in source_inventory if row.get('role') == 'page_overview' and Path(row['original_path']).name == f'ada_page_{page:03d}.png']
        if len(matches) != 1:
            raise ValueError(f'Expected one frozen-inventory overview for page {page}')
        overview_entries[page] = matches[0]
    for path, entry in image_entries.items():
        if not entry or entry.get('role') != 'image' or sha(Path(path)) != entry['sha256']:
            raise ValueError('Candidate image is absent from or differs from source inventory')
    for row in records:
        if image_entries.get(str(Path(row['image_path']).resolve()), {}).get('sha256') != row['image_sha256']:
            raise ValueError('Canonical image fingerprint differs from source inventory')
    for entry in overview_entries.values():
        if sha(Path(entry['original_path'])) != entry['sha256']:
            raise ValueError('Overview changed after AI review')
    output.mkdir(parents=True, exist_ok=False)
    marker = output / ".incomplete"
    marker.write_text("Packaging; do not distribute incomplete output.\n", encoding="utf-8")
    shutil.copytree(bundle, output / "bundle", symlinks=False)
    shutil.copytree(review, output / "review", symlinks=False)
    for source, target in ((bundle, output/'bundle'), (review, output/'review')):
        if _file_hashes(target) != tree_hashes[source]:
            raise ValueError('Delivery input tree changed during packaging')
    (output / "ai_notes").mkdir()
    for path, expected in note_hashes.items():
        _copy_file(path, output / "ai_notes" / path.name, expected)
    tools = output / "tools"
    tools.mkdir()
    for pattern in ("kb_v1*.py", "requirements-v1*.txt"):
        for path in PIPE.glob(pattern):
            shutil.copyfile(path, tools / path.name)
    for name in ("07_validate_visual_logic_outputs.py", "08_build_enhanced_guideline_kb.py"):
        shutil.copyfile(PIPE / name, tools / name)
    shutil.copytree(PIPE / "licenses", tools / "licenses")
    shutil.copyfile(PIPE / "KB_V1_README.md", output / "START_HERE.md")
    for name in ("KB_V1_IMPLEMENTATION_STATUS.md", "KB_V1_QUALITY_REVIEW.md"):
        path = PIPE / name
        if path.is_file():
            shutil.copyfile(path, output / name)
    if acceptance is not None:
        (output / "test_results").mkdir()
        for path, expected in acceptance_files.items():
            _copy_file(path, output / "test_results" / path.name, expected)
    sources = output / "review_sources"
    sources.mkdir()
    path_map = {}
    for path, entry in image_entries.items():
        image = Path(path)
        target = sources / (entry['sha256'][:16] + '_' + image.name)
        if not target.exists():
            _copy_file(image, target, entry['sha256'])
        path_map[str(image)] = target.relative_to(output).as_posix()
        path_map[entry['original_path']] = target.relative_to(output).as_posix()
    for row in records:
        path_map[row['image_path']] = path_map[str(Path(row['image_path']).resolve())]
    for page in sorted({r['page_number'] for r in records}):
        entry = overview_entries[page]
        overview = Path(entry['original_path'])
        _copy_file(overview, sources / overview.name, entry['sha256'])
        path_map[str(overview.resolve())] = f"review_sources/{overview.name}"
    pdf_map = json.loads((bundle / "sources.json").read_text(encoding="utf-8"))
    for row in records:
        path_map[row['source_pdf']] = 'bundle/' + pdf_map[row['source_pdf_sha256']]['relative_path']
    (output / "source_path_map.json").write_text(json.dumps(path_map, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    by_record = {r['record_id']: r for r in records}
    pages_dir = output / "review_pages"
    pages_dir.mkdir()
    links = []
    clinical_fields = ("condition", "patient_variables", "true_branch", "false_branch", "from_node", "to_node", "edge_condition", "arrow_text", "drug_class", "action", "trigger", "strength", "dose_or_use_logic", "caution_or_contraindication", "symbol", "meaning", "applies_to", "row_label", "clinical_dimension", "raw_symbol", "symbol_count", "interpretation", "evidence_text", "evidence_bbox")
    for page in sorted({r['page_number'] for r in records}):
        members = [r for r in ledger if r['page_number'] == page]
        page_tiles = [(path, image_entries[path]) for path in sorted(image_entries) if image_pages[path] == page]
        tile_links = ' · '.join(f"<a href='../{html.escape(path_map[path], quote=True)}'>{html.escape(Path(path).name)}</a>" for path, _ in page_tiles)
        links.append(f"- [PDF page {page} / printed S{182+page}: {len(members)} candidates](review_pages/page_{page:03d}.html)")
        parts = ["<!doctype html><meta charset='utf-8'><title>ADA AI review candidate</title>",
                 "<style>body{font:16px system-ui;max-width:1100px;margin:2em auto;padding:0 1em}article{border:1px solid #bbb;padding:1em;margin:1em 0}pre{white-space:pre-wrap;overflow-wrap:anywhere}img{max-width:100%;max-height:750px} .notice{background:#fff1cf;padding:1em}</style>",
                 f"<h1>PDF page {page} / S{182+page}</h1><p class='notice'>AI initial review only. All final dispositions pending. Do not use this page as released guideline evidence.</p>",
                 f"<p><a href='../review_sources/ada_page_{page:03d}.png'>Open full overview</a> · <a href='../SOURCE_NAVIGATION.md'>Navigation</a></p>",
                 f"<h2>All {len(page_tiles)} original detail tiles</h2><p>Includes tiles with no extracted candidates: inspect these for omissions.</p><p>{tile_links}</p>",
                 f"<img src='../review_sources/ada_page_{page:03d}.png' alt='Original page overview'>"]
        for row in members:
            original = by_record[row['record_id']]
            rid = html.escape(row['record_id'])
            fields = {k:original[k] for k in clinical_fields if original.get(k) not in (None, '', [])}
            tile = html.escape(path_map[original['image_path']])
            raw_link = html.escape(raw_links[row['record_id']], quote=True)
            parts += [f"<article><h2>{rid}</h2><p>AI: {html.escape(row['ai_review_status'])}; technical: {html.escape(row['validation_status'])}; human: PENDING</p>",
                      f"<a href='../{tile}'>Open original detail tile</a> · <a href='../{raw_link}'>Open frozen original extraction JSON</a><h3>Original normalized content (not corrected)</h3><pre>{html.escape(json.dumps(fields, ensure_ascii=False, indent=2))}</pre>",
                      "<h3>AI findings</h3><ul>" + ''.join('<li>'+html.escape(s)+'</li>' for s in row['findings']) + "</ul>",
                      "<h3>Proposals only</h3><pre>"+html.escape(json.dumps(row['suggested_corrections'],ensure_ascii=False,indent=2))+"</pre>",
                      "<p>"+html.escape(row['uncertainty'])+"</p></article>"]
        (pages_dir / f"page_{page:03d}.html").write_text('\n'.join(parts), encoding="utf-8")
    (output / "SOURCE_NAVIGATION.md").write_text("# Full visual review candidate — 623 records\n\nNo human approvals supplied by AI. Original source strings/fingerprints are retained; links use separate relative-path mappings. Open these pages locally in a browser.\n\n"+'\n'.join(links)+"\n\nEdit review copies only. See START_HERE.md for correction and approval workflow. Do not upload this controlled source bundle publicly.\n",encoding="utf-8")
    inventory = {p.relative_to(output).as_posix(): sha(p) for p in sorted(output.rglob('*')) if p.is_file() and p != marker}
    result = {"schema_version":1, "status":"review_candidate_only", "formal_v1_completed":False, "kb_version":manifest['kb_version'],
              "visual_candidates":len(records), "human_pending":len(ledger), "released_visual_records":0,
              "source_tiles":len(image_entries), "zero_candidate_source_tiles":sum(path not in {str(Path(row['image_path']).resolve()) for row in records} for path in image_entries),
              "files":inventory, "sharing":"Controlled local delivery only; source permissions must be confirmed before sharing."}
    (output / "delivery_manifest.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    if make_zip:
        with zipfile.ZipFile(zip_path, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(output.rglob('*')):
                if path.is_file() and path != marker:
                    archive.write(path, Path(output.name) / path.relative_to(output))
        result['zip_sha256'] = sha(zip_path)
        result['zip_path'] = str(zip_path)
    marker.unlink()
    return {k:v for k,v in result.items() if k != 'files'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('bundle','review','notes','output'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--zip',action='store_true')
    parser.add_argument('--acceptance', type=Path)
    args = parser.parse_args()
    print(json.dumps(package_candidate(args.bundle,args.review,args.notes,args.output,args.zip,args.acceptance),ensure_ascii=False,indent=2))
