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


# ── 1. Security — UNSCORED per Finding B (ingest pipeline stale) ──────
async def score_security(db) -> dict:
    doc = None
    if db is not None:
        try:
            doc = await db.synthetic_checks.find_one(
                {"kind": "g15_dep_scan"}, sort=[("finished_at", -1)],
            )
        except Exception:
            doc = None
    evidence = {}
    if doc:
        evidence = {
            "last_dep_scan_finished_at": str(doc.get("finished_at")),
            "total_findings": doc.get("total_findings"),
            "high_critical": doc.get("high_critical"),
            "note": "CI job runs and gates on every push (confirmed), "
                    "but result-persistence has been silently dropping "
                    "since production's AUREM_CI_INGEST_TOKEN is unset "
                    "(HTTP 503) — see PRD Finding B.",
        }
    return _unscored(
        "No trustworthy CURRENT security signal — dependency/secret-scan "
        "ingestion pipeline has been dropping results since ~2026-08-20 "
        "pending a production env var fix (Finding B). IDOR/injection "
        "checks have zero persisted history.",
        evidence,
    )


# ── 2. Bug Density — permanently UNSCORED, no tracker exists ──────────
async def score_bug_density(db) -> dict:
    return _unscored(
        "Permanently unscored — no internal bug-tracker source exists. "
        "Would require repurposing customer-project findings, which "
        "would misrepresent AUREM's own bug rate.",
    )


# ── 3. Reliability — UNSCORED, no 5xx/timeout aggregation exists ──────
async def score_reliability(db) -> dict:
    evidence = {}
    if db is not None:
        try:
            recent = await db.quality_scores.count_documents(
                {"timestamp_ts": {"$gte": time.time() - 7 * 86400}},
            )
            evidence = {"quality_scores_7d_count": recent,
                        "note": "quality_scores tracks LLM response-quality "
                                "heuristics (hallucination/refusal/repetition), "
                                "not 5xx/timeout/silent-failure rate — an "
                                "adjacent but different signal."}
        except Exception:
            pass
    return _unscored(
        "No rolling 5xx-rate / timeout-rate / unhandled-exception "
        "aggregation exists yet.",
        evidence,
    )


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
async def score_code_quality(db) -> dict:
    from services.architecture_health import run_health_report
    report = run_health_report()
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
    samples = await db.health_endpoint_latency.find(
        {"ts": {"$gte": cutoff_dt}}, {"_id": 0, "elapsed_ms": 1},
    ).to_list(20000)
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
        "tool_call_p95_ms_live": tool_p95,
        "sla_p95_good_ms": _PERF_P95_GOOD_MS,
        "sla_p95_bad_ms": _PERF_P95_BAD_MS,
    }
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
async def score_architecture(db) -> dict:
    from services.architecture_health import run_health_report
    report = run_health_report()
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
    try:
        from services.rollback_manager import rollback_status
        st = await rollback_status(db) or {}
    except Exception as e:
        return {"penalty": 20, "reason": f"rollback_status lookup failed: {e!r}"}
    last = st.get("last_rollback")
    if not last:
        return {"penalty": 25, "reason": "zero positive-path rollback evidence on record"}
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
    categories = {
        "security":      await score_security(db),
        "bug_density":   await score_bug_density(db),
        "reliability":   await score_reliability(db),
        "test_coverage": await score_test_coverage(db),
        "code_quality":  await score_code_quality(db),
        "data_handling": await score_data_handling(db),
        "performance":   await score_performance(db),
        "architecture":  await score_architecture(db),
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
