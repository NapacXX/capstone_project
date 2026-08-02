# Open-Source ADA Guideline Knowledge Base Pipeline

This folder builds a local, reproducible knowledge base from the full ADA
2026 pharmacotherapy guideline PDF. It keeps full-guideline content in the
default index and adds Type 2 Diabetes relevance tags for patient-specific
retrieval.

The goal is not to manually type ADA rules. The pipeline uses open-source
tools to extract guideline content, preserve source metadata, and retrieve
patient-specific guideline context for later treatment-plan generation or
scoring.

## Folder Structure

```text
open_source_kb_pipeline/
  requirements.txt
  01_extract_guideline_content.py
  02_build_type2_kb.py
  03_build_vector_index.py
  04_demo_retrieval.py
  05_render_pdf_pages_to_images.py
  06_extract_visual_guideline_logic.py
  07_validate_visual_logic_outputs.py
  08_build_enhanced_guideline_kb.py
  guideline_symbol_registry.csv
  outputs/                       # generated locally; ignored by Git
```

## Inputs

Default guideline PDF:

```text
/Users/jiayiwei/Documents/Capstone/materials/ada 2026 pharmacotherapy guidelines.pdf
```

Default vignette/model-output data for retrieval demo:

```text
../../../data/raw/final_results_capstone_data_ver2.csv
```

## Install

Use a virtual environment so the open-source PDF/RAG packages do not interfere
with the rest of the project:

```bash
cd /Users/jiayiwei/Documents/Capstone/Group/capstone_project/Data-Analyses/adaguideline/open_source_kb_pipeline
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-minimal.txt
```

The minimal install runs the full local retrieval demo with `pypdf`,
`PyMuPDF`, `sentence-transformers`, and `faiss-cpu`. For stronger PDF layout
and table extraction, install the full stack:

```bash
pip install -r requirements-full.txt
```

`requirements.txt` is kept as an alias for the full dependency set.

Optional local visual extraction uses an open-source multimodal model through
Ollama. For example:

```bash
brew install ollama
ollama pull llama3.2-vision
ollama serve
```

If Ollama is not available, the visual extraction step still writes a review
queue so the pipeline remains runnable.

## Run

```bash
python 01_extract_guideline_content.py
python 02_build_type2_kb.py
python 03_build_vector_index.py
python 04_demo_retrieval.py
```

To add image/flowchart-aware guideline logic:

```bash
python 05_render_pdf_pages_to_images.py
python 06_extract_visual_guideline_logic.py
python 07_validate_visual_logic_outputs.py
python 08_build_enhanced_guideline_kb.py
python 03_build_vector_index.py \
  --kb-dir outputs/enhanced_guideline_kb \
  --output-dir outputs/vector_index_enhanced
```

If the embedding model is already cached and you are offline, use:

```bash
python 03_build_vector_index.py \
  --kb-dir outputs/enhanced_guideline_kb \
  --output-dir outputs/vector_index_enhanced \
  --local-files-only
```

To actually call a local Llama vision model in step 06:

```bash
python 06_extract_visual_guideline_logic.py --use-ollama --model llama3.2-vision
```

## Outputs

Raw full-document extraction:

```text
outputs/raw_extracted/document.json
outputs/raw_extracted/text_blocks.csv
outputs/raw_extracted/tables.csv
outputs/raw_extracted/page_manifest.csv
```

Full guideline knowledge base with Type 2 relevance tags:

```text
outputs/full_guideline_kb/full_guideline_text_chunks.csv
outputs/full_guideline_kb/full_guideline_structured_tables.csv
outputs/full_guideline_kb/full_guideline_figure_pages.csv
outputs/full_guideline_kb/full_guideline_kb_manifest.json
```

Optional Type 2 Diabetes-focused knowledge base:

```text
outputs/type2_kb/type2_text_chunks.csv
outputs/type2_kb/type2_structured_tables.csv
outputs/type2_kb/type2_figure_pages.csv
outputs/type2_kb/type2_kb_manifest.json
```

Local vector index:

```text
outputs/vector_index/faiss.index
outputs/vector_index/chunk_metadata.parquet
outputs/vector_index/embedding_model.txt
```

Retrieval demo:

```text
outputs/demo_retrieval/demo_case_queries.csv
outputs/demo_retrieval/demo_retrieved_guideline_context.csv
outputs/demo_retrieval/demo_retrieval_summary.md
```

Rendered figure/table page images:

```text
outputs/page_images/page_image_manifest.csv
outputs/page_images/ada_page_*.png
```

By default, the page renderer targets Figure 9.1 through Figure 9.5 plus
Table 9.2 and Table 9.3. Additional targets can be passed with
`--target-pattern`.

Visual guideline logic extraction:

```text
outputs/visual_logic_raw/visual_logic_manifest.csv
outputs/visual_logic_raw/visual_logic_page_*.json
outputs/visual_logic_structured/visual_decision_nodes.csv
outputs/visual_logic_structured/visual_recommendation_edges.csv
outputs/visual_logic_structured/visual_drug_actions.csv
outputs/visual_logic_structured/visual_symbols_footnotes.csv
outputs/visual_logic_structured/visual_ordinal_symbols.csv
outputs/visual_logic_structured/visual_retrieval_records.csv
outputs/visual_logic_structured/visual_manual_review_queue.csv
```

Enhanced retrieval knowledge base:

```text
outputs/enhanced_guideline_kb/enhanced_guideline_kb_records.csv
outputs/enhanced_guideline_kb/enhanced_guideline_kb_manifest.json
outputs/vector_index_enhanced/faiss.index
outputs/vector_index_enhanced/chunk_metadata.parquet
```

## Design Notes

- The default pipeline extracts and indexes the full PDF. It also scores and
  tags Type 2 Diabetes relevance so vignette retrieval can prioritize the
  clinically relevant guideline sections.
- Tables are kept as structured retrieval rows rather than flattened only into
  ordinary text chunks.
- Flowcharts are now handled in two layers. First, page images are rendered for
  Figure 9.1 through Figure 9.5, Table 9.2, Table 9.3, or any user-specified target.
  Second, an optional local Llama vision model extracts decision nodes, graph
  edges, drug actions, and footnote/symbol meanings into structured JSON and
  CSV files.
- Figure 9.1-style plus signs and dollar signs are represented as ordinal
  metadata using `guideline_symbol_registry.csv`. Plus signs encode relative
  advantage/strength; dollar signs encode relative cost. These are comparative
  categories, not exact numeric values.
- The visual extraction output is intentionally reviewable. Arrows, symbols,
  abbreviated labels, and footnotes are high-risk transformation points, so the
  pipeline preserves `requires_manual_review` flags and writes a manual review
  queue.
- `outputs/enhanced_guideline_kb/` combines text chunks, table/page records,
  and visual logic records into one retrievable knowledge base. This lets a
  downstream LLM receive guideline text plus structured flowchart/table logic
  as context.
- Generated outputs may contain extracted ADA guideline text. Keep `outputs/`
  local unless the team confirms that those derivative files should be pushed.
- This pipeline builds retrieval infrastructure only. It does not call GPT,
  Claude, or any clinical scoring LLM.

## What Needs Manual Review

The open-source extraction makes the workflow reproducible, but it does not
remove the need for clinical and data-quality review. The main review targets
are:

- Table 9.2 and Table 9.3 medication rows, especially eGFR/dosing language.
- Figure 9.3 and Figure 9.4 decision branches, arrows, and priority ordering.
- Figure 9.1 plus/dollar symbol extraction, including whether more is better
  or worse for each clinical dimension.
- Figure 9.2 and Figure 9.5 arrow chains and insulin intensification logic.
- Footnotes and special symbols that modify a recommendation.
- Drug class normalization, such as GLP-1 RA versus dual GIP/GLP-1 RA.
- Any visual extraction row with `requires_manual_review == true`.

After review, the structured visual tables can be used to build a deterministic
decision model or to construct ADA-guided prompts for LLM treatment-plan
generation.
