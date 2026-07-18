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
`sentence-transformers`, and `faiss-cpu`. For stronger PDF layout and table
extraction, install the full stack:

```bash
pip install -r requirements-full.txt
```

`requirements.txt` is kept as an alias for the full dependency set.

## Run

```bash
python 01_extract_guideline_content.py
python 02_build_type2_kb.py
python 03_build_vector_index.py
python 04_demo_retrieval.py
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

## Design Notes

- The default pipeline extracts and indexes the full PDF. It also scores and
  tags Type 2 Diabetes relevance so vignette retrieval can prioritize the
  clinically relevant guideline sections.
- Tables are kept as structured retrieval rows rather than flattened only into
  ordinary text chunks.
- Flowcharts are initially represented as page-level/figure-level retrievable
  records, including Figure 9.3 and Figure 9.4 when detected. A later reviewed
  step can convert those retrieved figure records into executable decision
  nodes.
- Generated outputs may contain extracted ADA guideline text. Keep `outputs/`
  local unless the team confirms that those derivative files should be pushed.
- This pipeline builds retrieval infrastructure only. It does not call GPT,
  Claude, or any clinical scoring LLM.
