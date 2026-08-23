#!/usr/bin/env python3
"""
scripts/ci_check_coverage_ratchet.py — Iter arch-Phase0-item4 (CI-guard)

Two independent checks against a coverage.py JSON report
(--cov-report=json, standard `coverage.py` schema — totals.percent_covered
+ files[<path>].summary.percent_covered):

  (a) RATCHET — repo-wide total coverage must not drop below the
      committed baseline in backend/.coverage_baseline.json. The
      baseline only ever moves up (manually, by a human/agent
      reviewing a real improvement) — this script never lowers it
      and never auto-raises it, to avoid a silent, undiscussed
      change to the safety net.
  (b) FLOOR — every backend/{routers,services,core,cto_services}
      *.py file ADDED or MODIFIED in this diff must independently
      have >= FLOOR_PERCENT coverage in the same report. Editing a
      9%-covered file without adding tests is exactly what this
      exists to stop (founder directive, Phase 2c).

Override: add the `[coverage-approved]` label (PR) or include the
literal string `[coverage-approved]` in the head commit message
(push) — reviewer sign-off required, echoed in the CI log. Mirrors
the `[bloat-approved]` convention in ci_check_new_bloat.py.

Usage:
    python scripts/ci_check_coverage_ratchet.py <base_sha> <head_sha> \
        <coverage_json_path> [--override]

Exit codes:
    0 — no ratchet drop, no floor violation, or override present
    1 — ratchet drop and/or floor violation found without override
    2 — invocation / IO error
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

FLOOR_PERCENT = 60.0
BASELINE_PATH = os.path.join(os.path.dirname(__file__), "..", ".coverage_baseline.json")
_TRACKED_PREFIXES = (
    "backend/routers/", "backend/services/", "backend/core/", "backend/cto_services/",
)


def _touched_py_files(base_sha: str, head_sha: str) -> list[str]:
    """Files ADDED or MODIFIED (not deleted) in this diff, restricted
    to the tracked backend source prefixes and .py extension."""
    try:
        raw = subprocess.check_output(
            ["git", "diff", "--name-only", "--diff-filter=AM",
             f"{base_sha}..{head_sha}"],
            text=True, timeout=30,
        )
    except subprocess.CalledProcessError as e:
        print(f"::error::git diff failed: {e}", file=sys.stderr)
        sys.exit(2)
    return [
        p.strip() for p in raw.splitlines()
        if p.strip().startswith(_TRACKED_PREFIXES) and p.strip().endswith(".py")
    ]


def _load_baseline() -> float:
    try:
        with open(BASELINE_PATH, encoding="utf-8") as f:
            return float(json.load(f)["overall_min_percent"])
    except (OSError, KeyError, ValueError) as e:
        print(f"::error::could not read baseline at {BASELINE_PATH}: {e}", file=sys.stderr)
        sys.exit(2)


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print("usage: ci_check_coverage_ratchet.py <base_sha> <head_sha> "
              "<coverage_json_path> [--override]", file=sys.stderr)
        return 2
    base_sha, head_sha, cov_path = argv[1], argv[2], argv[3]
    override = "--override" in argv[4:]

    try:
        with open(cov_path, encoding="utf-8") as f:
            report = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"::error::could not read coverage report at {cov_path}: {e}", file=sys.stderr)
        return 2

    baseline = _load_baseline()
    current_total = float(report["totals"]["percent_covered"])
    violations: list[str] = []

    print(f"Overall coverage: {current_total:.2f}% (baseline floor: {baseline:.2f}%)")
    if current_total < baseline:
        violations.append(
            f"RATCHET: overall coverage dropped to {current_total:.2f}%, "
            f"below the committed baseline of {baseline:.2f}% "
            f"({BASELINE_PATH})."
        )
    elif current_total > baseline:
        print(f"(above baseline by {current_total - baseline:.2f} pts — "
              f"baseline may be manually ratcheted up after review, not done automatically)")

    touched = _touched_py_files(base_sha, head_sha)
    if touched:
        print(f"\nTouched backend source files in this diff ({len(touched)}):")
    files_map = report.get("files", {})
    for repo_path in touched:
        # coverage.json keys are relative to the backend/ working dir
        cov_key = repo_path[len("backend/"):]
        summary = files_map.get(cov_key, {}).get("summary")
        if summary is None:
            print(f"  - {repo_path}: not present in coverage report (no statements collected) — skipped")
            continue
        pct = float(summary["percent_covered"])
        print(f"  - {repo_path}: {pct:.2f}%")
        if pct < FLOOR_PERCENT:
            violations.append(
                f"FLOOR: {repo_path} is {pct:.2f}% covered "
                f"(< {FLOOR_PERCENT:.0f}% floor) — add real tests before merging this change."
            )

    if not violations:
        print("\nOK — no ratchet drop, no touched-file floor violation.")
        return 0

    print("\n=== ci_check_coverage_ratchet violations ===")
    for v in violations:
        print(f"::error::{v}")

    if override:
        print()
        print(f"[coverage-approved] override present — {len(violations)} "
              f"violation(s) logged but NOT blocking. Reviewer signed off.")
        return 0

    print()
    print(f"FAIL — {len(violations)} coverage violation(s) found. "
          f"Add tests to raise coverage, or add the `[coverage-approved]` "
          f"label/commit-message tag with reviewer sign-off.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
