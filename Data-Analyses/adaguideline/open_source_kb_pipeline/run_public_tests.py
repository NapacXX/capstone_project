#!/usr/bin/env python3
"""Run repository tests and write a source-bound, case-free result summary."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import sys
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New JSON file; never overwritten")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; choose a new path")
    for key, value in {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                       "TOKENIZERS_PARALLELISM": "false", "OMP_NUM_THREADS": "1",
                       "MKL_NUM_THREADS": "1", "VECLIB_MAXIMUM_THREADS": "1"}.items():
        os.environ[key] = value
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    started = datetime.now(timezone.utc).isoformat()
    tick = time.perf_counter()
    log = io.StringIO()
    result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    elapsed = time.perf_counter() - tick
    sys.stderr.write(log.getvalue())
    sources = sorted(set(ROOT.glob("*.py")) | set((ROOT / "tests").glob("*.py")))
    payload = {
        "status": "SUCCESS" if result.wasSuccessful() else "FAILED",
        "started_at_utc": started, "elapsed_seconds": elapsed,
        "python": platform.python_version(), "platform": platform.platform(),
        "tests_run": result.testsRun,
        "passed": result.testsRun - len(result.skipped) - len(result.errors) - len(result.failures)
                  - len(result.expectedFailures) - len(result.unexpectedSuccesses),
        "failures": [test.id() for test, _ in result.failures],
        "errors": [test.id() for test, _ in result.errors],
        "skipped": [{"test": test.id(), "reason": reason} for test, reason in result.skipped],
        "expected_failures": [test.id() for test, _ in result.expectedFailures],
        "unexpected_successes": [test.id() for test in result.unexpectedSuccesses],
        "source_sha256": {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in sources},
        "scope": "Repository tests in the available environment. Missing private fixtures are explicitly skipped. This is not clinical validation or a fresh dependency installation test.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
