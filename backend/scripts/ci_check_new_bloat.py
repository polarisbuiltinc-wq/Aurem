#!/usr/bin/env python3
"""
scripts/ci_check_new_bloat.py — Iter arch-Phase0-item3 (CI-guard)

Blocks a PR that introduces:
  (a) a NEW file (backend/routers, backend/services, backend/core,
      backend/cto_services, or frontend/src) exceeding 300 real
      source lines, or
  (b) a NEW Python function/method (radon `cc_visit`) with
      cyclomatic complexity > 10

Only counts files/functions ADDED in this diff — a PR that merely
edits an existing 4,000-line file is NOT blocked by this guard (that
debt is tracked separately in /app/memory/code_quality_ledger.md and
paid down deliberately via Phase 2/3, not via a CI trap on unrelated
work). This guard exists purely to stop the backlog from growing
while it's being paid down.

Override: add the `[bloat-approved]` label to the PR (mirrors the
`[docs-only]`/`[no-test-needed]` convention already used by
bug-fix-discipline above in the same workflow) — reviewer sign-off
required, echoed in the CI log.

Usage:
    python scripts/ci_check_new_bloat.py <base_sha> <head_sha> [--override]

Exit codes:
    0 — no new bloat, or override present
    1 — new bloat found without override
    2 — invocation / IO error
"""
from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from radon.complexity import cc_visit
except ImportError:  # pragma: no cover — radon is in requirements.txt
    cc_visit = None

LINE_LIMIT = 300
CC_LIMIT = 10
_TRACKED_PREFIXES = (
    "backend/routers/", "backend/services/", "backend/core/",
    "backend/cto_services/", "frontend/src/",
)


def _added_files(base_sha: str, head_sha: str) -> list[str]:
    """Files ADDED (not modified, not deleted) in this diff, restricted
    to the tracked source prefixes."""
    try:
        raw = subprocess.check_output(
            ["git", "diff", "--name-only", "--diff-filter=A",
             f"{base_sha}..{head_sha}"],
            text=True, timeout=30,
        )
    except subprocess.CalledProcessError as e:
        print(f"::error::git diff failed: {e}", file=sys.stderr)
        sys.exit(2)
    return [
        p.strip() for p in raw.splitlines()
        if p.strip().startswith(_TRACKED_PREFIXES)
        and p.strip().endswith((".py", ".js", ".jsx", ".ts", ".tsx"))
    ]


def _count_source_lines(path: str) -> int:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return sum(1 for line in f if line.strip())
    except OSError:
        return 0


def _new_functions_over_cc(path: str) -> list[tuple[str, int, int]]:
    """(func_name, lineno, cc) for every function in a NEW .py file
    whose complexity exceeds CC_LIMIT. New JS/JSX files are checked
    for size only — the complexity scanner is Python-only (same
    radon limitation documented in architecture_health.py)."""
    if not path.endswith(".py") or cc_visit is None:
        return []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            src = f.read()
        return [
            (b.fullname, b.lineno, b.complexity)
            for b in cc_visit(src) if b.complexity > CC_LIMIT
        ]
    except (SyntaxError, OSError):
        return []


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: ci_check_new_bloat.py <base_sha> <head_sha> [--override]",
              file=sys.stderr)
        return 2
    base_sha, head_sha = argv[1], argv[2]
    override = "--override" in argv[3:]

    added = _added_files(base_sha, head_sha)
    if not added:
        print("No new tracked source files in this diff. Guard passes trivially.")
        return 0

    print(f"New files in this diff ({len(added)}):")
    for p in added:
        print(f"  - {p}")
    print()

    violations: list[str] = []
    for path in added:
        if not os.path.exists(path):
            continue  # renamed/moved away again within the same diff
        n_lines = _count_source_lines(path)
        if n_lines > LINE_LIMIT:
            violations.append(
                f"{path}: {n_lines} lines (> {LINE_LIMIT} limit) — "
                f"new files must be split BEFORE merge, not after."
            )
        for func, lineno, cc in _new_functions_over_cc(path):
            violations.append(
                f"{path}:{lineno} `{func}` has CC={cc} (> {CC_LIMIT} limit) — "
                f"split into smaller functions before merge."
            )

    if not violations:
        print(f"OK — {len(added)} new file(s), none exceed the bloat thresholds.")
        return 0

    print("=== ci_check_new_bloat violations ===")
    for v in violations:
        print(f"::error::{v}")

    if override:
        print()
        print(f"[bloat-approved] override present — {len(violations)} "
              f"violation(s) logged but NOT blocking. Reviewer signed off.")
        return 0

    print()
    print(f"FAIL — {len(violations)} new-bloat violation(s) found. "
          f"Split the file/function before merge, or add the "
          f"`[bloat-approved]` label with reviewer sign-off.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
