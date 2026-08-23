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
from services.http import ext_client

from routers.admin import _require_admin

# Iter arch-2a — `_harvest_counts` + `_harvest_ci_status` (and their
# helpers) relocated verbatim to services/qa_matrix.py so
# services/health_checks.py can import them without an inverted
# service→router dependency. Re-imported here, unchanged behaviour.
from services.qa_matrix import (                                   # noqa: E402
    _harvest_counts, _harvest_ci_status, _count_matches, _glob_files,
    _TEST_FN_RE_PY, _TEST_FN_RE_JS, _JOB_NAMES_WE_CARE_ABOUT,
)


router = APIRouter(prefix="/admin/qa", tags=["admin-qa"],
                   dependencies=[Depends(require_admin_dep)])  # Iter 358 router-level gate

_APP_ROOT = Path("/app")


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
    if not r.get("ok"):
        # analyze_suite() returns ok=False (no exception) when the tests
        # dir doesn't exist on disk — e.g. Production, where backend/tests
        # is excluded from the Docker image (.dockerignore). Without this
        # check we silently rendered "0 tests analysed" as if it were a
        # real, passing measurement instead of an honest "unavailable".
        return {"available": False, "reason": r.get("reason") or "analyzer returned not-ok"}
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

# `_harvest_ci_status` (+ `_JOB_NAMES_WE_CARE_ABOUT`) is now imported
# from services/qa_matrix.py above — see that module for the
# implementation. Nothing below this point changed.


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


# 2026-08 — Fabrication learning loop. Admin visibility into
# recurring per-project/per-route CitationGuard + ORA-grounding
# fabrication patterns. `caution_active` mirrors the exact threshold
# (>=3 in 30d) the runtime injection uses so this view never claims
# a caution is live when it isn't.
@router.get("/fabrication-patterns")
async def fabrication_patterns(since_days: int = 30,
                               authorization: Optional[str] = Header(None)):
    """Recurring fabrication incident groups (source+project+route+
    signature) in the trailing `since_days` days, most-frequent
    first."""
    await _require_admin(authorization)
    from cto_services.db import get_db
    from services.ora_fix_learning import get_recurring_fabrication_patterns
    db = get_db()
    patterns = await get_recurring_fabrication_patterns(
        db, since_days=since_days, min_count=1, limit=50,
    )
    return {
        "generated_at": time.time(),
        "since_days":   since_days,
        "patterns":     patterns,
        "recurring_count": sum(1 for p in patterns if p["caution_active"]),
    }


# 2026-08-19 — Regression pattern registry. Replaces the markdown-only
# RECURRING_ISSUES.md approach with the same structured-collection +
# admin-visibility pattern as the fabrication-learning loop above.
# Reads PERSISTED verification results (written by
# scripts/verify_regression_patterns.py, same convention as G1/G15/G18)
# — never runs pytest inline inside this request.
@router.get("/regression-patterns")
async def regression_patterns(authorization: Optional[str] = Header(None)):
    await _require_admin(authorization)
    from cto_services.db import get_db
    from services.ora_fix_learning import list_regression_patterns
    db = get_db()
    patterns = await list_regression_patterns(db)
    return {
        "generated_at": time.time(),
        "patterns": patterns,
        "with_real_test": sum(1 for p in patterns if p.get("test_ref")),
        "total": len(patterns),
        "doc_ref": "/app/memory/RECURRING_ISSUES.md",
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


@router.get("/guard16-auth-hardening")
async def guard16_auth_hardening(authorization: Optional[str] = Header(None)):
    """G16 — Auth-hardening posture. Feb 2026, closes the "assumed
    21, actually 15" scope gap surfaced by the cockpit build.

    Checks 3 real invariants:
      1. JWT_SECRET is set AND is ≥32 chars (rejects default/short
         values that make JWT forging trivial).
      2. bcrypt cost factor (rounds) ≥ 12 — industry-safe against
         offline hash-cracking.
      3. Brute-force lockout wired: LOGIN_FAIL_LIMIT ∈ [1,10] AND
         LOGIN_LOCKOUT_MIN ≥ 5.
    """
    await _require_admin(authorization)
    import os
    import bcrypt
    from cto_services.auth import JWT_SECRET as _JWT
    findings: list[str] = []

    if not _JWT:
        findings.append("JWT_SECRET is unset")
    elif len(_JWT) < 32:
        findings.append(f"JWT_SECRET too short ({len(_JWT)} chars, need ≥32)")

    salt = bcrypt.gensalt().decode()
    try:
        rounds = int(salt[4:6])
    except (ValueError, IndexError):
        rounds = 0
    if rounds < 12:
        findings.append(f"bcrypt rounds too low ({rounds}, need ≥12)")

    try:
        fail_limit = int(os.getenv("LOGIN_FAIL_LIMIT", "5"))
    except ValueError:
        fail_limit = 999
    try:
        lockout_min = int(os.getenv("LOGIN_LOCKOUT_MIN", "15"))
    except ValueError:
        lockout_min = 0
    if fail_limit <= 0 or fail_limit > 10:
        findings.append(f"LOGIN_FAIL_LIMIT out of safe range: {fail_limit}")
    if lockout_min < 5:
        findings.append(f"LOGIN_LOCKOUT_MIN too short: {lockout_min}min (need ≥5)")

    return {
        "guard":     "G16",
        "available": True,
        "state":     "GREEN" if not findings else "RED",
        "checks": {
            "jwt_secret_present": bool(_JWT),
            "jwt_secret_length":  len(_JWT or ""),
            "bcrypt_rounds":      rounds,
            "login_fail_limit":   fail_limit,
            "login_lockout_min":  lockout_min,
        },
        "findings": findings,
    }


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
    """Return the true published state of the AUREM VS Code
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
        async with ext_client(
            "vscode_marketplace",
            timeout=httpx.Timeout(8.0),
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
    # Drift means "CI saw a REAL problem that local didn't". A
    # manually-cancelled run is neither — code-review LOW #5. Only
    # `failure` and `timed_out` count as divergence signal.
    any_ci_failure = any(c in ("failure", "timed_out")
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
