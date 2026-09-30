#!/usr/bin/env python3
"""Package a released whole-chart KB for controlled, offline team use.

This is separate from the historical legacy-candidate handoff. Packaging never
approves evidence, modifies a bundle, or proves clinical/platform accuracy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

# Consumer commands must not mutate the checksum-bound tools directory.
sys.dont_write_bytecode = True

PIPE = Path(__file__).resolve().parent
SCHEMA = "ada-chart-delivery-v1"
TOOL_FILES = (
    "kb_v1.py", "kb_v1_runtime.py", "kb_v1_review.py",
    "kb_v1_figure_review.py", "kb_v1_chart_release.py",
    "kb_v1_chart_delivery.py", "kb_v1_chart_acceptance.py", "kb_v1_chart_text.py",
    "requirements-v1-consumer.lock.txt",
    "requirements-v1-maintainer.lock.txt",
)
CHART_ACCEPTANCE_FILES = (
    "chart_probes/chart_probe_results.json", "chart_probes/chart_probe_cases.csv",
    "chart_probes/evidence/evidence.jsonl", "chart_probes/evidence/evidence.md",
    "chart_probes/evidence/retrieval_summary.json",
)
ACCEPTANCE_FILES = (
    "acceptance_results.json", "case_projection_audit.json",
    "unit_tests.stdout.txt", "unit_tests.stderr.txt", "faiss_worker.stderr.txt",
    "portable_query.stdout.txt", "portable_query.stderr.txt",
    "RETRIEVAL_QUALITY_REVIEW.md", "retrieval_quality_review.md",
    "release_quality_review.md", "acceptance_notes.md",
) + CHART_ACCEPTANCE_FILES
SYNTHETIC_CASE = (
    "Synthetic software test, not a real patient: an adult with type 2 diabetes, "
    "chronic kidney disease, eGFR 38 mL/min/1.73 m2, albuminuria, and HbA1c 8.1%. "
    "Retrieve guideline evidence about medication selection and relevant "
    "kidney-function restrictions. Do not generate a treatment plan.\n"
)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid UTF-8 JSON: {path}") from exc


def _write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _input_root(path):
    raw = Path(path)
    if any(part.is_symlink() for part in (raw.absolute(), *raw.absolute().parents)):
        raise ValueError(f"Input must not be a symlink: {raw}")
    root = raw.resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"Input must be a directory: {root}")
    return root


def _portable_name(relative):
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError(f"Unsafe delivery path: {relative!r}")
    parsed = PurePosixPath(relative)
    if parsed.is_absolute() or parsed.as_posix() != relative or any(
        part in {"", ".", ".."} or any(character in '<>:"|?*' or ord(character) < 32 for character in part)
        or part.endswith((" ", "."))
        or part.split(".", 1)[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}
        for part in parsed.parts
    ):
        raise ValueError(f"Unsafe delivery path: {relative!r}")
    return parsed


def _tree_hashes(root):
    result, caseless = {}, set()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Delivery input contains a symlink: {path}")
        relative = path.relative_to(root).as_posix()
        _portable_name(relative)
        # Avoid ambiguous filenames when moved to common Windows/macOS disks.
        normalized = relative.casefold()
        if normalized in caseless:
            raise ValueError(f"Case-insensitive filename collision: {relative}")
        caseless.add(normalized)
        if path.is_file():
            result[relative] = sha(path)
        elif not path.is_dir():
            raise ValueError(f"Delivery contains a nonregular file: {path}")
    return result


def _overlap(a, b):
    return a == b or a in b.parents or b in a.parents


def _safe_output(path, inputs):
    raw = Path(path)
    if raw.exists() or raw.is_symlink():
        raise FileExistsError(raw)
    output = raw.resolve()
    if any(_overlap(output, source) for source in inputs):
        raise ValueError("Delivery output and input paths must not overlap")
    # Normal output may be inside the pipeline but never contain its source.
    if output == PIPE or output in PIPE.parents:
        raise ValueError("Delivery output cannot contain the pipeline directory")
    return output


def _strict_bundle(bundle, inventory=None):
    from kb_v1_runtime import verify_bundle
    manifest = verify_bundle(bundle)  # Never permit a candidate override.
    if manifest.get("status") != "released":
        raise ValueError("Only a released whole-chart bundle may be packaged")
    if manifest.get("release_mode") != "whole_chart":
        raise ValueError("This delivery requires release_mode whole_chart, not the legacy release route")
    if manifest.get("visual_record_count", 0) <= 0:
        raise ValueError("A whole-chart delivery must include released visual evidence")
    actual = _tree_hashes(bundle) if inventory is None else inventory
    if actual != {**manifest["files"], "manifest.json": sha(bundle / "manifest.json")}:
        raise ValueError("Bundle manifest must cover every input file, with no unlisted extras")
    return manifest


def _acceptance(acceptance, manifest, bundle_hash):
    if acceptance is None:
        return {}, {"status": "NOT_RUN", "platform_results": {
            "macOS": {"status": "NOT_RUN", "reason": "No acceptance report supplied"},
            "Windows": {"status": "NOT_RUN", "reason": "No acceptance report supplied"},
        }}
    # Check even ignored descendants: the supplied input must be materialized.
    for path in acceptance.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Acceptance input contains a symlink: {path}")
    report = _json(acceptance / "acceptance_results.json")
    if not isinstance(report, dict) or report.get("status") != "SUCCESS":
        raise ValueError("Acceptance report must have status SUCCESS")
    if report.get("kb_version") != manifest["kb_version"] or report.get("bundle_manifest_sha256") != bundle_hash:
        raise ValueError("Acceptance report must bind this exact KB version and bundle manifest")
    platforms = report.get("platform_results")
    if not isinstance(platforms, dict):
        raise ValueError("Acceptance report requires explicit platform_results")
    for name in ("macOS", "Windows"):
        row = platforms.get(name)
        if not isinstance(row, dict) or row.get("status") not in {"SUCCESS", "NOT_RUN", "PARTIAL", "FAILED"}:
            raise ValueError(f"Acceptance requires an explicit {name} status")
        if row["status"] == "SUCCESS":
            actual = row.get("platform", "")
            expected = "macOS" if name == "macOS" else "Windows"
            if not isinstance(actual, str) or expected.casefold() not in actual.casefold():
                raise ValueError(f"Successful {name} acceptance requires its actual platform string")
        elif not row.get("reason"):
            raise ValueError(f"Incomplete {name} acceptance requires a reason")
    probe_path = acceptance / "chart_probes/chart_probe_results.json"
    if probe_path.is_file():
        probe = _json(probe_path)
        if (probe.get("status") != "SUCCESS" or probe.get("kb_version") != manifest["kb_version"]
                or probe.get("bundle_manifest_sha256") != bundle_hash
                or probe.get("queries") != manifest["visual_record_count"]
                or probe.get("complete_parents_returned") != manifest["visual_record_count"]):
            raise ValueError("Chart locator report must prove complete parents for this exact release")
        if any(not (acceptance / name).is_file() for name in CHART_ACCEPTANCE_FILES):
            raise ValueError("Chart locator acceptance must include its complete synthetic query and evidence files")
    elif any((acceptance / name).is_file() for name in CHART_ACCEPTANCE_FILES):
        raise ValueError("Chart locator evidence files require their bound acceptance report")
    selected = {name: sha(acceptance / name) for name in ACCEPTANCE_FILES if (acceptance / name).is_file()}
    return selected, {"status": "SUCCESS", "platform_results": platforms,
                      "report_path": "test_results/acceptance_results.json"}


def _copy_checked(source, target, expected):
    if source.is_symlink() or not source.is_file() or sha(source) != expected:
        raise ValueError(f"Delivery input changed before copying: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    if sha(target) != expected:
        raise ValueError(f"Delivery copy differs from input: {target}")


def _start_here(manifest, acceptance):
    statuses = acceptance["platform_results"]
    platform_text = "\n".join(f"- {name}: {statuses[name]['status']}." for name in ("macOS", "Windows"))
    return f'''# ADA guideline knowledge base teammate instructions

This controlled research package retrieves ADA guideline evidence with
source references. It does not generate recommendations, call a generation API,
or score clinical correctness. The bundle version is `{manifest['kb_version']}`.
Approved whole-chart content and its audit records are included alongside the
text and table baseline. Approval covers the reviewed representation, not a
clinical outcome evaluation or the whole ADA guideline collection.

## Before you begin

Keep this package intact. Do not edit its bundle, tools, or included reports;
their checksums bind the delivered version. Put environments and retrieval
outputs in sibling directories. Confirm that you are authorized to receive and
use the ADA material before sharing. See
[model and source notices](tools/licenses/MODEL_AND_SOURCE_NOTICE.md).
Do not publish the source PDF, extracts, or review materials publicly.

Use Python 3.12 and a CPU. The included dependency versions were exercised on
the recorded maintainer platform; a pinned requirements file alone does not
prove a fresh installation or Windows compatibility. Installing dependencies
normally needs internet access. Queries use the materialized model inside the
package, are configured offline, and require no API key or Ollama.

## macOS installation and test

Open Terminal in the extracted package directory. Use a new sibling environment.

```sh
python3.12 -m venv ../ada-kb-env
../ada-kb-env/bin/python -m pip install -r tools/requirements-v1-consumer.lock.txt
../ada-kb-env/bin/python selftest.py --output "../ADA acceptance macOS"
```

The output directory must not exist. Inspect `selftest_results.json` and all
four command logs there, including `chart-probes` and its full evidence outputs.
A successful run establishes an installation and
retrieval smoke test on that specific computer, not clinical validity.

## Windows installation and test

Open PowerShell in the extracted package directory. Activation is not needed.

```powershell
py -3.12 -m venv ..\\ada-kb-env
..\\ada-kb-env\\Scripts\\python.exe -m pip install -r tools\\requirements-v1-consumer.lock.txt
..\\ada-kb-env\\Scripts\\python.exe selftest.py --output "..\\ADA acceptance Windows"
```

Send the generated `selftest_results.json` and command logs to the maintainer
through an authorized channel. If installation fails, preserve the error and
report the actual Python and operating-system versions; do not label Windows
as tested merely because instructions exist.

## Check integrity and retrieve evidence

In the following commands, replace `python` with the full environment Python
path from your platform instructions. Paths are relative to the package root.

```sh
python tools/kb_v1_chart_delivery.py verify --delivery .
python tools/kb_v1.py doctor
python tools/kb_v1.py verify --bundle bundle
python tools/kb_v1.py query --bundle bundle --vignette-file examples/synthetic_case.txt --case-id synthetic-demo --output ../ada-demo-evidence
python tools/kb_v1.py query --bundle bundle --cases examples/synthetic_cases.csv --output ../ada-demo-batch
```

`evidence.jsonl` is the machine-readable API-workflow input; `evidence.md` is
readable by a teammate. The default is eight deduplicated evidence groups.
Each hit carries a KB version, record identifier, original evidence, source
page, and review status. Follow the source links and read all conditions,
footnotes, dependency evidence and restrictions before downstream use. Search
similarity is not a probability of clinical correctness.

For your own cases, use UTF-8 CSV columns `case_id,vignette_text`; quote commas
or line breaks according to CSV rules. Use only case facts, not an AI-generated
treatment plan, as the query. Keep sensitive cases and outputs local. Empty or
conflicting cases are rejected; long cases are windowed and preserved in the
output. Structured inputs are opt-in with `--input-mode structured`; see the
recognized field list in `tools/kb_v1_runtime.py` before selecting that mode.

## Evidence limitations and API use

This release preserves reviewed source ambiguities and does not turn them into
executable dose calculations or treatment rules. In particular, Figure 9.5
retains its flagged cross-reference, NPH wording and combination-language
limitations. A human approval of the faithful representation is not a clinical
resolution of those issues. Consumers must not discard these limitations when
passing evidence into an API prompt. No generation API is wired into this tool.

When included by the verified release contract, Table 9.1 retains its two-page
context, approximate dose-share total and ambiguous adjustment wording; it is
not an executable dosing calculator. Table 9.4 retains all product rows and
footnotes, its historical July 2025 price snapshot, printed unit ambiguities,
and the per-1,000-units versus monthly cross-reference limitation. It does not
provide current prices, individual out-of-pocket estimates or a price calculator.

An evidence package can be supplied to your team's existing API workflow as
retrieved context. Keep identifiers and source citations attached, distinguish
quoted source text from reviewed interpretation, and retain uncertainty. This
is separate from generating a plan or evaluating guideline adherence.

## What has actually been tested

{platform_text}

Detailed supplied acceptance evidence, when available, is in `test_results`.
It is bound to this exact bundle manifest, not a similarly named earlier
candidate. The included self-test records only its actual host and never marks
the other platform successful. Engineering acceptance is not retrieval
sensitivity, improved clinical accuracy, or outcome validation.

## Package contents

- `bundle`: records, retrieval units, normalized vectors, the fixed model,
  source files, approval audit and bundle manifest.
- `tools`: offline verification and retrieval code, pinned dependency files,
  and model/source notices.
- `examples`: synthetic software-test inputs only; no project patient CSV.
- `selftest.py`: portable environment, checksum and query smoke test.
  Its chart locator checks exercise the exact approved release set and confirm
  that complete parents, page references, footnotes and use limitations survive
  public retrieval output. These synthetic checks are not a clinical benchmark.
- `delivery_manifest.json` and `SHA256SUMS`: complete payload integrity inventory.

Checksums detect changed bytes, not malicious replacement of both files and
their manifests; this is not a digital signature. If an `.incomplete` file is
present or verification fails, stop and obtain a complete package.
'''


SELFTEST = r'''#!/usr/bin/env python3
"""Offline installation smoke test; records the actual host, never a second OS."""
import argparse
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    output = args.output.resolve()
    if args.output.exists() or args.output.is_symlink():
        parser.error("Output must be a new directory")
    if root == output or root in output.parents or output in root.parents:
        parser.error("Output must not overlap the immutable delivery package")
    output.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(root / "tools"))
    import kb_v1_chart_delivery as delivery
    report = {"status": "FAILED", "actual_platform": platform.platform(),
              "platform_system": platform.system(), "python": platform.python_version(),
              "recorded_at": datetime.now(timezone.utc).isoformat(),
              "only_this_host_tested": True, "clinical_validation": "NOT_PERFORMED",
              "commands": []}
    try:
        package = delivery.verify_delivery(root)
        report.update(kb_version=package["kb_version"],
                      bundle_manifest_sha256=package["bundle_manifest_sha256"],
                      delivery_manifest_sha256=delivery.sha(root / "delivery_manifest.json"))
        environment = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                           HF_DATASETS_OFFLINE="1", TOKENIZERS_PARALLELISM="false",
                           PYTHONDONTWRITEBYTECODE="1")
        commands = [
            ("doctor", ["doctor"]),
            ("verify", ["verify", "--bundle", str(root / "bundle")]),
            ("query", ["query", "--bundle", str(root / "bundle"),
                       "--vignette-file", str(root / "examples/synthetic_case.txt"),
                       "--case-id", "synthetic-install-test", "--output", str(output / "evidence")]),
        ]
        commands = [(label, [sys.executable, str(root / "tools/kb_v1.py"), *arguments])
                    for label, arguments in commands]
        commands.append(("chart-probes", [sys.executable, str(root / "tools/kb_v1_chart_acceptance.py"),
                         "--bundle", str(root / "bundle"), "--output", str(output / "chart_probes")]))
        for label, command in commands:
            try:
                result = subprocess.run(command, cwd=output, env=environment, text=True,
                                        encoding="utf-8", capture_output=True, timeout=300)
                code, stdout, stderr = result.returncode, result.stdout, result.stderr
            except subprocess.TimeoutExpired as exc:
                code, stdout, stderr = 124, "", "Timed out after 300 seconds: " + str(exc)
            (output / (label + ".stdout.txt")).write_text(stdout, encoding="utf-8")
            (output / (label + ".stderr.txt")).write_text(stderr, encoding="utf-8")
            report["commands"].append({"step": label, "command": command, "returncode": code})
        report["status"] = "SUCCESS" if all(row["returncode"] == 0 for row in report["commands"]) else "FAILED"
        if report["status"] == "SUCCESS":
            rows = [json.loads(line) for line in (output / "evidence/evidence.jsonl").read_text(encoding="utf-8").splitlines()]
            if len(rows) != 1 or not rows[0].get("evidence") or rows[0].get("kb_version") != package["kb_version"]:
                raise ValueError("Smoke query returned missing or mismatched evidence")
            report["evidence_count"] = len(rows[0]["evidence"])
            probe_report = json.loads((output / "chart_probes/chart_probe_results.json").read_text(encoding="utf-8"))
            if (probe_report.get("status") != "SUCCESS" or probe_report.get("kb_version") != package["kb_version"]
                    or probe_report.get("bundle_manifest_sha256") != package["bundle_manifest_sha256"]):
                raise ValueError("Chart locator checks failed or returned a mismatched release identity")
            report["chart_locator_queries"] = probe_report["queries"]
            report["complete_chart_parents_returned"] = probe_report["complete_parents_returned"]
            report["source_files_checked"] = 0
            from kb_v1_runtime import safe_bundle_path
            for hit in rows[0]["evidence"]:
                source = safe_bundle_path(root / "bundle", hit["source"]["relative_path"])
                if delivery.sha(source) != hit["source_id"]:
                    raise ValueError("Source link checksum mismatch")
                report["source_files_checked"] += 1
    except Exception as exc:
        report["status"], report["error"] = "FAILED", str(exc)
    (output / "selftest_results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "SUCCESS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
'''


def package_release(bundle, output, acceptance=None, make_zip=False):
    """Create one NEW complete local delivery, optionally with a sibling ZIP."""
    # Resolve outputs/overlap before reading large inputs or creating anything.
    raw_inputs = [Path(bundle).resolve(), *([Path(acceptance).resolve()] if acceptance is not None else [])]
    output = _safe_output(output, raw_inputs)
    archive = output.with_suffix(".zip")
    if make_zip and (archive == output or archive.exists() or archive.is_symlink()):
        raise FileExistsError(f"Archive target must be a new sibling file: {archive}")
    bundle = _input_root(bundle)
    acceptance = _input_root(acceptance) if acceptance is not None else None
    bundle_files = _tree_hashes(bundle)
    if ".incomplete" in bundle_files:
        raise ValueError("Cannot package an incomplete bundle")
    manifest = _strict_bundle(bundle, bundle_files)
    accepted_files, accepted = _acceptance(acceptance, manifest, bundle_files["manifest.json"])
    tools = {}
    for name in TOOL_FILES:
        path = PIPE / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Required materialized consumer tool missing: {name}")
        tools[name] = sha(path)
    licenses = _tree_hashes(_input_root(PIPE / "licenses"))
    if not {"MiniLM-APACHE-2.0.txt", "MODEL_AND_SOURCE_NOTICE.md"}.issubset(licenses):
        raise ValueError("Required model license and source notice are missing")
    output.mkdir(parents=True, exist_ok=False)
    marker = output / ".incomplete"
    marker.write_text("Packaging in progress; do not distribute.\n", encoding="utf-8")
    for name, digest in bundle_files.items():
        _copy_checked(bundle / name, output / "bundle" / name, digest)
    for name, digest in tools.items():
        _copy_checked(PIPE / name, output / "tools" / name, digest)
    for name, digest in licenses.items():
        _copy_checked(PIPE / "licenses" / name, output / "tools/licenses" / name, digest)
    for name, digest in accepted_files.items():
        _copy_checked(acceptance / name, output / "test_results" / name, digest)
    (output / "START_HERE.md").write_text(_start_here(manifest, accepted), encoding="utf-8")
    (output / "selftest.py").write_text(SELFTEST, encoding="utf-8")
    (output / "examples").mkdir()
    (output / "examples/synthetic_case.txt").write_text(SYNTHETIC_CASE, encoding="utf-8")
    # A fixed synthetic row needs no external CSV or private case material.
    (output / "examples/synthetic_cases.csv").write_text(
        'case_id,vignette_text\nsynthetic-demo,"' + SYNTHETIC_CASE.strip().replace('"', '""') + '"\n', encoding="utf-8")
    payload = _tree_hashes(output)
    payload.pop(".incomplete")
    (output / "SHA256SUMS").write_text("".join(f"{digest}  {name}\n" for name, digest in sorted(payload.items())), encoding="utf-8")
    payload["SHA256SUMS"] = sha(output / "SHA256SUMS")
    result = {"schema_version": SCHEMA, "status": "controlled_research_release",
              "kb_version": manifest["kb_version"], "bundle_status": "released",
              "bundle_manifest_sha256": bundle_files["manifest.json"],
              "packaged_at": datetime.now(timezone.utc).isoformat(),
              "released_visual_records": manifest["visual_record_count"],
              "chart_locator_selftest": True,
              "acceptance": accepted, "files": payload,
              "sharing": "Controlled local use only; confirm source permissions before sharing.",
              "clinical_validation": "NOT_PERFORMED", "generation_api": False}
    _write_json(output / "delivery_manifest.json", result)
    # Verify before removing the incomplete marker. No partial ZIP is presented
    # as complete; it is created only after the complete directory verifies.
    verify_delivery(output, _allow_incomplete=True)
    marker.unlink()
    response = {key: value for key, value in result.items() if key != "files"}
    response["delivery_directory"] = str(output)
    response["delivery_manifest_sha256"] = sha(output / "delivery_manifest.json")
    response["file_count"] = len(payload)
    if make_zip:
        with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as handle:
            for name in sorted([*payload, "delivery_manifest.json"]):
                handle.write(output / name, (Path(output.name) / name).as_posix())
        response.update(zip_path=str(archive), zip_sha256=sha(archive))
    return response


def verify_delivery(delivery, _allow_incomplete=False):
    root = _input_root(delivery)
    actual = _tree_hashes(root)
    if ".incomplete" in actual:
        if not _allow_incomplete:
            raise ValueError("Delivery is marked incomplete; do not use it")
        actual.pop(".incomplete")
    manifest = _json(root / "delivery_manifest.json")
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA or manifest.get("status") != "controlled_research_release":
        raise ValueError("Unsupported or non-released delivery manifest")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files or "delivery_manifest.json" in files:
        raise ValueError("Delivery manifest requires a complete payload inventory")
    for name, digest in files.items():
        _portable_name(name)
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError(f"Invalid delivery checksum: {name}")
    actual.pop("delivery_manifest.json", None)
    if files != actual:
        raise ValueError("Delivery checksum inventory differs: changed, missing, or unlisted file")
    expected_sums = "".join(f"{digest}  {name}\n" for name, digest in sorted(files.items()) if name != "SHA256SUMS")
    if (root / "SHA256SUMS").read_text(encoding="utf-8") != expected_sums:
        raise ValueError("SHA256SUMS differs from the delivery inventory")
    # Earlier valid seven-chart deliveries predate the extra locator selftest.
    # Preserve their verification contract; every newly created delivery opts in.
    required_tools = [name for name in TOOL_FILES if name not in {"kb_v1_chart_acceptance.py", "kb_v1_chart_text.py"}]
    if manifest.get("chart_locator_selftest") is True:
        required_tools = TOOL_FILES
    elif "chart_locator_selftest" in manifest:
        raise ValueError("Invalid chart locator selftest declaration")
    required = {"START_HERE.md", "selftest.py", "examples/synthetic_case.txt", "examples/synthetic_cases.csv", "SHA256SUMS",
                *["tools/" + name for name in required_tools], "tools/licenses/MiniLM-APACHE-2.0.txt", "tools/licenses/MODEL_AND_SOURCE_NOTICE.md"}
    if not required.issubset(files):
        raise ValueError("Delivery is missing required consumer tools or documentation")
    bundle = _strict_bundle(root / "bundle")
    if (manifest.get("kb_version") != bundle["kb_version"]
            or manifest.get("bundle_manifest_sha256") != sha(root / "bundle/manifest.json")
            or manifest.get("released_visual_records") != bundle["visual_record_count"]
            or manifest.get("bundle_status") != "released"):
        raise ValueError("Delivery and bundle identities do not match")
    if manifest.get("acceptance", {}).get("status") == "SUCCESS":
        _, accepted = _acceptance(root / "test_results", bundle, manifest["bundle_manifest_sha256"])
        if accepted != manifest["acceptance"]:
            raise ValueError("Delivery acceptance summary differs from its bound report")
    elif manifest.get("acceptance") != _acceptance(None, bundle, manifest["bundle_manifest_sha256"])[1]:
        raise ValueError("Invalid delivery acceptance status")
    return {key: value for key, value in manifest.items() if key != "files"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("package")
    create.add_argument("--bundle", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--acceptance", type=Path)
    create.add_argument("--zip", action="store_true")
    verify = sub.add_parser("verify")
    verify.add_argument("--delivery", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = (package_release(args.bundle, args.output, args.acceptance, args.zip)
                  if args.command == "package" else verify_delivery(args.delivery))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, FileNotFoundError, FileExistsError, ImportError, OSError) as exc:
        parser.exit(2, f"ERROR: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
