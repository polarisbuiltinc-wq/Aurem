"""
services/health_checks.py — Registered check adapters (Feb 2026)

Each function here calls a REAL existing mechanism (guard handler,
integration client, or infra probe) and maps its raw response into
the standard 3-state contract defined in services/health_registry.py.

Adapters MUST NOT reimplement business logic — they only translate.
If a guard's payload shape changes, the adapter is the single place
that breaks and gets fixed. This keeps the aggregator drift-free.

Proof-of-pattern batch (Feb 2026): G1, G7, G10, G17 first — see
tests/test_health_registry_adapters.py. Once proven, the remaining
11 guards + 6 integrations + infra checks are added below.
"""
from __future__ import annotations

import logging

from services.health_registry import (
    register_check,
    result_green,
    result_red,
    result_gray,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# GUARD ADAPTERS — proof-of-pattern batch (G1, G7, G10, G17)
# ═══════════════════════════════════════════════════════════════

async def _check_g1_route_sweep() -> dict:
    """G1 — Playwright route smoke sweep last-run snapshot.
    Real source: `synthetic_checks` collection, kind=g1_route_sweep.

    gray  — no runs yet (sweep never scheduled)
    green — last run had 0 failed routes
    red   — last run had >0 failed routes
    """
    from cto_services.db import get_db
    db = get_db()
    if db is None:
        return result_gray("database unavailable")
    last = await db.synthetic_checks.find_one(
        {"kind": "g1_route_sweep"},
        sort=[("finished_at", -1)],
    )
    if not last:
        return result_gray("no g1 runs yet — schedule scripts/g1_route_smoke_sweep.py")
    failed = int(last.get("failed") or 0)
    total  = int(last.get("total") or 0)
    if failed == 0:
        return result_green(f"{total} routes swept · 0 failures")
    return result_red(f"{failed}/{total} routes failed on last sweep")


async def _check_g7_payment_recon() -> dict:
    """G7 — hourly Stripe vs local payment reconciliation.
    Real source: services.payment_reconciliation.get_recon_summary().

    gray  — no recon ever run (last_run=None) OR Stripe not configured
    green — last run had no drift
    red   — drift detected between Stripe and local ledger
    """
    from cto_services.db import get_db
    from services.payment_reconciliation import get_recon_summary
    db = get_db()
    if db is None:
        return result_gray("database unavailable")
    summary = await get_recon_summary(db) or {}
    last_run = summary.get("last_run")
    if not last_run:
        return result_gray("no recon runs yet — Stripe may not be configured")
    drift = summary.get("drift_events") or summary.get("drift") or 0
    if drift and int(drift) > 0:
        return result_red(f"{drift} drift events detected on last recon")
    return result_green(f"recon clean · last run {last_run}")


async def _check_g10_founder_alerts() -> dict:
    """G10 — founder alert email channel wiring.
    Real source: services.founder_alerts._resend_conf() + last send row.

    gray  — RESEND_API_KEY / FOUNDER_ALERT_EMAIL missing (not enabled)
    green — enabled AND last send delivered=True (or never needed to send)
    red   — enabled but last actual send failed to deliver
    """
    from cto_services.db import get_db
    from services.founder_alerts import _resend_conf
    conf = _resend_conf()
    if not conf.get("enabled"):
        return result_gray("RESEND_API_KEY or FOUNDER_ALERT_EMAIL not set")
    db = get_db()
    if db is None:
        return result_gray("database unavailable (channel enabled but unverifiable)")
    last = await db.founder_alert_sends.find_one({}, sort=[("sent_at", -1)])
    if not last:
        return result_green("channel enabled · no alerts needed yet")
    delivered = last.get("delivered")
    if delivered is False:
        return result_red(f"last alert failed to deliver at {last.get('sent_at')}")
    return result_green(f"channel enabled · last delivered at {last.get('sent_at')}")


async def _check_g17_breakers() -> dict:
    """G17 — per-dependency circuit-breaker snapshot.
    Real source: services.retry_guard.snapshot_all().

    gray  — no breakers registered (unusual; means retry_guard didn't
            load, worth flagging)
    green — no breakers in `open` state
    red   — one or more breakers open
    """
    from services.retry_guard import snapshot_all
    snap = snapshot_all() or {}
    if not snap:
        return result_gray("no breakers registered")
    open_deps = [d for d, s in snap.items() if s.get("state") == "open"]
    if not open_deps:
        return result_green(f"all {len(snap)} breakers closed")
    return result_red(f"{len(open_deps)} breaker(s) open: {', '.join(open_deps[:3])}")


# ═══════════════════════════════════════════════════════════════
# REGISTRATION (call at module import so `services.health_checks`
# side-effects populate the registry).
# ═══════════════════════════════════════════════════════════════

register_check("g1_route_sweep",   "G1 · Route Sweep",        "guard", _check_g1_route_sweep)
register_check("g7_payment_recon", "G7 · Payment Recon",      "guard", _check_g7_payment_recon)
register_check("g10_founder_alerts", "G10 · Founder Alerts",  "guard", _check_g10_founder_alerts)
register_check("g17_breakers",     "G17 · Circuit Breakers",  "guard", _check_g17_breakers)


# ═══════════════════════════════════════════════════════════════
# REMAINING GUARD ADAPTERS (G3, G5, G6, G12, G13, G14, G15,
# G18, G19, G20, G21). Each calls the underlying service used by
# the existing /admin/qa/guardN endpoint and maps to 3-state.
# ═══════════════════════════════════════════════════════════════

async def _check_g3_scope_drift() -> dict:
    from cto_services.db import get_db
    from services.scope_drift_guard import get_scope_block_stats
    db = get_db()
    if db is None:
        return result_gray("database unavailable")
    stats = await get_scope_block_stats(db) or {}
    total = int(stats.get("blocks_7d") or stats.get("total") or 0)
    # Scope-drift blocks are protective events (guard doing its job),
    # not failures. Green means the guard is armed. Red would only
    # apply if the guard itself reported unavailable/broken.
    if stats.get("available") is False:
        return result_red(f"guard unavailable: {stats.get('reason','unknown')}")
    return result_green(f"guard armed · {total} blocks in last 7d")


async def _check_g5_invariants() -> dict:
    """G5 — inline invariant probes (no separate service module).
    Mirrors the /guard5-invariants endpoint body."""
    from cto_services.db import get_db
    from services.loop_engine import LoopState
    db = get_db()
    if db is None:
        return result_gray("database unavailable")
    known = {s.value for s in LoopState}
    null_tier = await db.dev_users.count_documents(
        {"$or": [{"tier": {"$exists": False}}, {"tier": None}]}
    )
    neg_grants = await db.dev_users.count_documents({"tokens_granted": {"$lt": 0}})
    orphan_states = await db.loop_sessions.count_documents(
        {"state": {"$nin": list(known)}}
    )
    violations = null_tier + neg_grants + orphan_states
    if violations > 0:
        return result_red(
            f"invariant violations: null_tier={null_tier}, "
            f"neg_grants={neg_grants}, orphan_states={orphan_states}"
        )
    return result_green("all 3 data invariants pass")


async def _check_g6_dedup_indexes() -> dict:
    from cto_services.db import get_db
    from services.db_indexes import get_dedup_index_report
    db = get_db()
    if db is None:
        return result_gray("database unavailable")
    rep = await get_dedup_index_report(db) or {}
    if rep.get("all_present"):
        return result_green(f"{len(rep.get('required', []))} dedup indexes present")
    missing = rep.get("missing") or []
    return result_red(f"{len(missing)} dedup indexes missing")


async def _check_g12_rollback() -> dict:
    from cto_services.db import get_db
    from services.rollback_manager import rollback_status
    db = get_db()
    if db is None:
        return result_gray("database unavailable")
    st = await rollback_status(db) or {}
    if st.get("last_drill_at"):
        return result_green(f"last rollback drill: {st.get('last_drill_at')}")
    return result_gray("no rollback drill executed yet")


async def _check_g13_cost() -> dict:
    from cto_services.db import get_db
    from services.llm_cost_breaker import spend_summary
    db = get_db()
    if db is None:
        return result_gray("database unavailable")
    st = await spend_summary(db) or {}
    if st.get("tripped"):
        return result_red(f"cost breaker TRIPPED — {st.get('reason','budget-exceeded')}")
    spend = st.get("spend_today_usd") or st.get("spend_today") or 0
    try:
        spend_str = f"${float(spend):.2f}"
    except (TypeError, ValueError):
        spend_str = str(spend)
    return result_green(f"cost breaker armed · spend today {spend_str}")


async def _check_g14_signup_abuse() -> dict:
    from cto_services.db import get_db
    from services.signup_guards import get_signup_abuse_stats
    db = get_db()
    if db is None:
        return result_gray("database unavailable")
    st = await get_signup_abuse_stats(db) or {}
    if not st.get("available", True):
        return result_gray("signup abuse stats unavailable")
    flagged = int(st.get("flagged_24h") or st.get("suspicious_signups_7d") or 0)
    if flagged > 20:
        return result_red(f"{flagged} suspicious signups in window — investigate")
    return result_green(f"{flagged} flagged (within tolerance)")


async def _check_g15_deps() -> dict:
    """G15 — inline dep-scan probe (no separate service module).
    Mirrors the /guard15-deps endpoint body: reads
    `synthetic_checks` where `kind=g15_dep_scan`."""
    from cto_services.db import get_db
    db = get_db()
    if db is None:
        return result_gray("database unavailable")
    last = await db.synthetic_checks.find_one(
        {"kind": "g15_dep_scan"}, sort=[("finished_at", -1)]
    )
    if not last:
        return result_gray("no dep-scan runs yet")
    high_crit = int(last.get("high_critical") or 0)
    total = int(last.get("total_findings") or 0)
    if high_crit > 0:
        return result_red(f"{high_crit} high/critical CVEs in pinned deps")
    return result_green(f"clean · {total} findings, 0 high/critical")


async def _check_g18_timeout_audit() -> dict:
    from scripts.timeout_audit import run_audit
    r = run_audit() or {}
    unbounded = int(r.get("unbounded_count") or r.get("violations") or 0)
    if unbounded > 0:
        return result_red(f"{unbounded} unbounded I/O sites detected")
    return result_green("all I/O sites have a timeout budget")


async def _check_g19_recovery() -> dict:
    from cto_services.db import get_db
    from services.process_recovery import recovery_status
    db = get_db()
    if db is None:
        return result_gray("database unavailable")
    snap = await recovery_status(db) or {}
    restarts_7d = int(snap.get("restarts_7d") or 0)
    loop_trips  = int(snap.get("loop_trips") or 0)
    if loop_trips > 0:
        return result_red(f"{loop_trips} process-loop trips detected — investigate")
    return result_green(f"{restarts_7d} controlled restarts in last 7d")


async def _check_g20_incidents() -> dict:
    from cto_services.db import get_db
    db = get_db()
    if db is None:
        return result_gray("database unavailable")
    open_count = await db.incidents.count_documents({"status": "open"})
    if open_count > 0:
        return result_red(f"{open_count} open incident(s) — review /admin/qa")
    return result_green("no open incidents")


async def _check_g21_security_scan() -> dict:
    from cto_services.db import get_db
    db = get_db()
    if db is None:
        return result_gray("database unavailable")
    last = await db.vanguard_findings.find_one(
        {"scanner": "trufflehog"}, sort=[("created_at", -1)]
    )
    if not last:
        return result_gray("no trufflehog scan ingested yet")
    verified = int(last.get("verified") or 0)
    if verified > 0:
        return result_red(f"{verified} verified secret(s) — rotate immediately")
    return result_green(f"trufflehog clean · last scan {last.get('created_at')}")


# ═══════════════════════════════════════════════════════════════
# INTEGRATION ADAPTERS — reuse external_services_registry.is_configured()
# and (where safe) a lightweight probe. is_configured=False → gray.
# ═══════════════════════════════════════════════════════════════

def _make_integration_check(display_id: str):
    """Return a check_fn bound to a specific integration_id from
    the external_services_registry. Config-only (no live probe) —
    keeps aggregator fast. A missing env is `gray` (not red) per
    3-state discipline."""
    async def _check() -> dict:
        from services.external_services_registry import REGISTRY, is_configured
        svc = next((s for s in REGISTRY if s.integration_id == display_id), None)
        if svc is None:
            return result_red(f"integration {display_id!r} not in registry")
        if not is_configured(svc):
            missing = [k for k in svc.env_keys if not __import__("os").getenv(k)]
            return result_gray(f"missing env: {', '.join(missing) or 'unknown'}")
        return result_green(f"{svc.display_name} configured · {len(svc.env_keys)} env key(s) set")
    return _check


register_check("g3_scope_drift",   "G3 · Scope Drift",        "guard", _check_g3_scope_drift)
register_check("g5_invariants",    "G5 · Data Invariants",    "guard", _check_g5_invariants)
register_check("g6_dedup_indexes", "G6 · Dedup Indexes",      "guard", _check_g6_dedup_indexes)
register_check("g12_rollback",     "G12 · Rollback",          "guard", _check_g12_rollback)
register_check("g13_cost",         "G13 · Cost Breaker",      "guard", _check_g13_cost)
register_check("g14_signup_abuse", "G14 · Signup Abuse",      "guard", _check_g14_signup_abuse)
register_check("g15_deps",         "G15 · Dependency CVE",    "guard", _check_g15_deps)
register_check("g18_timeout",      "G18 · Timeout Audit",     "guard", _check_g18_timeout_audit)
register_check("g19_recovery",     "G19 · Auto-Recovery",     "guard", _check_g19_recovery)
register_check("g20_incidents",    "G20 · Open Incidents",    "guard", _check_g20_incidents)
register_check("g21_security",     "G21 · Security Scan",     "guard", _check_g21_security_scan)

# Integrations (6 — matches founder's cockpit-tree count):
register_check("int_stripe",       "Stripe",                  "integration", _make_integration_check("stripe"))
register_check("int_vercel",       "Vercel Deploy Hook",      "integration", _make_integration_check("vercel_deploy_hook"))
register_check("int_aurem_org",    "AUREM Org (GitHub)",      "integration", _make_integration_check("aurem_org_github"))
register_check("int_tavily",       "Tavily Web Search",       "integration", _make_integration_check("tavily (web search)"))
register_check("int_firecrawl",    "Firecrawl Web Scrape",    "integration", _make_integration_check("firecrawl (web scrape)"))
register_check("int_github_oauth", "GitHub OAuth (Sign-in)",  "integration", _make_integration_check("github_oauth"))


# ═══════════════════════════════════════════════════════════════
# INFRA ADAPTERS
# ═══════════════════════════════════════════════════════════════

async def _check_db_reachable() -> dict:
    from cto_services.db import get_db
    db = get_db()
    if db is None:
        return result_red("database handle is None — Mongo unreachable")
    # Ping via a cheap collstats call. If Motor errors, aggregator
    # wrapper catches and returns red.
    await db.command("ping")
    return result_green(f"Mongo reachable · db={db.name}")


async def _check_supervised_tasks() -> dict:
    from services.supervised_tasks import health_snapshot
    snap = health_snapshot() or {}
    total = int(snap.get("supervised_count") or 0)
    dead_list = snap.get("dead") or []
    if total == 0:
        return result_gray("no supervised tasks registered")
    if dead_list:
        names = ", ".join(d.get("name", "?") for d in dead_list[:3])
        return result_red(f"{len(dead_list)}/{total} supervised task(s) died · {names}")
    return result_green(f"{total}/{total} supervised tasks alive")


async def _check_ci_vs_local_drift() -> dict:
    """Reuses admin_qa.ci_vs_local_drift logic (no HTTP self-call).
    gray when GITHUB_ACTIONS_TOKEN missing (honest); red when CI
    has failing jobs; green otherwise."""
    from routers.admin_qa import _harvest_counts, _harvest_ci_status
    local = _harvest_counts() or {}
    ci    = await _harvest_ci_status()
    if not ci.get("available"):
        return result_gray(f"CI unreachable — {ci.get('reason','GITHUB_ACTIONS_TOKEN unset')}")
    conclusions = [
        j.get("conclusion") for j in (ci.get("jobs") or {}).values()
        if j.get("conclusion")
    ]
    any_fail = any(c in ("failure", "timed_out") for c in conclusions)
    local_has_tests = (local.get("grand_total_tests") or 0) > 0
    if any_fail and local_has_tests:
        return result_red("CI has failing jobs while local pytest > 0 — drift")
    return result_green(f"CI ↔ local aligned · {len(conclusions)} jobs")


register_check("infra_db",              "Database reachable",         "infra", _check_db_reachable)
register_check("infra_supervised",      "Supervised tasks",           "infra", _check_supervised_tasks)
register_check("infra_ci_vs_local",     "CI ↔ local drift",           "infra", _check_ci_vs_local_drift)
