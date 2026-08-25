#!/usr/bin/env python3
"""
scripts/ci_check_regression_baseline.py — Production Hardening
Fix 3 (2026-08).

A total-red-count guard, STRICTER than the coverage ratchet's
touched-file floor: this blocks deploy if the suite's TOTAL failed
or errored count goes UP versus a locked baseline — not just "new
red in touched files". A new regression anywhere in the ~6,400-test
suite can no longer hide inside the pre-existing red noise.

Reads the same /tmp/pytest_output.txt the "Run tests" CI step
already tees (see .github/workflows/ci.yml), parses pytest's final
summary line, and compares against the locked counts in
tests/baseline_counts.txt.

The 259 @pytest.mark.legacy quarantined tests (tests/legacy_quarantine.txt)
are untouched by this guard — per founder ruling they're tracked,
never fixed/deleted/blocking, and this script does not read or
modify that file.

Override: add the `[regression-approved]` label (PR) or include the
literal string `[regression-approved]` in the head commit message
(push) — mirrors the `[coverage-approved]` / `[bloat-approved]`
convention used by the other CI guards in this repo.

Usage:
    python scripts/ci_check_regression_baseline.py <pytest_output_path> \
        [--baseline <baseline_path>] [--override]

Exit codes:
    0 — current failed/errors <= locked baseline, or override present
    1 — current failed/errors exceed the locked baseline, no override
    2 — invocation / IO / parse error
"""
from __future__ import annotations

import os
import re
import sys

_DEFAULT_BASELINE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "tests", "baseline_counts.txt",
)

# pytest's final summary line, in whatever order the categories show up,
# e.g. "325 failed, 5862 passed, 75 skipped, 103 deselected, 83 warnings,
# 73 errors in 1155.58s (0:19:15)" — categories are comma-separated and
# not guaranteed to all be present (a fully-green run has no "failed").
_SUMMARY_RE = re.compile(r"(\d+)\s+(failed|passed|error|errors)\b")


def _parse_baseline(path: str) -> dict[str, int]:
    out: dict[str, int] = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                if key in ("failed", "errors", "passed"):
                    out[key] = int(val.strip())
    except (OSError, ValueError) as e:
        print(f"::error::could not read baseline at {path}: {e}", file=sys.stderr)
        sys.exit(2)
    if "failed" not in out or "errors" not in out:
        print(f"::error::baseline at {path} missing 'failed=' or 'errors=' line", file=sys.stderr)
        sys.exit(2)
    return out


def _parse_current_counts(pytest_output_path: str) -> dict[str, int]:
    try:
        with open(pytest_output_path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError as e:
        print(f"::error::could not read pytest output at {pytest_output_path}: {e}", file=sys.stderr)
        sys.exit(2)

    # Scan from the end — the real summary line is the LAST line that
    # matches this shape (earlier lines can contain similarly-worded
    # text inside tracebacks/test names).
    counts = {"failed": 0, "passed": 0, "errors": 0}
    for line in reversed(text.splitlines()):
        matches = _SUMMARY_RE.findall(line)
        if not matches:
            continue
        # A real summary line has at least "in <seconds>s" after it,
        # or ends the run — accept the first (from the end) line with
        # >=1 category match as the summary.
        for num, label in matches:
            n = int(num)
            if label == "failed":
                counts["failed"] = n
            elif label == "passed":
                counts["passed"] = n
            elif label in ("error", "errors"):
                counts["errors"] = n
        break
    return counts


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: ci_check_regression_baseline.py <pytest_output_path> "
              "[--baseline <path>] [--override]", file=sys.stderr)
        return 2
    pytest_output_path = argv[1]
    override = "--override" in argv
    baseline_path = _DEFAULT_BASELINE_PATH
    if "--baseline" in argv:
        baseline_path = argv[argv.index("--baseline") + 1]

    baseline = _parse_baseline(baseline_path)
    current = _parse_current_counts(pytest_output_path)

    print(f"Locked baseline: failed<={baseline['failed']}, errors<={baseline['errors']} "
          f"({baseline_path})")
    print(f"Current run:     failed={current['failed']}, errors={current['errors']}, "
          f"passed={current['passed']}")

    violations: list[str] = []
    if current["failed"] > baseline["failed"]:
        violations.append(
            f"REGRESSION: {current['failed']} tests failed, up from the "
            f"locked baseline of {baseline['failed']}. Total red went up — "
            f"a new failure may be hiding among the pre-existing red."
        )
    if current["errors"] > baseline["errors"]:
        violations.append(
            f"REGRESSION: {current['errors']} tests errored, up from the "
            f"locked baseline of {baseline['errors']}."
        )

    if not violations:
        print("\nOK — total red did not increase versus the locked baseline.")
        return 0

    print("\n=== ci_check_regression_baseline violations ===")
    for v in violations:
        print(f"::error::{v}")

    if override:
        print()
        print(f"[regression-approved] override present — {len(violations)} "
              f"violation(s) logged but NOT blocking. Reviewer signed off.")
        return 0

    print()
    print(f"FAIL — {len(violations)} regression(s) found versus the locked "
          f"baseline. Fix the new failure(s), or add the "
          f"`[regression-approved]` label/commit-message tag with reviewer "
          f"sign-off (and re-baseline tests/baseline_counts.txt deliberately "
          f"if the new red is genuinely accepted debt).")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
