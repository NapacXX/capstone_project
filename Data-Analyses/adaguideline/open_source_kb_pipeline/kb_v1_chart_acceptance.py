"""Offline chart-locator smoke tests, not a clinical retrieval benchmark."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

from kb_v1_runtime import verify_bundle, read_jsonl, retrieve_cases


PROBES = (
    ("ada2026-ch9-figure-9-1", "Figure 9.1 insulin replacement plans in type 1 diabetes multiple daily injections pump automated insulin delivery flexibility hypoglycemia"),
    ("ada2026-ch9-figure-9-2", "Figure 9.2 initiation and adjustment of insulin multiple daily injections type 1 diabetes basal prandial titration"),
    ("ada2026-ch9-figure-9-3", "Figure 9.3 beta-cell replacement pancreas or islet transplantation after kidney simultaneous kidney severe metabolic complications"),
    ("ada2026-ch9-figure-9-4", "Figure 9.4 type 2 diabetes glucose-lowering medications cardiovascular kidney risk weight glycemic goals"),
    ("ada2026-ch9-figure-9-5", "Figure 9.5 intensifying injectable therapies basal insulin prandial insulin self-mixed split premixed NPH titration"),
    ("ada2026-ch9-table-9-2", "Table 9.2 features medications glucose-lowering efficacy hypoglycemia weight MACE heart failure kidney clinical adverse effects"),
    ("ada2026-ch9-table-9-3", "Table 9.3 medication cost average wholesale price AWP NADAC maximum approved daily dose July 2025"),
)
SUPPLEMENT_PROBES = (
    ("ada2026-ch9-table-9-1", "Table 9.1 examples subcutaneous insulin treatment plans type 1 diabetes insulin regimens timing distribution dose adjustment advantages disadvantages fixed four injections NPH regular insulin TDD"),
    ("ada2026-ch9-table-9-4", "Table 9.4 insulin product costs per 1,000 units AWP NADAC July 15 2025 dosage forms prefilled pen glargine degludec U-100 U-200 U-300"),
)
CONTRACT_V1 = "ada-whole-chart-release-v1"
CONTRACT_V2 = "ada-whole-chart-release-v2-with-table-supplement"
COMPLETE_PARENT_FIELDS = (
    "record_id", "figure_id", "revision", "content_sha256", "source_id",
    "page_number", "page_numbers", "printed_pages", "source_text", "source_blocks",
    "logic_paths", "symbols", "internal_dependencies", "open_questions", "use_policy",
    "review_status", "release_status", "human_approved", "approved_by", "approved_at",
)


def select_probes(contract, manifest, records):
    """Called only after verify_bundle validates the independently pinned audit.

    Never accept an arbitrary nine-record manifest as proof of supplemental
    approval. The verified release contract and exact parent set must agree.
    """
    schema = contract.get("schema_version")
    if schema == CONTRACT_V1:
        probes = PROBES
    elif schema == CONTRACT_V2:
        probes = (*PROBES, *SUPPLEMENT_PROBES)
    else:
        raise ValueError("Unsupported verified chart release contract")
    expected = {identifier for identifier, _ in probes}
    contracted = contract.get("records", [])
    charts = [row for row in records if row.get("content_type") == "visual_whole_chart"]
    if (contract.get("n_approved_charts") != len(probes)
            or manifest.get("visual_record_count") != len(probes)
            or len(contracted) != len(probes)
            or {row.get("figure_id") for row in contracted} != expected
            or len(charts) != len(probes)
            or {row.get("figure_id") for row in charts} != expected):
        raise ValueError("Chart probes require the exact approved seven- or nine-chart contract set")
    return probes


def inspect_complete_hit(parent, hit, source):
    """Check actual public query output against the approved complete parent."""
    if hit is None:
        return {"complete_parent_returned": False, "checks": {"found_in_top8": False}}
    expected_images = [image for image in source.get("images", [])
                       if image.get("figure_id") == parent["figure_id"]]
    footnotes = [block for block in parent["source_blocks"] if block["kind"] == "footnote"]
    returned_footnotes = [block for block in hit.get("source_blocks", []) if block.get("kind") == "footnote"]
    checks = {"parent_fields_unchanged": all(hit.get(field) == parent.get(field) for field in COMPLETE_PARENT_FIELDS),
              "all_source_pages_returned": hit.get("page_numbers") == parent["page_numbers"],
              "all_source_images_returned": hit.get("source_images") == expected_images and
                  {image.get("page_number") for image in expected_images} == set(parent["page_numbers"]),
              "all_footnotes_returned": returned_footnotes == footnotes,
              "all_source_limitations_returned": hit.get("open_questions") == parent["open_questions"],
              "source_pdf_reference_unchanged": hit.get("source") == source,
              "non_executable_use_policy_returned": hit.get("use_policy") == parent["use_policy"] and
                  hit.get("use_policy", {}).get("automatic_dose_calculation_allowed") is False and
                  hit.get("use_policy", {}).get("clinical_execution_allowed") is False}
    # These are fixed approved-table guardrails, not inferred clinical facts.
    questions = "\n".join(row.get("text", "") for row in hit.get("open_questions", []))
    if parent["figure_id"] == "ada2026-ch9-table-9-1":
        checks["two_page_table_complete"] = hit.get("page_numbers") == [3, 4]
        checks["nominal_90_percent_limitation_retained"] = "90%" in questions and "100%" in questions
        checks["ambiguous_or_wording_limitation_retained"] = "outside of activity time course, or URAA or RAA injections" in questions
    elif parent["figure_id"] == "ada2026-ch9-table-9-4":
        checks["table_page_complete"] = hit.get("page_numbers") == [22]
        checks["historical_price_date_limitation_retained"] = "15 July 2025" in questions
        checks["printed_unit_ambiguity_retained"] = "100/3.6 mg prefilled pen" in questions and "100/33 mg prefilled pen" in questions
        checks["per_1000_vs_monthly_limitation_retained"] = "1,000 units" in questions and "monthly" in questions
    return {"complete_parent_returned": all(checks.values()), "checks": checks,
            "pdf_pages_returned": hit.get("page_numbers", []),
            "printed_pages_returned": hit.get("printed_pages", []),
            "source_image_pages_returned": [image["page_number"] for image in hit.get("source_images", [])],
            "footnote_ids_returned": [block["id"] for block in returned_footnotes]}


def run(bundle, output):
    root, destination = Path(bundle).resolve(), Path(output).resolve()
    if destination.exists() or Path(output).is_symlink():
        raise FileExistsError(destination)
    if root == destination or root in destination.parents or destination in root.parents:
        raise ValueError("Keep probe output outside the immutable bundle")
    manifest = verify_bundle(root)
    records = read_jsonl(root / "records.jsonl")
    contract = json.loads((root / "audit/whole_charts/release_contract.json").read_text(encoding="utf-8"))
    sources = json.loads((root / "sources.json").read_text(encoding="utf-8"))
    probes = select_probes(contract, manifest, records)
    by_figure = {row["figure_id"]: row for row in records if row.get("content_type") == "visual_whole_chart"}
    destination.mkdir(parents=True, exist_ok=False)
    case_path = destination / "chart_probe_cases.csv"
    with case_path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["case_id", "vignette_text"])
        writer.writerows(probes)
    # Exercise the public JSONL/Markdown query path, not just internal ranking.
    retrieve_cases(root, case_path, destination / "evidence")
    evidence = read_jsonl(destination / "evidence/evidence.jsonl")
    if len(evidence) != len(probes) or [row.get("case_id") for row in evidence] != [row[0] for row in probes]:
        raise ValueError("Public chart probe case identifiers differ from the requested set")
    results = []
    for (expected, query), response in zip(probes, evidence):
        if response.get("kb_version") != manifest["kb_version"] or response.get("bundle_status") != "released":
            raise ValueError("Public chart probe evidence has a mismatched release identity")
        hits = response["evidence"]
        hit = next((r for r in hits if r.get("figure_id") == expected), None)
        parent = by_figure[expected]
        completeness = inspect_complete_hit(parent, hit, sources[parent["source_id"]])
        results.append({
            "expected_figure_id": expected, "query": query,
            "found_in_top8": hit is not None, "rank": hit["rank"] if hit else None,
            "source_blocks_returned": len(hit["source_blocks"]) if hit else 0,
            "logic_paths_returned": len(hit["logic_paths"]) if hit else 0,
            "source_questions_retained": hit["open_questions"] if hit else [],
            "use_policy_retained": hit["use_policy"] if hit else {},
            "top8_ids": [r["record_id"] for r in hits],
            **completeness,
        })
    report = {
        "kb_version": manifest["kb_version"],
        "bundle_manifest_sha256": hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest(),
        "release_contract_schema": contract["schema_version"],
        "status": "SUCCESS" if all(r["complete_parent_returned"] for r in results) else "PARTIAL",
        "queries": len(results), "charts_found": sum(r["found_in_top8"] for r in results),
        "complete_parents_returned": sum(r["complete_parent_returned"] for r in results),
        "method": "Source-aware synthetic title/topic locator probes. Not held-out cases, not Recall@8 or clinical correctness.",
        "public_evidence": "evidence/evidence.jsonl",
        "readable_evidence": "evidence/evidence.md",
        "results": results,
    }
    (destination / "chart_probe_results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.bundle, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["status"] == "SUCCESS" else 2)
