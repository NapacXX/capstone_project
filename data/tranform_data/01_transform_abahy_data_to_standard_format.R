############################################################
# 01_transform_abahy_data_to_standard_format.R
#
# Purpose:
# Convert Abhay/MSK raw output data into a standardized
# response-level dataset.
#
# One row = one model response to one case under one prompt
# condition and one run.
#
# Main output:
#   data/tranform_data/outputs/standardized_model_responses.csv
#
# Notes:
# - Current dataset appears baseline-only.
# - If prompt_condition is missing, set to "baseline".
# - If run_number is missing, set to 1.
# - Do not fabricate ADA-guided outputs.
############################################################

library(tidyverse)
library(janitor)
library(stringr)
library(readr)

# -----------------------------
# Paths
# -----------------------------

input_file <- "data/raw/final_results_capstone_data_ver2.csv"
output_dir <- "data/tranform_data/outputs"

dir.create(output_dir, showWarnings = FALSE, recursive = TRUE)

# -----------------------------
# Helper functions
# -----------------------------

get_first_existing_col <- function(data, candidates, default = NA) {
  existing <- intersect(candidates, names(data))
  if (length(existing) == 0) {
    return(rep(default, nrow(data)))
  }
  data[[existing[1]]]
}

is_empty_column <- function(x) {
  all(is.na(x) | str_squish(as.character(x)) == "")
}

standardize_model_name <- function(x) {
  x_lower <- str_to_lower(as.character(x))
  
  case_when(
    str_detect(x_lower, "gpt") & str_detect(x_lower, "5\\.4") ~ "GPT-5.4",
    str_detect(x_lower, "gpt") ~ "GPT",
    str_detect(x_lower, "opus") ~ "Claude Opus",
    str_detect(x_lower, "sonnet") ~ "Claude Sonnet",
    str_detect(x_lower, "claude") ~ "Claude",
    TRUE ~ as.character(x)
  )
}

extract_model_version <- function(model_raw, model_version_current) {
  model_raw_lower <- str_to_lower(as.character(model_raw))
  current <- as.character(model_version_current)
  
  extracted <- case_when(
    str_detect(model_raw_lower, "gpt-5\\.4") ~ "gpt-5.4",
    str_detect(model_raw_lower, "claude-sonnet-4-6") ~ "claude-sonnet-4-6",
    str_detect(model_raw_lower, "claude-opus-4-6-v1") ~ "claude-opus-4-6-v1",
    str_detect(model_raw_lower, "claude-opus-4-6") ~ "claude-opus-4-6",
    TRUE ~ model_raw_lower
  )
  
  if_else(
    is.na(current) | current == "" | str_to_lower(current) == "na",
    extracted,
    current
  )
}

standardize_prompt_condition <- function(x) {
  x_clean <- str_to_lower(str_squish(as.character(x)))
  x_clean <- str_replace_all(x_clean, "\\s+", "_")
  x_clean <- str_replace_all(x_clean, "-", "_")
  
  if_else(
    is.na(x_clean) | x_clean == "" | x_clean == "na",
    "baseline",
    x_clean
  )
}

yes_no_to_binary <- function(x) {
  x_lower <- str_to_lower(str_squish(as.character(x)))
  
  case_when(
    x_lower %in% c("yes", "y", "true", "1", "present") ~ 1L,
    x_lower %in% c("no", "n", "false", "0", "absent") ~ 0L,
    TRUE ~ NA_integer_
  )
}

# -----------------------------
# Read raw data
# -----------------------------

raw_original <- read_csv(input_file, show_col_types = FALSE) %>%
  clean_names()

# Identify and drop fully empty columns, such as x71, x72
empty_cols <- names(raw_original)[map_lgl(raw_original, is_empty_column)]

raw <- raw_original %>%
  select(-all_of(empty_cols)) %>%
  select(-matches("^unnamed"))

# Save which columns were dropped
dropped_empty_columns <- tibble(
  dropped_column = empty_cols
)

# -----------------------------
# Build standardized dataset
# -----------------------------

standardized <- raw %>%
  mutate(
    case_id = as.character(
      get_first_existing_col(
        raw,
        c("case_id", "case", "vignette_id", "patient_id")
      )
    ),
    case_id = str_to_upper(str_squish(case_id)),
    
    model_raw = as.character(
      get_first_existing_col(
        raw,
        c("model", "model_name", "llm", "llm_model")
      )
    ),
    model = standardize_model_name(model_raw),
    
    model_version_raw = as.character(
      get_first_existing_col(
        raw,
        c("model_version", "version", "llm_version"),
        default = NA_character_
      )
    ),
    model_version = extract_model_version(model_raw, model_version_raw),
    
    prompt_condition = as.character(
      get_first_existing_col(
        raw,
        c("prompt_condition", "prompt_type", "condition"),
        default = "baseline"
      )
    ),
    prompt_condition = standardize_prompt_condition(prompt_condition),
    
    run_number = suppressWarnings(as.integer(
      get_first_existing_col(
        raw,
        c("run_number", "run", "replicate", "iteration"),
        default = 1
      )
    )),
    run_number = if_else(is.na(run_number), 1L, run_number),
    
    treatment_plan = as.character(
      get_first_existing_col(
        raw,
        c(
          "treatment_plan",
          "treatment",
          "response",
          "model_response",
          "answer",
          "output"
        )
      )
    ),
    
    vignette_text = as.character(
      get_first_existing_col(
        raw,
        c("vignette_text", "vignette", "case_text", "patient_vignette"),
        default = NA_character_
      )
    ),
    
    age = suppressWarnings(as.numeric(
      get_first_existing_col(
        raw,
        c("age", "patient_age"),
        default = NA_real_
      )
    )),
    
    sex = as.character(
      get_first_existing_col(
        raw,
        c("sex", "gender"),
        default = NA_character_
      )
    ),
    
    race = as.character(
      get_first_existing_col(
        raw,
        c("race", "race_ethnicity", "race_and_ethnicity", "ethnicity"),
        default = NA_character_
      )
    ),
    
    hba1c = suppressWarnings(as.numeric(
      get_first_existing_col(
        raw,
        c("hba1c", "hba1c_percent", "a1c", "hemoglobin_a1c"),
        default = NA_real_
      )
    )),
    
    egfr = suppressWarnings(as.numeric(
      get_first_existing_col(
        raw,
        c("egfr", "egfr_ml_min_1_73m2", "egfr_ml_min", "estimated_gfr"),
        default = NA_real_
      )
    )),
    
    bmi = suppressWarnings(as.numeric(
      get_first_existing_col(
        raw,
        c("bmi", "bmi_kg_m2", "body_mass_index"),
        default = NA_real_
      )
    )),
    
    ckd = get_first_existing_col(
      raw,
      c("ckd", "chronic_kidney_disease"),
      default = NA_character_
    ),
    
    heart_failure = get_first_existing_col(
      raw,
      c("heart_failure", "heart_failure_present", "hf_present"),
      default = NA_character_
    ),
    
    baseline_diabetes_drugs = as.character(
      get_first_existing_col(
        raw,
        c(
          "baseline_diabetes_drugs",
          "current_dm_drugs",
          "current_diabetes_drugs",
          "diabetes_medications"
        ),
        default = NA_character_
      )
    )
  )

# -----------------------------
# Derive cancer_history
# -----------------------------

# If explicit cancer_history exists, keep it.
# If not, derive it from columns beginning with cancer_.
# If no cancer-related columns exist, set to NA.
cancer_cols <- names(standardized)[str_detect(names(standardized), "^cancer_")]

if ("cancer_history" %in% names(standardized)) {
  standardized <- standardized %>%
    mutate(cancer_history = as.character(cancer_history))
} else if (length(cancer_cols) > 0) {
  standardized <- standardized %>%
    mutate(
      cancer_history = if_else(
        rowSums(across(all_of(cancer_cols), ~ yes_no_to_binary(.x)), na.rm = TRUE) > 0,
        "yes",
        "no"
      )
    )
} else {
  standardized <- standardized %>%
    mutate(cancer_history = NA_character_)
}

# -----------------------------
# Create response_id and order columns
# -----------------------------

standardized <- standardized %>%
  mutate(
    response_id = paste0("resp_", str_pad(row_number(), width = 5, pad = "0"))
  ) %>%
  relocate(
    response_id,
    case_id,
    model,
    model_raw,
    model_version,
    prompt_condition,
    run_number,
    treatment_plan,
    vignette_text,
    age,
    sex,
    race,
    hba1c,
    egfr,
    bmi,
    ckd,
    heart_failure,
    cancer_history,
    baseline_diabetes_drugs
  )

# -----------------------------
# Warning checks
# -----------------------------

if (!"treatment_plan" %in% names(standardized)) {
  warning("No treatment_plan column was created. Please check raw column names.")
}

if (all(is.na(standardized$treatment_plan) | standardized$treatment_plan == "")) {
  warning("All treatment_plan values are missing or empty. Parsing cannot proceed.")
}

if (any(is.na(standardized$case_id) | standardized$case_id == "")) {
  warning("Some case_id values are missing or empty.")
}

if (any(is.na(standardized$model) | standardized$model == "")) {
  warning("Some model values are missing or empty.")
}

# -----------------------------
# Quality checks
# -----------------------------

duplicate_rows <- standardized %>%
  count(case_id, model, prompt_condition, run_number, name = "n") %>%
  filter(n > 1)

# Updated DM026 check:
# This now detects DM026, DM026(1), DM026(2), DM026(3), etc.
dm026_check <- standardized %>%
  filter(str_detect(case_id, "^DM026")) %>%
  group_by(case_id) %>%
  summarise(
    n_rows = n(),
    n_models = n_distinct(model),
    models_present = paste(sort(unique(model)), collapse = ", "),
    n_unique_vignette_texts = n_distinct(vignette_text),
    .groups = "drop"
  ) %>%
  arrange(case_id)

# Case-level model coverage check
all_models <- standardized %>%
  distinct(model) %>%
  pull(model) %>%
  sort()

case_model_coverage <- standardized %>%
  group_by(case_id, prompt_condition, run_number) %>%
  summarise(
    n_rows = n(),
    n_models = n_distinct(model),
    models_present = paste(sort(unique(model)), collapse = ", "),
    missing_models = paste(setdiff(all_models, unique(model)), collapse = ", "),
    .groups = "drop"
  ) %>%
  mutate(
    expected_n_models = length(all_models),
    complete_model_coverage = n_models == expected_n_models,
    missing_models = if_else(missing_models == "", NA_character_, missing_models)
  ) %>%
  arrange(case_id, prompt_condition, run_number)

case_model_coverage_problems <- case_model_coverage %>%
  filter(!complete_model_coverage)

# Treatment plan missingness by model
missing_treatment_by_model <- standardized %>%
  group_by(model, prompt_condition) %>%
  summarise(
    n_rows = n(),
    n_missing_treatment_plan = sum(is.na(treatment_plan) | treatment_plan == ""),
    pct_missing_treatment_plan = n_missing_treatment_plan / n_rows,
    .groups = "drop"
  )

# Overall QC summary
qc_summary <- tibble(
  check = c(
    "number_of_rows",
    "unique_case_ids",
    "unique_models",
    "unique_prompt_conditions",
    "unique_run_numbers",
    "missing_treatment_plan",
    "duplicated_case_model_prompt_run",
    "case_model_coverage_problems",
    "dm026_like_case_ids_detected",
    "dropped_empty_columns"
  ),
  value = c(
    nrow(standardized),
    n_distinct(standardized$case_id),
    n_distinct(standardized$model),
    n_distinct(standardized$prompt_condition),
    n_distinct(standardized$run_number),
    sum(is.na(standardized$treatment_plan) | standardized$treatment_plan == ""),
    nrow(duplicate_rows),
    nrow(case_model_coverage_problems),
    nrow(dm026_check),
    length(empty_cols)
  )
)

# -----------------------------
# Save outputs
# -----------------------------

write_csv(
  standardized,
  file.path(output_dir, "standardized_model_responses.csv")
)

write_csv(
  qc_summary,
  file.path(output_dir, "qc_summary_standardization.csv")
)

write_csv(
  duplicate_rows,
  file.path(output_dir, "duplicate_case_model_prompt_run_rows.csv")
)

write_csv(
  dm026_check,
  file.path(output_dir, "dm026_check.csv")
)

write_csv(
  case_model_coverage,
  file.path(output_dir, "case_model_coverage.csv")
)

write_csv(
  case_model_coverage_problems,
  file.path(output_dir, "case_model_coverage_problems.csv")
)

write_csv(
  missing_treatment_by_model,
  file.path(output_dir, "missing_treatment_by_model.csv")
)

write_csv(
  dropped_empty_columns,
  file.path(output_dir, "dropped_empty_columns.csv")
)

message("Saved standardized response-level dataset and QC outputs to: ", output_dir)
