# ADA Visual Guideline Manual-Review Package

## Critical source-evidence warning

This package contains **model-generated candidates and audit metadata only**.
It deliberately excludes the ADA PDF, rendered page/tile images, raw per-asset
model JSON, released retrieval records, the enhanced knowledge base, and vector
indexes. It is not sufficient evidence by itself.

Reviewers must use controlled copies of the source PDF and rendered images and
verify that their SHA-256 hashes match `package_inventory.json`. Do not approve
a clinical statement from these CSVs alone. The expected source-PDF hash is:

- `7c2913f79b61bc60f8f51327bb73b80391988fd277116b29e57df4242b2a2404`

- Step 06 model: `qwen3-vl:8b-instruct`
- Step 06 model digest: `0533d74300e4f9bc367d675d4e64ffd073d50ff16a2b4096cc2e8a1cf8c96319`
- Step 06 initial run finished: `2026-08-23T16:30:47.064491+00:00`
- Step 06 retry/merge completed: `2026-08-23T16:46:27.965395+00:00`


## Package contents

| File | Role | Data rows |
| --- | --- | ---: |
| `extraction_run.json` | Step 06 execution metadata | — |
| `guideline_symbol_registry.csv` | Symbol semantics used by Step 07 | 10 |
| `visual_candidate_retrieval_records.csv` | Valid, unreleased retrieval candidates | 416 |
| `visual_decision_nodes.csv` | Detailed decision-node candidates | 168 |
| `visual_drug_actions.csv` | Detailed medication-action candidates | 220 |
| `visual_logic_manifest.csv` | Step 06 asset and source-hash manifest | 60 |
| `visual_logic_summary.json` | Step 07 validation summary | — |
| `visual_manual_review_queue.csv` | Validation and review triage queue | 760 |
| `visual_ordinal_symbols.csv` | Detailed ordinal-symbol candidates | 22 |
| `visual_recommendation_edges.csv` | Detailed recommendation-edge candidates | 136 |
| `visual_review_approvals.csv` | Reviewer-editable approval sheet | 623 |
| `visual_symbols_footnotes.csv` | Detailed symbol and footnote candidates | 77 |

`package_inventory.json` records all row counts, source hashes, and checksums
for the copied review files and this README. `SHA256SUMS` also covers the
inventory itself. Recalculate the checksums before review if the package was
transferred outside Git.

Import every CSV column as **text** in spreadsheet software. Clinical `+`
markers such as `+HF`, `++`, and `+++++` can otherwise be interpreted as
formulas or altered during automatic type detection. Never approve a value
whose imported display differs from the raw CSV text.

The checksums attest the exported baseline. An authorized edit to
`visual_review_approvals.csv` will intentionally change that file's checksum.
Preserve the baseline commit and review the Git diff; do not regenerate the
inventory or checksums to conceal which review fields changed.

## How to review

1. Open `visual_manual_review_queue.csv` for triage. Asset/item rows are
   diagnostics and cannot be approved directly.
2. Join each clinical `record_id` in `visual_review_approvals.csv` to the
   appropriate detailed table: decision nodes, recommendation edges, drug
   actions, symbols/footnotes, or ordinal symbols.
3. Locate the exact source page/tile using `asset_id`, `page_number`, evidence
   text/bounding box, and the controlled image whose hash matches the manifest.
4. Check both correctness and completeness. Review the complete figure/table,
   not just isolated rows; an approved edge does not prove that both endpoint
   nodes or the full pathway are complete.
5. In `visual_review_approvals.csv`, do not change `record_id`, asset/item IDs,
   or `content_sha256`. Set the review status, accountable reviewer,
   timezone-aware `reviewed_at`, optional allowed `corrections_json`, and notes.
6. Return the edited approval CSV to the pipeline operator. The operator must
   run Step 07 again against the same canonical Step 06 output. New or corrected
   fingerprints require another review pass before release.

Do not edit generated detail tables, the Step 06 manifest, package inventory,
or checksum file to make a record pass. This package contains no released
guideline evidence; release remains governed by Steps 07 and 08.
