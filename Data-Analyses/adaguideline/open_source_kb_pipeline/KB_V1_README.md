# ADA Knowledge Base v1 — Teammate Usage and Review Guide

> Latest delivery (2026-09-29): 9 approved figure/table groups have been integrated into the official controlled research package. Please read the [Final Knowledge Base Delivery Guide](KB_V1_FINAL_DELIVERY.md) first. The historical instructions below are retained from the item-by-item review-candidate stage; their references to 0 visual records, pending item-by-item review, and `--allow-candidate` do not apply to the latest official package. Windows still awaits acceptance testing on an actual Windows machine.

## Historical Candidate Package Notes (2026-09-28)

Status at this stage: **review candidate, not official v1**. AI-assisted preliminary review was completed for 623 visual candidates, but final team dispositions and figure/table-level confirmation were still pending. The retrieval candidate was intended only for engineering acceptance testing and contained no unapproved visual conclusions; this did not establish text-only delivery as a substitute for official v1.

This program returns source evidence only. It does not generate treatment recommendations, call generation APIs, modify teammates' prompts, or score clinical guideline adherence. Similarity is not an accuracy score or a clinical applicability score.

## 1. Which Files Should I Open?

The local delivery folder contains:

- `START_HERE.md`: This guide.
- `SOURCE_NAVIGATION.md`: Navigation for 10 pages and 623 candidates; open the HTML pages to inspect original content, AI comments, and original-image links side by side.
- `review/review_ledger.csv`: A copy for the team's item-by-item review. The original team approval table has not been changed.
- `review/group_reviews.json`: Relationship confirmation for 10 page-level groups; Table9.2 also requires cross-page checking. These are not 10 independent figures/tables, nor do they represent completeness automatically proven by a model.
- `ai_notes/`: Item-by-item preliminary reviews and coverage/omission reports for each figure/table. Suggested corrections have not been applied.
- `bundle/`: An offline engineering retrieval package explicitly marked `review_candidate`.
- `tools/`: The standalone retrieval entry point, maintenance tools, pinned dependencies, and licenses.
- `review_sources/`: 60 crops and 10 full-page images.
- `source_path_map.json`: A separate mapping between historical absolute paths and package-relative paths. Do not directly change historical audit paths; they are included in the old approval fingerprints.
- `delivery_manifest.json`: A delivery file inventory with SHA-256 checksums; it is an integrity manifest, not an identity-authenticated electronic signature.
- `KB_V1_IMPLEMENTATION_STATUS.md` and `KB_V1_QUALITY_REVIEW.md`: Actual completion status and a quality spot check of 10 cases; `test_results/` contains the automated acceptance summary.

The original bare JSON filenames and absolute paths in the historical review Markdown are retained for auditing, not as cross-platform browsing entry points. Use the original-image, crop, and frozen-JSON links in the outer HTML navigation instead.

The package contains copyrighted PDFs/extracted content and may only be shared under controlled access within the project scope for which the appropriate permissions have been obtained. Creating a local package does not mean redistribution permission has been granted; do not upload it to public GitHub or public cloud storage. No upload was performed at this stage.

## 2. Installation on Windows and macOS

The target is **Python 3.12, CPU**. Installing dependencies for the first time requires network access; normal queries load the model from the package without downloading a model, requiring an API key, or requiring Ollama. Testing has been completed on macOS arm64; the Windows steps are provided for teammate acceptance testing, and **successful execution on Windows is not claimed**.

First extract the complete delivery package and enter the folder containing `bundle` and `tools`. Do not copy only `embeddings.npy` or symbolic links from the model cache.

macOS:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r tools/requirements-v1-consumer.lock.txt
.venv/bin/python tools/kb_v1.py doctor
.venv/bin/python tools/kb_v1.py verify --bundle bundle --allow-candidate
```

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r tools\requirements-v1-consumer.lock.txt
.\.venv\Scripts\python.exe tools\kb_v1.py doctor
.\.venv\Scripts\python.exe tools\kb_v1.py verify --bundle bundle --allow-candidate
```

Environment activation is not required, so there is no need to relax the PowerShell script execution policy. If installation fails, first save the error and the `doctor` output; do not arbitrarily substitute a different set of dependencies and describe the result as the same version. The pinned dependencies are the combination tested on macOS at this stage, not a guarantee of installation results across platforms.

`--allow-candidate` explicitly permits use of this historical engineering candidate package. Official release packages should not depend on this option. Rejecting candidate packages by default is expected behavior, not a program failure.

## 3. Single-Case and Batch Retrieval

The default CSV format uses UTF-8 encoding and must contain at least the following two columns; do not put a generated treatment plan in `vignette_text`:

```csv
case_id,vignette_text
example-001,"Synthetic case: adult with type 2 diabetes, HbA1c 8.2%, eGFR 45 mL/min/1.73m2, currently taking metformin."
```

macOS example (on Windows, replace `.venv/bin/python` with `.\.venv\Scripts\python.exe`):

```bash
.venv/bin/python tools/kb_v1.py query --bundle bundle --cases cases.csv --output evidence_run_01 --allow-candidate
```

A single case can also be placed in a UTF-8 text file, retaining the complete case content:

```bash
.venv/bin/python tools/kb_v1.py query --bundle bundle --vignette-file one_case.txt --case-id my-case-001 --output evidence_one --allow-candidate
```

The output directory must not already exist, to prevent overwriting historical results. Use a different directory name for each run. Outputs:

- `evidence.jsonl`: One case per line, including the complete case input, the actual query windows, the KB version, 8 deduplicated evidence groups by default, sources, and statuses; related dependency evidence is returned with the primary evidence.
- `evidence.md`: A readable version with PDF page links.
- `retrieval_summary.json`: Actual counts of cases, windows, and evidence items.

The program rejects empty cases, missing required fields, duplicate field names, and different case content assigned to the same `case_id`. Duplicate rows for the same case are retained only once. Long cases are split into windows according to the **actual model limit of 256 tokens**; each evidence item receives its highest similarity across the query windows. The end of a case is not silently discarded. The original case is still retained in full.

For existing structured cases, explicitly add `--input-mode structured`. Supported fields are listed in `STRUCTURED_FIELDS` in `kb_v1_runtime.py`, including HbA1c, eGFR, UACR, BMI, CKD/HF/ASCVD-related statuses, MASH/MASLD, baseline glucose-lowering medications, and preferences. Missing fields are not filled with default clinical conditions. At least one recognized clinical field must be nonempty; `treatment_plan`, `prompt`, `rationale`, and `model` are excluded from the query.

The repository's older case CSV has two empty column names and is rejected for duplicate column names if supplied directly. This was an input-quality issue actually found at this stage. The maintainer acceptance script **explicitly projects and logs** the required case fields into a new CSV, without modifying the old table or reading treatment plans to construct queries. Teammates should submit cases using the simple two-column specification above.

## 4. How Should Human Review Be Performed?

### Item-by-Item Review

Start with `SOURCE_NAVIGATION.md`, open the HTML for each page, and then inspect the source PDF/crops. For every item, confirm at least the following:

1. Whether the text, medication/classes, values, units, and applicable populations match.
2. Whether conditions, negation, AND/OR, frequency, and dose increases/decreases are preserved.
3. Whether arrows actually exist and their directions and endpoints are correct; table rows and columns are not flowchart nodes by default.
4. Whether complete table row labels, column labels, units, continuation pages, captions, and footnotes are present.
5. The role of symbols such as `+`, `$`, `†`, and `‡` within the figure; do not interpret cost or relative advantages as recommendation strength.
6. Whether the item duplicates other crops; if merging, explicitly identify the retained target without deleting the historical mapping to the original candidate.

An AI status of `reviewed_with_findings` does not necessarily mean an item is incorrect: it also covers duplicates, dependencies, context, or source locations requiring confirmation. An AI status of `reviewed_no_change` is not human approval either. Disagreements over clinical meaning should be adjudicated by members with the appropriate expertise.

The final disposition may be `approved`, `rejected`, or `merged`; approval after correction still uses `approved`, but a new fingerprint for the corrected content must be obtained first. Rejections must also record a reason, reviewer, and time. Do not delete pending-review rows to create the appearance that everything is complete.

### Corrections and Reapproval (Maintainer Workflow, in the Original Repository's Pipeline Directory)

AI suggestions are written only to `suggested_corrections` and do not automatically change the source evidence. First copy `review/validation/working_approval_template.csv` to a new working path, and have the team edit the permitted `corrections_json` in that copy. **Never pass the existing original team table to Step 07's `--approval-csv`: this argument is both an input and an output.**

```bash
python 07_validate_visual_logic_outputs.py --raw-dir outputs/visual_logic_raw_review_20260823_completed --approval-csv team_work/approvals.csv --output-dir team_work/validation_pass1 --symbol-registry guideline_symbol_registry.csv
```

When corrections are present, the first revalidation produces new fingerprints pending review. The team must confirm the corrected content before filling in the approval fields in the copy; then rerun Step 07 using a new output directory. Do not reuse old approval fingerprints. Retain both validation records.

Then create a new v1 review version (the output must not already exist):

```bash
python kb_v1.py review --raw-dir outputs/visual_logic_raw_review_20260823_completed --approvals team_work/approvals.csv --registry guideline_symbol_registry.csv --ai-notes outputs/kb_v1_work/20260928/ai_notes/all_623_ai_notes.jsonl --output team_work/review_after_corrections
```

The AI preliminary review comments concern the original candidates. Corrected content still requires renewed human confirmation; old AI comments should not be treated as revalidation of the corrected content. If new structures/relationships are needed, rather than small corrections to existing permitted fields, first establish a traceable new candidate version, mappings to the original candidates, and the corresponding technical validation. Do not bypass validation by manually changing canonical statuses. When adding candidates, pass `--origin-manifest` to `review`, pointing to the initial version's `review_manifest.json`; the checksum of the original set of 623 IDs is fixed, and additions must not replace or delete disposition records for the original candidates.

### Final Disposition Ledger and Figure/Table-Level Confirmation

In the new version's `review_ledger.csv`, edit only the following human-review columns:

- `final_disposition`, `human_reviewer`, `human_reviewed_at`, and `human_notes`.
- `reviewed_content_sha256`: After reviewing the actual content, enter the corresponding `current_v1_content_sha256` for that version; do not blindly copy values in bulk.
- `merge_target`: For a merge, point to the ultimately approved retained record.
- `dependency_ids` (a JSON array) and `dependency_reviewed` (explicit true/false): Approval must explicitly identify the required nodes, conditions, and footnotes; even when there are no dependencies, an empty array must be explicitly confirmed. Do not use an empty array to conceal missing conditions.

Use timezone-aware ISO-8601 review timestamps, such as `2026-09-28T15:30:00-04:00`. Do not change AI comments, original content, source locations, or existing hash columns. The importer accepts changes only to the human-review columns listed above and does not automatically supply signatures or approvals:

```bash
python kb_v1.py import-human-review --review-dir team_work/review_after_corrections --csv team_work/human_review.csv --output team_work/human_review_ledger.jsonl
```

For each group in `group_reviews.json`, the team must also confirm omissions across crops, deduplication, and pathway/table dependencies, and fill in `human_confirmed`, `group_status=complete`, the reviewer, a timezone-aware timestamp, the rationale, and the verified `reviewed_group_sha256`. Run the check only after every record has a final disposition and all dependencies are complete:

```bash
python kb_v1.py release-check --review-dir team_work/review_after_corrections --ledger team_work/human_review_ledger.jsonl
```

Automated checks can verify only field completeness, fingerprints, sources, and explicit dependencies; they cannot prove that signatures came from real people or that clinical logic is complete. The team must manage write permissions for approval files and responsibility for review. In this historical delivery, all of these human-review fields remained pending.

## 5. Maintainer Rebuild

In the repository's `Data-Analyses/adaguideline/open_source_kb_pipeline/` directory, use Python 3.12:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-v1-maintainer.lock.txt
.venv/bin/python kb_v1.py doctor --maintainer
```

Prepare the existing full-chapter `outputs/full_guideline_kb/`, the original PDF, the current raw visual JSON, and the pinned local MiniLM snapshot. The model revision is `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`. The maintainer must obtain the model in advance; neither building nor querying downloads it automatically.

Official release (**expected to fail at this historical stage because human review was incomplete**):

```bash
python kb_v1.py build --kb-dir outputs/full_guideline_kb --pdf "ADA principles for pharmacologic therapy.pdf" --model "/path/to/1110a243fdf4706b3f48f1d95db1a4f5529b4d41" --review-dir team_work/review_after_corrections --ledger team_work/human_review_ledger.jsonl --output outputs/releases/ada_ch9_v1
```

Explicitly add `--candidate` only for engineering development, producing a `review_candidate` without visual evidence. The official workflow requires final dispositions for all candidates, 07/08 technical checks, v1 human-review fingerprints, figure/table-level confirmation, and dependency closure, with at least one passing visual evidence record. Official builds are rejected when there are zero visual records.

Base text retains the original wording; the retrieval copy removes only clearly identifiable administrative/reference sections. Page previews do not consume retrieval slots; duplicate text on the same page that is entirely contained in a table is retained for auditing only. Tables remain **PDF text tables whose row/column relationships have not yet been validated**, not structured clinical rules proven correct; continuation pages and footnotes for the same table are supplied as dependencies with the evidence. Ordinary PDF text from known figure pages carries a layout warning: the absence of released visual-model records does not mean that this text correctly expresses the original figure's cross-column conditions, arrows, and footnotes.

## 6. Local and Teammate Acceptance Testing

```bash
python -m unittest discover -s tests -v
python kb_v1_acceptance.py --bundle outputs/kb_v1_work/20260928/portable_candidate_v4 --source-cases ../../../data/raw_inputs/final_results_capstone_data_ver2.csv --output outputs/kb_v1_work/20260928/acceptance_teammate_01
```

The maintainer report records: text and structured retrieval for 10 existing cases, actual token windows, a NumPy/FAISS inner-product comparison using the same vectors, tests of paths containing Chinese characters and spaces and of different working directories, source-file checksums, and unit-test logs. This script creates a separate new directory and does not overwrite results.

Windows teammates must additionally submit: the Python version, installation logs, doctor and verify results, at least one example retrieval, and whether the source PDF can be opened. Windows may be changed from "pending acceptance" to "accepted" only after these checks pass. Cross-language retrieval quality still requires a separate evaluation benchmark.

### Reproducing This Candidate Workspace and Local Delivery (Maintainers)

Run the following in the original repository's pipeline directory using the maintainer environment above. Inputs are the existing full-chapter KB, 60 raw visual JSON files and their manifest, the original team approval table, the symbol registry, original images, and the AI comments supplied in this delivery. Replace `--model` with the path to the pinned local snapshot. All output directory names must be new to avoid overwriting completed work.

```bash
python kb_v1.py review --raw-dir outputs/visual_logic_raw_review_20260823_completed --approvals manual_review_packages/ada_principles_20260823_qwen3vl_0533d743/visual_review_approvals.csv --registry guideline_symbol_registry.csv --ai-notes outputs/kb_v1_work/20260928/ai_notes/all_623_ai_notes.jsonl --output team_work/review_reproduced
python kb_v1.py build --kb-dir outputs/full_guideline_kb --pdf "ADA principles for pharmacologic therapy.pdf" --model "/path/to/1110a243fdf4706b3f48f1d95db1a4f5529b4d41" --review-dir team_work/review_reproduced --output team_work/candidate_reproduced --candidate
python kb_v1.py verify --bundle team_work/candidate_reproduced --allow-candidate
python kb_v1_acceptance.py --bundle team_work/candidate_reproduced --source-cases ../../../data/raw_inputs/final_results_capstone_data_ver2.csv --output team_work/acceptance_reproduced
python kb_v1_delivery.py --bundle team_work/candidate_reproduced --review team_work/review_reproduced --notes outputs/kb_v1_work/20260928/ai_notes --acceptance team_work/acceptance_reproduced --output team_work/ADA_KB_REVIEW_CANDIDATE --zip
```

Maintainer acceptance testing includes a FAISS comparison. The macOS sandbox restricted its shared memory at this stage, and the comparison ultimately passed outside the local sandbox with authorization. Do not count native-library errors as passing, or attribute this maintainer-side issue to teammates' NumPy retrieval. Teammates only query the existing package and do not need to rerun visual extraction or maintainer-side FAISS tests.

The old PDF, raw JSON, and images retain their original location strings. Maintainers need the corresponding source files to rebuild; consumers only need the fully extracted package and do not depend on those old absolute paths. Generating AI suggestions is not itself a deterministic rerun script. The reproduction above revalidates and builds from saved preliminary review results bound to their sources; it does not claim that every AI review will produce exactly the same judgments.

## 7. Boundary with the API Workflow

Teammates submit cases to this retriever and receive a JSONL evidence package with sources and the KB version; they can then decide how to use that evidence in their own API calls. Prompt integration, model generation, citation checking, and downstream scoring belong to the next stage. They are not provided at this stage, nor has an improvement in clinical accuracy been demonstrated.

The model uses exact NumPy inner products with no FAISS runtime dependency. Exact score ties are sorted by stable ID; tiny score differences within floating-point error have no clinical significance. The knowledge base may still return similar text when suitable evidence is missing, so conditions and applicability must be read; top-1 must not automatically be treated as the correct answer.
