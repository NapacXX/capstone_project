# ADA RAG Pipeline — Manual Review Guide

Status date: 2026-08-23

## 1. Purpose

This guide explains which parts of the ADA guideline knowledge-base and retrieval pipeline require human review, who should perform that review, and how review decisions should be recorded.

The most important rule is that **vision-model output is an untrusted transcription**. Automated validation can confirm that a record has the expected schema, provenance, graph structure, symbol format, and fingerprints. It cannot confirm that the record is clinically faithful to the source figure or table. Every visual record must therefore be compared with the source and explicitly approved before it can enter the enhanced knowledge base.

This pipeline supports evidence extraction and retrieval for the Hyperglycemia AI Capstone. It is not a clinical decision system, and review of extracted guideline evidence is separate from downstream review of AI-generated treatment recommendations.

## 2. What Requires Manual Review

There are three different review standards in this project:

1. **Software-mandatory release review:** every visual child record intended for release must be valid and explicitly approved against its current fingerprint.
2. **Scientifically required review:** source extraction, rendering coverage, visual-extraction accuracy and completeness, retrieval relevance, and downstream adherence scoring must be evaluated before making research claims.
3. **Recommended governance QA:** independent second review, adjudication, inter-reviewer agreement, and controlled retention strengthen the study, but the current code does not enforce them.

| Pipeline area | Manual review level | Why review is needed | Release consequence |
| --- | --- | --- | --- |
| Source PDF identity and edition | Required for a reproducible study | The team must confirm that the intended ADA edition and section were used and that the recorded hash belongs to the authorized source. | Do not compare runs built from different or uncertain source versions. |
| Step 01–02 text and table/page extraction | Recommended quality review | PDF text can have reading-order errors, broken headings, flattened tables, copyright/reference noise, and incorrect chunk boundaries. | Text records are not human-gated by the code, so sampling is needed before scientific use. |
| Step 05 page selection and rendering | Required before reviewing visual candidates | A correct caption may still be associated with a poor crop, wrong orientation, unreadable tile, or missing continuation page. | Do not approve records from an inadequate image asset. |
| Step 06 visual extraction | **Mandatory for every candidate before release** | Nodes, arrows, actions, symbols, thresholds, and footnotes may be omitted, misread, or invented. | Step 06 output must remain quarantined. |
| Step 07 validation warnings and review queue | **Mandatory** | Warnings are not automatically invalid. They require source-based judgment. | A structurally valid but unapproved record remains unreleased. |
| Corrections to a visual record | **Mandatory two-pass review** | A correction changes the effective fingerprint. The corrected record must be regenerated and approved again. | A stale approval cannot release a corrected record. |
| Step 08 enhanced-KB manifest | Required release sign-off | The team must verify that only approved records entered the KB and that excluded/unapproved counts are understood. | Do not call the KB visual-enhanced if the visual count is zero. |
| Retrieval results | Required for claims about retrieval quality | Similarity scores do not establish relevance; duplicated page/chunk hits and nonclinical noise can rank highly. | Do not claim improved retrieval without labeled review. |
| Guideline-adherence evaluation | Required clinical/scientific review | Extracted evidence, retrieved evidence, LLM recommendations, and adherence judgments are separate objects. | Do not treat retrieval or extraction success as clinical correctness. |

## 3. Current Review Status

The full extraction completed on 2026-08-23, and a sanitized review package is now available at:

`open_source_kb_pipeline/manual_review_packages/ada_principles_20260823_qwen3vl_0533d743/`

The completed run used Ollama 0.32.9 and `qwen3-vl:8b-instruct` against all 60 detail tiles. Fifty-nine tiles completed on the first run. The structure pass for dense Figure 9.4 tile `page_009_tile_r02_c02` reached its output limit, so that one pass was rerun with a larger bounded budget and merged through a provenance-checked utility. The completed manifest contains 60 assets, all with `extracted_unvalidated` status.

Step 07 produced the following review baseline:

| Metric | Count |
| --- | ---: |
| Decision nodes | 168 |
| Directed edges | 136 |
| Medication actions | 220 |
| Footnote/legend records | 77 |
| Ordinal-symbol records | 22 |
| Total child records | 623 |
| Structurally valid, unreleased candidates | 416 |
| Invalid/quarantined child records | 207 |
| Manual-review queue rows | 760 |
| Pending approval rows | 623 |
| Human-approved/released records | **0** |

The 760 queue rows consist of the 623 child records plus 60 asset-level and 77 item-level diagnostic rows. Asset/item diagnostics cannot be approved directly. The 416 valid candidates passed automated schema and provenance checks, but **have not been verified as clinically faithful**. The 207 invalid records remain quarantined and must be corrected through the permitted workflow, re-extracted, or rejected; they must not be bulk-approved.

Manual spot checks demonstrate why review remains mandatory. A Figure 9.4 goal node passed structural validation even though its extracted `true_branch` oversimplified a multi-branch visual pathway. Table 9.2 remains difficult because its sideways row/column layout is not well represented by the current flattened action schema. All 22 ordinal-symbol records are currently invalid against the symbol-registry rules. These are known review and extraction issues, not released guideline facts.

The validator used a warned manifest-title fallback for 184 child records whose model item title was blank; 95 of those are among the 416 valid candidates. The fallback supplies only a known figure/table identifier and does not add clinical logic, but those records should be prioritized because missing titles can indicate weaker extraction context.

The project therefore still has **zero released visual records**, which is the correct fail-closed state. The previous “0 released” result did not mean that no extraction ran; it meant that no model-generated record had completed accountable source review. Do not manually change that count. Complete record-by-record review, rerun Step 07, and only then build the strict enhanced KB.

## 4. Reviewer Roles

The code requires a nonempty reviewer identity and timestamp, but it cannot verify qualifications. The team should assign roles explicitly.

### Pipeline operator

- Runs Steps 05–08 with the documented environment and model.
- Confirms that the source, image, model, and run manifests are complete.
- Prepares the review package without changing clinical content.
- Does not approve records merely to make a strict command pass.

### Clinical/content reviewer

- Has sufficient diabetes/pharmacotherapy and guideline-reading expertise to compare the candidate with the source.
- Reviews clinical meaning, thresholds, branches, medication actions, cautions, symbols, and footnotes.
- Records an accountable reviewer name or team ID, timestamp, decision, and rationale.

### Technical/provenance reviewer

- Confirms page, asset, bounding-box, image, PDF hash, source JSON, and manifest consistency.
- Checks that the candidate is associated with the correct tile and source evidence.
- Confirms that no draft or stale record entered Step 08 or the vector index.

### Second reviewer or adjudicator

A second review is strongly recommended for all high-risk items and a prespecified stratified sample of the remainder: dense decision algorithms, medication dosing or kidney-function statements, contraindications, ambiguous arrows, cross-tile relationships, and ordinal symbol counts. Record independent decisions before adjudication, report agreement by record type, and document the resolution rather than resolving disagreements by majority guessing.

### Retrieval evaluator

- Labels retrieval results independently of the extraction reviewer where practical.
- Checks relevance, missing critical evidence, provenance correctness, duplicates, and unsupported context.
- Compares the text-only and approved enhanced KBs on the same clinician-authored queries.

### Data steward

- Preserves dated, read-only review snapshots and reviewer identities in the team-approved audit store.
- Controls access to copyrighted source material and non-Git output artifacts.
- Prevents reruns from silently replacing the only copy of an approval history.

Reviewer credentials, independence, dual review, and adjudication are governance requirements established by the team; the current scripts do not verify them.

## 5. Required Review Package

Do not begin approval until the following artifacts refer to the same completed run:

- Authorized source PDF and its SHA-256.
- Step 01 page manifest.
- Step 05 `page_image_manifest.csv`.
- Whole-page overview images for context.
- Exact high-resolution tile or crop used for inference.
- Completed Step 06 `visual_logic_manifest.csv` and `extraction_run.json`.
- Per-asset Step 06 JSON.
- Step 07 `visual_candidate_retrieval_records.csv`.
- Step 07 structured node, edge, action, footnote, and symbol CSVs.
- `visual_manual_review_queue.csv`.
- `visual_review_approvals.csv`.
- `guideline_symbol_registry.csv`.
- Step 07 `visual_logic_summary.json`.

The overview provides page context; the tile provides readable detail. Reviewers should inspect both when a relationship may extend beyond the tile.

The Git-tracked sanitized package supplies the review tables and provenance metadata, but deliberately omits the copyrighted PDF/images and raw model JSON. The pipeline operator or data steward must provide those omitted artifacts through the team's approved controlled channel. The Git package alone is not sufficient for clinical/source approval.

The approval sheet and review queue do not contain every extracted clinical field. Join each clinical candidate by `record_id` to its detailed table before deciding:

- `visual_decision_nodes.csv`
- `visual_recommendation_edges.csv`
- `visual_drug_actions.csv`
- `visual_symbols_footnotes.csv`
- `visual_ordinal_symbols.csv`

Do not approve a record by reading only `visual_review_approvals.csv` or the condensed review queue. Inspect the detailed record and the exact source image/PDF. Before every rerun of Step 07, preserve a dated, read-only snapshot of the complete review package; Step 07 rewrites the approval sheet for the current candidate set, and rows may disappear when candidate identities change.

When using Excel or another spreadsheet application, import every CSV column as text. Clinical values beginning with `+` can be reinterpreted as formulas or altered by automatic type detection. Compare any transformed display with the raw CSV before recording a decision.

## 6. End-to-End Review Procedure

### Step 1 — Confirm that Step 06 is complete

Before reviewing candidates, verify:

- every expected tile was attempted;
- the final manifest and run record exist;
- model tag and digest are recorded;
- no placeholder output is being mistaken for inference;
- truncated or malformed passes are identified;
- PDF and image hashes match the rendering manifest; and
- the source images remain available to the reviewer.

The completed local source directory for this review cycle is `outputs/visual_logic_raw_review_20260823_completed/`. It passed the 60-asset completeness check. Its raw model JSON and source images remain local and Git-ignored; the tracked review package contains sanitized CSV/JSON metadata only.

### Step 2 — Generate candidates and the approval sheet

From `Data-Analyses/adaguideline/open_source_kb_pipeline/`, run Step 07 against the completed canonical Step 06 directory:

```bash
python 07_validate_visual_logic_outputs.py \
  --raw-dir outputs/visual_logic_raw \
  --output-dir outputs/visual_logic_structured \
  --symbol-registry guideline_symbol_registry.csv \
  --require-valid-candidates
```

This first run should create pending rows in `visual_review_approvals.csv`. It should not release records merely because they are structurally valid.

### Step 3 — Triage the manual-review queue

Use `visual_manual_review_queue.csv` to prioritize:

1. invalid records and provenance/hash failures;
2. records the model marked for review;
3. validation or extraction warnings;
4. cross-tile arrows, missing endpoints, and duplicates;
5. medication doses, thresholds, cautions, and contraindications;
6. symbol/footnote associations; and
7. otherwise valid records still awaiting mandatory approval.

An invalid record should not be approved. Determine whether it needs a permitted correction, a new crop/model run, or rejection.

Asset- and item-level rows in the manual-review queue are diagnostic rather than releasable evidence records. Resolve the underlying image, manifest, payload, or provenance problem and rerun the pipeline; do not try to approve those rows directly.

### Step 4 — Compare each candidate with the source

For every candidate, verify all of the following:

- **Source identity:** correct PDF, page, figure/table, tile, and item.
- **Evidence:** quoted evidence is present and visibly supports the entire candidate—not just part of it.
- **Evidence location:** the normalized 0–1000 evidence box points to the correct pixels and does not hide an off-tile dependency. An empty box may produce only an automated warning, so the reviewer must manually localize and document the supporting region.
- **Completeness:** no clinically meaningful modifier, branch, threshold, qualifier, or footnote is omitted.
- **No invention:** strength, dosing, eGFR, contraindication, priority, or direction is not inferred beyond what is visible.
- **Context:** the candidate remains correct when the whole-page overview, legend, and adjacent tiles are considered.
- **Provenance:** source and image hashes, source JSON, and asset ID are consistent.
- **Duplicates:** overlapping tiles have not produced duplicate or paraphrased versions of the same fact or relationship. Automated checks do not replace human reconciliation of fuzzy and cross-tile duplicates.

Review both **correctness and completeness**. Candidate-only review can catch false statements but cannot reveal a node, edge, table row, footnote, or symbol that the model never emitted. Compare every target figure/table in full with the source and record clinically meaningful omissions.

For algorithms and tables, review a coherent figure/table bundle in addition to individual records. The release gate operates record by record; for example, it can release an approved edge without proving that every endpoint node or the complete graph was also approved and released. Reconcile all nodes, edges, actions, footnotes, symbols, and cross-tile links before treating a figure as reconstructed.

### Step 5 — Choose a review status

Only these statuses are accepted:

| Status | Use when |
| --- | --- |
| `pending` | Review has not been completed or evidence is still being gathered. |
| `approved` | The effective record is fully supported by the source and all release requirements are satisfied. |
| `rejected` | The record is hallucinated, materially incorrect, unsupported, or unsuitable for correction/release. |
| `corrections_required` | The record is potentially usable after a permitted, source-supported correction. |

Do not use `approved` to mean “mostly correct.” A record is the unit of release; any unsupported clinical assertion in that record must be corrected or rejected.

### Step 6 — Complete `visual_review_approvals.csv`

Preserve the generated identity and fingerprint fields:

- `record_id`
- `asset_id`
- `page_number`
- `record_type`
- `item_id`
- `local_id`
- `content_sha256`

For an approval, enter:

- `review_status=approved`
- `reviewer=<accountable reviewer name or team ID>`
- `reviewed_at=<timezone-aware ISO-8601 timestamp>`
- `review_notes=<concise source-check rationale>`
- optional `corrections_json`, only when a correction is needed

Example timestamp: `2026-08-23T14:30:00+08:00`.

Never manually create a new fingerprint, copy approval metadata between records, edit `visual_release_records.csv`, or set approval fields programmatically without record-by-record source review.

### Step 7 — Apply corrections safely

`corrections_json` must be a shallow JSON object and may change only the fields below.

| Record type | Permitted correction fields |
| --- | --- |
| Node | `condition`, `patient_variables`, `true_branch`, `false_branch`, `requires_manual_review`, `evidence_text`, `evidence_bbox` |
| Edge | `from_node`, `to_node`, `edge_condition`, `arrow_text`, `requires_manual_review`, `evidence_text`, `evidence_bbox` |
| Action | `drug_class`, `action`, `trigger`, `strength`, `dose_or_use_logic`, `caution_or_contraindication`, `requires_manual_review`, `evidence_text`, `evidence_bbox` |
| Footnote | `symbol`, `meaning`, `applies_to`, `requires_manual_review`, `evidence_text`, `evidence_bbox` |
| Ordinal symbol | `row_label`, `clinical_dimension`, `raw_symbol`, `symbol_type`, `symbol_count`, `ordinal_level`, `direction`, `symbol_role`, `interpretation`, `requires_manual_review`, `evidence_text`, `evidence_bbox` |

Adding or changing a correction changes the effective fingerprint and resets the record to `pending`. The correct workflow is:

1. enter the correction and run Step 07;
2. inspect the correction-applied record and its new fingerprint;
3. approve that new fingerprint in a second review pass; and
4. rerun Step 07.

Do not alter immutable provenance or identity fields through corrections.

Do not directly edit the raw Step 06 JSON or the generated detailed candidate tables to repair clinical content. Record permitted source-supported changes only through `corrections_json`; otherwise the fingerprint and audit trail no longer describe what was reviewed.

### Step 8 — Run the strict release gate

After approval, rerun Step 07:

```bash
python 07_validate_visual_logic_outputs.py \
  --raw-dir outputs/visual_logic_raw \
  --output-dir outputs/visual_logic_structured \
  --symbol-registry guideline_symbol_registry.csv \
  --require-valid-candidates \
  --require-released-records
```

Verify that:

- the command succeeds;
- `n_human_approved_release_records` is greater than zero;
- every released row has `validation_status=valid`;
- `review_status` and `approval_status` are `approved`;
- `release_status=released`;
- `human_approved=true`;
- `requires_manual_review=false`;
- reviewer identities and approval timestamps match; and
- content and effective-content hashes match.

The strict flags prove that at least one valid candidate and at least one released record exist; they do not prove extraction completeness or whole-figure coverage. Before batch sign-off, reconcile counts for expected/attempted/completed assets, extracted candidates, valid and invalid records, each approval status, corrections, rejections, and released records. Explain every material difference.

### Step 9 — Build and inspect the enhanced KB

```bash
python 08_build_enhanced_guideline_kb.py \
  --kb-dir outputs/full_guideline_kb \
  --visual-dir outputs/visual_logic_structured \
  --output-dir outputs/enhanced_guideline_kb \
  --require-visual-records
```

Review `enhanced_guideline_kb_manifest.json` and verify:

- the expected base-record count;
- a nonzero visual-record count;
- the number excluded as unapproved;
- the total enhanced count;
- unique chunk IDs; and
- preserved page, figure/table, image, and review provenance.

Only after this check should the enhanced corpus be re-indexed and used for retrieval comparison.

## 7. Record-Specific Review Checklist

### Decision nodes

- Condition text exactly matches the source meaning.
- Patient variables and thresholds are complete.
- AND/OR relationships are preserved.
- True/false branches are not swapped or invented.
- Priority and sequencing language is retained.

### Directed edges and arrows

- Source and destination nodes are correct.
- Arrow direction is visually supported.
- `Yes`, `No`, or other branch labels belong to the correct edge.
- Dashed feedback paths are not confused with primary flow.
- Off-tile endpoints are resolved using adjacent tiles and the overview.
- A table layout is not incorrectly represented as a graph.

### Medication actions

- Drug class and action are correctly paired.
- Trigger/condition is correct and complete.
- Strength or preference language is explicit in the source.
- Dose/use logic is supported by the cited evidence.
- Kidney-function, eGFR, caution, and contraindication details are not inferred.
- Alternatives, combinations, and exceptions are preserved.

### Footnotes and legends

- Symbol and meaning are correct.
- The footnote applies to the correct box, row, column, or action.
- Scope is not expanded beyond the visible source.
- Superscripts and punctuation are not confused with clinical symbols.

### `+` and `$` ordinal symbols

- Count each glyph directly from the source.
- Preserve row and clinical-dimension association.
- Verify `symbol_type`, ordinal level, direction, role, and interpretation against `guideline_symbol_registry.csv`.
- Distinguish an ordinal `+` scale from a presence marker or prefix such as `+HF`.
- Distinguish literal currency from an ordinal cost scale.
- Treat every registry-mapped symbol as requiring source review.

The registry itself also needs diabetes/pharmacotherapy expert sign-off and version control. Its mappings are hypotheses about context, not self-validating truth. Do not convert `+`/`$` interpretations into hard guideline-adherence rules until the symbol, legend, row/column scope, and clinical meaning have been verified in the source.

### Tables, especially Table 9.2

- Confirm medication class remains associated with the correct row.
- Confirm every property remains under the correct column header.
- Check continuation across all four pages.
- Pages 11–13 are sideways; page 14 has a sideways table region plus upright prose.
- Rotate only table regions on page 14; do not rotate the entire mixed-orientation page.
- Verify footnotes and repeated symbols across page breaks.
- Do not approve flattened action records as a complete table reconstruction unless row/column relationships have been explicitly checked.

## 8. Manual Review Outside the Visual Release Gate

### Text extraction and KB construction

Review a stratified sample covering recommendations, medication classes, thresholds, tables, references, and pages with complex layout. Check source fidelity, chunk boundaries, missing text, duplicate content, and whether copyright/bibliography material should be excluded or down-ranked.

The current base branch has no equivalent human release gate. Its eight “structured table” records are whole-page caption/page-text records, not validated row/column cell reconstructions, so clinically consequential table and dosing content warrants 100% source comparison before formal extraction claims. Also confirm that Step 05 selected every visual target needed for the study: its defaults include Figures 9.1–9.5 and Tables 9.2–9.3, but omit Tables 9.1 and 9.4 unless extra targets or `--render-all` are requested.

### Retrieval evaluation

For representative capstone cases, have reviewers label each retrieved record as relevant, partially relevant, irrelevant, or harmful/misleading. Record the expected source page or logic record and calculate retrieval measures such as recall@k and precision@k. Compare text-only and approved enhanced retrieval on the same cases.

### Guideline-adherence evaluation

The following must remain separate:

1. guideline knowledge extraction;
2. retrieval of potentially relevant evidence;
3. generation of an AI treatment recommendation; and
4. evaluation of that recommendation against reviewed evidence.

Clinical reviewers should define adherence criteria, acceptable alternatives, exceptions, uncertainty handling, and adjudication rules before reporting performance.

## 9. Review Acceptance Rules

Approve a visual record only when all of these are true:

- the source asset and provenance are correct;
- the entire clinical statement is visibly supported;
- the record is complete enough to stand alone in retrieval;
- graph, table, symbol, and footnote relationships are correct;
- warnings have been consciously resolved;
- no unsupported inference remains;
- the current fingerprint is the one reviewed; and
- reviewer identity, timestamp, and rationale are recorded.

Reject or require correction when any of these are true:

- evidence is missing, ambiguous, or off-tile;
- arrow direction or endpoint is uncertain;
- row/column association is uncertain;
- a threshold, dose, strength, or caution was invented or incompletely quoted;
- a symbol count or meaning is uncertain;
- the source/image hash changed;
- the approval fingerprint is stale; or
- a qualified reviewer cannot confidently support the record.

## 10. Audit Trail and Reproducibility

For each released batch, retain an authorized audit package containing:

- source edition and PDF hash;
- page-image manifest and image hashes;
- Ollama version, model tag, and model digest;
- Step 06 run manifest and warnings;
- Step 07 candidate, review, approval, release, and summary files;
- reviewer roles and adjudication notes;
- Step 08 enhanced-KB manifest;
- vector-index manifest; and
- retrieval-evaluation dataset and labels.

Create the dated, read-only snapshot **before each rerun**, not only after release. The repository ignores `outputs/` and the source PDF. Do not force-add copyrighted source material or model-generated artifacts without an explicit governance decision. Store review packages in an access-controlled, versioned location approved by the team.

## 11. Practices to Avoid

- Do not bulk-approve candidates to obtain a nonzero release count.
- Do not let the extraction model approve its own output.
- Do not equate `validation_status=valid` with source or clinical correctness.
- Do not edit raw extraction JSON, generated candidate tables, IDs, hashes, or release CSVs to make a record pass.
- Do not bypass strict flags to describe a text-only KB as visual-enhanced.
- Do not approve from a tile without checking overview and adjacent context when relationships cross boundaries.
- Do not reuse approval metadata after content, correction, image, or source changes.
- Do not report improved retrieval or clinical accuracy without a labeled evaluation.

## 12. Short Reviewer Checklist

For each candidate:

- [ ] Correct source PDF, page, figure/table, tile, and hashes.
- [ ] Evidence text visibly supports the entire record.
- [ ] Evidence box identifies the correct source region.
- [ ] No missing threshold, qualifier, branch, exception, or footnote.
- [ ] No invented dose, strength, caution, relationship, or symbol meaning.
- [ ] Node/edge, row/column, or symbol associations are correct.
- [ ] Warnings and off-tile context have been resolved.
- [ ] The complete figure/table was checked for records the model omitted.
- [ ] Related nodes, edges, actions, footnotes, and symbols form a coherent reviewed bundle.
- [ ] Decision recorded as pending, approved, rejected, or corrections required.
- [ ] Reviewer ID, timezone-aware timestamp, and rationale recorded.
- [ ] If corrected, the new fingerprint was reviewed and approved in a second pass.

Batch sign-off:

- [ ] Step 06 completed with a final manifest and run record.
- [ ] Step 07 strict valid-candidate and release gates passed.
- [ ] Released-record count and exclusion reasons were inspected.
- [ ] Expected, attempted, completed, candidate, invalid, pending, corrected, rejected, approved, and released counts were reconciled.
- [ ] Step 08 strict visual-record gate passed.
- [ ] Enhanced-KB and index manifests match the approved release set.
- [ ] Retrieval quality was reviewed separately before any performance claim.
