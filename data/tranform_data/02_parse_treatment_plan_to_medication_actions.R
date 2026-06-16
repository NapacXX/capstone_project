############################################################
# 02_parse_treatment_plan_to_medication_actions.R
#
# Purpose:
# Parse each treatment_plan into medication-action indicators.
#
# Important:
# This does NOT only count keywords.
# It tries to distinguish:
#   - active recommendation
#   - stop/avoid
#   - conditional
#   - caution/monitoring
#   - unclear/manual review
#   - not preferred / alternative-only mentions
#   - within-class medication switches
#
# Main output:
#   data/tranform_data/outputs/parsed_medication_actions_long.csv
#
# One row = response_id x drug_class
############################################################

library(tidyverse)
library(stringr)
library(readr)
library(purrr)

output_dir <- "data/tranform_data/outputs"

standardized <- read_csv(
  file.path(output_dir, "standardized_model_responses.csv"),
  show_col_types = FALSE
)

# -----------------------------
# Drug class dictionary
# -----------------------------

drug_patterns <- tibble::tribble(
  ~drug_class, ~drug_regex,
  "metformin", "\\bmetformin\\b",
  "sglt2i", "\\b(sglt2|sglt-2|empagliflozin|dapagliflozin|canagliflozin|ertugliflozin|jardiance|farxiga|invokana)\\b",
  "glp1", "\\b(glp-1|glp1|semaglutide|liraglutide|dulaglutide|exenatide|ozempic|wegovy|trulicity|victoza|rybelsus)\\b",
  "gip_glp1", "\\b(tirzepatide|mounjaro|zepbound|dual gip|gip/glp-1|gip-glp-1)\\b",
  "dpp4i", "\\b(dpp-4|dpp4|sitagliptin|linagliptin|saxagliptin|alogliptin|januvia|tradjenta|onglyza)\\b",
  "insulin_any", "\\b(insulin|glargine|degludec|detemir|lispro|aspart|regular insulin|nph|basal|bolus)\\b",
  "basal_insulin", "\\b(basal insulin|insulin glargine|glargine|degludec|detemir|nph)\\b",
  "prandial_insulin", "\\b(prandial insulin|mealtime insulin|bolus insulin|lispro|aspart|glulisine|regular insulin|basal-bolus)\\b",
  "sulfonylurea", "\\b(sulfonylurea|glipizide|glyburide|glimepiride)\\b",
  "tzd", "\\b(tzd|thiazolidinedione|pioglitazone|rosiglitazone)\\b"
)

# -----------------------------
# Action patterns
# -----------------------------

action_patterns <- list(
  start_add = "\\b(start|starts|starting|started|initiate|initiates|initiating|initiated|add|adds|adding|added|begin|begins|prescribe|prescribed|new)\\b",
  
  continue = "\\b(continue|continues|continued|continuing|maintain|maintains|maintained|keep|resume|remain on|remains on|remained on|remains appropriate)\\b",
  
  increase_intensify = "\\b(increase|increases|increased|increasing|intensify|intensifies|intensified|escalate|escalates|escalated|titrate up|uptitrate|raise|adjust upward)\\b",
  
  reduce = "\\b(reduce|reduces|reduced|reducing|decrease|decreases|decreased|lower|lowered|dose reduction|down-titrate|downtitrate|adjust downward)\\b",
  
  stop_or_avoid = "\\b(stop|stops|stopped|stopping|discontinue|discontinues|discontinued|discontinuing|hold|held|avoid|avoids|avoided|avoiding|do not use|do not recommend|not recommended|contraindicated|inappropriate|do not start|remain off|remains off|should remain off|left off|keep off|withhold|withheld)\\b",
  
  conditional = "\\b(consider|considered|may|could|if tolerated|if affordable|if egfr|depending on|provided that|unless|option|reasonable option|alternative)\\b",
  
  caution_monitor = "\\b(caution|monitor|monitoring|watch|risk|adverse effect|side effect|renal function|kidney function|volume depletion|hypoglycemia|genital infection|gu infection|dose adjust|dose adjustment)\\b",
  
  not_preferred = "\\b(preferred over|rather than|instead of|not preferred|less preferred|would not choose|avoid adding|do not add|not necessary|no need for|not needed|not indicated|no indication|defer|deferred|lower priority|not the best fit|poor fit|poor initial choice|without requiring)\\b"
)

# Insulin terms that often appear in rationale but do not mean insulin treatment
insulin_false_positive_regex <- "\\b(insulin sensitivity|insulin resistance|insulin secretion|insulin production|endogenous insulin)\\b"

# Terms that indicate actual insulin treatment/action, so do NOT remove the unit
insulin_treatment_context_regex <- "\\b(start|started|continue|continued|stop|stopped|discontinue|discontinued|reduce|reduced|increase|increased|dose|units|basal|bolus|prandial|mealtime|glargine|degludec|detemir|lispro|aspart|nph|regular insulin|need for insulin|requiring insulin|requires insulin|insulin therapy)\\b"

# -----------------------------
# Helper: parse table rows
# -----------------------------

split_pipe_row <- function(line) {
  line %>%
    str_remove("^\\s*\\|") %>%
    str_remove("\\|\\s*$") %>%
    str_split("\\|", simplify = FALSE) %>%
    .[[1]] %>%
    str_squish()
}

is_separator_row <- function(line) {
  str_detect(
    line,
    "^\\s*\\|?\\s*:?-{3,}:?\\s*(\\|\\s*:?-{3,}:?\\s*)+\\|?\\s*$"
  )
}

empty_table <- function() {
  tibble(
    medication = character(),
    action = character(),
    plan = character(),
    dose = character(),
    frequency = character(),
    unit_text = character()
  )
}

parse_markdown_med_table <- function(text) {
  if (is.na(text) || text == "") {
    return(empty_table())
  }
  
  lines <- str_split(text, "\\n")[[1]] %>%
    str_squish()
  
  lines <- lines[lines != ""]
  
  # Handles both strict markdown and loose rows like:
  # Medication | Plan
  table_lines <- lines[str_detect(lines, fixed("|"))]
  
  if (length(table_lines) < 2) {
    return(empty_table())
  }
  
  header_idx <- which(
    str_detect(str_to_lower(table_lines), "\\b(medication|drug)\\b") &
      str_detect(str_to_lower(table_lines), "\\b(plan|action|recommendation)\\b")
  )
  
  if (length(header_idx) == 0) {
    return(empty_table())
  }
  
  header_idx <- header_idx[1]
  
  if (header_idx >= length(table_lines)) {
    return(empty_table())
  }
  
  headers <- split_pipe_row(table_lines[header_idx])
  headers_clean <- str_to_lower(headers)
  
  med_col <- which(str_detect(headers_clean, "\\b(medication|drug)\\b"))[1]
  action_col <- which(str_detect(headers_clean, "\\b(action)\\b"))[1]
  plan_col <- which(str_detect(headers_clean, "\\b(plan|recommendation)\\b"))[1]
  dose_col <- which(str_detect(headers_clean, "\\b(dose|dosage)\\b"))[1]
  freq_col <- which(str_detect(headers_clean, "\\b(frequency|freq)\\b"))[1]
  
  if (is.na(med_col)) {
    return(empty_table())
  }
  
  data_lines <- table_lines[(header_idx + 1):length(table_lines)]
  data_lines <- data_lines[!is_separator_row(data_lines)]
  data_lines <- data_lines[str_detect(data_lines, fixed("|"))]
  
  if (length(data_lines) == 0) {
    return(empty_table())
  }
  
  rows <- map(data_lines, split_pipe_row)
  
  parsed <- map_dfr(rows, function(cells) {
    if (length(cells) < med_col) {
      return(NULL)
    }
    
    get_cell <- function(idx) {
      if (is.na(idx) || idx > length(cells)) {
        return("")
      }
      cells[idx]
    }
    
    medication <- get_cell(med_col)
    
    plan_text <- if (!is.na(plan_col)) {
      get_cell(plan_col)
    } else {
      paste(cells[-med_col], collapse = " | ")
    }
    
    action_text <- get_cell(action_col)
    dose_text <- get_cell(dose_col)
    freq_text <- get_cell(freq_col)
    
    tibble(
      medication = medication,
      action = action_text,
      plan = plan_text,
      dose = dose_text,
      frequency = freq_text,
      unit_text = paste(cells, collapse = " | ")
    )
  })
  
  parsed %>%
    filter(!is.na(medication), medication != "")
}

# -----------------------------
# Helper: split free text into sentence-level units
# -----------------------------

split_text_units <- function(text, remove_table_lines = TRUE) {
  if (is.na(text) || text == "") {
    return(character())
  }
  
  lines <- str_split(text, "\\n")[[1]] %>%
    str_squish()
  
  if (remove_table_lines) {
    lines <- lines[
      !str_detect(lines, fixed("|")) &
        !is_separator_row(lines)
    ]
  }
  
  text_no_table <- paste(lines, collapse = " ")
  
  units <- str_split(
    text_no_table,
    "(?<=[.!?])\\s+|;\\s+"
  )[[1]]
  
  units %>%
    str_squish() %>%
    discard(~ .x == "")
}

# -----------------------------
# Helper: remove insulin false-positive units
# -----------------------------

remove_insulin_false_positive_units <- function(units, drug_class) {
  if (length(units) == 0) {
    return(units)
  }
  
  if (drug_class != "insulin_any") {
    return(units)
  }
  
  false_positive <- str_detect(
    units,
    regex(insulin_false_positive_regex, ignore_case = TRUE)
  )
  
  true_treatment_context <- str_detect(
    units,
    regex(insulin_treatment_context_regex, ignore_case = TRUE)
  )
  
  # Remove only if it is a false-positive rationale phrase AND
  # there is no actual insulin treatment/action context.
  units[!(false_positive & !true_treatment_context)]
}

# -----------------------------
# Helper: parse one drug class from one response
# -----------------------------

parse_one_drug_class <- function(text, drug_class, drug_regex) {
  med_pat <- regex(drug_regex, ignore_case = TRUE)
  
  table_rows <- parse_markdown_med_table(text)
  table_has_rows <- nrow(table_rows) > 0
  
  table_units <- table_rows %>%
    filter(
      str_detect(medication, med_pat) |
        str_detect(action, med_pat) |
        str_detect(plan, med_pat) |
        str_detect(dose, med_pat) |
        str_detect(frequency, med_pat) |
        str_detect(unit_text, med_pat)
    ) %>%
    pull(unit_text)
  
  # If drug appears in the table, use table as primary source.
  if (length(table_units) > 0) {
    units <- table_units
    source_used <- "table"
  } else {
    free_units <- split_text_units(text, remove_table_lines = TRUE)
    units <- free_units[str_detect(free_units, med_pat)]
    
    if (table_has_rows) {
      source_used <- "free_text_rationale"
    } else {
      source_used <- "free_text_no_table"
    }
  }
  
  units <- remove_insulin_false_positive_units(units, drug_class)
  
  mentioned <- length(units) > 0
  
  if (!mentioned) {
    return(tibble(
      drug_class = drug_class,
      mentioned = 0L,
      active_recommendation = 0L,
      start_add = 0L,
      continue = 0L,
      increase_intensify = 0L,
      reduce = 0L,
      stop_or_avoid = 0L,
      conditional = 0L,
      caution_monitor = 0L,
      not_preferred = 0L,
      unclear = 0L,
      manual_review_flag = 0L,
      component_mixed_action = 0L,
      example_text = NA_character_,
      source_used = NA_character_,
      table_has_rows = as.integer(table_has_rows)
    ))
  }
  
  detect_action <- function(pattern) {
    any(str_detect(units, regex(pattern, ignore_case = TRUE)))
  }
  
  # Explicit table-style action labels
  table_start <- any(str_detect(units, regex("\\bSTART\\b", ignore_case = TRUE)))
  table_continue <- any(str_detect(units, regex("\\bCONTINUE\\b", ignore_case = TRUE)))
  table_change <- any(str_detect(units, regex("\\b(CHANGE|SWITCH|MODIFY|ADJUST)\\b", ignore_case = TRUE)))
  table_stop <- any(str_detect(units, regex("\\bSTOP\\b", ignore_case = TRUE)))
  table_reduce <- any(str_detect(units, regex("\\b(REDUCE|DECREASE|LOWER)\\b", ignore_case = TRUE)))
  
  start_add <- detect_action(action_patterns$start_add) | table_start
  continue <- detect_action(action_patterns$continue) | table_continue
  increase_intensify <- detect_action(action_patterns$increase_intensify)
  reduce <- detect_action(action_patterns$reduce) | table_reduce
  stop_or_avoid <- detect_action(action_patterns$stop_or_avoid) | table_stop
  conditional <- detect_action(action_patterns$conditional)
  caution_monitor <- detect_action(action_patterns$caution_monitor)
  not_preferred <- detect_action(action_patterns$not_preferred)
  
  # ----------------------------------------------------------
  # Active recommendation rules:
  #
  # Table source:
  #   If a class has START/CONTINUE/CHANGE in any table row, count it active.
  #   Do NOT erase active just because another row in the same class says STOP.
  #   Example:
  #     Dulaglutide STOP + Semaglutide START
  #   should be:
  #     glp1_active = 1
  #     glp1_stop_or_avoid = 1
  #     component_mixed_action = 1
  #
  # Free-text no-table source:
  #   Count active only if there is start/continue/intensify language without
  #   stop/avoid or not-preferred language.
  #
  # Free-text rationale source:
  #   Do not count active, because rationale mentions are often alternatives,
  #   comparisons, or explanations rather than recommendations.
  # ----------------------------------------------------------
  
  table_active_signal <- table_start |
    table_continue |
    table_change |
    increase_intensify |
    (start_add & !stop_or_avoid)
  
  free_text_active_signal <- (start_add | continue | increase_intensify) &
    !stop_or_avoid &
    !not_preferred
  
  active_recommendation <- case_when(
    source_used == "table" ~ table_active_signal,
    source_used == "free_text_no_table" ~ free_text_active_signal,
    source_used == "free_text_rationale" ~ FALSE,
    TRUE ~ FALSE
  )
  
  component_mixed_action <- as.integer(
    source_used == "table" &&
      active_recommendation &&
      stop_or_avoid
  )
  
  unclear <- mentioned &&
    !active_recommendation &&
    !stop_or_avoid &&
    !conditional &&
    !caution_monitor &&
    !not_preferred
  
  # Do not automatically flag table-level mixed actions as errors.
  # They often represent valid within-class switches, e.g.,
  # STOP dulaglutide and START semaglutide.
  manual_review_flag <- unclear |
    ((active_recommendation & stop_or_avoid) & component_mixed_action == 0L) |
    ((stop_or_avoid & (start_add | continue | increase_intensify)) & component_mixed_action == 0L)
  
  tibble(
    drug_class = drug_class,
    mentioned = as.integer(mentioned),
    active_recommendation = as.integer(active_recommendation),
    start_add = as.integer(start_add),
    continue = as.integer(continue),
    increase_intensify = as.integer(increase_intensify),
    reduce = as.integer(reduce),
    stop_or_avoid = as.integer(stop_or_avoid),
    conditional = as.integer(conditional),
    caution_monitor = as.integer(caution_monitor),
    not_preferred = as.integer(not_preferred),
    unclear = as.integer(unclear),
    manual_review_flag = as.integer(manual_review_flag),
    component_mixed_action = as.integer(component_mixed_action),
    example_text = paste(head(units, 2), collapse = " || "),
    source_used = source_used,
    table_has_rows = as.integer(table_has_rows)
  )
}

# -----------------------------
# Parse one response
# -----------------------------

parse_one_response <- function(text) {
  out <- map2_dfr(
    drug_patterns$drug_class,
    drug_patterns$drug_regex,
    ~ parse_one_drug_class(text, .x, .y)
  )
  
  # ----------------------------------------------------------
  # Fix insulin_any logic:
  #
  # A treatment plan may continue basal insulin while stopping
  # prandial insulin. In that situation:
  #   insulin_any_active should be 1
  # even if insulin_any_stop_or_avoid is also 1.
  # ----------------------------------------------------------
  
  get_max_indicator <- function(df, target_class, target_col) {
    vals <- df %>%
      filter(drug_class == target_class) %>%
      pull({{ target_col }})
    
    if (length(vals) == 0 || all(is.na(vals))) {
      return(0L)
    }
    
    out <- suppressWarnings(max(vals, na.rm = TRUE))
    
    if (!is.finite(out)) {
      return(0L)
    }
    
    as.integer(out)
  }
  
  basal_active <- get_max_indicator(out, "basal_insulin", active_recommendation)
  prandial_active <- get_max_indicator(out, "prandial_insulin", active_recommendation)
  
  basal_stop <- get_max_indicator(out, "basal_insulin", stop_or_avoid)
  prandial_stop <- get_max_indicator(out, "prandial_insulin", stop_or_avoid)
  
  any_insulin_component_active <- basal_active == 1L | prandial_active == 1L
  any_insulin_component_stop <- basal_stop == 1L | prandial_stop == 1L
  
  mixed_insulin_components <- any_insulin_component_active & any_insulin_component_stop
  
  out <- out %>%
    mutate(
      active_recommendation = if_else(
        drug_class == "insulin_any" & any_insulin_component_active,
        1L,
        active_recommendation
      ),
      mentioned = if_else(
        drug_class == "insulin_any" &
          (any_insulin_component_active | any_insulin_component_stop),
        1L,
        mentioned
      ),
      stop_or_avoid = if_else(
        drug_class == "insulin_any" & any_insulin_component_stop,
        1L,
        stop_or_avoid
      ),
      component_mixed_action = if_else(
        drug_class == "insulin_any" & mixed_insulin_components,
        1L,
        component_mixed_action
      ),
      manual_review_flag = if_else(
        drug_class == "insulin_any" & mixed_insulin_components,
        0L,
        manual_review_flag
      )
    )
  
  out
}

# -----------------------------
# Apply parser to all responses
# -----------------------------

parsed_long <- standardized %>%
  select(
    response_id,
    case_id,
    model,
    model_raw,
    model_version,
    prompt_condition,
    run_number,
    treatment_plan
  ) %>%
  mutate(parsed = map(treatment_plan, parse_one_response)) %>%
  unnest(parsed)

# -----------------------------
# Parser QC summaries
# -----------------------------

parser_summary_by_drug <- parsed_long %>%
  group_by(drug_class) %>%
  summarise(
    n_rows = n(),
    n_mentioned = sum(mentioned, na.rm = TRUE),
    n_active = sum(active_recommendation, na.rm = TRUE),
    n_stop_or_avoid = sum(stop_or_avoid, na.rm = TRUE),
    n_conditional = sum(conditional, na.rm = TRUE),
    n_caution_monitor = sum(caution_monitor, na.rm = TRUE),
    n_not_preferred = sum(not_preferred, na.rm = TRUE),
    n_unclear = sum(unclear, na.rm = TRUE),
    n_manual_review = sum(manual_review_flag, na.rm = TRUE),
    n_component_mixed_action = sum(component_mixed_action, na.rm = TRUE),
    .groups = "drop"
  )

parser_summary_by_source <- parsed_long %>%
  count(source_used, drug_class, name = "n") %>%
  arrange(source_used, drug_class)

manual_review_examples <- parsed_long %>%
  filter(manual_review_flag == 1) %>%
  select(
    response_id,
    case_id,
    model,
    prompt_condition,
    run_number,
    drug_class,
    example_text,
    source_used,
    mentioned,
    active_recommendation,
    stop_or_avoid,
    conditional,
    caution_monitor,
    not_preferred,
    unclear,
    component_mixed_action
  )

mixed_action_examples <- parsed_long %>%
  filter(component_mixed_action == 1) %>%
  select(
    response_id,
    case_id,
    model,
    prompt_condition,
    run_number,
    drug_class,
    example_text,
    source_used,
    active_recommendation,
    stop_or_avoid,
    component_mixed_action
  )

# -----------------------------
# Save outputs
# -----------------------------

write_csv(
  parsed_long,
  file.path(output_dir, "parsed_medication_actions_long.csv")
)

write_csv(
  parser_summary_by_drug,
  file.path(output_dir, "parser_summary_by_drug.csv")
)

write_csv(
  parser_summary_by_source,
  file.path(output_dir, "parser_summary_by_source.csv")
)

write_csv(
  manual_review_examples,
  file.path(output_dir, "parser_manual_review_examples.csv")
)

write_csv(
  mixed_action_examples,
  file.path(output_dir, "parser_mixed_action_examples.csv")
)

message("Saved parsed medication-action dataset and parser QC outputs to: ", output_dir)