Capstone LLM Data Exploration Summary
====================================
Rows: 300
Columns after dropping unnamed columns: 71
Unique case_id values: 98
Unique vignette contents: 100
Number of models: 3

Important caution:
- Use case_id carefully. The script checks whether any case_id maps to multiple distinct vignette contents.
- Medication indicators are regex-derived and require manual validation before final analysis.
- ADA-related flags are exploratory screening flags, not definitive clinical adjudications.

Key output files:
- data_quality_basic_summary.csv
- case_id_content_problems.csv
- processed_llm_treatment_indicators.csv
- recommendation_rates_by_model.csv
- model_agreement_summary.csv
- high_disagreement_cases.csv
- exploratory_guideline_flags.csv
- manual_review_flags.csv
- exploratory_logistic_regression_results.csv
