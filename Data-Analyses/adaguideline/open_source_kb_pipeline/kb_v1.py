#!/usr/bin/env python3
"""One explicit, offline entry point for ADA KB review, build and retrieval."""

from __future__ import annotations

import argparse
import csv
import importlib.metadata
import json
import platform
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True  # Keep shipped, hash-bound tools immutable.


def doctor(maintainer=False):
    packages = ["numpy", "sentence-transformers", "transformers", "torch"]
    if maintainer:
        packages += ["pandas", "jsonschema", "pypdf", "PyMuPDF", "faiss-cpu"]
    versions, missing = {}, []
    for name in packages:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            missing.append(name)
    return {
        "python": platform.python_version(), "platform": platform.platform(),
        "target_python": "3.12", "python_target_match": sys.version_info[:2] == (3, 12),
        "packages": versions, "missing_packages": missing, "device": "cpu",
        "network_required_for_query": False, "generation_api": False,
        "status": "SUCCESS" if not missing and sys.version_info[:2] == (3, 12) else "PARTIAL",
        "notice": "Dependency inventory only; verify a bundle and run a query to test model loading.",
    }


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    subs = p.add_subparsers(dest="command", required=True)
    d = subs.add_parser("doctor", help="Check the local environment without network access")
    d.add_argument("--maintainer", action="store_true")
    r = subs.add_parser("review", help="Freeze and validate a NEW review workspace, never approve")
    r.add_argument("--raw-dir", type=Path, required=True)
    r.add_argument("--approvals", type=Path, required=True)
    r.add_argument("--registry", type=Path, required=True)
    r.add_argument("--output", type=Path, required=True)
    r.add_argument("--ai-notes", type=Path)
    r.add_argument("--origin-manifest", type=Path, help="Frozen initial-candidate lineage for versions with additional candidates")
    b = subs.add_parser("build", help="Build a release only after the full human-review gate passes")
    b.add_argument("--kb-dir", type=Path, required=True)
    b.add_argument("--pdf", type=Path, required=True)
    b.add_argument("--model", type=Path, required=True)
    b.add_argument("--review-dir", type=Path)
    b.add_argument("--ledger", type=Path, help="Explicit NEW human-imported ledger; never overwrite original")
    b.add_argument("--chart-workspace", type=Path, help="Explicit approved whole-chart representation; cannot be mixed with legacy review/ledger or candidate mode")
    b.add_argument("--supplement-workspace", type=Path, help="Explicit approved Table 9.1/9.4 supplement; requires --chart-workspace and the pinned nine-chart release contract")
    b.add_argument("--output", type=Path, required=True)
    b.add_argument("--candidate", action="store_true", help="Explicit text/table development snapshot; NOT formal v1")
    v = subs.add_parser("verify", help="Verify bundle hashes, record/vector alignment and safety")
    v.add_argument("--bundle", type=Path, required=True)
    v.add_argument("--allow-candidate", action="store_true")
    q = subs.add_parser("query", help="Return cited evidence, NOT treatment recommendations")
    q.add_argument("--bundle", type=Path, required=True)
    inputs = q.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--cases", type=Path, help="UTF-8 CSV: case_id,vignette_text by default")
    inputs.add_argument("--vignette-file", type=Path, help="Single UTF-8 case text; use --case-id")
    q.add_argument("--case-id", default="case-001")
    q.add_argument("--input-mode", choices=["text", "structured"], default="text")
    q.add_argument("--output", type=Path, required=True)
    q.add_argument("--top-k", type=int, default=8)
    q.add_argument("--allow-candidate", action="store_true")
    g = subs.add_parser("release-check", help="Check human dispositions and evidence dependencies")
    g.add_argument("--review-dir", type=Path, required=True)
    g.add_argument("--ledger", type=Path)
    h = subs.add_parser("import-human-review", help="Validate explicitly human-edited CSV into a NEW ledger, never synthesize approvals")
    h.add_argument("--review-dir", type=Path, required=True)
    h.add_argument("--csv", type=Path, required=True)
    h.add_argument("--output", type=Path, required=True)
    fr = subs.add_parser("figure-review", help="Prepare a NEW whole-chart review packet, never approve or index")
    fr.add_argument("--document", type=Path, required=True)
    fr.add_argument("--scope", type=Path, required=True)
    fr.add_argument("--output", type=Path, required=True)
    fv = subs.add_parser("figure-verify", help="Verify an immutable whole-chart review packet, not clinical correctness")
    fv.add_argument("--review-dir", type=Path, required=True)
    fi = subs.add_parser("figure-import-decision", help="Validate an explicit human chart decision into a NEW file")
    fi.add_argument("--review-dir", type=Path, required=True)
    fi.add_argument("--decision", type=Path, required=True)
    fi.add_argument("--output", type=Path, required=True)
    fs = subs.add_parser("figure-scope-check", help="Check whole-chart coverage and approval; does NOT build or release a KB")
    fs.add_argument("--scope", type=Path, required=True)
    fs.add_argument("--review-dirs", type=Path, nargs="*", default=[])
    fs.add_argument("--decisions", type=Path, nargs="*", default=[])
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "doctor":
            result = doctor(args.maintainer)
        elif args.command == "figure-review":
            from kb_v1_figure_review import prepare_figure_review
            result = prepare_figure_review(args.document, args.scope, args.output)
        elif args.command == "figure-verify":
            from kb_v1_figure_review import verify_figure_review
            result = verify_figure_review(args.review_dir)
        elif args.command == "figure-import-decision":
            from kb_v1_figure_review import import_figure_decision
            result = import_figure_decision(args.review_dir, args.decision, args.output)
        elif args.command == "figure-scope-check":
            from kb_v1_figure_review import check_figure_scope
            result = check_figure_scope(args.scope, args.review_dirs, args.decisions)
        elif args.command == "review":
            from kb_v1_review import prepare_review
            result = prepare_review(args.raw_dir, args.approvals, args.registry, args.output, args.ai_notes, origin_manifest_path=args.origin_manifest)
        elif args.command == "release-check":
            from kb_v1_review import assert_release_ready, load_jsonl, verify_review_inputs
            verify_review_inputs(args.review_dir)
            approved = assert_release_ready(
                load_jsonl(args.review_dir / "canonical_records.jsonl"),
                load_jsonl(args.ledger or args.review_dir / "review_ledger.jsonl"),
                json.loads((args.review_dir / "group_reviews.json").read_text(encoding="utf-8")),
            )
            result = {"status": "SUCCESS", "approved_visual_records": len(approved)}
        elif args.command == "import-human-review":
            from kb_v1_review import import_human_ledger_csv
            result = import_human_ledger_csv(args.review_dir, args.csv, args.output)
        elif args.command == "build":
            from kb_v1_build import build_bundle
            extra = {"chart_workspace": args.chart_workspace} if args.chart_workspace is not None else {}
            if args.supplement_workspace is not None:
                extra["supplement_workspace"] = args.supplement_workspace
            result = build_bundle(args.kb_dir, args.pdf, args.model, args.output, args.review_dir, args.candidate, ledger_path=args.ledger, **extra)
        elif args.command == "verify":
            from kb_v1_runtime import verify_bundle
            result = verify_bundle(args.bundle, allow_candidate=args.allow_candidate)
        else:
            from kb_v1_runtime import retrieve_cases
            if args.vignette_file:
                if args.input_mode != "text":
                    raise ValueError("--vignette-file requires --input-mode text")
                with tempfile.TemporaryDirectory(prefix="ada-single-case-") as folder:
                    case_path = Path(folder) / "case.csv"
                    with case_path.open("w", encoding="utf-8", newline="") as handle:
                        writer = csv.DictWriter(handle, fieldnames=["case_id", "vignette_text"])
                        writer.writeheader()
                        writer.writerow({"case_id": args.case_id, "vignette_text": args.vignette_file.read_text(encoding="utf-8-sig")})
                    result = retrieve_cases(args.bundle, case_path, args.output, "text", args.top_k, args.allow_candidate)
            else:
                result = retrieve_cases(args.bundle, args.cases, args.output, args.input_mode, args.top_k, args.allow_candidate)
        display = {k:v for k,v in result.items() if k not in {"files", "expected_record_ids", "origin_record_ids"}}
        print(json.dumps(display, ensure_ascii=False, indent=2, default=str))
        return 2 if result.get("status") in {"PARTIAL", "BLOCKED"} else 0
    except (ValueError, FileNotFoundError, FileExistsError, ImportError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
