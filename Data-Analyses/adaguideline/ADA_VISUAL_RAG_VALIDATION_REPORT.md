# ADA Visual RAG Validation Report

Audit date: 2026-08-23

Repository baseline: `d4d09f1`; validation was performed against the candidate patch documented below

## 1. Executive Summary

The local visual branch is now able to call a real vision model and produce schema-constrained candidate transcriptions from ADA page tiles. Ollama 0.32.9 was installed from the official macOS release in a user-local directory, and `qwen3-vl:8b-instruct` was selected because it supports image input and direct final output.

Two runtime incompatibilities were found and fixed. The original `qwen3-vl:8b` tag placed its JSON in Ollama's `thinking` field while leaving the final `response` empty. The pipeline now rejects reasoning traces as evidence and uses the instruct tag. The first instruct smoke response was then cut off at Ollama's implicit token limit. Step 06 now uses bounded, pass-specific schemas, an explicit 8,192-token context and 4,096-token output budget, and a distinct error for `done_reason=length`.

The full Step 06 extraction completed on all 60 detail tiles. Fifty-nine assets completed all three passes on the first run. The structure pass for dense Figure 9.4 tile `page_009_tile_r02_c02` reached the 6,144-token output limit; its already successful action and symbol passes were preserved, the structure pass alone was rerun with a larger bounded budget, and a provenance-checked merge utility created a separate completed directory. The original run and retry directories were not modified.

The completed output contains 168 decision nodes, 136 edges, 220 medication actions, 77 footnote/legend records, and 22 ordinal-symbol records: 623 child records in total. Step 07 classified 416 as structurally valid, unreleased candidates and quarantined 207 as invalid. It generated 760 review-queue rows and 623 pending approval rows. No record received qualified human approval, so the correct released count remains zero.

A sanitized 4.7 MB review package is now tracked under `open_source_kb_pipeline/manual_review_packages/ada_principles_20260823_qwen3vl_0533d743/`. It includes review CSVs, run metadata, an inventory, and checksums, but deliberately excludes the source PDF, rendered images, raw model JSON, release files, enhanced KB, and vectors. Reviewers need authorized, hash-matching source assets to evaluate the package. Strict Step 08 must remain closed until that review is complete.

## 2. Environment

| Component | Verified value |
| --- | --- |
| Platform | macOS 26.5.2, arm64 Apple Silicon |
| Python | 3.12.4 from `/opt/anaconda3/bin/python3` for the completed run |
| Ollama | 0.32.9, installed user-locally and live during the completed extraction |
| Vision model | `qwen3-vl:8b-instruct` |
| Model digest | `0533d74300e4f9bc367d675d4e64ffd073d50ff16a2b4096cc2e8a1cf8c96319` |
| Model properties | qwen3vl, 8.8B, Q4_K_M, vision/completion/tools, 6.1 GB |
| Retrieval model | `sentence-transformers/all-MiniLM-L6-v2`, 384 dimensions |
| Important packages | pandas 3.0.5, NumPy 2.5.2, PyMuPDF 1.28.2, pypdf 6.15.0, FAISS 1.15.0, sentence-transformers 5.7.0, requests 2.34.2, jsonschema 4.26.0 |

No cloud API key was required or used.

## 3. Previous Baseline

Before live Ollama inference, the visual branch produced 60 `needs_visual_model` placeholders, 120 review-queue rows, and zero nodes, edges, medication actions, symbols, candidates, approvals, or releases. The non-strict Step 08 build consequently contained the same 234 base records and no visual records. Strict Step 07 and Step 08 checks failed closed as intended.

## 4. Ollama Setup

The official Ollama 0.32.9 CLI archive was installed under `~/.local/opt/ollama-0.32.9/`, with a convenience symlink at `~/.local/bin/ollama`. The downloaded archive was 148,150,513 bytes with SHA-256 `17a5b096d4515d00a6415012db847a2b353b389ed7ab33d025e3b98c2f05b49c`. The executable reported client version 0.32.9.

During validation, the service responded on `127.0.0.1:11434`. `/api/version`, `/api/tags`, and `ollama show` confirmed the model, digest, family, architecture, and vision capability. The official model page documents the instruct tag and its image-input capability: <https://ollama.com/library/qwen3-vl%3A8b-instruct>.

The original thinking tag was not accepted because its final response was empty. Step 06 now sets `think=false`, consumes only `response`, and raises an actionable error when output exists only in `thinking`.

## 5. Step 06 — Live Visual Extraction

Final smoke command:

```bash
python 06_extract_visual_guideline_logic.py \
  --image-manifest /private/tmp/ada-visual-validation-20260813/smoke_manifest.csv \
  --output-dir /private/tmp/ada-visual-validation-20260813/smoke_instruct_final_raw \
  --use-ollama \
  --model qwen3-vl:8b-instruct \
  --ollama-url http://127.0.0.1:11434 \
  --timeout 240 \
  --num-ctx 8192 \
  --num-predict 4096
```

Completed full-run command:

```bash
python 06_extract_visual_guideline_logic.py \
  --image-manifest outputs/page_images/page_image_manifest.csv \
  --output-dir outputs/visual_logic_raw_review_20260823 \
  --use-ollama \
  --model qwen3-vl:8b-instruct \
  --ollama-url http://127.0.0.1:11434 \
  --timeout 300 \
  --num-ctx 12288 \
  --num-predict 6144

# After a structure-only retry of page_009_tile_r02_c02 into a separate
# directory, the completed output was created with:
python merge_visual_logic_retry.py \
  --primary-raw-dir outputs/visual_logic_raw_review_20260823 \
  --retry-raw-dir outputs/visual_logic_retry_page_009_r02_c02 \
  --output-dir outputs/visual_logic_raw_review_20260823_completed \
  --expected-assets 60
```

Step 06 selects 60 detail tiles from the 70-asset manifest, retaining the ten overviews for human context. It performs three sequential requests per tile: structure, medication actions, and symbols. It has no automatic retry/backoff or concurrency. Its output is explicitly unvalidated.

## 6. Visual Extraction Results

| Metric | Previous placeholder baseline | Earlier smoke | Completed full run |
| --- | ---: | ---: | ---: |
| Expected tiles | 60 | 1 | 60 |
| Completed real assets | 0 | 1 | 60 |
| Placeholders | 60 | 0 | 0 |
| Decision nodes | 0 | 11 | 168 |
| Recommendation edges | 0 | 11 | 136 |
| Medication actions | 0 | 5 | 220 |
| Symbol/footnote records | 0 | 7 | 77 |
| Ordinal-symbol records | 0 | 0 | 22 |
| Total child records | 0 | 34 | 623 |
| Structurally valid candidates | 0 | 34 | 416 |
| Invalid/quarantined children | 0 | 0 | 207 |
| Released visual records | 0 | 0 | 0 |

The initial full run started at `2026-08-23T15:10:40.193047+00:00` and finished at `2026-08-23T16:30:47.064491+00:00`. It produced 59 complete assets and one partial asset. Only the rejected structure pass for `page_009_tile_r02_c02` was rerun; the retry finished at `2026-08-23T16:33:24.629248+00:00` and produced 12 nodes and 11 edges. `merge_visual_logic_retry.py` verified asset, page, source/image hashes, model tag and digest, Ollama version, runtime identity, and disjoint pass names before writing the 60-asset completed output at `2026-08-23T16:46:27.965395+00:00`.

These counts describe model output volume, not clinical correctness.

## 7. Manual Inspection

Representative source images were compared with the model output.

| Source | What matched | Problems requiring review |
| --- | --- | --- |
| Figure 9.4, page 9, tile r01/c01 | Major cardiovascular/kidney goal, ASCVD/high-risk/HF/CKD branches, medication-class labels, and `+` presence markers were recognized. | Crop-limited graph context remains ambiguous. An earlier smoke invented a `strong` strength label and used inconsistent coordinate assumptions; the final prompt/schema fixes removed the strength invention and declared normalized coordinates. Human confirmation of every edge and condition is still required. |
| Figure 9.4, page 9, tile r02/c02 | The targeted retry recovered 12 nodes and 11 edges while preserving the earlier action/symbol passes. | The retry warns that the tile is partial/cut-off flowchart context. Cross-tile endpoints and branch meaning still require source review. |
| Figure 9.4, page 9, tile r01/c01 | Candidate text and boxes visibly correspond to major goal and disease-category regions. | One structurally valid goal node represented a multi-category visual branch as a single `true_branch`. This confirms that “valid” is not equivalent to clinically faithful. |
| Table 9.2, page 11 | All six source tiles were processed. | The page is sideways, and the flattened action schema did not yield reliable row/column reconstruction; sampled output contained no valid medication actions. |
| Figure 9.1, page 2 | All six tiles were processed and ordinal-symbol candidates were emitted. | Exact row/column association and every `+`/`$` count still require human comparison; all 22 ordinal-symbol records were invalid under current registry checks. |

The smoke validator's “valid” state means schema/provenance/graph consistency, not source truth. No candidate was approved.

## 8. Table 9.2 Evaluation

All four Table 9.2 pages were inspected at the image level. Pages 11–13 contain sideways table content. Page 14 mixes a sideways table region with upright article prose, so whole-page rotation would damage part of that page.

The completed run processed all tiles on pages 11–14. Completion did not solve the layout problem: the existing schema flattens medication actions rather than representing explicit table cells, row/column keys, or page-continuation links. A manual sample of page 11 found no valid medication-action candidates, so record volume must not be used as evidence of table fidelity.

The recommended minimal follow-up is to retain the original assets and add 90-degree-clockwise derivative assets only for detected table tiles/regions, with distinct IDs and hashes. Page 14 must be region-specific. Orientation should be judged by human-verified row/header alignment, not by record count.

## 9. Step 07 — Validation Results

The completed full-run validation produced:

| Metric | Result |
| --- | ---: |
| Assets evaluated | 60 |
| Nodes | 168 |
| Edges | 136 |
| Medication actions | 220 |
| Symbol/footnote records | 77 |
| Ordinal-symbol records | 22 |
| Total child records | 623 |
| Structurally valid candidates | 416 |
| Invalid children | 207 |
| Manual-review rows | 760 |
| Pending approval rows | 623 |
| Human-approved releases | 0 |

`--require-valid-candidates` succeeded. Release remained closed because all model-derived records require accountable source review. Frequent invalidity causes included missing triggers, missing drug classes/actions, exact duplicates, unresolved edge endpoints, empty visual items, and symbol-registry mismatch. Step 07 also now uses a nonempty manifest figure/table ID as a deterministic title fallback when the model returns a blank item title; it records a warning and does not alter raw JSON or invent clinical content.

## 10. Step 08 — Enhanced Knowledge Base

No live visual record met the human-release contract. The last completed non-strict diagnostic build remains:

```text
234 base text/table/page records + 0 released visual records = 234 records
```

Strict Step 08 is expected to fail until Step 07 produces at least one current, human-approved release. The KB must not be described as multimodal or visual-enhanced yet.

## 11. Retrieval After Visual Integration

There was no visual integration to index. The verified text-only baseline indexed 234 records with `sentence-transformers/all-MiniLM-L6-v2` at 384 dimensions. Six capstone cases produced 48 top-k rows. The base and text-only “enhanced” retrieval outputs were identical because no visual record was released.

No claim about improved retrieval relevance or clinical accuracy is supported.

## 12. What Now Works

### Implemented

- Caption-targeted page selection, high-resolution tiling, image/source hashes, and reviewer overviews.
- Real Ollama vision requests with direct-output model selection and strict JSON schemas.
- Separate structure/action/symbol passes with bounded output, explicit token budgets, per-pass metrics, and progress output.
- Thinking-only and length-truncated outputs rejected as evidence.
- Qwen3-VL normalized 0–1000 bounding-box provenance and range validation.
- Step 07 graph, symbol, provenance, duplicate, fingerprint, and human-release gates.
- Step 08/vector-index release enforcement.

### Successfully run

- The complete text/render/index/retrieval baseline.
- A real one-tile, three-pass vision smoke test.
- Full three-pass extraction for all 60 tiles, with one provenance-checked targeted retry.
- Canonical Step 07 validation and sanitized package export.

### Successfully validated

- 113 automated tests and a clean `git diff --check`.
- Source/image hash and release-gate behavior in tests and baseline runs.
- Structural validity of 416 full-run candidates, without human approval or clinical truth validation.
- Package inventory/checksums, record-ID reconciliation, and absence of local absolute paths.

Qualified clinical/source validation, release, full visual indexing, retrieval relevance, and clinical outcome performance have not been demonstrated.

## 13. Remaining Problems

- The completed run required a manual targeted retry for one dense pass; Step 06 still lacks automatic checkpoint/resume and bounded retry orchestration.
- Model warnings and invalid child records are common and need systematic triage.
- Table 9.2 needs orientation-aware region preprocessing and a table-specific schema.
- Tiling can split arrows, labels, rows, and footnotes; cross-tile graph/entity stitching is limited.
- Automated validation cannot prove that a transcription matches the source.
- No qualified review or approval occurred, so no visual record is releasable.
- Retrieval relevance and downstream guideline-adherence scoring remain unevaluated.
- The public package omits copyrighted source images/PDF but contains model-transcribed evidence snippets; reviewers need authorized local source access, and the team should confirm redistribution permissions.

## 14. Scientific Interpretation

This work demonstrates that a local vision model can receive real ADA tiles and produce structured candidate transcriptions, and that the software can reject incompatible, truncated, malformed, unapproved, or provenance-inconsistent material. It does not demonstrate complete or clinically accurate transcription, improved retrieval quality, better guideline adherence, better recommendations, or patient benefit.

The defensible current conclusion is: the visual-transcription and automated-validation branches have run at full 60-tile scale, but source/clinical validation, release, enhanced indexing, and retrieval evaluation remain unfinished.

## 15. Recommended Next Steps

1. Give reviewers controlled access to the hash-matching source PDF and images, then triage the 760-row queue.
2. Have qualified reviewers assess correctness and completeness, beginning with Figure 9.4, Table 9.2, medication thresholds/cautions, and all ordinal symbols.
3. Categorize the 207 invalid child records and decide whether each needs correction, re-extraction, or rejection.
4. Perform a four-page original-versus-rotated Table 9.2 experiment using table-only regions and a table-specific schema.
5. Add automatic checkpoint/resume and bounded retry orchestration around Step 06.
6. Run strict Step 07 and Step 08 only after fingerprinted approval, then rebuild the FAISS index.
7. Compare text-only and released enhanced retrieval using clinician-labeled relevance judgments.
8. Retain the pinned model digest, run manifests, source/image hashes, reviewer decisions, and adjudication trail.

# 3-Minute Team Meeting Update

The visual branch used to stop before inference because no compatible local model runtime was available. We installed Ollama 0.32.9 and tested the official Qwen3-VL vision model.

That exposed two real compatibility issues. The default thinking model put its JSON in a reasoning field and returned no final answer, so we switched to the direct-output instruct tag and made the pipeline reject reasoning traces. Then the first real response was cut off by Ollama's token limit, so we added bounded pass-specific schemas, explicit context/output budgets, and a clear truncation error.

We have now completed all 60 tiles. Fifty-nine completed on the first run. One dense Figure 9.4 structure pass hit its output limit, so we reran only that pass with a larger bounded budget and merged it after checking the asset, hashes, model digest, runtime, and pass provenance.

The completed run produced 623 child records: 168 nodes, 136 edges, 220 medication actions, 77 footnote/legend records, and 22 ordinal symbols. Automated validation marked 416 structurally valid and quarantined 207. It created 760 review-queue rows and 623 pending approval rows. Zero are released because qualified human review has not happened yet.

Table 9.2 remains the biggest visual-layout problem. Three pages are sideways, and the fourth mixes sideways table content with upright prose, so we need region-specific rotation and a table-aware representation.

The enhanced knowledge base still has 234 base records plus zero released visual records, and retrieval is therefore unchanged. That zero is intentional: automated validity is not source truth. A sanitized review package has been prepared with candidates, detailed tables, provenance, inventory, and checksums, but not the copyrighted PDF/images. Reviewers must use authorized, hash-matching source assets. The next step is qualified review, correction/re-extraction of invalid records, and strict release only after fingerprinted approval.
