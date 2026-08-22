"""
services/health_coverage_scan.py — Health Score: Test Coverage
instrumentation (2026-08-23).

Runs a bounded, keyword-filtered slice of the backend pytest suite
with --cov, persists real coverage % (over that slice) + critical-
module integration-test evidence to `health_test_coverage_runs`.

Scoped deliberately — the full ~5,300-test suite with --cov
instrumentation was tried first and did not complete within 600s in
this environment (confirmed by direct measurement, 2026-08-23); a
10+ minute on-demand admin action is impractical, and re-attempting
the full suite is out of scope for this instrumentation pass. The
`scope` field in the persisted doc always discloses this is NOT
whole-repo coverage. On-demand only (admin "Run now" button) — no
default recurring cron, to avoid unplanned resource use on the pod.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone

logger = logging.getLogger("health_coverage_scan")

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COVERAGE_JSON_PATH = os.path.join(BACKEND_ROOT, "coverage.json")
SUBPROCESS_TIMEOUT_S = int(os.environ.get("HEALTH_COVERAGE_TIMEOUT_S", "240"))

# Curated, real critical modules (auth, chat, findings, fix pipeline,
# payments) — same modules the founder named when scoping this category.
CRITICAL_MODULES = [
    "cto_services/auth.py",
    "routers/chat.py",
    "routers/findings.py",
    "routers/fix_pipeline.py",
    "routers/admin_payments.py",
]

# Keyword filter bounding the run to tests touching the critical-path
# areas above (auth/chat/findings/fix-pipeline/payments) — real tests,
# real assertions, just not the entire repo's test suite in one shot.
_SCOPE_KEYWORD_EXPR = "auth or chat or findings or fix_pipeline or payment"


def _parse_pytest_summary(text: str) -> dict:
    counts = {"passed": 0, "failed": 0, "errors": 0}
    for key, pattern in (
        ("passed", r"(\d+) passed"),
        ("failed", r"(\d+) failed"),
        ("errors", r"(\d+) error"),
    ):
        m = re.search(pattern, text)
        if m:
            counts[key] = int(m.group(1))
    return counts


async def run_coverage_scan(db) -> dict:
    started = time.time()
    cmd = [
        sys.executable, "-m", "pytest", "-q", "--timeout=30",
        "-k", _SCOPE_KEYWORD_EXPR,
        "--cov=.", "--cov-report=json",
        "--continue-on-collection-errors",
        "--ignore=tests/test_iter138_acceptance_seven.py",
        "--ignore=tests/test_iter212m163_aggression_chat.py",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=BACKEND_ROOT,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=SUBPROCESS_TIMEOUT_S)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            logger.warning("health_coverage_scan timed out — not persisting a score")
            return {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "ok": False,
                "error": f"timed out after {SUBPROCESS_TIMEOUT_S}s — run incomplete",
                "duration_s": round(time.time() - started, 1),
            }
        out_text = (stdout or b"").decode(errors="replace")
    except Exception as e:
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "ok": False, "error": f"subprocess failed: {e!r}",
        }

    summary = _parse_pytest_summary(out_text)
    total_tests_seen = summary["passed"] + summary["failed"] + summary["errors"]
    if total_tests_seen == 0:
        logger.warning("health_coverage_scan: 0 tests observed in output — not trusting "
                        "any coverage.json on disk (could be stale). tail=%r", out_text[-800:])
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "ok": False,
            "error": "pytest run reported 0 passed/failed/errors — treating as a failed "
                     "run rather than trusting a possibly-stale coverage.json",
            "stdout_tail": out_text[-800:],
            "duration_s": round(time.time() - started, 1),
        }

    coverage_pct = None
    per_file: dict[str, float] = {}
    try:
        cov_mtime = os.path.getmtime(COVERAGE_JSON_PATH)
        if cov_mtime < started:
            raise RuntimeError(
                f"coverage.json mtime ({cov_mtime}) predates this run's start "
                f"({started}) — stale file, not overwritten by this run",
            )
        with open(COVERAGE_JSON_PATH) as fh:
            cov = json.load(fh)
        coverage_pct = round(cov.get("totals", {}).get("percent_covered", 0), 1)
        for path, info in (cov.get("files") or {}).items():
            rel = path.replace(BACKEND_ROOT + "/", "")
            per_file[rel] = round(info.get("summary", {}).get("percent_covered", 0), 1)
    except Exception as e:
        logger.warning("coverage.json parse failed: %r", e)

    critical_modules = []
    for mod in CRITICAL_MODULES:
        pct = per_file.get(mod)
        critical_modules.append({
            "module": mod, "covered_pct": pct,
            "has_integration_test": bool(pct and pct > 0),
        })
    critical_hit_frac = (
        sum(1 for m in critical_modules if m["has_integration_test"]) / len(critical_modules)
        if critical_modules else 0.0
    )

    commit_sha = None
    try:
        from services.deploy_logger import get_current_commit
        commit_sha = get_current_commit().get("commit_sha")
    except Exception:
        pass

    doc = {
        "generated_at":         datetime.now(timezone.utc).isoformat(),
        "ok":                   coverage_pct is not None,
        "scope":                "keyword-filtered critical-path subset "
                                 f"({_SCOPE_KEYWORD_EXPR}) — NOT whole-repo coverage",
        "commit_sha":           commit_sha,
        "overall_coverage_pct": coverage_pct,
        "critical_modules":     critical_modules,
        "critical_hit_frac":    round(critical_hit_frac, 3),
        "test_counts":          summary,
        "duration_s":           round(time.time() - started, 1),
        "source":               "live_run_in_pod",
    }
    if db is not None:
        try:
            await db.health_test_coverage_runs.insert_one(dict(doc))
        except Exception as e:
            logger.warning("persist coverage run failed: %r", e)
    return doc
