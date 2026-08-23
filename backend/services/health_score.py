"""
services/health_score.py — Codebase Health Score (2026-08-23)

Computes AUREM's OWN codebase health across 9 founder-specified
categories, using ONLY real, evidence-backed sources — never a typed-
in number. Categories without a trustworthy fresh source return
{"status": "unscored"} with a plain-English reason instead of a
fabricated score. See memory/PRD.md "Codebase Health Score" entries
for the full feasibility investigation this implements.

Fixed weights (founder spec, sum to 100):
    security 25 · bug_density 15 · reliability 15 · test_coverage 10
    · code_quality 10 · data_handling 10 · performance 5
    · architecture 5 · devops_infra 5
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

logger = logging.getLogger("health_score")

WEIGHTS = {
    "security":       25,
    "bug_density":     15,
    "reliability":      15,
    "test_coverage":    10,
    "code_quality":     10,
    "data_handling":    10,
    "performance":       5,
    "architecture":      5,
    "devops_infra":      5,
}

# 2026-08-24 · founder-mandated disclaimer for the Reliability/Bug
# Density categories: whether preview-pod dev-restart churn makes this
# score meaningfully different from what production would show is
# UNCERTAIN — NOT confirmed either way until real production data is
# observed. Do not let this become a settled assumption. Surfaced in
# the widget via CategoryBar's `caveat` field.
_RELIABILITY_BUG_DENSITY_CAVEAT = (
    "UNCERTAIN — not yet confirmed against real production data. This "
    "score is built from G17/G19/G20 guard history, which in a "
    "frequently-restarted preview/dev pod can include restart-loop "
    "noise from ordinary hot-reloads and fork sessions. Whether "
    "production (which restarts far less) gives a meaningfully "
    "different number is a plausible but UNVERIFIED theory."
)

_TEST_COVERAGE_STALE_DAYS = 7
_ARCH_REVIEW_STALE_DAYS   = 30
_DRILL_STALE_DAYS         = 14   # drill cadence is ~7d — allow 2x buffer
_PERF_WINDOW_DAYS         = 7
_PERF_MIN_SAMPLES         = 200

# Explicit SLA thresholds for the Performance category (founder spec
# requires these be defined before any score is computed — not
# invented per-request). p95 <= GOOD → 100; degrades linearly to 0 at
# p95 >= BAD. Endpoint-latency samples come from `_health_latency_
# sampler_mw` in main.py.
_PERF_P95_GOOD_MS = 800
_PERF_P95_BAD_MS  = 5000


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _age_days(iso_ts: Optional[str]) -> Optional[float]:
    if not iso_ts:
        return None
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0
    except Exception:
        return None


def _unscored(reason: str, evidence: Optional[dict] = None, live: bool = False) -> dict:
    return {"status": "unscored", "score": None, "reason": reason,
            "evidence": evidence or {}, "live": live, "last_verified": None}


def _scored(score: int, evidence: dict, last_verified: Optional[str], live: bool) -> dict:
    return {"status": "scored", "score": max(0, min(100, round(score))),
            "reason": None, "evidence": evidence, "live": live,
            "last_verified": last_verified}


# ── 1. Security — LIVE via Health Registry guards G21+G16+G3 ──────────
# 2026-08-24 · Inventory Sweep wiring — the old dep-scan-only signal is
# replaced with three already-shipped, already-persisted guard checks
# (services/health_checks.py) that were previously only feeding the
# admin cockpit tiles and never touched health_score.py.
async def score_security(db) -> dict:
    import asyncio
    from scripts.g21_security_scan import run_scan
    from services.health_checks import g16_auth_hardening_raw
    from services.scope_drift_guard import get_scope_block_stats

    g21 = await asyncio.to_thread(run_scan)
    g16 = g16_auth_hardening_raw()
    g3 = await get_scope_block_stats(db) if db is not None else {"available": False}

    unpinned = g21["supply_chain"]["unpinned_count"]
    misconfig = g21["misconfig"]["finding_count"]
    g21_score = max(0, 100 - unpinned * 10 - misconfig * 15)

    g16_findings = g16["findings"]
    g16_score = max(0, 100 - len(g16_findings) * 25)

    g3_score = 100 if g3.get("available") else 0

    score = 0.4 * g21_score + 0.4 * g16_score + 0.2 * g3_score

    dep_scan_doc = None
    if db is not None:
        try:
            dep_scan_doc = await db.synthetic_checks.find_one(
                {"kind": "g15_dep_scan"}, sort=[("finished_at", -1)],
            )
        except Exception:
            dep_scan_doc = None

    evidence = {
        "g21_security_scan": {"unpinned_deps": unpinned,
                              "misconfig_findings": misconfig,
                              "misconfig_details": g21["misconfig"]["findings"],
                              "pass": g21["pass"]},
        "g16_auth_hardening": g16,
        "g3_scope_drift_guard": g3,
        "g15_dep_scan_ci_ingest": {
            "last_finished_at": str(dep_scan_doc.get("finished_at")) if dep_scan_doc else None,
            "note": "Separate CI job (runs+gates on every push, confirmed) — "
                    "result-persistence has been silently dropping since "
                    "production's AUREM_CI_INGEST_TOKEN is unset (Finding B). "
                    "NOT part of this score; informational only.",
        },
        "note": "Live static scan (G21) + auth-hardening posture (G16) + "
                "scope-drift protected-path guard (G3) — see PRD Inventory "
                "Sweep 2026-08-24. Was UNSCORED before this wiring.",
    }
    return _scored(score, evidence, _iso_now(), live=True)


# ── 2. Bug Density — PARTIAL PROXY via G20 incident log (2026-08-24) ──
# NOT a code-level bug count. Counts AUREM's own infra/guard-detected
# incidents (open + resolved-30d + MTTR). A dedicated bug-tracker table
# for actual code defects remains separate future work — see PRD
# Inventory Sweep 2026-08-24.
async def score_bug_density(db) -> dict:
    if db is None:
        return _unscored("database unavailable")
    from services.incident_log import incident_stats
    stats = await incident_stats(db)
    evidence = {
        "g20_incident_log": stats,
        "note": "PARTIAL PROXY ONLY — this is AUREM's own infra/guard-"
                "detected incident count (G20 incident log: open + "
                "resolved-30d + MTTR), NOT a code-level bug count. No "
                "source exists yet for actual bug/defect tracking. A "
                "dedicated bug-tracker table remains separate future work.",
    }
    if not stats.get("total"):
        result = _unscored(
            "No incidents logged yet by the guard system (G20 incident "
            "log is empty) — not a failure signal, just no history yet.",
            evidence,
        )
        result["caveat"] = _RELIABILITY_BUG_DENSITY_CAVEAT
        return result
    open_ = stats.get("open") or 0
    resolved_30d = stats.get("resolved_30d") or 0
    # 2026-08-24 — founder-approved recalibration: resolving incidents is
    # near-neutral (capped), only OPEN incidents drive the score down.
    # Old coefficients floored a healthy production (0 open, 50 resolved)
    # at 0. Reference healthy profile scores 90.
    score = max(0, 100 - open_ * 10 - min(20, resolved_30d * 0.2))
    result = _scored(score, evidence, _iso_now(), live=True)
    result["caveat"] = _RELIABILITY_BUG_DENSITY_CAVEAT
    return result


# ── 3. Reliability — LIVE via Health Registry guards G17+G19+G20 ──────
# 2026-08-24 · Inventory Sweep wiring. Sentry (live, real exception
# capture) was identified as a strong future addition but deliberately
# deferred by founder decision — no new credential dependency added
# until this wiring's signal is evaluated first.
async def score_reliability(db) -> dict:
    if db is None:
        return _unscored("database unavailable")
    from services.retry_guard import snapshot_all, trip_counts_7d
    from services.process_recovery import recovery_status
    from services.incident_log import incident_stats

    breakers = snapshot_all()
    trips_7d = await trip_counts_7d(db)
    g19 = await recovery_status(db)
    g20 = await incident_stats(db)

    open_breakers = [d for d, s in breakers.items() if s["state"] == "open"]
    total_trips_7d = sum(trips_7d.values())
    g17_score = max(0, 100 - len(open_breakers) * 30 - total_trips_7d * 5)

    # 2026-08-23 — BUG FIX: was reading `loop_trips_7d`/`restarts_7d`,
    # which count EVERY boot (including benign dev hot-reload restarts
    # and intentional deploys). Switched to the `*_crash_only` fields
    # (see services/process_recovery.py::recovery_status) so this score
    # reflects actual instability, not preview-pod dev churn. Falls back
    # to the raw fields if crash-only is unavailable (older data shape).
    loop_trips = g19.get("loop_trips_7d_crash_only")
    if loop_trips is None:
        loop_trips = g19.get("loop_trips_7d") or 0
    restarts = g19.get("restarts_7d_crash_only")
    if restarts is None:
        restarts = g19.get("restarts_7d") or 0
    # 2026-08-24 — founder-approved recalibration against real production
    # data (138 restarts/7d, 6 loop-trips/7d, healthy): allow 1 deploy-
    # burst trip/day and 30 restarts/day of normal platform churn before
    # penalising. Old coefficients (trips×50, (restarts−5)×3) floored a
    # healthy production at 0. A true crash-loop day still floors.
    g19_score = max(0, 100 - max(0, loop_trips - 7) * 15 - max(0, restarts - 210) * 0.5)

    open_incidents = g20.get("open") or 0
    resolved_30d = g20.get("resolved_30d") or 0
    g20_score = max(0, 100 - open_incidents * 20 - min(15, resolved_30d * 0.2))

    score = 0.35 * g17_score + 0.30 * g19_score + 0.35 * g20_score

    evidence = {
        "g17_breakers": {"open_deps": open_breakers, "trip_counts_7d": trips_7d,
                         "snapshot": breakers},
        "g19_process_recovery": g19,
        "g20_incidents": g20,
        "note": "Live breaker/process/incident guards (G17/G19/G20) — see "
                "PRD Inventory Sweep 2026-08-24. No HTTP 5xx-rate "
                "aggregation exists yet; Sentry (live, real exception "
                "capture) identified as the natural next addition but "
                "deferred by founder decision for now. 2026-08-23: g19 "
                "now scores on crash-only restarts/loop-trips — see "
                "g19_process_recovery.restarts_7d_crash_only.",
    }
    result = _scored(score, evidence, _iso_now(), live=True)
    result["caveat"] = _RELIABILITY_BUG_DENSITY_CAVEAT
    return result


# ── 4. Test Coverage — feasible with instrumentation (built) ──────────
async def score_test_coverage(db) -> dict:
    if db is None:
        return _unscored("database unavailable")
    doc = await db.health_test_coverage_runs.find_one(
        {}, sort=[("generated_at", -1)],
    )
    if not doc:
        return _unscored(
            "No coverage run yet — trigger one from the widget "
            "(admin: POST /admin/health-score/test-coverage/run).",
        )
    evidence = {
        "generated_at": doc.get("generated_at"),
        "commit_sha": doc.get("commit_sha"),
        "scope": doc.get("scope"),
        "overall_coverage_pct": doc.get("overall_coverage_pct"),
        "critical_modules": doc.get("critical_modules"),
        "test_counts": doc.get("test_counts"),
        "duration_s": doc.get("duration_s"),
    }
    age = _age_days(doc.get("generated_at"))
    if not doc.get("ok") or doc.get("overall_coverage_pct") is None:
        return _unscored(
            f"Last run did not complete cleanly: {doc.get('error') or 'unknown'}",
            evidence,
        )
    if age is not None and age > _TEST_COVERAGE_STALE_DAYS:
        return _unscored(
            f"Stale — last real coverage run was {age:.1f}d ago "
            f"(threshold {_TEST_COVERAGE_STALE_DAYS}d). Run again.",
            evidence,
        )
    coverage_pct = float(doc.get("overall_coverage_pct") or 0)
    critical_frac = float(doc.get("critical_hit_frac") or 0) * 100
    score = 0.5 * coverage_pct + 0.5 * critical_frac
    return _scored(score, evidence, doc.get("generated_at"), live=False)


# ── 5. Code Quality — LIVE, run_health_report() re-scans on every call ─
# 2026-08-23 · BUG FIX — run_health_report() walks + reads every backend
# AND frontend source file (multiple passes: line-count, radon
# complexity, import graph). Calling it synchronously inside an async
# def handler blocks the whole uvicorn event loop for the entire scan
# (10-30s+), starving every other concurrent request AND nginx's own
# /health liveness probe — this is exactly what produced "timeout of
# 40000ms exceeded" on the frontend plus "No response returned" /
# "upstream timed out" errors on unrelated requests. Fixed by running
# it in a worker thread (asyncio.to_thread — same pattern score_security
# already used for g21) so the event loop stays responsive.
async def score_code_quality(db, report: Optional[dict] = None) -> dict:
    if report is None:
        import asyncio
        from services.architecture_health import run_health_report
        report = await asyncio.to_thread(run_health_report)
    bloated = len(report["bloated_files"])
    complex_hits = len(report["complexity_hits"])
    total_files = max(1, report["total_files"])
    penalty = (bloated / total_files * 100) * 1.5 + (complex_hits / total_files * 100) * 0.8
    score = 100 - penalty
    evidence = {
        "total_files": report["total_files"],
        "bloated_files_count": bloated,
        "complexity_hits_count": complex_hits,
        "line_limit": report["line_limit"],
        "cc_limit": report["cc_limit"],
        "generated_at_epoch": report["generated_at"],
        "duration_ms": report["duration_ms"],
    }
    return _scored(score, evidence, datetime.fromtimestamp(
        report["generated_at"], tz=timezone.utc).isoformat(), live=True)


# ── 6. Data Handling — recurring drill snapshots + rollback penalty ────
async def score_data_handling(db) -> dict:
    if db is None:
        return _unscored("database unavailable")
    backup = await db.backup_history.find_one({}, sort=[("created_at", -1)])
    drill = await db.restore_drill_history.find_one({}, sort=[("checked_at", -1)])
    rollback = await _rollback_penalty(db)
    if not backup or not drill:
        return _unscored("No backup or restore-drill history yet.",
                          {"rollback": rollback})
    drill_age = _age_days(drill.get("checked_at"))
    evidence = {
        "latest_backup_status": backup.get("status"),
        "latest_backup_created_at": backup.get("created_at"),
        "latest_drill_ok": drill.get("ok"),
        "latest_drill_checked_at": drill.get("checked_at"),
        "latest_drill_coverage": drill.get("collection_coverage"),
        "rollback": rollback,
    }
    if drill_age is not None and drill_age > _DRILL_STALE_DAYS:
        return _unscored(
            f"Stale — last restore drill was {drill_age:.1f}d ago "
            f"(threshold {_DRILL_STALE_DAYS}d).",
            evidence,
        )
    score = 100.0
    if backup.get("status") != "success":
        score -= 30
    if not drill.get("ok"):
        score -= 40
    else:
        cov = float(drill.get("collection_coverage") or 1.0)
        score -= max(0, (1 - cov) * 100) * 0.5
    score -= rollback["penalty"]
    return _scored(score, evidence, drill.get("checked_at"), live=False)


# ── 7. Performance — general endpoints LIVE-aggregated rolling window ─
async def score_performance(db) -> dict:
    if db is None:
        return _unscored("database unavailable")
    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=_PERF_WINDOW_DAYS)
    raw = await db.health_endpoint_latency.find(
        {"ts": {"$gte": cutoff_dt}}, {"_id": 0, "elapsed_ms": 1, "path": 1},
    ).to_list(20000)
    # 2026-08-24 — self-instrumentation pollution fix (founder-approved):
    # known-long-running admin/self-check endpoints (the health-score
    # computation itself, on-demand coverage runs, QA harvesters, boundary
    # probes, founder-summary LLM generation) were skewing the p95 the
    # score judges — the widget was scoring its own cost. They are
    # excluded from the SLA sample; the unfiltered p95 stays in evidence
    # for honesty.
    # 2026-08-23 — BUG FIX: that exclusion only covered 4 of the many
    # admin-cockpit endpoints. The rest (PAT-inventory audit reports,
    # rollback drills, admin status/BI dashboards, backup drills, etc.)
    # were still counted — real evidence showed `/admin/github-auth/
    # pat-inventory` alone runs a consistent ~4.5s median (48 samples),
    # and `/admin/status/all` spikes to 6-8s on a fraction of calls.
    # These are founder/ops-only tools (gated by admin auth — no real
    # customer ever calls them) and were never meant to represent "how
    # fast the product feels" to an actual user. Generalized the
    # exclusion to the whole `/admin/` namespace instead of a partial
    # hand-picked list, so this can't silently miss the next admin
    # endpoint someone adds. Genuinely customer-facing endpoints
    # (`/chat/send`, `/auth/login`, `/cto/projects/connection-status`,
    # etc.) are deliberately NOT excluded — their latency is real
    # product signal, not noise.
    excluded = ("/api/aurem-dev/admin/",)
    samples = [s for s in raw
               if not str(s.get("path") or "").startswith(excluded)]
    excluded_count = len(raw) - len(samples)
    tool_p95 = None
    try:
        cutoff_iso = cutoff_dt.isoformat()
        tool_docs = await db.ora_skill_usage.find(
            {"ts": {"$gte": cutoff_iso}},
            {"_id": 0, "elapsed_ms": 1},
        ).to_list(20000)
        tool_elapsed = sorted(
            d["elapsed_ms"] for d in tool_docs if isinstance(d.get("elapsed_ms"), (int, float))
        )
        if tool_elapsed:
            tool_p95 = tool_elapsed[int(len(tool_elapsed) * 0.95) - 1] if len(tool_elapsed) > 1 else tool_elapsed[0]
    except Exception:
        pass
    evidence = {
        "window_days": _PERF_WINDOW_DAYS,
        "endpoint_sample_count": len(samples),
        "sla_excluded_sample_count": excluded_count,
        "sla_excluded_prefixes": [p.replace("/api/aurem-dev", "") for p in excluded],
        "tool_call_p95_ms_live": tool_p95,
        "sla_p95_good_ms": _PERF_P95_GOOD_MS,
        "sla_p95_bad_ms": _PERF_P95_BAD_MS,
    }
    # Unfiltered p95 kept in evidence so the exclusion is transparent.
    all_elapsed = sorted(s["elapsed_ms"] for s in raw)
    if all_elapsed:
        evidence["p95_ms_unfiltered"] = all_elapsed[int(len(all_elapsed) * 0.95) - 1]
    if len(samples) < _PERF_MIN_SAMPLES:
        return _unscored(
            f"Insufficient data — {len(samples)} endpoint-latency samples "
            f"collected so far (need {_PERF_MIN_SAMPLES}+ over "
            f"{_PERF_WINDOW_DAYS}d). Instrumentation just started; this "
            f"will fill in over the next few days of real traffic.",
            evidence,
        )
    elapsed = sorted(s["elapsed_ms"] for s in samples)
    p50 = elapsed[int(len(elapsed) * 0.50)]
    p95 = elapsed[int(len(elapsed) * 0.95) - 1]
    p99 = elapsed[int(len(elapsed) * 0.99) - 1]
    evidence.update({"p50_ms": p50, "p95_ms": p95, "p99_ms": p99})
    if p95 <= _PERF_P95_GOOD_MS:
        score = 100
    elif p95 >= _PERF_P95_BAD_MS:
        score = 0
    else:
        score = 100 * (1 - (p95 - _PERF_P95_GOOD_MS) / (_PERF_P95_BAD_MS - _PERF_P95_GOOD_MS))
    return _scored(score, evidence, _iso_now(), live=True)


# ── 8. Architecture — automated half LIVE + qualitative review-log ────
# 2026-08-23 · same event-loop-blocking bug as score_code_quality above
# — offloaded to a worker thread; also accepts a pre-computed `report`
# so get_health_score() can run the (expensive) scan ONCE and share it
# with score_code_quality instead of scanning the whole codebase twice.
async def score_architecture(db, report: Optional[dict] = None) -> dict:
    if report is None:
        import asyncio
        from services.architecture_health import run_health_report
        report = await asyncio.to_thread(run_health_report)
    circ = len(report["circular_imports"])
    bnd = len(report["boundary_violations"])
    auto_score = 100 - (circ * 15 + bnd * 3)
    auto_score = max(0, min(100, auto_score))
    automated_evidence = {
        "circular_imports_count": circ,
        "boundary_violations_count": bnd,
        "god_files_top": report["god_files"][:5],
    }
    review = None
    if db is not None:
        review = await db.architecture_review_log.find_one(
            {}, sort=[("date", -1)],
        )
    review_evidence = None
    qualitative_score = None
    if review:
        review_age = _age_days(review.get("date"))
        review_evidence = {
            "reviewer": review.get("reviewer"),
            "date": review.get("date"),
            "rubric": review.get("rubric"),
            "notes": review.get("notes"),
        }
        if review_age is not None and review_age <= _ARCH_REVIEW_STALE_DAYS:
            rubric = review.get("rubric") or {}
            vals = [v for v in rubric.values() if isinstance(v, (int, float))]
            if vals:
                qualitative_score = sum(vals) / len(vals)
    evidence = {"automated": automated_evidence, "qualitative": review_evidence}
    if qualitative_score is None:
        score = auto_score
        evidence["note"] = ("Qualitative human/AI review half is UNSCORED — "
                             "no fresh review logged (POST "
                             "/admin/health-score/architecture-review). "
                             "Score below is the automated half only.")
    else:
        score = 0.5 * auto_score + 0.5 * qualitative_score
    return _scored(score, evidence, _iso_now(), live=True)


# ── 9. DevOps/Infra — LIVE GitHub Actions pull + rollback penalty ─────
async def score_devops_infra(db) -> dict:
    ci = await _ci_pass_rate_30d()
    rollback = await _rollback_penalty(db) if db is not None else {"penalty": 0, "reason": "db unavailable"}
    evidence = {"ci": ci, "rollback": rollback}
    if not ci.get("available"):
        return _unscored(f"GitHub Actions CI status unavailable — {ci.get('reason')}", evidence)
    if ci.get("total_runs_30d", 0) == 0:
        return _unscored("No ci.yml push runs in the last 30 days.", evidence)
    score = ci["pass_rate"] * 100 - rollback["penalty"]
    return _scored(score, evidence, _iso_now(), live=True)


# ── shared: rollback-unverified penalty (Data Handling + DevOps/Infra) ─
async def _rollback_penalty(db) -> dict:
    # 2026-08-24 — ROOT FIX: this previously read ONLY the legacy
    # rollback_manager ledger (loop_sessions.rollback_status), which is
    # blind to the rollback-v2 system's `rollback_attempts` ledger where
    # all real drill/production rollback evidence now lives. Result: a
    # permanent -25 "zero positive-path rollback evidence" penalty on
    # DevOps/Infra + Data Handling despite verified successful rollbacks.
    # Order: v2 ledger first (authoritative), legacy ledger as fallback.
    try:
        latest_v2 = await db.rollback_attempts.find_one(
            {}, sort=[("timestamp", -1)],
        )
    except Exception as e:
        latest_v2 = None
        logger_reason = f"rollback_attempts lookup failed: {e!r}"
    else:
        logger_reason = None
    if latest_v2:
        if latest_v2.get("result") == "success":
            return {"penalty": 0,
                    "reason": (f"last rollback (v2 ledger) success — "
                               f"mechanism={latest_v2.get('mechanism')}, "
                               f"verified={latest_v2.get('verified')}, "
                               f"at {latest_v2.get('finished_at') or latest_v2.get('timestamp')}")}
        return {"penalty": 30,
                "reason": (f"last rollback attempt (v2 ledger) FAILED: "
                           f"{latest_v2.get('failure_reason')}")}
    try:
        from services.rollback_manager import rollback_status
        st = await rollback_status(db) or {}
    except Exception as e:
        return {"penalty": 20, "reason": logger_reason or f"rollback_status lookup failed: {e!r}"}
    last = st.get("last_rollback")
    if not last:
        return {"penalty": 25, "reason": "zero positive-path rollback evidence on record (both ledgers empty)"}
    if last.get("error") or last.get("status") == "failed":
        return {"penalty": 30, "reason": f"last rollback attempt FAILED: {last.get('error')}"}
    return {"penalty": 0, "reason": f"last rollback {last.get('status')} "
                                     f"({last.get('completed_at') or last.get('started_at')})"}


# ── live GitHub Actions ci.yml pass-rate over 30 days ──────────────────
async def _ci_pass_rate_30d() -> dict:
    from services.http.client import ext_client
    token = os.environ.get("GITHUB_ACTIONS_TOKEN")
    repo = os.environ.get("GITHUB_REPO")
    if not token or not repo:
        return {"available": False, "reason": "GITHUB_ACTIONS_TOKEN/GITHUB_REPO not set"}
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    runs: list[dict] = []
    try:
        async with ext_client("github", timeout=httpx.Timeout(10.0)) as client:
            for page in range(1, 4):
                r = await client.get(
                    f"https://api.github.com/repos/{repo}/actions/workflows/ci.yml/runs"
                    f"?per_page=100&event=push&page={page}",
                    headers=headers,
                )
                if r.status_code != 200:
                    break
                batch = (r.json() or {}).get("workflow_runs") or []
                if not batch:
                    break
                runs.extend(batch)
                try:
                    oldest_dt = datetime.fromisoformat(batch[-1]["created_at"].replace("Z", "+00:00"))
                except Exception:
                    break
                if oldest_dt < cutoff:
                    break
    except (httpx.HTTPError, ValueError) as e:
        return {"available": False, "reason": f"GitHub API call failed: {e!r}"}

    recent = []
    for r in runs:
        try:
            dt = datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
        except Exception:
            continue
        if dt >= cutoff:
            recent.append(r)
    total = len(recent)
    success = sum(1 for r in recent if r.get("conclusion") == "success")
    pass_rate = (success / total) if total else None
    return {
        "available": True,
        "total_runs_30d": total,
        "success_30d": success,
        "pass_rate": pass_rate,
        "sample_recent": [
            {"sha": r.get("head_sha", "")[:7], "conclusion": r.get("conclusion"),
             "created_at": r.get("created_at")}
            for r in recent[:10]
        ],
    }


# ── overall roll-up ─────────────────────────────────────────────────────
async def get_health_score(db) -> dict:
    import asyncio
    from services.architecture_health import run_health_report
    # Run the expensive full-codebase scan ONCE, off the event loop, and
    # share it between code_quality + architecture (previously each ran
    # its own synchronous, on-event-loop copy of this scan — see the
    # 2026-08-23 fix notes on both functions above).
    shared_report = await asyncio.to_thread(run_health_report)
    categories = {
        "security":      await score_security(db),
        "bug_density":   await score_bug_density(db),
        "reliability":   await score_reliability(db),
        "test_coverage": await score_test_coverage(db),
        "code_quality":  await score_code_quality(db, report=shared_report),
        "data_handling": await score_data_handling(db),
        "performance":   await score_performance(db),
        "architecture":  await score_architecture(db, report=shared_report),
        "devops_infra":  await score_devops_infra(db),
    }
    scored_weight = sum(WEIGHTS[k] for k, v in categories.items() if v["status"] == "scored")
    if scored_weight == 0:
        overall = None
    else:
        overall = sum(
            WEIGHTS[k] * v["score"] for k, v in categories.items() if v["status"] == "scored"
        ) / scored_weight
        overall = round(overall)
    unscored = [k for k, v in categories.items() if v["status"] == "unscored"]
    return {
        "generated_at": _iso_now(),
        "weights": WEIGHTS,
        "categories": categories,
        "overall_score": overall,
        "weight_scored_pct": round(scored_weight, 1),
        "weight_unscored_pct": round(100 - scored_weight, 1),
        "unscored_categories": unscored,
    }
