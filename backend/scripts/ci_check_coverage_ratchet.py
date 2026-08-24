#!/usr/bin/env python3
"""
scripts/ci_check_coverage_ratchet.py — Iter arch-Phase0-item4 (CI-guard)

Three independent checks against a coverage.py JSON report
(--cov-report=json, standard `coverage.py` schema — totals.percent_covered
+ files[<path>].summary.percent_covered + files[<path>].executed_lines /
missing_lines):

  (a) RATCHET — repo-wide total coverage must not drop below the
      committed baseline in backend/.coverage_baseline.json. The
      baseline only ever moves up (manually, by a human/agent
      reviewing a real improvement) — this script never lowers it
      and never auto-raises it, to avoid a silent, undiscussed
      change to the safety net.
  (b) FLOOR — every backend/{routers,services,core,cto_services}
      *.py file ADDED or MODIFIED in this diff must independently
      have >= its tier's floor coverage in the same report. Editing a
      9%-covered file without adding tests is exactly what this
      exists to stop (founder directive, Phase 2c). High-risk paths
      (chat.py, cto_projects.py, auth.py, loop_engine.py — real
      customer-money/data paths) hold a higher floor than the rest.
  (c) DIFF-COVERAGE (2026-08-24, Guard 22) — (b) only ever looked at
      a touched file's TOTAL coverage %, which a well-covered old
      file can pass while the actual NEW lines added in this diff are
      0% covered. This checks coverage of just the added line numbers
      (via `git diff -U0`, intersected with the report's
      executed_lines/missing_lines) against the SAME tiered floor —
      closing the exact gap the blueprint review flagged.

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
import re
import subprocess
import sys

FLOOR_PERCENT = 60.0          # default tier
HIGH_RISK_FLOOR_PERCENT = 80.0
BASELINE_PATH = os.path.join(os.path.dirname(__file__), "..", ".coverage_baseline.json")
_TRACKED_PREFIXES = (
    "backend/routers/", "backend/services/", "backend/core/", "backend/cto_services/",
)
# 2026-08-24 — Phase 4.2 tiered coverage targets (blueprint gap).
# Real customer-money / auth / task-execution surfaces get a higher
# bar than the rest of the codebase. Substring match against the
# repo-relative path (e.g. "backend/routers/chat.py" contains "chat.py").
_HIGH_RISK_FILES = (
    "routers/chat.py", "routers/cto_projects.py", "routers/auth.py",
    "cto_services/auth.py", "services/loop_engine.py",
)


def _is_high_risk(repo_path: str) -> bool:
    return any(marker in repo_path for marker in _HIGH_RISK_FILES)


def _floor_for(repo_path: str) -> float:
    return HIGH_RISK_FLOOR_PERCENT if _is_high_risk(repo_path) else FLOOR_PERCENT


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


_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _added_line_numbers(base_sha: str, head_sha: str, repo_path: str) -> set[int]:
    """Line numbers ADDED to `repo_path` in this diff (new-file line
    numbering), via a zero-context unified diff's hunk headers +
    leading '+' lines. Pure deletions/context produce an empty set."""
    try:
        raw = subprocess.check_output(
            ["git", "diff", "-U0", f"{base_sha}..{head_sha}", "--", repo_path],
            text=True, timeout=30,
        )
    except subprocess.CalledProcessError as e:
        print(f"::warning::git diff -U0 failed for {repo_path}: {e}", file=sys.stderr)
        return set()
    added: set[int] = set()
    cur_line = None
    for line in raw.splitlines():
        m = _HUNK_RE.match(line)
        if m:
            cur_line = int(m.group(1))
            continue
        if cur_line is None:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            added.add(cur_line)
            cur_line += 1
        elif line.startswith("-") and not line.startswith("---"):
            continue  # deleted line — doesn't consume a new-file line number
        else:
            cur_line += 1
    return added


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
        file_report = files_map.get(cov_key)
        floor = _floor_for(repo_path)
        tier_label = "HIGH-RISK" if _is_high_risk(repo_path) else "standard"
        if file_report is None:
            print(f"  - {repo_path}: not present in coverage report (no statements collected) — skipped")
            continue
        summary = file_report["summary"]
        pct = float(summary["percent_covered"])
        print(f"  - {repo_path} [{tier_label}, floor {floor:.0f}%]: {pct:.2f}% total")
        if pct < floor:
            violations.append(
                f"FLOOR: {repo_path} is {pct:.2f}% covered "
                f"(< {floor:.0f}% {tier_label} floor) — add real tests before merging this change."
            )

        # 2026-08-24 — Guard 22: diff-coverage. A file's TOTAL % can
        # look fine while the lines actually touched in THIS diff are
        # untested. Only meaningful when lines were added (pure
        # deletions/renames have nothing to check here).
        added_lines = _added_line_numbers(base_sha, head_sha, repo_path)
        if not added_lines:
            continue
        executed = set(file_report.get("executed_lines") or [])
        missing = set(file_report.get("missing_lines") or [])
        tracked_added = added_lines & (executed | missing)
        if not tracked_added:
            print(f"    (diff-coverage: {len(added_lines)} added line(s), "
                  f"none are executable statements — skipped)")
            continue
        covered_added = tracked_added & executed
        diff_pct = len(covered_added) / len(tracked_added) * 100
        print(f"    diff-coverage: {len(covered_added)}/{len(tracked_added)} "
              f"new statement line(s) covered = {diff_pct:.1f}%")
        if diff_pct < floor:
            violations.append(
                f"DIFF-COVERAGE: {repo_path} — only {diff_pct:.1f}% of the "
                f"{len(tracked_added)} new/changed statement line(s) in this "
                f"diff are covered (< {floor:.0f}% {tier_label} floor). "
                f"This file's overall coverage may look fine while the "
                f"NEW code in this PR is untested — add tests for the "
                f"lines you just added."
            )

    if not violations:
        print("\nOK — no ratchet drop, no floor violation, no diff-coverage violation.")
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
