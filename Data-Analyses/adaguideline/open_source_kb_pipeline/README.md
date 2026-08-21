# Open-Source ADA Guideline Knowledge Base Pipeline

This folder builds a local, auditable retrieval knowledge base from a guideline
PDF. Visual-model output is treated as untrusted evidence: it is tiled,
schema-checked, validated against its source provenance, and held outside the
retrieval corpus until a human reviewer approves the exact content fingerprint.

This is retrieval infrastructure, not an autonomous clinical decision system.
Do not publish or use extracted recommendations for care without qualified
clinical review and confirmation against the source guideline.

## Install

From this directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-minimal.txt
```

The minimal dependencies support text extraction, PDF rendering, validation,
and local vector retrieval. `requirements-full.txt` adds Docling. The generic
`requirements.txt` is an alias for the full set.

Visual extraction is optional and uses a local Ollama vision model. The default
is the direct-output `qwen3-vl:8b-instruct` variant:

```bash
ollama pull qwen3-vl:8b-instruct
ollama serve
```

Do not substitute the `qwen3-vl:8b` thinking tag. With affected Ollama builds,
schema-constrained output from that tag can be placed entirely in the
`thinking` field while the final `response` is empty. Step 06 deliberately does
not promote reasoning traces to guideline evidence and fails with an actionable
error instead. Requests also set `think=false`, but the instruct tag remains the
tested configuration because the thinking tag may ignore that switch.

Do not use a model that Ollama reports as an unsupported architecture. Step 06
checks the Ollama version, installed model, model metadata, structured-output
support, and vision runner before processing any guideline assets. A failed
preflight writes `extraction_run.json` and stops without partial model output.

## Run the text pipeline

Use one explicit PDF path for steps 01 and 05:

```bash
GUIDELINE_PDF="/absolute/path/to/guideline.pdf"

python 01_extract_guideline_content.py --pdf "$GUIDELINE_PDF"
python 02_build_type2_kb.py
python 03_build_vector_index.py
python 04_demo_retrieval.py
```

Generated outputs live under `outputs/`, which is ignored by Git because it may
contain extracted guideline text.

Step 01 records figure and table IDs only when it finds caption syntax such as
`Figure 9.4—...` or `Table 9.2—Continued`. Step 05 targets those caption-defined
IDs rather than ordinary prose references such as “see Figure 9.4,” which keeps
the default visual run focused. Multi-page continuation captions are selected
independently. Inspect `outputs/raw_extracted/page_manifest.csv` before a visual
run; use `--render-all` if a source PDF does not expose usable caption text.

## Run the visual pipeline

### 1. Render overview and high-resolution tiles

Targeted rendering defaults to a page overview plus overlapping 320-DPI tiles.
This preserves the whole-page context while making dense flowcharts, arrows,
small medication labels, and repeated `+`/`$` glyphs more legible.

```bash
python 05_render_pdf_pages_to_images.py \
  --pdf "$GUIDELINE_PDF" \
  --asset-mode both \
  --tile-dpi 320 \
  --tile-overlap 0.20
```

Use `--target-pattern "Figure 9.4"` to add targets. `--render-all` defaults to
overview-only to avoid an accidental tile explosion; pass `--asset-mode both`
explicitly if every page really needs tiles. Every asset records its page,
PDF-coordinate bounding box, source-PDF SHA-256, image SHA-256, and stable ID.
When tiles exist, step 06 keeps the overview as reviewer context and sends only
the detail tiles to the model by default. Use `--include-overviews` only when
whole-page model extraction is intentional.

### 2. Extract schema-constrained, review-only JSON

```bash
python 06_extract_visual_guideline_logic.py \
  --use-ollama \
  --model qwen3-vl:8b-instruct
```

Step 06 runs separate structure, medication-action, and symbol passes. It sends
Ollama a bounded, pass-specific JSON Schema with one aggregate item per asset,
then validates the returned JSON locally. The default Ollama budget is an
8,192-token context with at most 4,096 generated tokens per pass; a
`done_reason=length` response is rejected explicitly rather than parsed as
partial evidence. Override these only after a representative smoke test with
`--num-ctx` and `--num-predict` (the latter must be smaller). It also
uses asset-scoped IDs and collision-safe filenames so tiles cannot overwrite
one another. The output status remains `extracted_unvalidated` (or
`partial_extraction`/`model_output_invalid`); nothing is released here.
Per-pass Ollama token/timing metrics are retained in each raw JSON, and Step 06
prints one progress line after every asset so long runs can be monitored.

Qwen3-VL evidence boxes are recorded on its normalized 0–1000 image grid, not
as source-image pixels. Step 06 declares that coordinate space in both the raw
JSON and manifest. Step 07 rejects an absent/mismatched declaration and any
nonempty coordinate outside 0–1000. Conversion back to image pixels is
`[x/1000*width, y/1000*height]`; any such localization still requires source
review.

Running without `--use-ollama` creates provenance-preserving placeholders only.
Those placeholders cannot pass the release validator.

### 3. Validate and create the human-review sheet

```bash
python 07_validate_visual_logic_outputs.py
```

The first run creates:

```text
outputs/visual_logic_structured/visual_review_approvals.csv
outputs/visual_logic_structured/visual_manual_review_queue.csv
outputs/visual_logic_structured/visual_candidate_retrieval_records.csv
outputs/visual_logic_structured/visual_retrieval_records.csv
```

`visual_retrieval_records.csv` is intentionally header-only until records pass
validation and receive current human approval. Candidate rows never go directly
to the enhanced KB.

Review the source image/PDF and each candidate. In
`visual_review_approvals.csv`, leave the auto-generated identity and
`content_sha256` unchanged, then set:

- `review_status` to `approved`;
- `reviewer` to the accountable reviewer's name or ID;
- `reviewed_at` to a timezone-aware ISO-8601 timestamp, such as
  `2026-08-13T14:30:00+08:00`;
- optional `corrections_json` to a JSON object containing only allowed clinical
  fields; and
- `review_notes` to the source-check rationale.

Run step 07 again after editing the sheet. Approvals are bound to the effective
record fingerprint, including correction-applied clinical content, immutable
item metadata, the asset box, and PDF/image hashes. Changed model content,
replaced source assets, or added/edited corrections automatically invalidate the
previous approval. A new or changed `corrections_json` is preserved but reset to
`pending`; inspect the corrected candidate and approve its newly generated
`content_sha256` on the next run. Invalid graph endpoints, duplicate IDs, exact
same-page duplicates across assets, missing evidence, provenance or file-hash
mismatch, ambiguous symbols, stale fingerprints, missing reviewer, or naive
timestamps all remain quarantined.

For automated pipeline runs, add `--require-valid-candidates` to fail if step 07
produces no valid candidates, and add `--require-released-records` after review
to fail if no records pass the human-release gate. Both checks are opt-in so the
initial review-sheet generation remains a successful workflow step.

### 4. Build and index only released evidence

```bash
python 08_build_enhanced_guideline_kb.py --require-visual-records
python 03_build_vector_index.py \
  --kb-dir outputs/enhanced_guideline_kb \
  --output-dir outputs/vector_index_enhanced
```

Steps 08 and 03 independently enforce the release gate. A visual row must be
explicitly human-approved/released and have
`requires_manual_review=false`. Duplicate chunk IDs abort the build instead of
silently overwriting evidence. Audit and provenance columns remain attached to
the enhanced records and vector metadata.

Use `--require-visual-records` in automated runs so a silently text-only build
cannot be reported as a complete visual pipeline. If the embedding model is
already cached and the machine is offline, append `--local-files-only` to step
03 (and to step 04 for the retrieval demo).

On macOS, steps 03 and 04 default the native BLAS/OpenMP thread pools to one
thread to avoid a PyTorch/FAISS hang or crash in mixed runtimes. Explicit
`OMP_NUM_THREADS`, `MKL_NUM_THREADS`, or `VECLIB_MAXIMUM_THREADS` settings are
left unchanged.

## Symbol rules

`guideline_symbol_registry.csv` defines ordinal comparative scales:

- repeated `+` glyphs can express relative advantage/strength;
- repeated `$` glyphs express relative cost; and
- the count, level, type, and direction must match the registry.

A single leading `+` in labels such as `+HF` or `+CKD` is different: it is a
presence/addition marker, not the lowest ordinal score. Step 06 requests an
explicit `symbol_role`; step 07 rejects an ambiguous single `+` and checks the
glyph count and role. All symbol evidence still requires a human source check.

## Important outputs

```text
outputs/raw_extracted/                         # source text/page manifest
outputs/page_images/page_image_manifest.csv   # overview/tile provenance
outputs/visual_logic_raw/                     # untrusted model JSON
outputs/visual_logic_structured/               # candidates, approvals, releases
outputs/enhanced_guideline_kb/                 # released retrieval corpus
outputs/vector_index_enhanced/                 # FAISS index + audit metadata
```

## Tests

```bash
python -m unittest discover -s tests -v
```

The focused suite covers tile geometry and provenance, Ollama preflight and
error bodies, strict model-response validation, ID collision handling, graph
and symbol validation, fingerprint-bound approval, duplicate-ID rejection, and
fail-closed enhanced/vector KB integration.

## Review priorities

- dense decision paths and off-tile arrow endpoints in Figures 9.3 and 9.4;
- insulin initiation/intensification chains in Figures 9.2 and 9.5;
- medication rows and kidney dosing/caution language in Tables 9.2 and 9.3;
- every `+`/`$` count, role, direction, and modifying footnote; and
- drug-class normalization, thresholds, AND/OR logic, priority language, and
  any evidence box that is empty or crosses a tile boundary.

The visual model accelerates transcription; the validator and reviewer decide
what, if anything, is safe to release as guideline evidence.
