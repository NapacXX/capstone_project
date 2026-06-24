############################################################
# 03_create_analysis_ready_dataset.R
#
# Purpose:
# Convert parsed long medication-action data into a wide
# analysis-ready dataset.
#
# Main output:
#   data/tranform_data/outputs/analysis_ready_wide.csv
#
# One row = one model response.
############################################################

library(tidyverse)
library(stringr)
library(readr)

# -----------------------------
# Paths
# -----------------------------

output_dir <- "data/transform_data/outputs"

standardized <- read_csv(
  file.path(output_dir, "standardized_model_responses.csv"),
  show_col_types = FALSE
)

parsed_long <- read_csv(
  file.path(output_dir, "parsed_medication_actions_long.csv"),
  show_col_types = FALSE
)

# -----------------------------
# Make parsed_long robust
# -----------------------------
# These columns should exist if Script 2 was updated.
# This block prevents Script 3 from crashing if an older parser output is used.

needed_parsed_cols <- c(
  "mentioned",
  "active_recommendation",
  "stop_or_avoid",
  "conditional",
  "manual_review_flag",
  "not_preferred",
  "component_mixed_action",
  "source_used"
)

for (col in needed_parsed_cols) {
  if (!col %in% names(parsed_long)) {
    if (col == "source_used") {
      parsed_long[[col]] <- NA_character_
    } else {
      parsed_long[[col]] <- 0L
    }
  }
}

parsed_long <- parsed_long %>%
  mutate(
    source_used = replace_na(source_used, "unknown"),
    
    manual_review_table = as.integer(
      manual_review_flag == 1 & source_used == "table"
    ),
    
    manual_review_rationale = as.integer(
      manual_review_flag == 1 & source_used == "free_text_rationale"
    ),
    
    manual_review_no_table = as.integer(
      manual_review_flag == 1 & source_used == "free_text_no_table"
    )
  )

# -----------------------------
# Convert medication parser output to wide format
# -----------------------------

parsed_wide <- parsed_long %>%
  transmute(
    response_id,
    drug_class,
    
    mentioned = mentioned,
    active = active_recommendation,
    stop_avoid = stop_or_avoid,
    conditional = conditional,
    not_preferred = not_preferred,
    component_mixed_action = component_mixed_action,
    
    manual_review = manual_review_flag,
    manual_review_table = manual_review_table,
    manual_review_rationale = manual_review_rationale,
    manual_review_no_table = manual_review_no_table
  ) %>%
  pivot_wider(
    id_cols = response_id,
    names_from = drug_class,
    values_from = c(
      mentioned,
      active,
      stop_avoid,
      conditional,
      not_preferred,
      component_mixed_action,
      manual_review,
      manual_review_table,
      manual_review_rationale,
      manual_review_no_table
    ),
    names_glue = "{drug_class}_{.value}",
    values_fill = list(
      mentioned = 0L,
      active = 0L,
      stop_avoid = 0L,
      conditional = 0L,
      not_preferred = 0L,
      component_mixed_action = 0L,
      manual_review = 0L,
      manual_review_table = 0L,
      manual_review_rationale = 0L,
      manual_review_no_table = 0L
    )
  )

# -----------------------------
# Other text-level indicators
# -----------------------------

# Stricter urgent-evaluation pattern:
# Avoid broad words like "hospital" or "severe hyperglycemia" alone.
# Also avoid "catabolic symptoms" alone, because it often appears as:
#   "no catabolic symptoms"
#   "without catabolic symptoms"
# Only flag clearer urgent-care, ED, DKA/HHS workup, ketone-check,
# or admission language.

urgent_eval_pattern <- str_c(
  c(
    "\\burgent\\s+(evaluation|assessment|care|referral)\\b",
    "\\bemergency\\s+(department|evaluation|care)\\b",
    "\\b(ed|er)\\s+(evaluation|visit|referral|care)\\b",
    "\\bsend\\s+(to|for).*\\b(ed|er|emergency department|hospital)\\b",
    "\\brefer\\s+(to|for).*\\b(ed|er|emergency department|hospital)\\b",
    "\\bhospital\\s+admission\\b",
    "\\badmit\\s+(to\\s+)?(the\\s+)?hospital\\b",
    "\\bhospitali[sz]e\\b",
    "\\bevaluate\\s+for\\s+(dka|hhs)\\b",
    "\\bassess\\s+for\\s+(dka|hhs)\\b",
    "\\brule\\s+out\\s+(dka|hhs)\\b",
    "\\b(dka|hhs)\\s+(workup|evaluation|assessment)\\b",
    "\\b(check|obtain|measure)\\s+.*\\bketones?\\b",
    "\\bserum\\s+ketones?\\b",
    "\\burine\\s+ketones?\\b"
  ),
  collapse = "|"
)

cost_pattern <- str_c(
  c(
    "\\bcost\\b",
    "\\bcosts\\b",
    "\\bcostly\\b",
    "\\bcost-effective\\b",
    "\\bafford\\b",
    "\\baffordable\\b",
    "\\baffordability\\b",
    "\\bfinancial\\b",
    "\\binsurance\\b",
    "\\bcoverage\\b",
    "\\bcopay\\b",
    "\\bco-pay\\b",
    "\\bout-of-pocket\\b",
    "\\bcheap\\b",
    "\\binexpensive\\b",
    "\\blow-cost\\b",
    "\\bfixed income\\b"
  ),
  collapse = "|"
)

text_flags <- standardized %>%
  transmute(
    response_id,
    
    treatment_plan_lower = str_to_lower(replace_na(treatment_plan, "")),
    
    urgent_eval_mentioned = as.integer(
      str_detect(
        treatment_plan_lower,
        regex(urgent_eval_pattern, ignore_case = TRUE)
      )
    ),
    
    cost_mentioned = as.integer(
      str_detect(
        treatment_plan_lower,
        regex(cost_pattern, ignore_case = TRUE)
      )
    )
  ) %>%
  select(-treatment_plan_lower)

# -----------------------------
# Join everything together
# -----------------------------

analysis_ready <- standardized %>%
  left_join(parsed_wide, by = "response_id") %>%
  left_join(text_flags, by = "response_id")

# -----------------------------
# Ensure expected indicator columns exist
# -----------------------------

expected_drug_classes <- c(
  "metformin",
  "sglt2i",
  "glp1",
  "gip_glp1",
  "dpp4i",
  "insulin_any",
  "basal_insulin",
  "prandial_insulin",
  "sulfonylurea",
  "tzd"
)

expected_measures <- c(
  "mentioned",
  "active",
  "stop_avoid",
  "conditional",
  "not_preferred",
  "component_mixed_action",
  "manual_review",
  "manual_review_table",
  "manual_review_rationale",
  "manual_review_no_table"
)

expected_indicator_cols <- as.vector(
  outer(expected_drug_classes, expected_measures, paste, sep = "_")
)

expected_indicator_cols <- c(
  expected_indicator_cols,
  "urgent_eval_mentioned",
  "cost_mentioned"
)

for (col in expected_indicator_cols) {
  if (!col %in% names(analysis_ready)) {
    analysis_ready[[col]] <- 0L
  }
}

# Replace missing parsed indicators with 0
indicator_cols <- names(analysis_ready)[
  str_detect(
    names(analysis_ready),
    "_mentioned$|_active$|_stop_avoid$|_conditional$|_not_preferred$|_component_mixed_action$|_manual_review$|_manual_review_table$|_manual_review_rationale$|_manual_review_no_table$|urgent_eval_mentioned$|cost_mentioned$"
  )
]

analysis_ready <- analysis_ready %>%
  mutate(
    across(
      all_of(indicator_cols),
      ~ replace_na(as.integer(.x), 0L)
    )
  )

# -----------------------------
# Row-level helper
# -----------------------------

row_any_cols <- function(data, cols) {
  if (length(cols) == 0) {
    return(rep(0L, nrow(data)))
  }
  
  as.integer(
    rowSums(
      data %>% select(all_of(cols)),
      na.rm = TRUE
    ) > 0
  )
}

# -----------------------------
# Row-level manual review flags
# -----------------------------

manual_review_cols <- names(analysis_ready)[
  str_detect(names(analysis_ready), "_manual_review$")
]

manual_review_table_cols <- names(analysis_ready)[
  str_detect(names(analysis_ready), "_manual_review_table$")
]

manual_review_rationale_cols <- names(analysis_ready)[
  str_detect(names(analysis_ready), "_manual_review_rationale$")
]

manual_review_no_table_cols <- names(analysis_ready)[
  str_detect(names(analysis_ready), "_manual_review_no_table$")
]

component_mixed_action_cols <- names(analysis_ready)[
  str_detect(names(analysis_ready), "_component_mixed_action$")
]

analysis_ready <- analysis_ready %>%
  mutate(
    manual_review_any = row_any_cols(analysis_ready, manual_review_cols),
    manual_review_table = row_any_cols(analysis_ready, manual_review_table_cols),
    manual_review_rationale = row_any_cols(analysis_ready, manual_review_rationale_cols),
    manual_review_no_table = row_any_cols(analysis_ready, manual_review_no_table_cols),
    component_mixed_action_any = row_any_cols(analysis_ready, component_mixed_action_cols)
  )

# -----------------------------
# QC outputs for Script 3
# -----------------------------

script3_summary <- tibble(
  check = c(
    "number_of_rows",
    "unique_response_ids",
    "unique_case_ids",
    "unique_models",
    "unique_prompt_conditions",
    "unique_run_numbers",
    "missing_treatment_plan",
    "manual_review_any",
    "manual_review_table",
    "manual_review_rationale",
    "manual_review_no_table",
    "component_mixed_action_any",
    "urgent_eval_mentioned",
    "cost_mentioned"
  ),
  value = c(
    nrow(analysis_ready),
    n_distinct(analysis_ready$response_id),
    n_distinct(analysis_ready$case_id),
    n_distinct(analysis_ready$model),
    n_distinct(analysis_ready$prompt_condition),
    n_distinct(analysis_ready$run_number),
    sum(is.na(analysis_ready$treatment_plan) | analysis_ready$treatment_plan == ""),
    sum(analysis_ready$manual_review_any, na.rm = TRUE),
    sum(analysis_ready$manual_review_table, na.rm = TRUE),
    sum(analysis_ready$manual_review_rationale, na.rm = TRUE),
    sum(analysis_ready$manual_review_no_table, na.rm = TRUE),
    sum(analysis_ready$component_mixed_action_any, na.rm = TRUE),
    sum(analysis_ready$urgent_eval_mentioned, na.rm = TRUE),
    sum(analysis_ready$cost_mentioned, na.rm = TRUE)
  )
)

active_rate_by_model <- analysis_ready %>%
  group_by(model, prompt_condition) %>%
  summarise(
    n_responses = n(),
    
    metformin_active_n = sum(metformin_active, na.rm = TRUE),
    metformin_active_rate = mean(metformin_active, na.rm = TRUE),
    
    sglt2i_active_n = sum(sglt2i_active, na.rm = TRUE),
    sglt2i_active_rate = mean(sglt2i_active, na.rm = TRUE),
    
    glp1_active_n = sum(glp1_active, na.rm = TRUE),
    glp1_active_rate = mean(glp1_active, na.rm = TRUE),
    
    gip_glp1_active_n = sum(gip_glp1_active, na.rm = TRUE),
    gip_glp1_active_rate = mean(gip_glp1_active, na.rm = TRUE),
    
    dpp4i_active_n = sum(dpp4i_active, na.rm = TRUE),
    dpp4i_active_rate = mean(dpp4i_active, na.rm = TRUE),
    
    insulin_any_active_n = sum(insulin_any_active, na.rm = TRUE),
    insulin_any_active_rate = mean(insulin_any_active, na.rm = TRUE),
    
    basal_insulin_active_n = sum(basal_insulin_active, na.rm = TRUE),
    basal_insulin_active_rate = mean(basal_insulin_active, na.rm = TRUE),
    
    prandial_insulin_active_n = sum(prandial_insulin_active, na.rm = TRUE),
    prandial_insulin_active_rate = mean(prandial_insulin_active, na.rm = TRUE),
    
    sulfonylurea_active_n = sum(sulfonylurea_active, na.rm = TRUE),
    sulfonylurea_active_rate = mean(sulfonylurea_active, na.rm = TRUE),
    
    tzd_active_n = sum(tzd_active, na.rm = TRUE),
    tzd_active_rate = mean(tzd_active, na.rm = TRUE),
    
    cost_mentioned_n = sum(cost_mentioned, na.rm = TRUE),
    cost_mentioned_rate = mean(cost_mentioned, na.rm = TRUE),
    
    urgent_eval_mentioned_n = sum(urgent_eval_mentioned, na.rm = TRUE),
    urgent_eval_mentioned_rate = mean(urgent_eval_mentioned, na.rm = TRUE),
    
    manual_review_any_n = sum(manual_review_any, na.rm = TRUE),
    manual_review_table_n = sum(manual_review_table, na.rm = TRUE),
    manual_review_rationale_n = sum(manual_review_rationale, na.rm = TRUE),
    
    component_mixed_action_any_n = sum(component_mixed_action_any, na.rm = TRUE),
    
    .groups = "drop"
  )

manual_review_response_list <- analysis_ready %>%
  filter(manual_review_any == 1) %>%
  select(
    response_id,
    case_id,
    model,
    prompt_condition,
    run_number,
    manual_review_any,
    manual_review_table,
    manual_review_rationale,
    manual_review_no_table,
    component_mixed_action_any,
    treatment_plan
  )

urgent_eval_response_list <- analysis_ready %>%
  filter(urgent_eval_mentioned == 1) %>%
  select(
    response_id,
    case_id,
    model,
    prompt_condition,
    run_number,
    urgent_eval_mentioned,
    treatment_plan
  )

# -----------------------------
# Save outputs
# -----------------------------

write_csv(
  analysis_ready,
  file.path(output_dir, "analysis_ready_wide.csv")
)

write_csv(
  script3_summary,
  file.path(output_dir, "script3_summary.csv")
)

write_csv(
  active_rate_by_model,
  file.path(output_dir, "script3_active_rate_by_model.csv")
)

write_csv(
  manual_review_response_list,
  file.path(output_dir, "script3_manual_review_response_list.csv")
)

write_csv(
  urgent_eval_response_list,
  file.path(output_dir, "script3_urgent_eval_response_list.csv")
)

message("Saved analysis-ready wide dataset and Script 3 QC outputs to: ", output_dir)