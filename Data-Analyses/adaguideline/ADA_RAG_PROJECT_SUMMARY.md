# ADA RAG Project Summary

### Objective

The ADA guideline RAG work gives the Hyperglycemia AI Capstone a traceable evidence layer for evaluating AI-generated diabetes-management recommendations. It converts ADA pharmacotherapy guidance into searchable records that can later be retrieved for a clinical vignette and used in a separate guideline-adherence evaluation.

This is retrieval and evaluation infrastructure, not an autonomous clinical decision tool. The results below demonstrate software operation and identify remaining validation work; they do not demonstrate improved clinical accuracy, guideline adherence, or patient outcomes.

### What We Changed

The earlier workflow could extract PDF text, divide it into searchable records, attach metadata, build a local vector index, and retrieve context for capstone cases. Its main limitation was that ordinary PDF text extraction does not reliably preserve the relationships encoded by treatment algorithms, table layout, boxes, arrows, legends, footnotes, and repeated symbols such as `+` and `$`.

The current implementation adds caption-targeted page selection, high-resolution overview images and overlapping tiles, structured visual extraction with a local vision model, a registry defining visual-symbol semantics, automated validation, and a human approval gate. The enhanced knowledge-base stage can combine text records with visual records, but only after those visual records pass validation and exact source review.

Live testing also led to several reliability fixes. The original `qwen3-vl:8b` thinking tag put JSON in an Ollama reasoning field and returned no final answer, so the pipeline now rejects reasoning traces and defaults to `qwen3-vl:8b-instruct`. A later response was truncated at Ollama's implicit token limit; Step 06 now uses bounded pass-specific schemas, explicit context/output budgets, direct-response-only handling, normalized evidence coordinates, and a clear truncation error. Step 07 enforces the same coordinate contract.

### Current Pipeline

The text branch extracts page-preserving PDF text, constructs text/table/page records, creates embeddings and a FAISS vector index, and retrieves relevant records for capstone cases.

The visual branch selects pages with target figure/table captions, renders an overview plus high-resolution tiles, and sends tiles to a local vision model in separate structure, medication-action, and symbol passes. It represents candidate decision nodes, directed edges, actions, footnotes, and ordinal symbols as structured data.

Vision output is treated as untrusted transcription. Automated checks verify schema, graph relationships, symbol definitions, evidence coordinates, source paths, and hashes. Even structurally valid candidates remain quarantined until a reviewer compares them with the source and approves an exact content fingerprint. Only released records may enter the enhanced KB or vector index.

### Current Results

The supplied 33-page ADA 2026 pharmacotherapy PDF was processed with the following verified results:

- The full base KB contains 234 records: 193 text chunks, 8 caption-detected table/page-text records, and 33 page/figure records. The Type 2-focused KB contains 177 records.
- Rendering selected 10 pages and produced 70 image assets: 10 overviews at 220 DPI and 60 overlapping tiles at 320 DPI.
- The full 234-record and Type 2 177-record corpora were indexed. Six distinct capstone cases produced 48 retrieval rows per corpus. Retrieval relevance was not clinically graded.
- All eight scripts compile, and all 94 automated tests pass.
- Ollama 0.32.9 and `qwen3-vl:8b-instruct` completed a real three-pass smoke test on one Figure 9.4 tile. The model returned 11 nodes, 11 edges, 5 medication actions, and 7 symbol/footnote records. Step 07 judged all 34 children structurally valid and created 35 review rows, but released none because no qualified human approval was supplied.
- An attempted 60-tile live run was interrupted after 33 tile JSONs. Thirty-two assets completed all three passes; one retained action and symbol output but its dense structure response hit the explicit 4,096-token cap. The interrupted run has no final manifest or run record and is therefore diagnostic, not canonical.

No live visual record has been approved or released. The current completed enhanced-KB baseline remains 234 base records plus 0 visual records, so enhanced and base retrieval are identical. The generated source images, PDF-derived outputs, and partial model outputs remain ignored and outside version control.

### Why the Changes Matter

A flowchart can encode a condition, branch, action, and exception through position and arrows; a repeated glyph can encode an ordinal comparison; and a table can depend on row and column alignment. Those relationships may be lost even when the PDF's words are technically extractable.

The updated architecture is designed to improve guideline representation and retrieval coverage while preserving page, tile, model, and file-hash provenance. It also prevents malformed, truncated, or unapproved model output from silently entering retrieval. A clinician-reviewed comparison is still required to determine whether the visual records are correct or improve retrieval.

### Current Limitations

- The full visual run is incomplete and lacks a canonical Step 06 manifest.
- Dense tiles can exceed the output cap; there is not yet checkpoint/resume or automatic bounded retry behavior.
- Model warnings were common in the partial run, and automated validation cannot prove that a candidate matches the source.
- Table 9.2 needs orientation-aware, table-region processing. Pages 11–13 are sideways, while page 14 mixes sideways table content with upright prose.
- Tiling can split arrows, labels, rows, and footnotes; cross-tile graph/entity reconciliation is limited.
- No qualified reviewer has approved a real visual record, so the enhanced KB remains text-only.
- Retrieval relevance, old-versus-enhanced retrieval quality, downstream guideline-adherence scoring, and clinical outcomes have not been evaluated.
- The local source PDF is intentionally ignored and excluded from the commit; collaborators still need documented authorized access to the exact source version.

### Next Steps

1. Add checkpoint/resume and bounded retry handling, restart the installed Ollama service, and complete all 60 tiles with a canonical final manifest.
2. Test original versus clockwise-rotated table-only regions across all four Table 9.2 pages; keep page 14 rotation region-specific.
3. Run Step 07 on the completed output and have qualified reviewers assess a stratified sample of nodes, edges, actions, thresholds, footnotes, and symbol counts.
4. Measure extraction correctness/completeness and reviewer agreement before approving records.
5. Build and index the strict enhanced KB only after fingerprinted approval.
6. Compare text-only and released enhanced retrieval using clinician-labeled relevance judgments.
7. Integrate reviewed evidence with the capstone recommendation-evaluation workflow and define quantitative adherence metrics before reporting model-performance conclusions.

For commands, full metrics, Git history, architecture, and reproducibility details, see `ADA_RAG_PIPELINE_CURRENT_STATUS.md`. For the live model audit and partial-run findings, see `ADA_VISUAL_RAG_VALIDATION_REPORT.md`.
