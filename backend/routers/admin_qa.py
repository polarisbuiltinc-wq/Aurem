"""
routers/admin_qa.py — Iter 303 (Gap 2: /admin/qa dashboard)

Founder-facing QA status endpoint. One route:

    GET /api/admin/qa/status

Returns a single JSON payload the frontend renders as 4 cards:

    1. Test counts (backend Pytest, Frontend Vitest, Playwright, a11y)
    2. Style-classifier ratio (STATIC_GREP % — the iter289 CI-guard)
    3. a11y baseline counts (component + journey)
    4. CI status per job (frontend-vitest, visual-regression, test-style-guard,
       invariants) — pulled from GitHub Actions API if
       `GITHUB_ACTIONS_TOKEN` is set + `GITHUB_REPO` is set;
       otherwise reports `{"available": False, "reason": ...}`
       (honest — no fake green statuses).

All counts are computed at request time from the ACTUAL test files
on disk — no cached numbers, no manual bookkeeping to drift.

Auth: reuses `_require_admin` from routers/admin.py — same pattern
as every other admin endpoint. No new auth surface introduced.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Depends
from pydantic import BaseModel
from cto_services.auth import require_admin_dep

from routers.admin import _require_admin


router = APIRouter(prefix="/admin/qa", tags=["admin-qa"],
                   dependencies=[Depends(require_admin_dep)])  # Iter 358 router-level gate


# ═══════════════════════════════════════════════════════════════════
# Test-count harvesters (deterministic on-disk grep)
# ═══════════════════════════════════════════════════════════════════

_APP_ROOT = Path("/app")

_TEST_FN_RE_PY  = re.compile(r"^\s*(?:async\s+)?def\s+test_\w+\s*\(", re.M)
_TEST_FN_RE_JS  = re.compile(r"^\s*(?:test|it)\s*\(\s*[\"']", re.M)


def _count_matches(paths: list[Path], regex: re.Pattern) -> int:
    total = 0
    for p in paths:
        if not p.exists() or not p.is_file():
            continue
        try:
            src = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        total += len(regex.findall(src))
    return total


def _glob_files(root: Path, patterns: list[str]) -> list[Path]:
    out: list[Path] = []
    for pat in patterns:
        out.extend(root.glob(pat))
    return out


def _harvest_counts() -> dict:
    """Test counts. For backend Pytest we use the AST-based analyzer
    (same source as the style-ratio guard) so the number aligns with
    what CI grades against — a plain regex-grep would count test
    helpers inside class bodies too."""
    # ── Backend pytest — trust the AST analyzer ─────────────────
    try:
        from services.test_style_analyzer import analyze_suite
        r = analyze_suite()
        backend_pytest_total = r.get("total_tests") or 0
    except Exception:                                       # noqa: BLE001
        backend_pytest_total = 0
    backend_pytest_files = len(list(
        (_APP_ROOT / "backend" / "tests").glob("test_*.py")
    )) + len(list(
        (_APP_ROOT / "backend" / "tests" / "reasoning").glob("test_*.py")
    ))

    # ── Frontend Vitest (component RTL + a11y) ───────────────────
    frontend_vitest_files = list(
        (_APP_ROOT / "frontend" / "src").rglob("*.test.jsx")
    ) + list(
        (_APP_ROOT / "frontend" / "src").rglob("*.test.js")
    )
    frontend_vitest_total = _count_matches(frontend_vitest_files, _TEST_FN_RE_JS)

    # ── Playwright ───────────────────────────────────────────────
    playwright = list(
        (_APP_ROOT / "frontend" / "tests" / "visual").glob("*.spec.js")
    )
    playwright_total = _count_matches(playwright, _TEST_FN_RE_JS)

    # ── Reasoning-eval suite (Track 3) — subset of backend ──────
    reasoning = list(
        (_APP_ROOT / "backend" / "tests" / "reasoning").glob("test_*.py")
    )
    reasoning_total = _count_matches(reasoning, _TEST_FN_RE_PY)

    out = {
        "backend_pytest": {
            "files": backend_pytest_files,
            "tests": backend_pytest_total,
        },
        "frontend_vitest": {
            "files": len(frontend_vitest_files),
            "tests": frontend_vitest_total,
        },
        "playwright": {
            "files": len(playwright),
            "tests": playwright_total,
        },
        "reasoning_evals": {
            "files": len(reasoning),
            "tests": reasoning_total,
            "note": "subset of backend_pytest (Track 3 v1)",
        },
        "grand_total_tests": (
            backend_pytest_total + frontend_vitest_total + playwright_total
        ),
        "source": "live_fs",
    }
    # ── Iter 351 — build-manifest fallback ────────────────────────
    # Prod pods ship WITHOUT backend/tests (build strips them), so the
    # live glob honestly returns 0 files and the admin tile contradicts
    # the Overview claim. predeploy_gate.sh regenerates
    # backend/qa_manifest.json from the live counts before every
    # deploy; fall back to it per-suite when the live count is 0.
    if backend_pytest_files == 0:
        try:
            mpath = _APP_ROOT / "backend" / "qa_manifest.json"
            m = json.loads(mpath.read_text())
            mc = m.get("test_counts") or {}
            for suite in ("backend_pytest", "frontend_vitest",
                          "playwright", "reasoning_evals"):
                if (out.get(suite) or {}).get("files") == 0 and mc.get(suite):
                    out[suite] = mc[suite]
            out["grand_total_tests"] = m.get(
                "grand_total_tests", out["grand_total_tests"])
            out["source"] = "build_manifest"
            out["manifest_generated_at"] = m.get("generated_at")
        except Exception:                                 # noqa: BLE001
            pass
    return out


# ═══════════════════════════════════════════════════════════════════
# Style-classifier ratio (iter289 CI-guard number)
# ═══════════════════════════════════════════════════════════════════

def _harvest_test_style_ratio() -> dict:
    """Delegates to services.test_style_analyzer.analyze_suite — the
    SAME code path the CI guard uses, so this dashboard number is
    always aligned with whatever CI is grading against."""
    try:
        from services.test_style_analyzer import analyze_suite
        r = analyze_suite()
    except Exception as e:                                # noqa: BLE001
        return {"available": False, "reason": f"analyzer error: {e!r}"}
    counts = r.get("counts") or {}
    total  = r.get("total_tests") or 0
    static_grep = counts.get("STATIC_GREP", 0)
    ratio = (static_grep / total) if total else 0.0
    return {
        "available":         True,
        "static_grep":       static_grep,
        "total_tests":       total,
        "ratio_pct":         round(ratio * 100, 1),
        "threshold_pct":     60.0,
        "passes_threshold":  ratio <= 0.60,
        "weak_p0_count":     len(r.get("weak_p0") or []),
    }


# ═══════════════════════════════════════════════════════════════════
# a11y baselines (component-level + journey-level)
# ═══════════════════════════════════════════════════════════════════

_A11Y_BASELINES = {
    "components": _APP_ROOT / "docs" / "a11y_baseline.json",
    "journeys":   _APP_ROOT / "docs" / "a11y_journey_baseline.json",
}


def _harvest_a11y() -> dict:
    out: dict = {}
    for kind, path in _A11Y_BASELINES.items():
        try:
            j = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            out[kind] = {"available": False, "reason": str(e)}
            continue
        # Exclude keys starting with "_" (metadata like "_note").
        per_key = {k: v for k, v in j.items() if not k.startswith("_")}
        total_violations = sum(
            len(v) if isinstance(v, list) else 0
            for v in per_key.values()
        )
        out[kind] = {
            "available":         True,
            "surfaces_tracked":  len(per_key),
            "surfaces_clean":    sum(
                1 for v in per_key.values()
                if isinstance(v, list) and len(v) == 0
            ),
            "total_known_violations": total_violations,
            "per_surface": {
                k: (v if isinstance(v, list) else [])
                for k, v in per_key.items()
            },
        }
    return out


# ═══════════════════════════════════════════════════════════════════
# GitHub Actions status per job (honest: unavailable if not wired)
# ═══════════════════════════════════════════════════════════════════

# Env contract:
#   GITHUB_ACTIONS_TOKEN  — a PAT or fine-grained token with `actions:read`
#                          scope on the repo. Read-only; never mutating.
#   GITHUB_REPO           — "owner/name" of the repo whose runs to fetch.
# When either is missing, we return an honest "unavailable" payload
# instead of faking green statuses.

_JOB_NAMES_WE_CARE_ABOUT = (
    "bug-fix-discipline",
    "invariants",
    "test-style-guard",
    "frontend-vitest",
    "visual-regression",
)


async def _harvest_ci_status() -> dict:
    token = os.environ.get("GITHUB_ACTIONS_TOKEN")
    repo  = os.environ.get("GITHUB_REPO")
    if not token or not repo:
        return {
            "available": False,
            "reason": (
                "GITHUB_ACTIONS_TOKEN and/or GITHUB_REPO not set. "
                "Set both in backend/.env to enable live CI status. "
                "Token needs `actions:read` scope on the repo."
            ),
            "jobs": {},
        }

    url = (f"https://api.github.com/repos/{repo}/actions/runs?"
           f"per_page=15&event=push")
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            r = await client.get(url, headers=headers)
        if r.status_code != 200:
            return {
                "available": False,
                "reason": f"GitHub API returned {r.status_code}: {r.text[:200]}",
                "jobs": {},
            }
        runs = (r.json() or {}).get("workflow_runs") or []
    except (httpx.HTTPError, ValueError) as e:                 # noqa: BLE001
        return {
            "available": False,
            "reason": f"GitHub API call failed: {e!r}",
            "jobs": {},
        }

    # For each named job we care about, find the MOST RECENT run
    # whose workflow lists that job. Simplification: use the newest
    # `quality-gate` run and enumerate its jobs.
    quality_runs = [r for r in runs
                    if (r.get("name") or "").lower().startswith("quality")]
    if not quality_runs:
        return {
            "available": False,
            "reason": "no quality-gate workflow runs found",
            "jobs": {},
        }
    latest = quality_runs[0]
    jobs_url = latest.get("jobs_url")
    if not jobs_url:
        return {"available": False, "reason": "run has no jobs_url", "jobs": {}}
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            rj = await client.get(jobs_url, headers=headers)
        job_data = (rj.json() or {}).get("jobs") or []
    except (httpx.HTTPError, ValueError) as e:                 # noqa: BLE001
        return {"available": False, "reason": f"jobs fetch failed: {e!r}",
                 "jobs": {}}

    jobs_map = {}
    for j in job_data:
        name = j.get("name")
        if name in _JOB_NAMES_WE_CARE_ABOUT:
            jobs_map[name] = {
                "status":     j.get("status"),           # queued/in_progress/completed
                "conclusion": j.get("conclusion"),       # success/failure/cancelled/None
                "started_at": j.get("started_at"),
                "completed_at": j.get("completed_at"),
                "html_url":   j.get("html_url"),
            }
    # Fill in missing jobs so the UI can render "unknown" chips.
    for name in _JOB_NAMES_WE_CARE_ABOUT:
        jobs_map.setdefault(name, {
            "status": "unknown", "conclusion": None,
            "started_at": None, "completed_at": None,
            "html_url": latest.get("html_url"),
        })
    return {
        "available":       True,
        "workflow_run_id": latest.get("id"),
        "workflow_url":    latest.get("html_url"),
        "commit_sha":      latest.get("head_sha"),
        "commit_message":  (latest.get("head_commit") or {}).get("message"),
        "run_started_at":  latest.get("run_started_at"),
        "jobs":            jobs_map,
    }


# ═══════════════════════════════════════════════════════════════════
# Route
# ═══════════════════════════════════════════════════════════════════

@router.get("/latest-report")
async def get_latest_qa_report(authorization: Optional[str] = Header(None)):
    """Iter 334 — Auto-QA agent report viewer. Serves the markdown
    written by services/qa_matrix.write_report() (locally or by the
    auto-qa-agent CI job committing .emergent/latest-qa-report.md).
    Honest empty-state when the job has never run."""
    await _require_admin(authorization)
    path = "/app/.emergent/latest-qa-report.md"
    if not os.path.exists(path):
        return {"error": "No report yet — auto-qa-agent job has not run"}
    with open(path, "r", encoding="utf-8") as f:
        return {"content": f.read(), "modified_at": os.path.getmtime(path)}


@router.get("/counts")
async def qa_counts(authorization: Optional[str] = Header(None)):
    """Iter 351 — lightweight test-count payload (no style/a11y/CI
    harvest) for the Overview strip. Admin/founder only."""
    await _require_admin(authorization)
    return _harvest_counts()


@router.get("/status")
async def qa_status(authorization: Optional[str] = Header(None)):
    """Aggregate QA snapshot for /admin/qa. Locked to admin/founder."""
    await _require_admin(authorization)
    t0 = time.time()
    counts    = _harvest_counts()
    style     = _harvest_test_style_ratio()
    a11y      = _harvest_a11y()
    ci_status = await _harvest_ci_status()
    return {
        "generated_at": time.time(),
        "took_ms":      int((time.time() - t0) * 1000),
        "test_counts":  counts,
        "test_style":   style,
        "a11y":         a11y,
        "ci_status":    ci_status,
    }


@router.get("/guard17-breakers")
async def guard17_breakers(authorization: Optional[str] = Header(None)):
    """Guard 17 — per-dependency circuit-breaker state, trip counts
    (7d, from breaker_events) and recent transitions."""
    await _require_admin(authorization)
    from cto_services.db import get_db
    from services.retry_guard import recent_transitions, snapshot_all, trip_counts_7d
    trips_7d: dict = {}
    db = get_db()
    if db is not None:
        trips_7d = await trip_counts_7d(db)
    snap = snapshot_all()
    return {
        "guard": "G17",
        "generated_at": time.time(),
        "breakers": snap,
        "open_deps": [d for d, s in snap.items() if s["state"] == "open"],
        "trips_7d": trips_7d,
        "recent_transitions": recent_transitions(30),
    }


@router.get("/guard18-timeout-audit")
async def guard18_timeout_audit(authorization: Optional[str] = Header(None)):
    """Guard 18 — universal timeout budget. Runs the static audit
    (scripts/timeout_audit.py) at request time; computed from actual
    source on disk, never cached/fabricated."""
    await _require_admin(authorization)
    from scripts.timeout_audit import run_audit
    t0 = time.time()
    result = run_audit()
    return {
        "guard": "G18",
        "generated_at": time.time(),
        "took_ms": int((time.time() - t0) * 1000),
        **result,
    }


@router.get("/guard21-security-scan")
async def guard21_security_scan(authorization: Optional[str] = Header(None)):
    """Guard 21 — OWASP/CWE misconfig + supply-chain static scan,
    computed live from disk (never cached)."""
    await _require_admin(authorization)
    from scripts.g21_security_scan import run_scan
    t0 = time.time()
    result = run_scan()
    return {"guard": "G21", "generated_at": time.time(),
            "took_ms": int((time.time() - t0) * 1000), **result}


@router.get("/guard19-recovery")
async def guard19_recovery(authorization: Optional[str] = Header(None)):
    """Guard 19 — process auto-recovery status: restarts (7d), last
    boot reason, loop trips, heartbeat age, current window boots."""
    await _require_admin(authorization)
    from cto_services.db import get_db
    from services.process_recovery import recovery_status
    return await recovery_status(get_db())


@router.get("/guard20-incidents")
async def guard20_incidents(status: str = "all",
                            authorization: Optional[str] = Header(None)):
    """Guard 20 — automated postmortem/incident log. Chronological,
    filterable (all/open/resolved), with open count + MTTR (30d)."""
    await _require_admin(authorization)
    from cto_services.db import get_db
    from services.incident_log import incident_stats, list_incidents
    db = get_db()
    return {
        "guard": "G20",
        "generated_at": time.time(),
        "stats": await incident_stats(db),
        "incidents": await list_incidents(db, status=status, limit=100),
    }



# ── Iter 366 — Wave 1 + Wave 2 catch-up QA endpoints ──────────────

@router.get("/guard1-route-sweep")
async def guard1_route_sweep(authorization: Optional[str] = Header(None)):
    """G1 — Playwright route smoke sweep last-run snapshot.
    Result rows are written by scripts/g1_route_smoke_sweep.py."""
    await _require_admin(authorization)
    from cto_services.db import get_db
    db = get_db()
    if db is None:
        return {"guard": "G1", "available": False}
    try:
        last = await db.synthetic_checks.find_one(
            {"kind": "g1_route_sweep"},
            sort=[("finished_at", -1)],
        )
        if not last:
            return {"guard": "G1", "available": True,
                    "state": "STALE", "reason": "no_runs_yet"}
        failed = last.get("failed", 0)
        return {
            "guard": "G1",
            "available": True,
            "state": "GREEN" if failed == 0 else "RED",
            "last_finished_at": last.get("finished_at"),
            "total": last.get("total"),
            "failed": failed,
        }
    except Exception as e:
        return {"guard": "G1", "available": False, "error": str(e)[:200]}


@router.get("/guard3-scope-drift")
async def guard3_scope_drift(authorization: Optional[str] = Header(None)):
    """G3 — scope-drift hard-block stats."""
    await _require_admin(authorization)
    from cto_services.db import get_db
    from services.scope_drift_guard import get_scope_block_stats
    return {"guard": "G3", **(await get_scope_block_stats(get_db()))}


@router.get("/guard5-invariants")
async def guard5_invariants(authorization: Optional[str] = Header(None)):
    """G5 — data-invariant snapshot: live counts of dev_users w/ null
    tier, negative tokens_granted, orphan loop_sessions state."""
    await _require_admin(authorization)
    from cto_services.db import get_db
    db = get_db()
    if db is None:
        return {"guard": "G5", "available": False}
    from services.loop_engine import LoopState
    known = {s.value for s in LoopState}
    payload = {"guard": "G5", "available": True, "checks": {}}
    try:
        payload["checks"]["null_tier_users"] = await db.dev_users.count_documents(
            {"$or": [{"tier": {"$exists": False}}, {"tier": None}]}
        )
        payload["checks"]["negative_grants"] = await db.dev_users.count_documents(
            {"tokens_granted": {"$lt": 0}}
        )
        payload["checks"]["orphan_loop_states"] = await db.loop_sessions.count_documents(
            {"state": {"$nin": list(known)}}
        )
        payload["state"] = ("GREEN"
                             if all(v == 0 for v in payload["checks"].values())
                             else "RED")
    except Exception as e:
        payload["available"] = False; payload["error"] = str(e)[:200]
    return payload


@router.get("/guard6-dedup-indexes")
async def guard6_dedup_indexes(authorization: Optional[str] = Header(None)):
    """G6 — DB dedup unique-index report."""
    await _require_admin(authorization)
    from cto_services.db import get_db
    from services.db_indexes import get_dedup_index_report
    rep = await get_dedup_index_report(get_db())
    rep["guard"] = "G6"
    rep["state"] = "GREEN" if rep.get("all_present") else "RED"
    return rep


@router.get("/guard7-payment-recon")
async def guard7_payment_recon(authorization: Optional[str] = Header(None)):
    """G7 — hourly Stripe vs local payment reconciliation summary."""
    await _require_admin(authorization)
    from cto_services.db import get_db
    from services.payment_reconciliation import get_recon_summary
    summary = await get_recon_summary(get_db())
    return {"guard": "G7", **summary}


@router.get("/guard10-founder-alerts")
async def guard10_founder_alerts(authorization: Optional[str] = Header(None)):
    """G10 — founder alert email channel status."""
    await _require_admin(authorization)
    from cto_services.db import get_db
    from services.founder_alerts import _resend_conf
    conf = _resend_conf()
    db = get_db()
    if db is None:
        return {"guard": "G10", "available": False, "enabled": conf["enabled"]}
    last = None
    try:
        last = await db.founder_alert_sends.find_one({}, sort=[("sent_at", -1)])
    except Exception:
        pass
    return {
        "guard": "G10",
        "available": True,
        "enabled": conf["enabled"],
        "state": "GREEN" if conf["enabled"] else "STALE",
        "reason": None if conf["enabled"] else "RESEND_API_KEY_or_FOUNDER_ALERT_EMAIL_missing",
        "last_send_at": last.get("sent_at").isoformat() if last and last.get("sent_at") else None,
        "last_delivered": last.get("delivered") if last else None,
    }


@router.get("/guard12-rollback")
async def guard12_rollback_status(authorization: Optional[str] = Header(None)):
    """G12 — one-click rollback status + candidate list."""
    await _require_admin(authorization)
    from cto_services.db import get_db
    from services.rollback_manager import rollback_status, get_rollback_candidates
    db = get_db()
    status = await rollback_status(db)
    return {"guard": "G12", **status,
            "candidates": await get_rollback_candidates(db, limit=10)}


@router.get("/guard13-cost")
async def guard13_cost(authorization: Optional[str] = Header(None)):
    """G13 — LLM cost breaker current spend vs caps."""
    await _require_admin(authorization)
    from cto_services.db import get_db
    from services.llm_cost_breaker import spend_summary
    summary = await spend_summary(get_db())
    return {"guard": "G13", **summary}


@router.get("/guard14-signup-abuse")
async def guard14_signup_abuse(authorization: Optional[str] = Header(None)):
    """G14 — signup + task abuse counts (7d)."""
    await _require_admin(authorization)
    from cto_services.db import get_db
    from services.signup_guards import get_signup_abuse_stats
    stats = await get_signup_abuse_stats(get_db())
    return {"guard": "G14", **stats,
            "state": "GREEN" if stats.get("available") else "STALE"}


@router.get("/guard15-deps")
async def guard15_deps(authorization: Optional[str] = Header(None)):
    """G15 — dependency vulnerability scan last-run snapshot."""
    await _require_admin(authorization)
    from cto_services.db import get_db
    db = get_db()
    if db is None:
        return {"guard": "G15", "available": False}
    try:
        last = await db.synthetic_checks.find_one(
            {"kind": "g15_dep_scan"}, sort=[("finished_at", -1)],
        )
        if not last:
            return {"guard": "G15", "available": True, "state": "STALE",
                    "reason": "no_runs_yet"}
        return {"guard": "G15", "available": True,
                "state": "GREEN" if last.get("high_critical", 0) == 0 else "RED",
                "last_finished_at": last.get("finished_at"),
                "high_critical": last.get("high_critical", 0),
                "total_findings": last.get("total_findings", 0)}
    except Exception as e:
        return {"guard": "G15", "available": False, "error": str(e)[:200]}


# ── Rollback trigger endpoint (G12 write) ─────────────────────────

class RollbackBody(BaseModel):
    target_sha: str
    reason:     Optional[str] = ""


@router.post("/guard12-rollback/trigger")
async def guard12_trigger_rollback(
    body: RollbackBody,
    bg: BackgroundTasks,
    authorization: Optional[str] = Header(None),
):
    """Founder-gated. Resolves target_sha → loop_outcomes and fires a
    REAL github_api_writer.revert_commit() via loop_rollback (Iter 367
    audit fix — was fake-writing to rollback_trigger with no consumer)."""
    admin = await _require_admin(authorization)
    from cto_services.db import get_db
    from services.rollback_manager import execute_rollback
    return await execute_rollback(
        get_db(),
        target_sha=(body.target_sha or "").strip(),
        triggered_by=admin.get("email") or admin.get("user_id") or "?",
        reason=(body.reason or "").strip(),
        bg=bg,
    )


# ── Iter 367 · Item D — Risk-based routing summary + rules graduation ──


@router.get("/risk-routing/summary")
async def risk_routing_summary(authorization: Optional[str] = Header(None)):
    """Aggregate 30d of risk_scores + current shadow/enforce mode."""
    await _require_admin(authorization)
    from cto_services.db import get_db
    from services.risk_routing import admin_summary
    db = get_db()
    if db is None:
        return {"available": False}
    return {"available": True, **(await admin_summary(db))}


@router.get("/browser-selftest")
async def browser_selftest_recent(
    authorization: Optional[str] = Header(None),
    limit: int = 20,
):
    """Iter 367 · Item E — last N post-ship Playwright smoke runs."""
    await _require_admin(authorization)
    from cto_services.db import get_db
    db = get_db()
    if db is None:
        return {"available": False}
    limit = max(1, min(int(limit or 20), 100))
    rows = []
    total = 0
    failed = 0
    async for r in db.browser_selftest_runs.find({}, {"_id": 0}) \
            .sort("ts", -1).limit(limit):
        rows.append(r)
        total += 1
        if not r.get("ok"):
            failed += 1
    return {"available": True, "count_returned": len(rows),
            "failed_in_window": failed, "runs": rows}


@router.post("/correction-rules/graduate")
async def correction_rules_graduate(
    authorization: Optional[str] = Header(None),
    dry_run: bool = False,
):
    """Iter 367 · Item C — manual trigger for the 14-day auto-graduation
    sweep. Dry-run returns the eligibility list without writing."""
    await _require_admin(authorization)
    from cto_services.db import get_db
    from services.correction_rules import graduate_shadow_eligible_rules
    db = get_db()
    if db is None:
        return {"available": False}
    return await graduate_shadow_eligible_rules(db, dry_run=dry_run)


# ═══════════════════════════════════════════════════════════════════
# Iter 367 · Session 6 · Item 1 — VS Code Marketplace live status
# ═══════════════════════════════════════════════════════════════════
# Real-check the Marketplace HTML/API so /admin can render an honest
# state for the "VS Code extension" badge — instead of the previous
# hardcoded "live" that stayed green even when the founder hadn't
# published yet (Marketplace returns 404 for the identifier).
#
# Same discipline the codebase already applies to the Supabase /
# Vercel silent-no-op fix: NEVER hardcode "live" for a feature whose
# true prod-reachability hasn't been verified.
_VSCODE_MARKETPLACE_CACHE: dict = {"ttl_epoch": 0.0, "payload": None}
_VSCODE_MARKETPLACE_TTL_S = 300  # 5 min


@router.get("/vscode-marketplace-status")
async def vscode_marketplace_status(
    authorization: Optional[str] = Header(None),
):
    """Return the true published state of the AUREM CTO VS Code
    extension on the VS Marketplace.

    Uses a 5-minute in-memory cache so the admin dashboard doesn't
    hammer marketplace.visualstudio.com on every page refresh. On
    Marketplace-side failure (network error, unexpected HTML) we
    return `published=False, reason="check_failed"` — the frontend
    treats that as "grey/pending" so a transient upstream blip
    doesn't lie in the other direction (fake-green)."""
    await _require_admin(authorization)
    import time as _time
    import httpx

    now = _time.time()
    if (_VSCODE_MARKETPLACE_CACHE["payload"]
            and now < _VSCODE_MARKETPLACE_CACHE["ttl_epoch"]):
        return _VSCODE_MARKETPLACE_CACHE["payload"]

    # publisher.name derived from /app/vscode-extension/package.json
    # ("publisher": "auremcto", "name": "aurem-cto"). The Marketplace
    # itemName format is "<publisher>.<name>".
    item_name = "auremcto.aurem-cto"
    url = f"https://marketplace.visualstudio.com/items?itemName={item_name}"

    payload: dict = {
        "item_name":    item_name,
        "url":          url,
        "checked_at":   int(now),
        "cache_ttl_s":  _VSCODE_MARKETPLACE_TTL_S,
    }

    try:
        async with httpx.AsyncClient(
            timeout=8.0,
            follow_redirects=True,
            headers={"User-Agent": "AUREM-CTO-Admin-Health/1.0"},
        ) as c:
            r = await c.get(url)
    except Exception as e:
        payload.update({
            "published":  False,
            "http_code":  None,
            "reason":     "check_failed",
            "detail":     f"network_error: {type(e).__name__}",
        })
        # Do NOT cache network errors — retry sooner.
        _VSCODE_MARKETPLACE_CACHE["ttl_epoch"] = now + 30
        _VSCODE_MARKETPLACE_CACHE["payload"]   = payload
        return payload

    # Marketplace responds 200 for both "published" AND "not found" pages
    # (with different HTML). Real 404 is rare but possible for older
    # deprecated slugs. We look for the "Extension not found" marker
    # in the returned HTML — Marketplace's canonical unpublished-state
    # signal — rather than relying on status code alone.
    body_lower = r.text.lower()
    unpublished_markers = (
        "we couldn't find any extensions matching",
        "extension not found",
        "no results found",
        "the extension you are looking for could not be found",
    )
    is_missing = (
        r.status_code == 404
        or any(m in body_lower for m in unpublished_markers)
    )

    if is_missing:
        payload.update({
            "published": False,
            "http_code": r.status_code,
            "reason":    "not_published",
            "detail":    "Marketplace has no listing for this itemName. "
                         "Ship the .vsix via `vsce publish` after configuring "
                         "the Azure DevOps PAT.",
        })
    elif r.status_code == 200 and item_name.split(".")[1] in body_lower:
        # Sanity: the item slug should appear somewhere on a real
        # extension page. Belt-and-braces against Marketplace serving
        # a generic error page with 200 status.
        payload.update({
            "published": True,
            "http_code": 200,
            "reason":    "published",
            "detail":    "Live on the VS Marketplace.",
        })
    else:
        # Ambiguous response — treat as unpublished to avoid fake-green.
        payload.update({
            "published": False,
            "http_code": r.status_code,
            "reason":    "check_failed",
            "detail":    f"Marketplace returned HTTP {r.status_code} but the "
                         "response didn't include the extension slug.",
        })

    _VSCODE_MARKETPLACE_CACHE["ttl_epoch"] = now + _VSCODE_MARKETPLACE_TTL_S
    _VSCODE_MARKETPLACE_CACHE["payload"]   = payload
    return payload


# ═══════════════════════════════════════════════════════════════════
# QA-Hardening Item 2 — CI-vs-Local drift check
#
# Purpose: during the audit-arc that produced this hardening pass,
# our LOCAL pytest runs reported "4014/0 pass" while the REAL GitHub
# Actions CI was red with 468 failures. Nobody noticed for multiple
# sessions because the two numbers lived on two different surfaces
# and no code cross-referenced them.
#
# This endpoint is the cross-reference. It's cheap:
#   - Local: `_harvest_counts()` (already used by /admin/qa/counts)
#   - CI:    `_harvest_ci_status()` (already used by /admin/qa/status)
# Then we flag `drift_detected: True` when CI's latest conclusion is
# "failure" — because that alone is proof that the local "everything
# passing" claim is wrong. Honest-empty when GITHUB_ACTIONS_TOKEN /
# GITHUB_REPO aren't configured.
# ═══════════════════════════════════════════════════════════════════

@router.get("/ci-vs-local-drift")
async def ci_vs_local_drift(authorization: Optional[str] = Header(None)):
    """QA-Hardening Item 2 — cross-reference local pytest counts vs
    the latest real GitHub Actions quality-gate run so a divergence
    (green locally, red on CI) can't hide for multiple sessions."""
    await _require_admin(authorization)
    t0 = time.time()

    local = _harvest_counts()
    ci    = await _harvest_ci_status()

    ci_available   = bool(ci.get("available"))
    ci_conclusions = [
        j.get("conclusion") for j in (ci.get("jobs") or {}).values()
        if j.get("conclusion")
    ]
    any_ci_failure = any(c in ("failure", "timed_out", "cancelled")
                         for c in ci_conclusions)
    all_ci_success = ci_available and ci_conclusions and all(
        c == "success" for c in ci_conclusions
    )

    # A drift is confirmed if:
    #   (a) CI is available (we can trust it), AND
    #   (b) CI has any failing conclusion, AND
    #   (c) local believes "grand_total_tests > 0" — i.e. we ARE
    #       running tests locally. If local also has 0 tests we
    #       can't call it a drift, just an infra-outage.
    local_has_tests = (local.get("grand_total_tests") or 0) > 0
    drift_detected  = ci_available and any_ci_failure and local_has_tests

    return {
        "generated_at":   time.time(),
        "took_ms":        int((time.time() - t0) * 1000),
        "ci_available":   ci_available,
        "ci_reason":      ci.get("reason"),
        "ci_run_url":     ci.get("workflow_url"),
        "ci_commit_sha":  ci.get("commit_sha"),
        "ci_run_started_at": ci.get("run_started_at"),
        "ci_conclusions": ci_conclusions,
        "ci_all_success": bool(all_ci_success),
        "ci_any_failure": bool(any_ci_failure),
        "local_grand_total_tests": local.get("grand_total_tests"),
        "local_source":            local.get("source"),
        "drift_detected": bool(drift_detected),
        "drift_reason":   (
            "CI has one or more failing jobs while local pytest count > 0. "
            "The local surface is not seeing what CI is seeing — "
            "investigate the failing GitHub Actions run above."
            if drift_detected else None
        ),
    }
