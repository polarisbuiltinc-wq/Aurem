"""churn_risk.py — Code-churn × complexity risk ranking (2026-08-26).

Phase 3a research (see CHANGELOG 2026-08-26) found the founder's own
"files in progress" list was almost exactly the highest-git-churn list,
independently confirmed against `architecture_health.py`'s bloated/
complexity scan. This turns that into a live, reusable signal: no new
dependency, pure `git log` parsing + the existing health-report scan.

High churn + high complexity/bloat is the real risk combination — a
file that changes often AND is hard to reason about is where regressions
concentrate. Low churn + high complexity is lower priority (stable,
rarely touched); high churn + low complexity is fine (simple file,
frequently edited, low risk).
"""
from __future__ import annotations

import os
import subprocess
import time
from typing import Optional

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _git_churn_counts(days: int) -> dict[str, int]:
    """{repo-relative path: commit count touching it} over the window."""
    try:
        out = subprocess.run(
            ["git", "log", f"--since={days} days ago", "--name-only",
             "--pretty=format:"],
            cwd=_REPO_ROOT, capture_output=True, text=True, timeout=20,
        )
    except Exception:
        return {}
    counts: dict[str, int] = {}
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line or not (line.startswith("backend/") or line.startswith("frontend/")):
            continue
        counts[line] = counts.get(line, 0) + 1
    return counts


def _row_for_file(path: str, n_commits: int, bloated_by_rel: dict,
                   complex_files: set, days: int) -> Optional[dict]:
    """Build a risk row for one churned path, or None if it has no
    quality signal (not in bloated/complex sets) or isn't backend/frontend."""
    rel = None
    if path.startswith("backend/"):
        rel = path[len("backend/"):]
    elif path.startswith("frontend/src/"):
        rel = path[len("frontend/src/"):]
    if rel is None:
        return None
    is_bloated = rel in bloated_by_rel
    is_complex = rel in complex_files
    if not (is_bloated or is_complex):
        return None  # only files with a REAL, existing quality signal
    # Risk score: churn is the multiplier, bloat/complexity the base.
    risk = n_commits * (1 + int(is_bloated) + int(is_complex))
    return {
        "file": path,
        "commits_last_%dd" % days: n_commits,
        "bloated": is_bloated,
        "lines": bloated_by_rel.get(rel),
        "has_complex_function": is_complex,
        "risk_score": risk,
    }


def compute_churn_risk(days: int = 90, top_n: int = 15,
                        report: Optional[dict] = None) -> dict:
    """Rank files by churn × (bloated + complex) risk.

    `report` lets a caller pass an already-computed `run_health_report()`
    result to avoid a second full-codebase scan (same sharing pattern as
    health_score.py's code_quality/architecture split)."""
    started = time.time()
    if report is None:
        from services.architecture_health import run_health_report
        report = run_health_report()

    churn = _git_churn_counts(days)
    if not churn:
        return {"ok": False, "reason": "git log unavailable or empty window",
                "rows": [], "generated_at": started}

    bloated_by_rel = {b["rel"]: b["lines"] for b in report["bloated_files"]}
    complex_files = {h["file"] for h in report["complexity_hits"]}

    rows = []
    for path, n_commits in churn.items():
        row = _row_for_file(path, n_commits, bloated_by_rel, complex_files, days)
        if row is not None:
            rows.append(row)
    rows.sort(key=lambda r: r["risk_score"], reverse=True)
    return {
        "ok": True,
        "window_days": days,
        "total_files_considered": len(churn),
        "flagged_files": len(rows),
        "rows": rows[:top_n],
        "generated_at": started,
        "duration_ms": int((time.time() - started) * 1000),
    }
