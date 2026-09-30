#!/usr/bin/env python3
"""Reproducible maintainer checks on a real, explicitly candidate or released KB.

Creates new local artifacts only. This is engineering acceptance, not a clinical
benchmark; the quality review must be written separately after reading results.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("OMP_NUM_THREADS", "1")
PIPE = Path(__file__).resolve().parent


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def export_cases(source, output):
    """Explicitly project case fields, not generation output, from the legacy CSV.

    The legacy file has duplicate unnamed trailing columns. We reject any
    duplicate selected field; unused columns are explicitly logged and omitted.
    No source file is changed, and conflicting case text is never discarded.
    """
    from kb_v1_runtime import prepare_cases, STRUCTURED_FIELDS, sha256_file
    wanted = ["case_id", "vignette_text", *[f for f, _ in STRUCTURED_FIELDS]]
    with Path(source).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        if any(header.count(field) != 1 for field in wanted):
            raise ValueError("Missing or repeated clinical case field; explicit mapping required")
        indices = [header.index(field) for field in wanted]
        rows = []
        for line in reader:
            if len(line) != len(header):
                raise ValueError("Malformed legacy CSV row")
            rows.append(dict(zip(wanted, (line[i] for i in indices))))
    unique = prepare_cases(rows, "text")
    prepare_cases(rows, "structured")  # Also reject conflicting structured data.
    selected_ids = ["DM020", "DM022", "DM025", "DM026(1)", "DM030", "DM040", "DM050", "DM060", "DM080", "DM100"]
    by_id = {r["case_id"]: r for r in unique}
    if not set(selected_ids) <= set(by_id):
        raise ValueError("The fixed ten-case acceptance sample is missing from this input")
    with (output / "sample_cases.csv").open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["case_id", "vignette_text"])
        writer.writeheader()
        for identifier in selected_ids:
            writer.writerow({"case_id": identifier, "vignette_text": by_id[identifier]["case_input"]["vignette_text"]})
    projected = {r["case_id"]: r for r in rows}
    with (output / "sample_cases_structured.csv").open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=wanted)
        writer.writeheader()
        writer.writerows(projected[i] for i in selected_ids)
    return {"source_sha256": sha256_file(Path(source)), "source_rows": len(rows), "distinct_cases": len(unique), "sample_case_ids": selected_ids,
            "method": "Explicit projection to unique clinical-input fields; no treatment_plan/prompt/model/rationale columns read as queries.",
            "legacy_empty_header_count": header.count(""), "selected_fields": wanted,
            "omitted_fields": [h for h in header if h not in wanted]}


def run_checks(bundle, source_cases, output):
    import numpy as np
    from kb_v1_runtime import verify_bundle, retrieve_cases, load_embedding_model, load_cases, token_windows, read_jsonl, sha256_file
    bundle, output = Path(bundle).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    manifest = verify_bundle(bundle, allow_candidate=True)
    stats = {"platform": platform.platform(), "python": platform.python_version(), "kb_version": manifest["kb_version"],
             "bundle_status": manifest["status"], "windows_acceptance": "NOT RUN: no Windows host available",
             "bundle_manifest_sha256": sha256_file(bundle / "manifest.json"),
             "clinical_accuracy_evaluation": "NOT PERFORMED"}
    write_json(output / "case_projection_audit.json", export_cases(source_cases, output))
    stats["text_retrieval"] = retrieve_cases(bundle, output / "sample_cases.csv", output / "ten_case_evidence", allow_candidate=True)
    stats["structured_retrieval"] = retrieve_cases(bundle, output / "sample_cases_structured.csv", output / "structured_evidence", input_mode="structured", allow_candidate=True)
    records, units = read_jsonl(bundle / "records.jsonl"), read_jsonl(bundle / "units.jsonl")
    vectors = np.load(bundle / "embeddings.npy", allow_pickle=False)
    model = load_embedding_model(bundle, manifest)
    cases = load_cases(output / "sample_cases.csv")
    texts = [w["text"] for c in cases for w in token_windows(c["retrieval_query"], model.tokenizer, model.max_seq_length)]
    queries = np.asarray(model.encode(texts, normalize_embeddings=True, show_progress_bar=False), dtype="float32")
    # Keep FAISS and Torch in separate processes. On this macOS environment
    # their OpenMP libraries abort with Error #179 even when import is delayed.
    # The child reads the exact same float32 arrays; no model is loaded there.
    np.save(output / "comparison_query_vectors.npy", queries, allow_pickle=False)
    worker = "\n".join([
        "import sys, numpy as np, faiss",
        "faiss.omp_set_num_threads(1)",
        "v=np.load(sys.argv[1],allow_pickle=False); q=np.load(sys.argv[2],allow_pickle=False)",
        "index=faiss.IndexFlatIP(v.shape[1]); index.add(v)",
        "d,i=index.search(q,len(v))",
        "np.savez(sys.argv[3], distances=d, positions=i)",
    ])
    child = subprocess.run([sys.executable, "-c", worker, str(bundle / "embeddings.npy"), str(output / "comparison_query_vectors.npy"), str(output / "faiss_comparison.npz")], text=True, capture_output=True, env=os.environ.copy())
    (output / "faiss_worker.stderr.txt").write_text(child.stderr, encoding="utf-8")
    if child.returncode:
        raise AssertionError("Isolated FAISS comparison failed; see faiss_worker.stderr.txt")
    with np.load(output / "faiss_comparison.npz", allow_pickle=False) as comparison:
        distances, positions = comparison["distances"], comparison["positions"]
    faiss_scores = np.empty((len(queries), len(vectors)), dtype="float32")
    np.put_along_axis(faiss_scores, positions, distances, axis=1)
    numpy_scores = queries @ vectors.T
    error = float(np.max(np.abs(numpy_scores - faiss_scores)))
    tolerance = 2e-6
    if error > tolerance:
        raise AssertionError(f"FAISS/NumPy score mismatch: {error}")
    rank_mismatches, substantive = 0, 0
    for a, b in zip(numpy_scores, faiss_scores):
        order_a = sorted(range(len(units)), key=lambda i: (-float(a[i]), units[i]["record_id"], units[i]["unit_id"]))
        order_b = sorted(range(len(units)), key=lambda i: (-float(b[i]), units[i]["record_id"], units[i]["unit_id"]))
        for i, j in zip(order_a, order_b):
            if i != j:
                rank_mismatches += 1
                substantive += int(abs(float(a[i]) - float(a[j])) > tolerance)
    if substantive:
        raise AssertionError("FAISS/NumPy ranking differs outside numerical-tie tolerance")
    stats["exact_inner_product_comparison"] = {"query_windows": len(queries), "vectors": len(vectors), "max_absolute_score_error": error,
        "tolerance": tolerance, "different_rank_positions": rank_mismatches, "non_tie_rank_differences": substantive,
        "tie_policy": "Runtime exact-score ties: stable record_id then unit_id. Comparator permits float32 differences <=2e-6; no clinical meaning is attached to tiny score differences."}
    stats["content"] = {"source_records": len(records), "indexable_parent_records": len({u['record_id'] for u in units}),
        "units": len(units), "vector_dimension": vectors.shape[1], "max_unit_tokens": max(len(model.tokenizer(u['text'], add_special_tokens=True, truncation=False)['input_ids']) for u in units),
        "visual_records": sum(r['content_type'].startswith('visual_') for r in records),
        "page_previews_in_index": sum(r['content_type'] == 'figure_or_page' for r in records),
        "symlink_files": sum(p.is_symlink() for p in bundle.rglob('*'))}
    relocated = output / "可移植测试 with spaces" / "bundle"
    shutil.copytree(bundle, relocated, symlinks=False)
    verify_bundle(relocated, allow_candidate=True)
    long_text = cases[0]["retrieval_query"] * 9 + " Final condition retained: 中文测试 and final evidence context."
    with (output / "long_case.csv").open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["case_id", "vignette_text"])
        writer.writeheader()
        writer.writerow({"case_id": "long-portability-case", "vignette_text": long_text})
    command = [sys.executable, str(PIPE / "kb_v1.py"), "query", "--bundle", str(relocated), "--cases", str(output / "long_case.csv"), "--output", str(output / "relocated_evidence"), "--allow-candidate"]
    completed = subprocess.run(command, cwd=output, text=True, capture_output=True, env=os.environ.copy())
    (output / "portable_query.stdout.txt").write_text(completed.stdout, encoding="utf-8")
    (output / "portable_query.stderr.txt").write_text(completed.stderr, encoding="utf-8")
    if completed.returncode:
        raise AssertionError("Relocated query failed; see captured logs")
    result = read_jsonl(output / "relocated_evidence/evidence.jsonl")[0]
    assert result["case_input"]["vignette_text"] == long_text
    assert len(result["query_windows"]) > 1
    windows = result["query_windows"]
    assert windows[0]["start_char"] == 0
    assert windows[-1]["end_char"] == len(long_text)
    end = 0
    for window in windows:
        assert window["start_char"] <= end
        assert window["text"] == long_text[window["start_char"]:window["end_char"]]
        assert len(model.tokenizer(window["text"], add_special_tokens=True, truncation=False)["input_ids"]) <= model.max_seq_length
        end = max(end, window["end_char"])
    assert len({e['record_id'] for e in result['evidence']}) == len(result['evidence'])
    paths = []
    for evidence in result["evidence"]:
        source = relocated / evidence["source"]["relative_path"]
        assert source.is_file() and sha256_file(source) == evidence["source_id"]
        paths.append(source.relative_to(relocated).as_posix())
    stats["portability"] = {"status": "SUCCESS on " + platform.system(), "different_working_directory": True, "unicode_and_space_path": True,
        "source_files_resolved": len(paths), "long_query_characters": len(long_text), "long_query_windows": len(result["query_windows"]), "full_input_preserved": True,
        "query_window_character_coverage": "complete, including final character", "all_window_token_lengths_valid": True}
    test = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=PIPE, text=True, capture_output=True)
    (output / "unit_tests.stdout.txt").write_text(test.stdout, encoding="utf-8")
    (output / "unit_tests.stderr.txt").write_text(test.stderr, encoding="utf-8")
    stats["unit_test_exit_code"] = test.returncode
    stats["status"] = "SUCCESS" if test.returncode == 0 else "PARTIAL"
    actual_platform = "macOS" if platform.system() == "Darwin" else platform.system()
    stats["platform_results"] = {name: {"status": "NOT_RUN", "reason": "No execution on this platform in this run"}
                                 for name in ("macOS", "Windows")}
    stats["platform_results"][actual_platform] = {"status": stats["status"], "platform": platform.platform(), "python": platform.python_version()}
    if actual_platform == "Windows":
        stats["windows_acceptance"] = stats["status"] + ": executed on Windows"
    write_json(output / "acceptance_results.json", stats)
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--source-cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run_checks(args.bundle, args.source_cases, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["status"] == "SUCCESS" else 1)
