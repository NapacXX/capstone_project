"""Exact source-bound mixed-page text policy for the two-table supplement.

Page 22 has clinical prose as well as Table 9.4. Only the three frozen PDF
text chunks below are transformed. Originals stay intact in records.jsonl.
This is a retrieval/excerpt transformation, not a change to human approvals.
"""

from __future__ import annotations

import hashlib

SOURCE_SHA256 = "7c2913f79b61bc60f8f51327bb73b80391988fd277116b29e57df4242b2a2404"
TABLE_RECORD_ID = "ada2026-ch9-table-9-4::draft-001-en"
TEXT_PINS = {
    "text_p022_001_01": ("2edb8bfbacd4d51e362b58dc260a4357a8cf58d27170672700f8243fc28e56ff", 584),
    "text_p022_001_02": ("676a081459c8f401632913edb5a0f6a71de43e318f10e26997f842b079abda77", 0),
    "text_p022_001_03": ("bea70a5918cb0b62966859e0c26a044683a68b6c9cd6d418c2bace7efd1fee26", 0),
}


def _selected(records):
    by_id = {row['record_id']: row for row in records}
    for suffix, (digest, keep) in TEXT_PINS.items():
        identifier = SOURCE_SHA256 + ':' + suffix
        row = by_id.get(identifier)
        if (row is None or row.get('source_id') != SOURCE_SHA256
                or row.get('page_number') != 22 or row.get('content_type') != 'text'
                or hashlib.sha256(row.get('source_text', '').encode()).hexdigest() != digest):
            raise ValueError('Pinned mixed-page source text changed or missing: ' + identifier)
        yield row, keep


def _projection(row, keep):
    return {
        'retrieval_text': row['source_text'][:keep].strip(),
        'indexable': bool(keep),
        'cleaning_applied': True,
        'evidence_text': row['source_text'][:keep].strip(),
        'source_excerpt_char_range': [0, keep],
        'original_source_text_retained_in': 'records.jsonl',
        'source_text_scope': (
            'Exact clinical-prose excerpt from a mixed PDF page, not a full page or an individually reviewed clinical recommendation. '
            'The Table 9.4 span is represented by its separately approved complete parent. '
            'The original extracted chunk remains unchanged in records.jsonl; the final 9.31b sentence continues on the next PDF page.'
            if keep else 'Audit-only original: Table 9.4 extraction plus any nonclinical section/running footer.'
        ),
        'supplement_text_policy': 'ada2026-p22-exact-excerpts-v1',
        'related_approved_chart': TABLE_RECORD_ID,
    }


def apply_supplement_text_policy(records):
    """Retain clinical prefix, suppress duplicate table text, never infer spans."""
    selected = list(_selected(records))  # Fail before changing any row.
    log = []
    for row, keep in selected:
        original = row['retrieval_text']
        row.update(_projection(row, keep))
        log.append({
            'record_id': row['record_id'],
            'reason': 'pinned_mixed_page_table_span_superseded_preserving_clinical_prose' if keep else 'pinned_table_fragment_and_nonclinical_footer_audit_only',
            'replacement_record_id': TABLE_RECORD_ID,
            'removed_retrieval_text': original[keep:],
            'source_text_preserved': True,
            'retained_source_char_range': [0, keep],
        })
    return log


def verify_supplement_text_policy(records):
    """Consumer-side fail-closed verification, independent of manifest rehash."""
    for row, keep in _selected(records):
        for field, expected in _projection(row, keep).items():
            if row.get(field) != expected:
                raise ValueError('Mixed-page prose/table policy differs from pinned source: ' + row['record_id'] + '/' + field)
