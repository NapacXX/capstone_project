# ADA Knowledge Base and Retrieval Program Delivery Guide

The current controlled-access knowledge base version is `ada2026-ch9-cf6259c5b1fb35c5`, built on 2026-09-29. It covers the pharmacologic treatment material in ADA 2026 Chapter 9, including narrative text and 9 manually confirmed complete figure/table groups: Figure 9.1–9.5 and Table 9.1–9.4. This program accepts cases and returns evidence with sources; it does not generate treatment plans, call generative APIs, or calculate clinical adherence scores.

## What Is Available on GitHub

This directory publicly provides the retrieval, build, review, and packaging programs, pinned dependency lists, license notices, automated tests, and a [test summary](test_reports/20260929/README.md) with the original case text and guideline transcriptions removed. Team members can inspect the code and run the tests.

The complete knowledge base package, `ADA_KB_V1_TEAM.zip`, is not on public GitHub. It contains the guideline PDF, source images, and transcriptions; request controlled access from the project maintainer and confirm usage permissions. The model snapshot, original approval materials, case CSV, and complete evidence outputs are also excluded from this upload. Cloning the repository does not provide these files; the public test summary is not a complete public dataset.

## Current Knowledge Base Size

| Item | Actual Count |
| --- | ---: |
| Source material | 1 PDF, 33 pages |
| Retained records | 210: 193 narrative text records, 8 flattened table carrier records, 9 complete figure/table groups |
| Retrievable evidence groups | 111: 102 narrative text records, 9 complete figure/table groups |
| Retrieval windows and vectors | 528, 384 dimensions |
| Complete figure/table coverage | 9 groups, 13 PDF pages |
| Figure/table source-text blocks and relationship descriptions | 359 blocks, 157 relationships |

Retrieval uses a pinned MiniLM snapshot, the CPU, and NumPy inner products of normalized vectors. Long cases are split into windows according to the model's actual 256-token limit; the complete case and processing log are returned. Each figure/table group occupies only one primary evidence slot. A match returns the complete figure/table content, conditions, footnotes, source locations, and review status; isolated prices or doses are not treated as independently approved conclusions.

The historical 623 sliced candidates and the current complete-figure/table release use different representations. Approval of complete figures/tables did not automatically approve all older candidates. The phrase “0 released visual records” in the older candidate documentation describes the candidate package at that time; it does not mean that the current controlled-access nine-figure/table version lacks visual evidence. [KB_V1_README.md](KB_V1_README.md) preserves the historical candidate workflow and is used by the older candidate packaging tool; the latest official package does not use `--allow-candidate`.

## Running the Ready-to-Use Knowledge Base Package as a Team Member

After receiving the complete ZIP through authorized sharing, extract it and enter `ADA_KB_V1_TEAM`. Install Python 3.12 first. The initial dependency installation usually requires network access; queries load the model included in the package without downloading a model and do not require Ollama or an API key.

macOS:

```sh
python3.12 -m venv ../ada-kb-env
../ada-kb-env/bin/python -m pip install -r tools/requirements-v1-consumer.lock.txt
../ada-kb-env/bin/python selftest.py --output "../ADA acceptance macOS"
../ada-kb-env/bin/python tools/kb_v1.py query --bundle bundle --cases examples/synthetic_cases.csv --output ../ada-demo-evidence
```

Windows PowerShell (commands provided for acceptance testing on an actual Windows machine; not yet tested on Windows):

```powershell
py -3.12 -m venv ..\ada-kb-env
..\ada-kb-env\Scripts\python.exe -m pip install -r tools\requirements-v1-consumer.lock.txt
..\ada-kb-env\Scripts\python.exe selftest.py --output "..\ADA acceptance Windows"
..\ada-kb-env\Scripts\python.exe tools\kb_v1.py query --bundle bundle --cases examples\synthetic_cases.csv --output ..\ada-demo-evidence
```

By default, the CSV only requires `case_id,vignette_text`. Use UTF-8 and include only case facts; generated treatment plans must not be used as queries. The same ID must not refer to different cases. For a single case, use `--vignette-file one_case.txt --case-id example-001` in place of `--cases`. Structured-field mode requires explicitly adding `--input-mode structured`. All output directories must be new directories.

The outputs are `evidence.jsonl`, `evidence.md`, and `retrieval_summary.json`. Team members can use the JSONL in subsequent API workflows, preserving the knowledge base version, evidence IDs, sources, review status, and limitations. This release does not provide automated generation or clinical scoring integration; similarity is not clinical accuracy.

## Running Tests and Retrieval from Source

From the repository root, enter the pipeline directory:

```sh
cd Data-Analyses/adaguideline/open_source_kb_pipeline
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-v1-maintainer.lock.txt
.venv/bin/python kb_v1.py doctor --maintainer
.venv/bin/python run_public_tests.py --output outputs/my_public_test_result.json
```

On Windows, use `py -3.12 -m venv .venv` and `.venv\Scripts\python.exe` instead. Pre-release retesting used an existing macOS Python 3.12.4 environment; this does not validate a fresh dependency installation. Integration tests that lack controlled-access fixtures are explicitly skipped; skips must not be counted as passes. You can also use the standard command `python -m unittest discover -s tests -v`.

If you have already received an authorized knowledge base package, specify its absolute path from the source directory:

```sh
python kb_v1.py verify --bundle "/path/to/ADA_KB_V1_TEAM/bundle"
python kb_v1.py query --bundle "/path/to/ADA_KB_V1_TEAM/bundle" --cases "/path/to/cases.csv" --input-mode text --top-k 8 --output outputs/retrieval_run_01
```

Rebuilding as a maintainer requires the complete chapter text extraction, the original PDF, the approval workspace for seven figures/tables, the approval workspace for the two supplementary tables, and the actual MiniLM snapshot files; these are not public test fixtures. Use a new output directory:

```sh
python kb_v1.py build --kb-dir "/path/to/full_guideline_kb" --pdf "/path/to/guideline.pdf" --model "/path/to/1110a243fdf4706b3f48f1d95db1a4f5529b4d41" --chart-workspace "/path/to/approved_seven_charts" --supplement-workspace "/path/to/approved_tables_9_1_9_4" --output outputs/rebuild_9charts/bundle
python kb_v1.py verify --bundle outputs/rebuild_9charts/bundle
python kb_v1_chart_acceptance.py --bundle outputs/rebuild_9charts/bundle --output outputs/rebuild_9charts/chart_probes
python kb_v1_chart_delivery.py package --bundle outputs/rebuild_9charts/bundle --output outputs/rebuild_9charts/ADA_KB_V1_TEAM --zip
```

`kb_v1_acceptance.py` additionally provides maintainer-side acceptance testing for the original 10 cases and requires the controlled-access case table; it is not the selector for the 10 additional cases. The original run records for the 10 additional cases remain local; see the test summary for public statistics and reproducibility boundaries.

## Verification and Known Limitations

SHA-256 of the original controlled-access ZIP:

`b72fa32a20e2a1926f144afa5b39b4b463196a050b24dc7b67f77523e875bef5`

SHA-256 of the knowledge base `manifest.json`:

`73d58d6666b6b195574db5b42336d11b91e50d1ff303989e311628194d269bfa`

This code release did not rebuild or modify that ZIP. The newly added public test reporting tool is not part of the frozen ZIP above, so the sets of tool files in the source directory and the package are not identical.

Local offline retrieval on macOS and relocation to paths containing Chinese characters and spaces have been verified; Windows and installation in a fresh environment still await acceptance testing. Retrieval completed for all 10 additional cases, but quality spot checks still found issues with inapplicable populations, inclusion of special contexts, overlapping adjacent narrative text, and unlinked conditions across pages. Table 9.1 and 9.4 did not naturally appear in the top 8 for this batch of cases; “included in the knowledge base” must not be interpreted as “retrieved every time.”

Not all ordinary narrative text has been manually approved record by record. Approval of complete figures/tables also does not establish that every returned item applies to the current case. There is no complete, independently expert-annotated reference set, so this round does not report Recall, Precision, or improvements in clinical accuracy. The next step is to create an annotated set of applicable evidence and evaluate population and context filtering, deduplication, and cross-page linking before integrating into the team's API workflow. See the [Model and Source Notice](licenses/MODEL_AND_SOURCE_NOTICE.md) for authorization boundaries.
