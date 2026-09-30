# ADA RAG Test Results and Publication Scope

This directory contains the automated test results collected before code publication and a summary of the additional ten-case retrieval test conducted on September 29, 2026. Case narratives, complete guideline evidence, original approval materials, and model files are distributed separately under controlled access. Successful execution and the applicability of retrieved evidence are evaluated separately.

## Code Tests Before Publication

The tests were run using `run_public_tests.py` from the pipeline directory in the existing macOS Python 3.12.4 environment. The result file was copied from a temporary directory into this directory. To reproduce the test run, choose a new output file:

```sh
python run_public_tests.py --output outputs/my_public_checkout_tests.json
```

Of 364 tests discovered, 312 passed and 52 were skipped, with zero failures and zero errors. The [machine-readable results](public_checkout_tests.json) record the start time, elapsed time, reason for each skip, and source-code SHA-256 hashes. The checkout was based on the remote main branch with the published code and documentation added; controlled local test fixtures were not copied into it. This run did not test installation in a fresh environment or execution on Windows.

The first standard unittest run produced two errors because the publication file list omitted `KB_V1_README.md`, which the candidate-package builder requires. The document was added and the tests were rerun. A missing parenthesis in the new reporting utility was also corrected before that utility executed any tests. After one successful test run, saving the report failed because of directory permissions in the separate checkout; the final report was saved to a temporary file and then copied here. Retrieval ranking, approvals, KB content, and production code were not changed to make tests pass.

The source PDF and complete approval workspace are not included in the public repository, so the integration tests that require them explicitly report skips. The maintainer previously ran all 364 tests without skips in the original workspace containing the complete controlled inputs. That historical result is separate from the public-checkout result reported here.

## Results for the Additional Ten Cases

The tested KB version was `ada2026-ch9-cf6259c5b1fb35c5`. Execution started at 00:55 UTC on September 30, 2026, which was September 29 in New York. This publication summarizes the existing results; cases were not reselected and retrieval parameters were not adjusted.

The selected case IDs were DM021, DM023, DM024, DM026(3), DM038, DM049, DM053, DM097, DM117, and DM120, with no overlap with the previous ten cases. Only `case_id,vignette_text` were used; generated plans, prompts, and model responses were excluded from retrieval queries. The complete inputs and item-level AI review of all 80 results are retained under controlled access.

| Check | Observed result |
| --- | --- |
| Environment | macOS arm64, Python 3.12.4, CPU, bundled model loaded offline |
| Retrieval settings | Text mode, top 8 per case, no generation API |
| Cases / evidence groups / query windows | 10 / 80 / 16; all ten cases completed successfully |
| Text / whole-chart hits | 60 / 20 |
| Distinct evidence records | 25 |
| Chart hits | Figure 9.4: 10; Table 9.2: 6; Figure 9.5: 4 |
| Natural retrieval of the added tables | Neither Table 9.1 nor Table 9.4 appeared in the top 8 for this batch |
| Actual query-window lengths | 42–256 tokens; all original case text was preserved |
| Exact duplicate records / text within a case | 0; partial and semantic overlap still occurred |
| Chart approval and provenance | All 20 chart hits retained approval status, complete chart fields, and source-page images |
| Query elapsed time | 2.740 seconds, including model loading and verification |
| Total automated execution and verification time | 5.619 seconds, excluding human or AI quality review |
| Protected files | 553 checked; zero changed between the before and after snapshots |

The [machine-readable ten-case summary](additional_ten_cases_summary.json) contains aggregate metrics and checksums for the controlled originals, including the release manifest and ZIP. Team members with authorized access to those originals can verify them against these hashes. Hashes do not replace the underlying data or establish clinical validity.

The retrieval command succeeded in the initial `run_001`, but the new audit script raised a KeyError when ordinary text evidence lacked the optional `evidence_group_id` field. The audit script was corrected to fall back to `record_id`, matching the existing program, and rerun into a new directory, `run_002`. The complete evidence JSON objects from both runs were identical; ranking and the KB were unchanged. The logged 432>256 warning arose during tokenization of the complete query for token counting. An independent check confirmed that all 16 actual inference windows contained at most 256 tokens.

## Remaining Evidence Quality Issues

AI-assisted review covered all 80 evidence groups: 14 were labeled relevant, 42 required conditions or additional context, 24 were not applicable, and zero were uncertain. These labels have not been adjudicated by clinical experts and are not estimates of precision or clinical accuracy.

- Evidence about the type 1 diabetes population was returned for three type 2 diabetes cases.
- Post-transplant evidence was returned for six cases whose inputs did not establish that context.
- Mixed-topic passages included special treatment contexts unsupported by the case inputs.
- Some cross-page conditions were not explicitly linked; adjacent passages overlapped, and flattened table text sometimes duplicated whole-table evidence.
- Source evidence totaled approximately 15,060–25,847 characters per case. Downstream API integration must preserve necessary footnotes and conditions when handling that context.

Checks of declared dependencies cannot identify every missing condition that has not been declared. Engineering success also does not guarantee population or subtype applicability, such as HFpEF versus HFrEF. Without a complete expert-labeled relevance set, Recall@8 was not calculated and no improvement in clinical accuracy is claimed. Indexing the added tables does not guarantee their retrieval for these cases; the earlier targeted chart probes and natural case retrieval evaluate different behavior.

## Reproduction

Run the code tests using the command above with an output filename that does not already exist. Tests that lack controlled inputs should report skips rather than be counted as passes.

Reproducing retrieval for the additional cases requires authorized access to the same released bundle and the original batch's `sample_cases.csv`. After obtaining these inputs and verifying the SHA-256 values in the summary, run the following from the pipeline directory:

```sh
python kb_v1.py doctor
python kb_v1.py verify --bundle "/path/to/ADA_KB_V1_TEAM/bundle"
python kb_v1.py query --bundle "/path/to/ADA_KB_V1_TEAM/bundle" --cases "/path/to/new_ten/sample_cases.csv" --input-mode text --top-k 8 --output outputs/reproduce_new_ten/evidence
python kb_v1.py verify --bundle "/path/to/ADA_KB_V1_TEAM/bundle"
```

These commands reproduce retrieval and bundle verification. They do not regenerate the historical 553-file preservation snapshots, AI labels, or complete review report. The historical `run_test.py` depends on local approval, ZIP, and earlier acceptance directories, so it is retained with its logs under controlled access rather than published as a general-purpose test entry point. `kb_v1_acceptance.py` uses a different fixed set of ten cases and must not be substituted for this sample.

## Validation Still Pending

This report does not establish Windows execution, installation in a fresh environment, generation API integration, expert adjudication of all relevance labels, or clinical validity. The next evaluation should establish an expert-labeled set of applicable evidence, then measure the effects of population filtering, reranking, deduplication, and cross-page dependency handling.
