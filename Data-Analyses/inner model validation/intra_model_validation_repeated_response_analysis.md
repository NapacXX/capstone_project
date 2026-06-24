# Intra-Model Validation for Repeated Model Responses

* [1 Purpose](#purpose)
* [2 Input Preparation](#input-preparation)
* [3 Treatment Plan Parser](#treatment-plan-parser)
* [4 Parse Repeated Responses](#parse-repeated-responses)
* [5 Structured Treatment Signatures](#structured-treatment-signatures)
* [6 Krippendorff’s Alpha](#krippendorffs-alpha)
* [7 Secondary Agreement Metrics](#secondary-agreement-metrics)
* [8 Visualizations](#visualizations)
* [9 Manual Review Sample](#manual-review-sample)
* [10 Output Files](#output-files)

# 1 Purpose

This workflow evaluates intra-model consistency using the current repeated response dataset. It does not call any API and does not generate new model responses. Each model is evaluated separately across five repeated responses per case.

The primary stability metric is the case-level variation ratio, calculated from a structured treatment signature derived from parsed medication-action indicators.

```
project_root <- normalizePath(file.path(getwd(), "../.."), mustWork = TRUE)

raw_path <- file.path(project_root, "data/raw/Capstone_repeated_data.csv")
output_dir <- file.path(project_root, "data/inner model validation/outputs")

dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)

raw_path
```

```
## [1] "/Users/jiayiwei/Documents/Capstone/Group/capstone_project/data/raw/Capstone_repeated_data.csv"
```

```
output_dir
```

```
## [1] "/Users/jiayiwei/Documents/Capstone/Group/capstone_project/data/inner model validation/outputs"
```

# 2 Input Preparation

```
clean_column_names <- function(x) {
  x %>%
    str_replace_all("[^A-Za-z0-9]+", "_") %>%
    str_replace_all("^_|_$", "") %>%
    str_to_lower()
}

raw <- read_csv(raw_path, show_col_types = FALSE) %>%
  rename_with(clean_column_names)

required_cols <- c("case_id", "model", "treatment_plan", "vignette_text")
missing_cols <- setdiff(required_cols, names(raw))

if (length(missing_cols) > 0) {
  stop(
    "Missing required columns: ",
    paste(missing_cols, collapse = ", ")
  )
}

standardize_model_name <- function(x) {
  x_lower <- str_to_lower(as.character(x))

  case_when(
    str_detect(x_lower, "gpt") & str_detect(x_lower, "5\\.4") ~ "GPT-5.4",
    str_detect(x_lower, "opus") ~ "Claude Opus 4.6",
    str_detect(x_lower, "sonnet") ~ "Claude Sonnet 4.6",
    TRUE ~ as.character(x)
  )
}

standardized <- raw %>%
  mutate(
    case_id = str_squish(as.character(case_id)),
    model_raw = as.character(model),
    model_clean = standardize_model_name(model_raw),
    treatment_plan = replace_na(as.character(treatment_plan), ""),
    vignette_text = as.character(vignette_text)
  ) %>%
  arrange(case_id, model_clean) %>%
  group_by(case_id, model_clean) %>%
  mutate(repeat_id = row_number()) %>%
  ungroup() %>%
  mutate(
    response_id = paste0("resp_", str_pad(row_number(), width = 5, pad = "0"))
  ) %>%
  relocate(
    response_id,
    case_id,
    model_raw,
    model_clean,
    repeat_id,
    vignette_text,
    treatment_plan
  )

repeat_check <- standardized %>%
  count(model_clean, case_id, name = "n_repeats") %>%
  summarise(
    model_clean = unique(model_clean),
    n_cases = n(),
    min_repeats = min(n_repeats),
    max_repeats = max(n_repeats),
    all_have_5_repeats = all(n_repeats == 5),
    .by = model_clean
  )

duplicate_check <- standardized %>%
  count(case_id, model_clean, repeat_id, name = "n") %>%
  filter(n > 1)

if (nrow(duplicate_check) > 0) {
  stop("Duplicate case_id + model_clean + repeat_id rows detected.")
}

if (!all(repeat_check$all_have_5_repeats)) {
  stop("Not every case_id + model_clean combination has exactly 5 repeats.")
}

write_csv(
  standardized,
  file.path(output_dir, "intra_model_standardized_responses.csv")
)
```

```
overview <- tibble(
  metric = c(
    "response_rows",
    "unique_cases",
    "unique_models",
    "expected_repeats_per_case_model",
    "blank_treatment_plans"
  ),
  value = c(
    nrow(standardized),
    n_distinct(standardized$case_id),
    n_distinct(standardized$model_clean),
    5,
    sum(standardized$treatment_plan == "")
  )
)

overview %>%
  kable(caption = "Dataset overview") %>%
  kable_styling(full_width = FALSE)
```

Dataset overview

| metric | value |
| --- | --- |
| response\_rows | 1500 |
| unique\_cases | 100 |
| unique\_models | 3 |
| expected\_repeats\_per\_case\_model | 5 |
| blank\_treatment\_plans | 0 |

```
repeat_check %>%
  arrange(model_clean) %>%
  kable(caption = "Repeat-count validation by model") %>%
  kable_styling(full_width = FALSE)
```

Repeat-count validation by model

| model\_clean | n\_cases | min\_repeats | max\_repeats | all\_have\_5\_repeats |
| --- | --- | --- | --- | --- |
| Claude Opus 4.6 | 100 | 5 | 5 | TRUE |
| Claude Sonnet 4.6 | 100 | 5 | 5 | TRUE |
| GPT-5.4 | 100 | 5 | 5 | TRUE |

# 3 Treatment Plan Parser

```
drug_patterns <- tibble::tribble(
  ~drug_class, ~drug_regex,
  "metformin",
  "\\bmetformin\\b",
  "sglt2i",
  "\\b(sglt2|sglt-2|empagliflozin|dapagliflozin|canagliflozin|ertugliflozin|jardiance|farxiga|invokana)\\b",
  "glp1",
  "\\b(glp-1|glp1|semaglutide|liraglutide|dulaglutide|exenatide|ozempic|wegovy|trulicity|victoza|rybelsus)\\b",
  "gip_glp1",
  "\\b(tirzepatide|mounjaro|zepbound|dual gip|gip/glp-1|gip-glp-1)\\b",
  "dpp4i",
  "\\b(dpp-4|dpp4|sitagliptin|linagliptin|saxagliptin|alogliptin|januvia|tradjenta|onglyza)\\b",
  "insulin_any",
  "\\b(insulin|glargine|degludec|detemir|lispro|aspart|regular insulin|nph|basal|bolus)\\b",
  "basal_insulin",
  "\\b(basal insulin|insulin glargine|glargine|degludec|detemir|nph)\\b",
  "prandial_insulin",
  "\\b(prandial insulin|mealtime insulin|bolus insulin|lispro|aspart|glulisine|regular insulin|basal-bolus)\\b",
  "sulfonylurea",
  "\\b(sulfonylurea|glipizide|glyburide|glimepiride)\\b",
  "tzd",
  "\\b(tzd|thiazolidinedione|pioglitazone|rosiglitazone)\\b"
)

action_patterns <- list(
  start_add = "\\b(start|starts|starting|started|initiate|initiates|initiating|initiated|add|adds|adding|added|begin|begins|prescribe|prescribed|new)\\b",
  continue = "\\b(continue|continues|continued|continuing|maintain|maintains|maintained|keep|resume|remain on|remains on|remained on|remains appropriate)\\b",
  increase_intensify = "\\b(increase|increases|increased|increasing|intensify|intensifies|intensified|escalate|escalates|escalated|titrate up|uptitrate|raise|adjust upward|change)\\b",
  reduce = "\\b(reduce|reduces|reduced|reducing|decrease|decreases|decreased|lower|lowered|dose reduction|down-titrate|downtitrate|adjust downward)\\b",
  stop_or_avoid = "\\b(stop|stops|stopped|stopping|discontinue|discontinues|discontinued|discontinuing|hold|held|avoid|avoids|avoided|avoiding|do not use|do not recommend|not recommended|contraindicated|inappropriate|do not start|remain off|remains off|should remain off|left off|keep off|withhold|withheld)\\b",
  conditional = "\\b(consider|considered|may|could|if tolerated|if affordable|if egfr|depending on|provided that|unless|option|reasonable option|alternative)\\b",
  caution_monitor = "\\b(caution|monitor|monitoring|watch|risk|adverse effect|side effect|renal function|kidney function|volume depletion|hypoglycemia|genital infection|gu infection|dose adjust|dose adjustment)\\b",
  not_preferred = "\\b(preferred over|rather than|instead of|not preferred|less preferred|would not choose|avoid adding|do not add|not necessary|no need for|not needed|not indicated|no indication|defer|deferred|lower priority|not the best fit|poor fit|poor initial choice|without requiring)\\b"
)

insulin_false_positive_regex <- "\\b(insulin sensitivity|insulin resistance|insulin secretion|insulin production|endogenous insulin)\\b"

insulin_treatment_context_regex <- "\\b(start|started|continue|continued|stop|stopped|discontinue|discontinued|reduce|reduced|increase|increased|dose|units|basal|bolus|prandial|mealtime|glargine|degludec|detemir|lispro|aspart|nph|regular insulin|need for insulin|requiring insulin|requires insulin|insulin therapy)\\b"

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
  headers <- split_pipe_row(table_lines[header_idx])
  headers_clean <- str_to_lower(headers)

  med_col <- which(str_detect(headers_clean, "\\b(medication|drug)\\b"))[1]
  action_col <- which(str_detect(headers_clean, "\\b(action)\\b"))[1]
  plan_col <- which(str_detect(headers_clean, "\\b(plan|recommendation)\\b"))[1]
  dose_col <- which(str_detect(headers_clean, "\\b(dose|dosage)\\b"))[1]
  freq_col <- which(str_detect(headers_clean, "\\b(frequency|freq)\\b"))[1]

  if (is.na(med_col) || header_idx >= length(table_lines)) {
    return(empty_table())
  }

  data_lines <- table_lines[(header_idx + 1):length(table_lines)]
  data_lines <- data_lines[!is_separator_row(data_lines)]
  data_lines <- data_lines[str_detect(data_lines, fixed("|"))]

  map_dfr(data_lines, function(line) {
    cells <- split_pipe_row(line)

    get_cell <- function(idx) {
      if (is.na(idx) || idx > length(cells)) {
        return("")
      }
      cells[idx]
    }

    if (length(cells) < med_col || get_cell(med_col) == "") {
      return(NULL)
    }

    plan_text <- if (!is.na(plan_col)) {
      get_cell(plan_col)
    } else {
      paste(cells[-med_col], collapse = " | ")
    }

    tibble(
      medication = get_cell(med_col),
      action = get_cell(action_col),
      plan = plan_text,
      dose = get_cell(dose_col),
      frequency = get_cell(freq_col),
      unit_text = paste(cells, collapse = " | ")
    )
  })
}

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

  units <- str_split(
    paste(lines, collapse = " "),
    "(?<=[.!?])\\s+|;\\s+"
  )[[1]]

  units %>%
    str_squish() %>%
    purrr::discard(~ .x == "")
}

remove_insulin_false_positive_units <- function(units, drug_class) {
  if (length(units) == 0 || drug_class != "insulin_any") {
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

  units[!(false_positive & !true_treatment_context)]
}

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

  if (length(table_units) > 0) {
    units <- table_units
    source_used <- "table"
  } else {
    free_units <- split_text_units(text, remove_table_lines = TRUE)
    units <- free_units[str_detect(free_units, med_pat)]
    source_used <- if_else(
      table_has_rows,
      "free_text_rationale",
      "free_text_no_table"
    )
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

  manual_review_flag <- unclear |
    ((active_recommendation & stop_or_avoid) & component_mixed_action == 0L) |
    ((stop_or_avoid & (start_add | continue | increase_intensify)) &
      component_mixed_action == 0L)

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

parse_one_response <- function(text) {
  out <- map2_dfr(
    drug_patterns$drug_class,
    drug_patterns$drug_regex,
    ~ parse_one_drug_class(text, .x, .y)
  )

  get_max_indicator <- function(df, target_class, target_col) {
    vals <- df %>%
      filter(drug_class == target_class) %>%
      pull({{ target_col }})

    if (length(vals) == 0 || all(is.na(vals))) {
      return(0L)
    }

    as.integer(max(vals, na.rm = TRUE))
  }

  basal_active <- get_max_indicator(out, "basal_insulin", active_recommendation)
  prandial_active <- get_max_indicator(out, "prandial_insulin", active_recommendation)
  basal_stop <- get_max_indicator(out, "basal_insulin", stop_or_avoid)
  prandial_stop <- get_max_indicator(out, "prandial_insulin", stop_or_avoid)

  any_insulin_component_active <- basal_active == 1L | prandial_active == 1L
  any_insulin_component_stop <- basal_stop == 1L | prandial_stop == 1L
  mixed_insulin_components <- any_insulin_component_active &
    any_insulin_component_stop

  out %>%
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
}
```

# 4 Parse Repeated Responses

```
parsed_long <- standardized %>%
  select(
    response_id,
    case_id,
    model_raw,
    model_clean,
    repeat_id,
    treatment_plan
  ) %>%
  mutate(parsed = map(treatment_plan, parse_one_response)) %>%
  unnest(parsed)

write_csv(
  parsed_long,
  file.path(output_dir, "intra_model_parsed_long.csv")
)

parsed_wide <- parsed_long %>%
  transmute(
    response_id,
    drug_class,
    mentioned,
    active = active_recommendation,
    start_add,
    continue,
    increase_intensify,
    reduce,
    stop_avoid = stop_or_avoid,
    conditional,
    caution_monitor,
    not_preferred,
    manual_review = manual_review_flag,
    component_mixed_action
  ) %>%
  pivot_wider(
    id_cols = response_id,
    names_from = drug_class,
    values_from = c(
      mentioned,
      active,
      start_add,
      continue,
      increase_intensify,
      reduce,
      stop_avoid,
      conditional,
      caution_monitor,
      not_preferred,
      manual_review,
      component_mixed_action
    ),
    names_glue = "{drug_class}_{.value}",
    values_fill = 0L
  ) %>%
  left_join(standardized, by = "response_id") %>%
  relocate(
    response_id,
    case_id,
    model_raw,
    model_clean,
    repeat_id,
    treatment_plan
  )

write_csv(
  parsed_wide,
  file.path(output_dir, "intra_model_parsed_wide.csv")
)

parser_qc <- tibble(
  metric = c(
    "parsed_long_rows",
    "parsed_wide_rows",
    "drug_classes",
    "expected_parsed_long_rows",
    "manual_review_flags"
  ),
  value = c(
    nrow(parsed_long),
    nrow(parsed_wide),
    n_distinct(parsed_long$drug_class),
    nrow(standardized) * nrow(drug_patterns),
    sum(parsed_long$manual_review_flag, na.rm = TRUE)
  )
)

parser_qc %>%
  kable(caption = "Parser QC") %>%
  kable_styling(full_width = FALSE)
```

Parser QC

| metric | value |
| --- | --- |
| parsed\_long\_rows | 15000 |
| parsed\_wide\_rows | 1500 |
| drug\_classes | 10 |
| expected\_parsed\_long\_rows | 15000 |
| manual\_review\_flags | 501 |

# 5 Structured Treatment Signatures

```
signature_cols <- c(
  "mentioned",
  "active_recommendation",
  "start_add",
  "continue",
  "increase_intensify",
  "reduce",
  "stop_or_avoid",
  "conditional",
  "not_preferred",
  "manual_review_flag"
)

treatment_signatures <- parsed_long %>%
  mutate(
    signature_piece = str_c(
      drug_class,
      mentioned,
      active_recommendation,
      start_add,
      continue,
      increase_intensify,
      reduce,
      stop_or_avoid,
      conditional,
      not_preferred,
      manual_review_flag,
      sep = ":"
    )
  ) %>%
  arrange(response_id, drug_class) %>%
  summarise(
    treatment_signature = paste(signature_piece, collapse = "|"),
    n_drug_classes_mentioned = sum(mentioned, na.rm = TRUE),
    n_active_classes = sum(active_recommendation, na.rm = TRUE),
    n_stop_avoid_classes = sum(stop_or_avoid, na.rm = TRUE),
    n_manual_review_classes = sum(manual_review_flag, na.rm = TRUE),
    .by = c(response_id, case_id, model_clean, repeat_id)
  )

signature_counts <- treatment_signatures %>%
  count(model_clean, case_id, treatment_signature, name = "signature_count") %>%
  arrange(model_clean, case_id, desc(signature_count))

case_stability <- signature_counts %>%
  summarise(
    n_repeats = sum(signature_count),
    n_unique_signatures = n(),
    modal_signature_count = max(signature_count),
    variation_ratio = 1 - modal_signature_count / n_repeats,
    exact_match = as.integer(n_unique_signatures == 1),
    .by = c(model_clean, case_id)
  ) %>%
  arrange(model_clean, desc(variation_ratio), case_id)

model_summary <- case_stability %>%
  summarise(
    n_cases = n(),
    mean_variation_ratio = mean(variation_ratio),
    median_variation_ratio = median(variation_ratio),
    exact_match_cases = sum(exact_match),
    exact_match_rate = mean(exact_match),
    cases_with_any_variation = sum(variation_ratio > 0),
    max_variation_ratio = max(variation_ratio),
    .by = model_clean
  ) %>%
  arrange(mean_variation_ratio)

variation_ratio_distribution <- case_stability %>%
  count(model_clean, variation_ratio, name = "n_cases") %>%
  group_by(model_clean) %>%
  mutate(percent_cases = n_cases / sum(n_cases)) %>%
  ungroup() %>%
  arrange(model_clean, variation_ratio)

top_unstable_cases <- case_stability %>%
  group_by(model_clean) %>%
  slice_max(
    order_by = variation_ratio,
    n = 10,
    with_ties = FALSE
  ) %>%
  ungroup() %>%
  left_join(
    standardized %>%
      select(case_id, vignette_text) %>%
      distinct(),
    by = "case_id"
  )

write_csv(
  treatment_signatures,
  file.path(output_dir, "intra_model_treatment_signatures.csv")
)
write_csv(
  signature_counts,
  file.path(output_dir, "intra_model_signature_counts.csv")
)
write_csv(
  case_stability,
  file.path(output_dir, "intra_model_case_stability.csv")
)
write_csv(
  model_summary,
  file.path(output_dir, "intra_model_model_summary.csv")
)
write_csv(
  variation_ratio_distribution,
  file.path(output_dir, "intra_model_variation_ratio_distribution.csv")
)
write_csv(
  top_unstable_cases,
  file.path(output_dir, "intra_model_top_unstable_cases.csv")
)
```

```
model_summary %>%
  mutate(
    mean_variation_ratio = round(mean_variation_ratio, 3),
    median_variation_ratio = round(median_variation_ratio, 3),
    exact_match_rate = percent(exact_match_rate, accuracy = 0.1)
  ) %>%
  kable(caption = "Model-level intra-model stability summary") %>%
  kable_styling(full_width = FALSE)
```

Model-level intra-model stability summary

| model\_clean | n\_cases | mean\_variation\_ratio | median\_variation\_ratio | exact\_match\_cases | exact\_match\_rate | cases\_with\_any\_variation | max\_variation\_ratio |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Claude Opus 4.6 | 100 | 0.274 | 0.2 | 38 | 38.0% | 62 | 0.8 |
| Claude Sonnet 4.6 | 100 | 0.538 | 0.6 | 4 | 4.0% | 96 | 0.8 |
| GPT-5.4 | 100 | 0.616 | 0.6 | 2 | 2.0% | 98 | 0.8 |

```
variation_ratio_distribution %>%
  mutate(percent_cases = percent(percent_cases, accuracy = 0.1)) %>%
  kable(caption = "Variation ratio distribution by model") %>%
  kable_styling(full_width = FALSE)
```

Variation ratio distribution by model

| model\_clean | variation\_ratio | n\_cases | percent\_cases |
| --- | --- | --- | --- |
| Claude Opus 4.6 | 0.0 | 38 | 38.0% |
| Claude Opus 4.6 | 0.2 | 22 | 22.0% |
| Claude Opus 4.6 | 0.4 | 13 | 13.0% |
| Claude Opus 4.6 | 0.6 | 19 | 19.0% |
| Claude Opus 4.6 | 0.8 | 8 | 8.0% |
| Claude Sonnet 4.6 | 0.0 | 4 | 4.0% |
| Claude Sonnet 4.6 | 0.2 | 14 | 14.0% |
| Claude Sonnet 4.6 | 0.4 | 18 | 18.0% |
| Claude Sonnet 4.6 | 0.6 | 37 | 37.0% |
| Claude Sonnet 4.6 | 0.8 | 27 | 27.0% |
| GPT-5.4 | 0.0 | 2 | 2.0% |
| GPT-5.4 | 0.2 | 6 | 6.0% |
| GPT-5.4 | 0.4 | 16 | 16.0% |
| GPT-5.4 | 0.6 | 34 | 34.0% |
| GPT-5.4 | 0.8 | 42 | 42.0% |

# 6 Krippendorff’s Alpha

Krippendorff’s alpha is used here as a formal agreement statistic for repeated model outputs. For each model, the five repeated responses are treated like five raters assigning a structured treatment signature to each case. Because the signature is a categorical plan identity, this workflow uses nominal alpha.

```
krippendorff_alpha_nominal <- function(x) {
  x <- as.matrix(x)

  unit_counts <- apply(x, 1, function(row_values) {
    row_values <- row_values[!is.na(row_values) & row_values != ""]

    if (length(row_values) < 2) {
      return(NA_real_)
    }

    tab <- table(row_values)
    n_unit <- sum(tab)

    sum(tab * (n_unit - tab)) / (n_unit * (n_unit - 1))
  })

  observed_disagreement <- mean(unit_counts, na.rm = TRUE)

  all_values <- as.vector(x)
  all_values <- all_values[!is.na(all_values) & all_values != ""]
  pooled_tab <- table(all_values)
  pooled_n <- sum(pooled_tab)

  expected_disagreement <- sum(pooled_tab * (pooled_n - pooled_tab)) /
    (pooled_n * (pooled_n - 1))

  if (is.na(expected_disagreement) || expected_disagreement == 0) {
    return(NA_real_)
  }

  1 - observed_disagreement / expected_disagreement
}

alpha_by_model <- treatment_signatures %>%
  select(model_clean, case_id, repeat_id, treatment_signature) %>%
  mutate(repeat_id = paste0("repeat_", repeat_id)) %>%
  pivot_wider(
    names_from = repeat_id,
    values_from = treatment_signature
  ) %>%
  group_by(model_clean) %>%
  group_modify(~ {
    signature_matrix <- .x %>%
      select(starts_with("repeat_")) %>%
      as.matrix()

    tibble(
      krippendorff_alpha = krippendorff_alpha_nominal(signature_matrix),
      n_cases = nrow(.x),
      n_repeats = ncol(signature_matrix)
    )
  }) %>%
  ungroup() %>%
  mutate(
    interpretation = case_when(
      is.na(krippendorff_alpha) ~ "Not estimable",
      krippendorff_alpha >= 0.80 ~ "Strong agreement",
      krippendorff_alpha >= 0.67 ~ "Tentative agreement",
      krippendorff_alpha > 0 ~ "Low agreement",
      TRUE ~ "No better than chance"
    )
  ) %>%
  arrange(desc(krippendorff_alpha))

model_summary <- model_summary %>%
  left_join(
    alpha_by_model %>%
      select(model_clean, krippendorff_alpha, alpha_interpretation = interpretation),
    by = "model_clean"
  )

write_csv(
  alpha_by_model,
  file.path(output_dir, "intra_model_krippendorff_alpha.csv")
)
write_csv(
  model_summary,
  file.path(output_dir, "intra_model_model_summary.csv")
)

alpha_by_model %>%
  mutate(
    krippendorff_alpha = round(krippendorff_alpha, 3)
  ) %>%
  kable(caption = "Krippendorff's alpha for structured treatment signatures") %>%
  kable_styling(full_width = FALSE)
```

Krippendorff’s alpha for structured treatment signatures

| model\_clean | krippendorff\_alpha | n\_cases | n\_repeats | interpretation |
| --- | --- | --- | --- | --- |
| Claude Opus 4.6 | 0.575 | 100 | 5 | Low agreement |
| Claude Sonnet 4.6 | 0.220 | 100 | 5 | Low agreement |
| GPT-5.4 | 0.142 | 100 | 5 | Low agreement |

Higher Krippendorff’s alpha indicates that the model repeatedly assigned the same structured treatment signature to the same case more often than expected by chance. Values near 1 indicate strong intra-model consistency. Values near 0 indicate agreement no better than chance after accounting for the overall distribution of treatment signatures.

# 7 Secondary Agreement Metrics

```
medication_class_agreement <- parsed_long %>%
  summarise(
    repeat_count = n(),
    mentioned_agreement = as.integer(n_distinct(mentioned) == 1),
    active_agreement = as.integer(n_distinct(active_recommendation) == 1),
    stop_avoid_agreement = as.integer(n_distinct(stop_or_avoid) == 1),
    any_mentioned = max(mentioned, na.rm = TRUE),
    any_active = max(active_recommendation, na.rm = TRUE),
    any_stop_avoid = max(stop_or_avoid, na.rm = TRUE),
    .by = c(model_clean, case_id, drug_class)
  ) %>%
  summarise(
    n_case_drug_pairs = n(),
    mentioned_agreement_rate = mean(mentioned_agreement),
    active_agreement_rate = mean(active_agreement),
    stop_avoid_agreement_rate = mean(stop_avoid_agreement),
    n_pairs_ever_mentioned = sum(any_mentioned),
    n_pairs_ever_active = sum(any_active),
    n_pairs_ever_stop_avoid = sum(any_stop_avoid),
    .by = c(model_clean, drug_class)
  ) %>%
  arrange(model_clean, drug_class)

action_level_agreement <- parsed_long %>%
  pivot_longer(
    cols = all_of(signature_cols),
    names_to = "action_indicator",
    values_to = "value"
  ) %>%
  summarise(
    agreement = as.integer(n_distinct(value) == 1),
    ever_present = max(value, na.rm = TRUE),
    .by = c(model_clean, case_id, drug_class, action_indicator)
  ) %>%
  summarise(
    n_case_drug_action_pairs = n(),
    agreement_rate = mean(agreement),
    n_pairs_ever_present = sum(ever_present),
    .by = c(model_clean, action_indicator)
  ) %>%
  arrange(model_clean, action_indicator)

manual_review_burden <- parsed_long %>%
  summarise(
    manual_review_classes = sum(manual_review_flag, na.rm = TRUE),
    any_manual_review = as.integer(any(manual_review_flag == 1)),
    .by = c(model_clean, response_id, case_id, repeat_id)
  ) %>%
  summarise(
    n_responses = n(),
    responses_with_manual_review = sum(any_manual_review),
    response_manual_review_rate = mean(any_manual_review),
    total_manual_review_classes = sum(manual_review_classes),
    .by = model_clean
  ) %>%
  arrange(desc(response_manual_review_rate))

write_csv(
  medication_class_agreement,
  file.path(output_dir, "intra_model_medication_class_agreement.csv")
)
write_csv(
  action_level_agreement,
  file.path(output_dir, "intra_model_action_level_agreement.csv")
)
write_csv(
  manual_review_burden,
  file.path(output_dir, "intra_model_manual_review_burden.csv")
)

medication_class_agreement %>%
  mutate(
    mentioned_agreement_rate = percent(mentioned_agreement_rate, accuracy = 0.1),
    active_agreement_rate = percent(active_agreement_rate, accuracy = 0.1),
    stop_avoid_agreement_rate = percent(stop_avoid_agreement_rate, accuracy = 0.1)
  ) %>%
  kable(caption = "Medication-class agreement by model") %>%
  kable_styling(full_width = FALSE)
```

Medication-class agreement by model

| model\_clean | drug\_class | n\_case\_drug\_pairs | mentioned\_agreement\_rate | active\_agreement\_rate | stop\_avoid\_agreement\_rate | n\_pairs\_ever\_mentioned | n\_pairs\_ever\_active | n\_pairs\_ever\_stop\_avoid |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Claude Opus 4.6 | basal\_insulin | 100 | 94.0% | 96.0% | 99.0% | 30 | 28 | 1 |
| Claude Opus 4.6 | dpp4i | 100 | 99.0% | 99.0% | 100.0% | 7 | 5 | 2 |
| Claude Opus 4.6 | gip\_glp1 | 100 | 100.0% | 100.0% | 100.0% | 7 | 7 | 0 |
| Claude Opus 4.6 | glp1 | 100 | 88.0% | 97.0% | 95.0% | 82 | 58 | 5 |
| Claude Opus 4.6 | insulin\_any | 100 | 79.0% | 96.0% | 92.0% | 54 | 28 | 10 |
| Claude Opus 4.6 | metformin | 100 | 94.0% | 98.0% | 93.0% | 90 | 67 | 24 |
| Claude Opus 4.6 | prandial\_insulin | 100 | 100.0% | 100.0% | 100.0% | 3 | 2 | 1 |
| Claude Opus 4.6 | sglt2i | 100 | 92.0% | 98.0% | 95.0% | 86 | 58 | 13 |
| Claude Opus 4.6 | sulfonylurea | 100 | 99.0% | 100.0% | 99.0% | 2 | 0 | 2 |
| Claude Opus 4.6 | tzd | 100 | 100.0% | 100.0% | 100.0% | 4 | 4 | 0 |
| Claude Sonnet 4.6 | basal\_insulin | 100 | 95.0% | 99.0% | 100.0% | 35 | 31 | 0 |
| Claude Sonnet 4.6 | dpp4i | 100 | 88.0% | 98.0% | 97.0% | 18 | 6 | 4 |
| Claude Sonnet 4.6 | gip\_glp1 | 100 | 99.0% | 99.0% | 100.0% | 8 | 8 | 0 |
| Claude Sonnet 4.6 | glp1 | 100 | 80.0% | 96.0% | 79.0% | 99 | 61 | 21 |
| Claude Sonnet 4.6 | insulin\_any | 100 | 59.0% | 99.0% | 85.0% | 78 | 31 | 18 |
| Claude Sonnet 4.6 | metformin | 100 | 94.0% | 97.0% | 91.0% | 100 | 61 | 36 |
| Claude Sonnet 4.6 | prandial\_insulin | 100 | 95.0% | 98.0% | 99.0% | 8 | 3 | 3 |
| Claude Sonnet 4.6 | sglt2i | 100 | 80.0% | 99.0% | 73.0% | 96 | 37 | 33 |
| Claude Sonnet 4.6 | sulfonylurea | 100 | 95.0% | 99.0% | 97.0% | 6 | 1 | 4 |
| Claude Sonnet 4.6 | tzd | 100 | 98.0% | 100.0% | 98.0% | 3 | 1 | 2 |
| GPT-5.4 | basal\_insulin | 100 | 90.0% | 90.0% | 100.0% | 21 | 21 | 0 |
| GPT-5.4 | dpp4i | 100 | 92.0% | 92.0% | 98.0% | 13 | 11 | 3 |
| GPT-5.4 | gip\_glp1 | 100 | 100.0% | 100.0% | 100.0% | 7 | 7 | 0 |
| GPT-5.4 | glp1 | 100 | 74.0% | 85.0% | 91.0% | 62 | 47 | 10 |
| GPT-5.4 | insulin\_any | 100 | 49.0% | 90.0% | 43.0% | 97 | 21 | 59 |
| GPT-5.4 | metformin | 100 | 88.0% | 95.0% | 80.0% | 96 | 68 | 22 |
| GPT-5.4 | prandial\_insulin | 100 | 96.0% | 100.0% | 96.0% | 6 | 1 | 5 |
| GPT-5.4 | sglt2i | 100 | 77.0% | 87.0% | 85.0% | 82 | 59 | 17 |
| GPT-5.4 | sulfonylurea | 100 | 76.0% | 98.0% | 92.0% | 26 | 3 | 9 |
| GPT-5.4 | tzd | 100 | 92.0% | 100.0% | 93.0% | 8 | 0 | 7 |

```
manual_review_burden %>%
  mutate(
    response_manual_review_rate = percent(
      response_manual_review_rate,
      accuracy = 0.1
    )
  ) %>%
  kable(caption = "Manual-review burden by model") %>%
  kable_styling(full_width = FALSE)
```

Manual-review burden by model

| model\_clean | n\_responses | responses\_with\_manual\_review | response\_manual\_review\_rate | total\_manual\_review\_classes |
| --- | --- | --- | --- | --- |
| GPT-5.4 | 500 | 147 | 29.4% | 199 |
| Claude Sonnet 4.6 | 500 | 127 | 25.4% | 160 |
| Claude Opus 4.6 | 500 | 115 | 23.0% | 142 |

# 8 Visualizations

```
theme_report <- theme_minimal(base_size = 12) +
  theme(
    plot.title = element_text(face = "bold"),
    panel.grid.minor = element_blank(),
    legend.position = "bottom"
  )

plot_variation_distribution <- variation_ratio_distribution %>%
  mutate(
    variation_ratio_label = factor(
      sprintf("%.2f", variation_ratio),
      levels = sprintf("%.2f", seq(0, 0.8, by = 0.2))
    )
  ) %>%
  ggplot(aes(x = variation_ratio_label, y = n_cases, fill = model_clean)) +
  geom_col(position = "dodge", width = 0.75) +
  geom_text(
    aes(label = n_cases),
    position = position_dodge(width = 0.75),
    vjust = -0.25,
    size = 3.2
  ) +
  scale_fill_brewer(palette = "Set2") +
  labs(
    title = "Variation Ratio Distribution by Model",
    x = "Case-level variation ratio",
    y = "Number of cases",
    fill = "Model"
  ) +
  theme_report

plot_model_summary <- model_summary %>%
  ggplot(
    aes(
      x = reorder(model_clean, mean_variation_ratio),
      y = mean_variation_ratio,
      fill = model_clean
    )
  ) +
  geom_col(width = 0.65, show.legend = FALSE) +
  geom_text(
    aes(label = sprintf("%.2f", mean_variation_ratio)),
    vjust = -0.25,
    size = 3.5
  ) +
  scale_y_continuous(labels = number_format(accuracy = 0.01)) +
  scale_fill_brewer(palette = "Set2") +
  labs(
    title = "Mean Intra-Model Variation Ratio",
    x = NULL,
    y = "Mean variation ratio"
  ) +
  theme_report

plot_exact_match <- model_summary %>%
  ggplot(
    aes(
      x = reorder(model_clean, exact_match_rate),
      y = exact_match_rate,
      fill = model_clean
    )
  ) +
  geom_col(width = 0.65, show.legend = FALSE) +
  geom_text(
    aes(label = percent(exact_match_rate, accuracy = 1)),
    vjust = -0.25,
    size = 3.5
  ) +
  scale_y_continuous(labels = percent_format()) +
  scale_fill_brewer(palette = "Set2") +
  labs(
    title = "Exact-Match Rate by Model",
    x = NULL,
    y = "Cases with all 5 repeated plans matching"
  ) +
  theme_report

plot_unstable_cases <- top_unstable_cases %>%
  mutate(case_id = fct_reorder(case_id, variation_ratio)) %>%
  ggplot(aes(x = variation_ratio, y = case_id, fill = model_clean)) +
  geom_col(show.legend = FALSE) +
  facet_wrap(~ model_clean, scales = "free_y") +
  scale_x_continuous(labels = number_format(accuracy = 0.1)) +
  scale_fill_brewer(palette = "Set2") +
  labs(
    title = "Top Unstable Cases by Model",
    x = "Variation ratio",
    y = "Case ID"
  ) +
  theme_report

plot_med_agreement <- medication_class_agreement %>%
  ggplot(aes(x = drug_class, y = model_clean, fill = active_agreement_rate)) +
  geom_tile(color = "white") +
  geom_text(aes(label = percent(active_agreement_rate, accuracy = 1)), size = 3) +
  scale_fill_gradient(low = "#f1a340", high = "#1b7837", labels = percent) +
  labs(
    title = "Active-Recommendation Agreement by Medication Class",
    x = "Medication class",
    y = "Model",
    fill = "Agreement"
  ) +
  theme_report +
  theme(axis.text.x = element_text(angle = 35, hjust = 1))

plot_action_agreement <- action_level_agreement %>%
  ggplot(
    aes(
      x = action_indicator,
      y = agreement_rate,
      fill = model_clean
    )
  ) +
  geom_col(position = "dodge", width = 0.75) +
  scale_y_continuous(labels = percent_format()) +
  scale_fill_brewer(palette = "Set2") +
  labs(
    title = "Action-Level Agreement by Model",
    x = "Parsed action indicator",
    y = "Agreement rate",
    fill = "Model"
  ) +
  theme_report +
  theme(axis.text.x = element_text(angle = 35, hjust = 1))

plot_variation_distribution
```

![](data:image/png;base64...)

```
plot_model_summary
```

![](data:image/png;base64...)

```
plot_exact_match
```

![](data:image/png;base64...)

```
plot_unstable_cases
```

![](data:image/png;base64...)

```
plot_med_agreement
```

![](data:image/png;base64...)

```
plot_action_agreement
```

![](data:image/png;base64...)

# 9 Manual Review Sample

```
manual_review_sample <- top_unstable_cases %>%
  select(model_clean, case_id, variation_ratio, n_unique_signatures) %>%
  left_join(
    treatment_signatures %>%
      select(
        model_clean,
        case_id,
        repeat_id,
        response_id,
        treatment_signature,
        n_active_classes,
        n_stop_avoid_classes,
        n_manual_review_classes
      ),
    by = c("model_clean", "case_id")
  ) %>%
  left_join(
    standardized %>%
      select(response_id, treatment_plan),
    by = "response_id"
  ) %>%
  arrange(model_clean, desc(variation_ratio), case_id, repeat_id)

write_csv(
  manual_review_sample,
  file.path(output_dir, "intra_model_manual_review_sample.csv")
)

manual_review_sample %>%
  select(
    model_clean,
    case_id,
    repeat_id,
    variation_ratio,
    n_active_classes,
    n_stop_avoid_classes,
    n_manual_review_classes
  ) %>%
  head(30) %>%
  kable(caption = "Manual review sample from unstable cases") %>%
  kable_styling(full_width = FALSE)
```

Manual review sample from unstable cases

| model\_clean | case\_id | repeat\_id | variation\_ratio | n\_active\_classes | n\_stop\_avoid\_classes | n\_manual\_review\_classes |
| --- | --- | --- | --- | --- | --- | --- |
| Claude Opus 4.6 | DM024 | 1 | 0.8 | 2 | 0 | 1 |
| Claude Opus 4.6 | DM024 | 2 | 0.8 | 2 | 0 | 0 |
| Claude Opus 4.6 | DM024 | 3 | 0.8 | 2 | 0 | 0 |
| Claude Opus 4.6 | DM024 | 4 | 0.8 | 2 | 0 | 0 |
| Claude Opus 4.6 | DM024 | 5 | 0.8 | 2 | 2 | 0 |
| Claude Opus 4.6 | DM028 | 1 | 0.8 | 2 | 0 | 0 |
| Claude Opus 4.6 | DM028 | 2 | 0.8 | 2 | 0 | 0 |
| Claude Opus 4.6 | DM028 | 3 | 0.8 | 2 | 0 | 1 |
| Claude Opus 4.6 | DM028 | 4 | 0.8 | 2 | 0 | 0 |
| Claude Opus 4.6 | DM028 | 5 | 0.8 | 2 | 1 | 0 |
| Claude Opus 4.6 | DM033 | 1 | 0.8 | 3 | 0 | 1 |
| Claude Opus 4.6 | DM033 | 2 | 0.8 | 3 | 1 | 1 |
| Claude Opus 4.6 | DM033 | 3 | 0.8 | 3 | 0 | 0 |
| Claude Opus 4.6 | DM033 | 4 | 0.8 | 3 | 0 | 1 |
| Claude Opus 4.6 | DM033 | 5 | 0.8 | 3 | 0 | 2 |
| Claude Opus 4.6 | DM071 | 1 | 0.8 | 2 | 0 | 0 |
| Claude Opus 4.6 | DM071 | 2 | 0.8 | 2 | 0 | 0 |
| Claude Opus 4.6 | DM071 | 3 | 0.8 | 2 | 0 | 0 |
| Claude Opus 4.6 | DM071 | 4 | 0.8 | 2 | 0 | 0 |
| Claude Opus 4.6 | DM071 | 5 | 0.8 | 2 | 0 | 0 |
| Claude Opus 4.6 | DM098 | 1 | 0.8 | 2 | 0 | 0 |
| Claude Opus 4.6 | DM098 | 2 | 0.8 | 2 | 0 | 1 |
| Claude Opus 4.6 | DM098 | 3 | 0.8 | 2 | 0 | 0 |
| Claude Opus 4.6 | DM098 | 4 | 0.8 | 2 | 0 | 0 |
| Claude Opus 4.6 | DM098 | 5 | 0.8 | 2 | 0 | 1 |
| Claude Opus 4.6 | DM101 | 1 | 0.8 | 2 | 2 | 0 |
| Claude Opus 4.6 | DM101 | 2 | 0.8 | 2 | 1 | 0 |
| Claude Opus 4.6 | DM101 | 3 | 0.8 | 2 | 1 | 0 |
| Claude Opus 4.6 | DM101 | 4 | 0.8 | 2 | 1 | 1 |
| Claude Opus 4.6 | DM101 | 5 | 0.8 | 2 | 0 | 0 |

# 10 Output Files

```
output_files <- tibble(
  file = list.files(output_dir, full.names = FALSE)
) %>%
  arrange(file)

output_files %>%
  kable(caption = "Files written by this workflow") %>%
  kable_styling(full_width = FALSE)
```

Files written by this workflow

| file |
| --- |
| intra\_model\_action\_level\_agreement.csv |
| intra\_model\_case\_stability.csv |
| intra\_model\_krippendorff\_alpha.csv |
| intra\_model\_manual\_review\_burden.csv |
| intra\_model\_manual\_review\_sample.csv |
| intra\_model\_medication\_class\_agreement.csv |
| intra\_model\_model\_summary.csv |
| intra\_model\_parsed\_long.csv |
| intra\_model\_parsed\_wide.csv |
| intra\_model\_signature\_counts.csv |
| intra\_model\_standardized\_responses.csv |
| intra\_model\_top\_unstable\_cases.csv |
| intra\_model\_treatment\_signatures.csv |
| intra\_model\_variation\_ratio\_distribution.csv |