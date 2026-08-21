# ADA Visual RAG Validation Report

Audit date: 2026-08-22

Repository baseline: `d4d09f1`; validation was performed against the candidate patch documented below

## 1. Executive Summary

The local visual branch is now able to call a real vision model and produce schema-constrained candidate transcriptions from ADA page tiles. Ollama 0.32.9 was installed from the official macOS release in a user-local directory, and `qwen3-vl:8b-instruct` was selected because it supports image input and direct final output.

Two runtime incompatibilities were found and fixed. The original `qwen3-vl:8b` tag placed its JSON in Ollama's `thinking` field while leaving the final `response` empty. The pipeline now rejects reasoning traces as evidence and uses the instruct tag. The first instruct smoke response was then cut off at Ollama's implicit token limit. Step 06 now uses bounded, pass-specific schemas, an explicit 8,192-token context and 4,096-token output budget, and a distinct error for `done_reason=length`.

The final one-tile smoke test completed all three extraction passes on a Figure 9.4 tile. It produced 11 decision nodes, 11 edges, 5 medication actions, and 7 symbol/footnote records. Step 07 classified all 34 children as structurally valid candidates, but released none because no qualified human approval was supplied. Manual inspection found the content useful but not release-ready without source review.

The attempted full Step 06 run did not finish. It produced 33 of 60 expected tile JSON files (55%) before the process was interrupted. Thirty-two assets completed all three passes; one Figure 9.4 tile retained its action and symbol passes but lost its structure pass to the explicit 4,096-token cap. Because the run did not write its final manifest or `extraction_run.json`, these files are a partial diagnostic run, not a canonical completed extraction.

No live visual record was approved or released. Strict Step 08 therefore has no basis to promote model-derived content, and the last completed enhanced-KB/retrieval baseline remains 234 text/table/page records plus 0 visual records. The knowledge base is not yet genuinely visual-enhanced.

## 2. Environment

| Component | Verified value |
| --- | --- |
| Platform | macOS 26.5.2, arm64 Apple Silicon |
| Python | 3.12.4 in `/private/tmp/ada-rag-real-20260813/venv` during validation |
| Ollama | 0.32.9, installed user-locally; server was live during extraction but was not running when rechecked on 2026-08-22 |
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

Full-run command:

```bash
python 06_extract_visual_guideline_logic.py \
  --image-manifest outputs/page_images/page_image_manifest.csv \
  --output-dir outputs/visual_logic_raw_live \
  --use-ollama \
  --model qwen3-vl:8b-instruct \
  --ollama-url http://127.0.0.1:11434 \
  --timeout 240 \
  --num-ctx 8192 \
  --num-predict 4096
```

Step 06 selects 60 detail tiles from the 70-asset manifest, retaining the ten overviews for human context. It performs three sequential requests per tile: structure, medication actions, and symbols. It has no automatic retry/backoff or concurrency. Its output is explicitly unvalidated.

## 6. Visual Extraction Results

| Metric | Previous placeholder baseline | Final smoke | Interrupted full run |
| --- | ---: | ---: | ---: |
| Expected tiles | 60 | 1 | 60 |
| Tiles with JSON | 60 placeholders | 1 real | 33 real (55%) |
| Fully completed assets | 0 | 1 | 32 |
| Partial assets | 0 | 0 | 1 |
| Placeholders | 60 | 0 | 0 among completed assets |
| Visual items | 60 placeholder items | 3 | 98 |
| Decision nodes | 0 | 11 | 105 |
| Recommendation edges | 0 | 11 | 87 |
| Medication actions | 0 | 5 | 107 |
| Symbol/footnote records | 0 | 7 | 67 |
| Ordinal-symbol records | 0 | 0 | 22 |
| Warnings | 60 placeholder warnings | 7 | 163 |
| Released visual records | 0 | 0 | 0 |

The partial run completed 98 of the 99 attempted pass calls. Every completed call recorded `done_reason=stop`. The missing result was the structure pass for `page_009_tile_r02_c02`, rejected because it reached 4,096 generated tokens. The interrupted run never reached the remaining 27 tiles and did not write its final manifest/run summary.

These counts describe model output volume, not clinical correctness.

## 7. Manual Inspection

Representative source images were compared with the model output.

| Source | What matched | Problems requiring review |
| --- | --- | --- |
| Figure 9.4, page 9, tile r01/c01 | Major cardiovascular/kidney goal, ASCVD/high-risk/HF/CKD branches, medication-class labels, and `+` presence markers were recognized. | Crop-limited graph context remains ambiguous. An earlier smoke invented a `strong` strength label and used inconsistent coordinate assumptions; the final prompt/schema fixes removed the strength invention and declared normalized coordinates. Human confirmation of every edge and condition is still required. |
| Figure 9.4, page 9, tile r02/c02 | Action and symbol passes completed. | Structure output exceeded the 4,096-token cap and was correctly rejected; this tile demonstrates that one large fixed cap is not sufficient for every dense region. |
| Figure 9.5, page 16 | Source inspection confirmed a relatively direct injectable-therapy path suitable for later QA. | The interrupted full run did not reach page 16, so no live full-run claim is available. |
| Figure 9.1, page 2 | The live partial run reached all six tiles and produced ordinal-symbol candidates. | Exact row/column association and every `+`/`$` count still require human comparison; aggregate counts alone are insufficient. |

The smoke validator's “valid” state means schema/provenance/graph consistency, not source truth. No candidate was approved.

## 8. Table 9.2 Evaluation

All four Table 9.2 pages were inspected at the image level. Pages 11–13 contain sideways table content. Page 14 mixes a sideways table region with upright article prose, so whole-page rotation would damage part of that page.

The interrupted live run completed all six tiles on page 11 and only three tiles on page 12; it did not reach pages 13–14. Therefore, a four-page extraction comparison was not completed. The existing schema also flattens medication actions rather than representing explicit table cells, row/column keys, or page-continuation links.

The recommended minimal follow-up is to retain the original assets and add 90-degree-clockwise derivative assets only for detected table tiles/regions, with distinct IDs and hashes. Page 14 must be region-specific. Orientation should be judged by human-verified row/header alignment, not by record count.

## 9. Step 07 — Validation Results

The completed final smoke validation produced:

| Metric | Result |
| --- | ---: |
| Assets evaluated | 1 |
| Nodes | 11 |
| Edges | 11 |
| Medication actions | 5 |
| Symbol/footnote records | 7 |
| Structurally valid candidates | 34 |
| Invalid children | 0 |
| Manual-review rows | 35 |
| Human-approved releases | 0 |

`--require-valid-candidates` succeeded for the smoke. Release remained closed because all model-derived records require accountable source review. The interrupted 33-tile run was not sent through canonical Step 07 because it lacks a completed Step 06 manifest.

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
- Strict Step 07 candidate validation on that smoke output.
- A partial 33-of-60 full extraction attempt.

### Successfully validated

- 94 automated tests, compilation of all eight scripts, and a clean `git diff --check`.
- Source/image hash and release-gate behavior in tests and baseline runs.
- Structural validity of 34 smoke candidates, without human approval.

Qualified clinical/source validation, release, full visual indexing, retrieval relevance, and clinical outcome performance have not been demonstrated.

## 13. Remaining Problems

- The full run is incomplete and has no canonical final manifest.
- Dense tiles can still exceed the output cap; there is no retry, checkpoint, or resume mechanism.
- Model warnings were common: all 33 partial-run assets had at least one warning.
- Table 9.2 needs orientation-aware region preprocessing and a table-specific schema.
- Tiling can split arrows, labels, rows, and footnotes; cross-tile graph/entity stitching is limited.
- Automated validation cannot prove that a transcription matches the source.
- No qualified review or approval occurred, so no visual record is releasable.
- Retrieval relevance and downstream guideline-adherence scoring remain unevaluated.
- Ollama is installed but must be started again before another live run.

## 14. Scientific Interpretation

This work demonstrates that a local vision model can receive real ADA tiles and produce structured candidate transcriptions, and that the software can reject incompatible, truncated, malformed, unapproved, or provenance-inconsistent material. It does not demonstrate complete or clinically accurate transcription, improved retrieval quality, better guideline adherence, better recommendations, or patient benefit.

The defensible current conclusion is: the visual-transcription branch is operational for a verified smoke test and partially operational at scale, but the end-to-end visual release/index/retrieval branch remains unfinished.

## 15. Recommended Next Steps

1. Add checkpoint/resume and bounded retry behavior, then complete all 60 tiles.
2. Run Step 07 on the completed canonical manifest and categorize every rejection/warning.
3. Perform a four-page original-versus-rotated Table 9.2 experiment using table-only regions.
4. Have qualified reviewers annotate a stratified sample and measure node, edge, action, threshold, footnote, and symbol correctness/completeness.
5. Correct candidates through the fingerprinted approval workflow; do not edit release files directly.
6. Run strict Step 07 and Step 08 only after approval, then rebuild the FAISS index.
7. Compare text-only and released enhanced retrieval using clinician-labeled relevance judgments.
8. Pin the environment/model digest and retain source/model/image provenance for reproducibility.

# 3-Minute Team Meeting Update

The visual branch used to stop before inference because no compatible local model runtime was available. We installed Ollama 0.32.9 and tested the official Qwen3-VL vision model.

That exposed two real compatibility issues. The default thinking model put its JSON in a reasoning field and returned no final answer, so we switched to the direct-output instruct tag and made the pipeline reject reasoning traces. Then the first real response was cut off by Ollama's token limit, so we added bounded pass-specific schemas, explicit context/output budgets, and a clear truncation error.

The final smoke test worked through all three passes. On one Figure 9.4 tile it produced 11 nodes, 11 edges, 5 medication actions, and 7 symbol/footnote records. Automated validation found 34 structurally valid candidates, but zero were released because we did not perform qualified human approval.

We then started all 60 tiles. The process was interrupted after 33 tiles. Thirty-two completed fully and one retained two of three passes; its dense structure response hit the 4,096-token limit. The partial output contains substantial candidate material, but it is not a completed run and has not been through canonical validation.

Table 9.2 remains the biggest visual-layout problem. Three pages are sideways, and the fourth mixes sideways table content with upright prose, so we need region-specific rotation and a table-aware representation.

The enhanced knowledge base still has 234 base records plus zero released visual records, and retrieval is therefore unchanged. What we have proven is that real local visual transcription and the fail-closed controls work on a smoke test—not that visual extraction is clinically accurate or improves retrieval. The next step is resumable full extraction followed by qualified source review.
