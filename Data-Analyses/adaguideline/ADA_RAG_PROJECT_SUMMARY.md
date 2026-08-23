# ADA RAG Project Summary

### Objective

The ADA guideline RAG work gives the Hyperglycemia AI Capstone a traceable evidence layer for evaluating AI-generated diabetes-management recommendations. It converts ADA pharmacotherapy guidance into searchable records that can later be retrieved for a clinical vignette and used in a separate guideline-adherence evaluation.

This is retrieval and evaluation infrastructure, not an autonomous clinical decision tool. The results below demonstrate software operation and identify remaining validation work; they do not demonstrate improved clinical accuracy, guideline adherence, or patient outcomes.

### What We Changed

The earlier workflow could extract PDF text, divide it into searchable records, attach metadata, build a local vector index, and retrieve context for capstone cases. Its main limitation was that ordinary PDF text extraction does not reliably preserve the relationships encoded by treatment algorithms, table layout, boxes, arrows, legends, footnotes, and repeated symbols such as `+` and `$`.

The current implementation adds caption-targeted page selection, high-resolution overview images and overlapping tiles, structured visual extraction with a local vision model, a registry defining visual-symbol semantics, automated validation, and a human approval gate. The enhanced knowledge-base stage can combine text records with visual records, but only after those visual records pass validation and exact source review.

Live testing also led to several reliability fixes. The original `qwen3-vl:8b` thinking tag put JSON in an Ollama reasoning field and returned no final answer, so the pipeline now rejects reasoning traces and defaults to `qwen3-vl:8b-instruct`. Later responses exposed token-limit and blank-title failures. Step 06 now uses bounded pass-specific schemas, explicit context/output budgets, direct-response-only handling, normalized evidence coordinates, and a clear truncation error. Step 07 enforces the same coordinate contract and can use the manifest figure/table ID as a warned, deterministic title fallback when the model returns no title. A separate merge utility supports a tightly checked one-pass retry without overwriting the original run.

### Current Pipeline

The text branch extracts page-preserving PDF text, constructs text/table/page records, creates embeddings and a FAISS vector index, and retrieves relevant records for capstone cases.

The visual branch selects pages with target figure/table captions, renders an overview plus high-resolution tiles, and sends tiles to a local vision model in separate structure, medication-action, and symbol passes. It represents candidate decision nodes, directed edges, actions, footnotes, and ordinal symbols as structured data.

Vision output is treated as untrusted transcription. Automated checks verify schema, graph relationships, symbol definitions, evidence coordinates, source paths, and hashes. Even structurally valid candidates remain quarantined until a reviewer compares them with the source and approves an exact content fingerprint. Only released records may enter the enhanced KB or vector index.

### Current Results

The supplied 33-page ADA 2026 pharmacotherapy PDF was processed with the following verified results:

- The full base KB contains 234 records: 193 text chunks, 8 caption-detected table/page-text records, and 33 page/figure records. The Type 2-focused KB contains 177 records.
- Rendering selected 10 pages and produced 70 image assets: 10 overviews at 220 DPI and 60 overlapping tiles at 320 DPI.
- The full 234-record and Type 2 177-record corpora were indexed. Six distinct capstone cases produced 48 retrieval rows per corpus. Retrieval relevance was not clinically graded.
- All 113 automated tests pass, and `git diff --check` is clean.
- Ollama 0.32.9 and `qwen3-vl:8b-instruct` completed all 60 visual tiles. Fifty-nine completed on the initial run. One dense Figure 9.4 structure pass reached its bounded output limit; that pass alone was rerun and merged only after provenance, model, runtime, source, and image checks.
- The completed output contains 623 child records: 168 decision nodes, 136 edges, 220 medication actions, 77 footnote/legend records, and 22 ordinal-symbol records.
- Step 07 marked 416 records structurally valid and quarantined 207 as invalid. It created 760 review-queue rows and 623 pending approval rows. No candidate has been human-approved or released.
- A sanitized manual-review package was generated under `open_source_kb_pipeline/manual_review_packages/ada_principles_20260823_qwen3vl_0533d743/`. It includes review tables, provenance, an inventory, and checksums, while excluding the PDF, images, raw model JSON, release records, KB, and vector files.

No live visual record has been approved or released. The current completed enhanced-KB baseline remains 234 base records plus 0 visual records, so enhanced and base retrieval are identical. The generated source images, PDF-derived outputs, and partial model outputs remain ignored and outside version control.

### Why the Changes Matter

A flowchart can encode a condition, branch, action, and exception through position and arrows; a repeated glyph can encode an ordinal comparison; and a table can depend on row and column alignment. Those relationships may be lost even when the PDF's words are technically extractable.

The updated architecture is designed to improve guideline representation and retrieval coverage while preserving page, tile, model, and file-hash provenance. It also prevents malformed, truncated, or unapproved model output from silently entering retrieval. A clinician-reviewed comparison is still required to determine whether the visual records are correct or improve retrieval.

### Current Limitations

- One dense pass needed a manually orchestrated retry; Step 06 does not yet provide automatic checkpoint/resume and bounded retry orchestration.
- Automated validation cannot prove that a candidate matches the source. A spot check found a structurally valid Figure 9.4 branch that oversimplified a multi-branch pathway.
- The 207 invalid children need correction, re-extraction, or rejection. All 22 ordinal-symbol records currently fail registry validation.
- Table 9.2 needs orientation-aware, table-region processing. Pages 11–13 are sideways, while page 14 mixes sideways table content with upright prose.
- Tiling can split arrows, labels, rows, and footnotes; cross-tile graph/entity reconciliation is limited.
- No qualified reviewer has approved a real visual record, so the enhanced KB remains text-only.
- The public review package omits the copyrighted source PDF/images but still contains model-transcribed evidence snippets. Reviewers need controlled access to authorized copies with matching hashes, and the team should confirm redistribution permissions.
- Retrieval relevance, old-versus-enhanced retrieval quality, downstream guideline-adherence scoring, and clinical outcomes have not been evaluated.
- The local source PDF is intentionally ignored and excluded from the commit; collaborators still need documented authorized access to the exact source version.

### Next Steps

1. Give qualified reviewers controlled access to the source PDF/images and triage the 760-row review queue, prioritizing Figure 9.4, Table 9.2, medication cautions/thresholds, and symbols.
2. Categorize each invalid record for permitted correction, new extraction, or rejection, and measure correctness, completeness, and reviewer agreement.
3. Test original versus clockwise-rotated table-only regions across all four Table 9.2 pages; keep page 14 rotation region-specific and use a table-aware schema.
4. Add automatic checkpoint/resume and bounded retry orchestration.
5. Build and index the strict enhanced KB only after fingerprinted approval.
6. Compare text-only and released enhanced retrieval using clinician-labeled relevance judgments.
7. Integrate reviewed evidence with the capstone recommendation-evaluation workflow and define quantitative adherence metrics before reporting model-performance conclusions.

For commands, full metrics, Git history, architecture, and reproducibility details, see `ADA_RAG_PIPELINE_CURRENT_STATUS.md`. For the completed live-model run and validation findings, see `ADA_VISUAL_RAG_VALIDATION_REPORT.md`. For review responsibilities and the exact approval workflow, see `ADA_RAG_MANUAL_REVIEW_GUIDE.md`.
