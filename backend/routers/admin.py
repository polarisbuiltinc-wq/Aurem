"""
routers/admin.py — Admin panel endpoints.

All routes require a JWT with `is_admin: true`. The admin user is whoever
matches the email in env `ADMIN_EMAIL`; on login the existing auth router
sets `is_admin=true` for that user.

Mounted under /api/aurem-dev/admin/* by main.py.
"""
# arch: allow-http — Founder-scoped health probes to OpenRouter / GitHub (iter 212m-225)
from __future__ import annotations

import logging
import os
import asyncio
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request, Depends
from pydantic import BaseModel

from cto_services.auth import current_dev, require_admin_dep
from cto_services.db import get_db, require_db
from services.usage import get_usage
# Iter 212m-71 — 60 s TTL cache for the heavy admin aggregations
# (activation funnel, dev_users buckets, etc.). Founders click around
# the admin panel rapidly; without this every click fires 5+ heavy
# aggregations against Mongo.
from services.admin_analytics_cache import (
    cached_agg,
    invalidate as _cache_invalidate,
    mongo_swr_cache,
)

logger = logging.getLogger(__name__)
# Iter 358 — router-level admin gate (defense-in-depth). EVERY route on
# this router is denied to non-founders at the router boundary, so a new
# endpoint added later is protected by default. Individual handlers keep
# their inline `await _require_admin(...)` too (harmless redundancy).
# The one intentionally-public sink (/admin/errors/report) lives on the
# separate, un-gated routers/admin_public.py at the same URL.
router = APIRouter(
    prefix="/admin", tags=["Admin"],
    dependencies=[Depends(require_admin_dep)],
)


async def _require_admin(authorization: Optional[str]) -> dict:
    user = await current_dev(authorization)
    if user.get("is_admin"):
        return user
    # Stale-JWT escape hatch: the JWT might be from before the user was
    # promoted (e.g. founder allow-list added after their last login).
    # Trust the live DB row over the cached claim.
    db = get_db()
    if db is not None:
        row = await db.dev_users.find_one(
            {"user_id": user.get("user_id")},
            {"is_admin": 1, "tier": 1, "email": 1, "is_unlimited": 1},
        )
        if row and (row.get("is_admin") or row.get("tier") == "founder"):
            user["is_admin"] = True
            user["tier"] = row.get("tier") or user.get("tier")
            user["is_unlimited"] = bool(row.get("is_unlimited"))
            user["email"] = row.get("email") or user.get("email")
            return user
    raise HTTPException(403, "Admin access required")


# ── Auth check ──────────────────────────────────────────────────────────


# ── Iter 357 · Guard 8 (partial) — GitHub sync detection ───────────────
# ONE check (services/github_sync.py), surfaced next to the existing
# build badge on Overview; >48h gap escalates into the topup_alerts
# banner. The /admin/qa guards row will read this same endpoint.


# ── Iter 356 — one-time cleanup of E2E test-run chat sessions ──────────
# Our own prod smoke tests (test_iter212m_prod_e2e_founder.py) created
# sessions with the "prod-e2e-" prefix under the founder's account and
# never deleted them → they leaked into the real chat sidebar as
# duplicate-looking debris. This founder-only endpoint removes them.


# ── Iter 210 — Audit feed (CitationGuard + ToolExecutor signals) ─────


# ── Dashboard ──────────────────────────────────────────────────────────


# ─── Iter 212m-153 — Production observability endpoint ────────────────
# Reads LIVE from the existing collections — no mock, no cache.  Mongo
# aggregations do the math in the DB.  Returns a single JSON snapshot
# for the SystemStatsPage admin dashboard.


# ── Iter 212m-192 — Council health (LongCat live-availability) ─────


# Iter 212m-221 — Manual reprobe.  Council A gets stuck in "degraded"
# for 15 min after any transient OpenRouter blip (429, network hiccup)
# because reprobes are on a fixed cadence.  This endpoint lets a
# founder force an immediate re-check from the Admin UI or a curl.


# Iter 212m-221 — Alias for the historical /council-health (hyphen)
# path.  The 20-feature validation agent tried the hyphen form and
# 404-ed; a redirect-alias keeps both spellings alive.


_COUNCIL_REPROBE_LAST_AT: float = 0.0


# ── Users ──────────────────────────────────────────────────────────


# ── Projects ──────────────────────────────────────────────────────────


# ── Tasks ──────────────────────────────────────────────────────────


# ── Token P&L (best-effort from existing data) ─────────────────────────


# ── Iter 65 — Per-agent token consumption with range selector ──────────
# UI calls this with ?range=24h|7d|30d|90d|365d and renders a small
# comparison chart in the Users tab. Goal: Teji can answer "kya
# Claude/Maxx ka extra cost worth hai vs DeepSeek for the same task?"


def _bucket_label(ts: float, granularity: str) -> str:
    """Human-readable bucket label for the chart x-axis."""
    from datetime import datetime, timezone as _tz
    dt = datetime.fromtimestamp(ts, tz=_tz.utc)
    if granularity == "hourly":
        return dt.strftime("%H:00")
    if granularity == "daily":
        return dt.strftime("%b %d")
    if granularity == "weekly":
        return f"wk {dt.strftime('%b %d')}"
    return dt.strftime("%b %Y")


# ── Daily digest ──────────────────────────────────────────────────────


# ── Architecture ──────────────────────────────────────────────────────


# ── Settings ──────────────────────────────────────────────────────────


# ── Iter 40 — ORA Council (Two-Agent Maxx telemetry) ───────────────────


# ── Iter 41 — Project Brain (per-repo persistent memory) ───────────────


# ── Iter 47 — Brain inline-delete endpoints ──


# ── Code surface (live file map for /admin/architecture) ─────────────


# ── Web skill smoke endpoints (Iter 79) ───────────────────────────────
# Direct REST entry points so devs (and pytest) can hit Tavily/Firecrawl
# without going through the LLM tool-call loop. Mounted at
# /api/aurem-dev/admin/skills/*. Admin-only — these calls cost money.


async def _run_skill(name: str, body: _SkillBody, authorization: Optional[str]):
    await _require_admin(authorization)
    from services.web_skills import invoke_web_tool
    args = {k: v for k, v in body.model_dump().items() if v is not None}
    res = await invoke_web_tool(name, args, {})
    if res is None:
        raise HTTPException(404, f"Unknown skill: {name}")
    return res


# ── Persona Quality Score (Iter 124g) ────────────────────────────────
# Surfaces the eval-as-CI history so the admin tile + customers (later
# via a public trust-badge) see real ORA quality over time.


# ── Architecture health report (Iter 86) ──────────────────────────────
# Surfaces the static-analysis health signal in /admin/architecture so
# the next 1952-line file is caught at 320, not 2000. Read-only, admin
# only, no LLM, no network — pure AST + filesystem walk via radon.


# ── Recent commits with SHAs (powers BrainDump "Show diff →" buttons) ─


# ── Mode classifier telemetry — rolling-window 100 docs ───────────────


# ── Product analytics — DAU/WAU/MAU, mode usage, task success, token burn ────


# ── Cache stats — observability for in-memory route cache ─────────────


# ── Feature flags — MongoDB-backed kill switches / canaries ───────────


# ── Brain replay — sandbox "what would ORA say" without committing ───


# ── Iter 48 — Sentry test endpoint ────────────────────────────────────
# Founder-only. Hit this once after adding SENTRY_DSN to prod env to
# confirm the integration works end-to-end. Look at sentry.io's Issues
# tab — you should see the test event within seconds.


# ── Iter 63 — Cache purge & frontend refresh ────────────────────────────


# ── Iter 98 — Live Integration Health Center ───────────────────────────
# Real-time probes of every external dependency. Cached in Mongo so the
# UI is fast; refreshed automatically once daily and on-demand by the
# founder via POST /admin/integrations/refresh.


# ── Iter 212m-17 — Top-up Alerts admin endpoints ────────────────────────


# ── Iter 100 — Live Financial Command Center ───────────────────────────
# Real MongoDB → metrics, editable settings, FX-aware presentation.


    # Return a full re-computed payload so the UI updates atomically.


# ── Iter 102 — Manual trigger for end-of-month overage billing ─────────
# Defensive: founder can run the cron on-demand if the scheduled 1st-of-
# month tick was missed (e.g., backend was down, redeploy in progress).


# ── Vanguard audit log (iter 112) ──────────────────────────────────


# ── DB health (iter 117) ───────────────────────────────────────────


# ── Iter 123b — ORA skill usage analytics ────────────────────────────
# Industry research says <18 skills is optimal. We're at 22. After
# 2 weeks of live traffic this endpoint surfaces which skills are
# pulling weight so the founder can prune confidently.


# ─── Iter 188 — extended overview metrics + new admin surfaces ────────
#
# Single aggregator endpoint that fuels the new metric cards on the
# Overview tab AND the new sidebar sections (MCP Usage, Warm Start,
# Graph Status, Agent Performance, Post-scan Issues, Revenue). One
# round-trip → multiple cards. All counts are scoped to the last
# 24 h / 7 d windows defined inline so the UI can render
# date-stamped chips without doing date math.


# Lightweight list endpoints powering the new sidebar sections.


# ─── Iter 191 — Stripe API key admin panel + live ping ─────────────────
#
# GET  /admin/stripe-config   — current key (masked) + live ping status
# POST /admin/stripe-config   — validate + save a new key, hot-swap at
#                                runtime via payments.set_runtime_stripe_key
#
# The current key resolution prefers (in order): runtime override (set
# at boot from this DB row), env var, .env file. The admin panel always
# writes to MongoDB so changes survive across replica pods AND across
# deploys without touching the secrets manager.


# ═══════════════════════════════════════════════════════════════════════
# Session-fork · 2026-02-09 — /admin/stripe-prices
#
# Multi-worker split-brain fix. Previously the 6 STRIPE_*_PRICE_ID env
# vars were the ONLY source of truth for price IDs, and stale env panel
# values (or partial pod restarts across horizontally-scaled workers)
# caused the exact "checkerboard" the founder saw on prod — identical
# back-to-back checkout requests failed for different plans depending on
# which worker/pod handled the request.
#
# This endpoint pair mirrors the /admin/stripe-config pattern for the
# secret key: persist all 6 price IDs in `admin_settings._id="stripe_price_ids"`
# and hot-swap into the runtime cache used by `services.stripe_client.
# price_id_for()` — which every checkout call now goes through.
# ═══════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════
# 2026-02-10 — /admin/github-app-config
#
# GitHub App credential store (Phase 1.2/1.3 of the PAT → App migration).
# Same DB-override pattern as `/admin/stripe-config` + `/admin/stripe-prices`:
# a single Mongo doc `admin_settings._id="github_app_config"` is the truth,
# every uvicorn worker hydrates it at boot, POST hot-swaps into the
# runtime cache (`services.github_app_config._RUNTIME_GITHUB_APP`).
#
# POST validates by signing a short-lived RS256 JWT with the submitted
# private key and calling `GET https://api.github.com/app` — GitHub
# returns 200 + our App metadata if the App ID + PEM pair is real and
# matches. Refuses to persist unless every field is validated.
#
# The webhook_secret is opaque to us at store time (only HMAC verify
# uses it later inside the future webhook route) — but we still
# require it non-empty here so partial configs cannot land.
# ═══════════════════════════════════════════════════════════════════════


async def _github_app_live_probe(
    app_id: str, private_key_pem: str,
) -> dict:
    """Sign a 60-second App JWT with `private_key_pem` and call
    `GET https://api.github.com/app`. Returns a small summary dict.

    Kept INSIDE routers/admin.py by design — the full GitHub App
    service module (JWT caching, installation-token minting, etc.) is
    Phase 1.1 and doesn't exist yet. This inline probe is the minimum
    needed to validate a paste before persistence.
    """
    import jwt as _jwt          # PyJWT 2.10.0 (already in requirements)
    import httpx as _httpx

    now = int(time.time())
    payload = {
        # GitHub allows ±60s clock skew; use 30s in the past to be safe.
        "iat": now - 30,
        "exp": now + 60,
        "iss": str(app_id).strip(),
    }
    try:
        token = _jwt.encode(payload, private_key_pem, algorithm="RS256")
    except Exception as e:      # noqa: BLE001
        return {"ok": False, "error": f"JWT sign failed: {type(e).__name__}: {e}"[:200]}

    # PyJWT ≥2 returns str; ≤1 returned bytes. Normalise.
    if isinstance(token, bytes):
        token = token.decode("utf-8")

    try:
        async with _httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                "https://api.github.com/app",
                headers={
                    "Authorization":        f"Bearer {token}",
                    "Accept":               "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent":           "aurem-admin-probe",
                },
            )
    except _httpx.RequestError as e:
        return {"ok": False, "error": f"Network error: {type(e).__name__}: {e}"[:200]}

    if r.status_code == 401:
        return {"ok": False, "error": "GitHub returned 401 — App ID and private key do not match."}
    if r.status_code == 404:
        return {"ok": False, "error": "GitHub returned 404 — App ID not found."}
    if r.status_code != 200:
        return {"ok": False, "error": f"GitHub returned HTTP {r.status_code}: {r.text[:120]}"}

    try:
        data = r.json() or {}
    except Exception:
        data = {}
    return {
        "ok":            True,
        "app_id":        data.get("id"),
        "app_slug":      data.get("slug"),
        "app_name":      data.get("name"),
        "owner_login":   ((data.get("owner") or {}).get("login") or ""),
        "owner_type":    ((data.get("owner") or {}).get("type") or ""),
        "html_url":      data.get("html_url"),
        "permissions":   data.get("permissions") or {},
        "events":        data.get("events") or [],
    }


# ═══════════════════════════════════════════════════════════════════════
# 2026-02-10 — /admin/github-app-diagnostics  (Phase 1.1 E2E prover)
#
# Read-only. Exercises services/github_app.py end-to-end against the
# real GitHub API using the currently-configured App credentials:
#
#   1. app_jwt()                — proves RS256 signing + cache work.
#   2. GET /app                 — proves the JWT authenticates.
#   3. GET /app/installations   — lists every installation of our App
#                                 (empty list is a valid pass — no user
#                                 has installed the App yet).
#   4. If any installation exists → get_installation_token() for it,
#      then list_installation_repos() to prove short-lived token minting
#      + repo listing pipeline works.
#
# Nothing is written; no user state is touched. Safe to hit repeatedly.
# Use this endpoint on preview + prod to verify the service before
# building the install router on top of it.
# ═══════════════════════════════════════════════════════════════════════


# ─── Iter 193 — User delete + bulk email offers ────────────────────────
#
# DELETE /admin/users/{user_id}       — hard-delete a user + cascade
# POST   /admin/users/email-offer     — send offer email to N users via
#                                        Resend (one call, N parallel)
#
# Both endpoints require admin. Delete cascades to: cto_sessions,
# cto_projects, cto_tasks, cto_payments, api_keys, post_task_scans
# (all collections that hold a user_id). Founder accounts (allowlisted
# in FOUNDER_EMAILS) are refused — accidental founder wipe would lock
# us out of our own product.


# ─── Iter 196 — Activation funnel insights ─────────────────────────────
#
# GET /admin/insights/activation-funnel
#
# Filters out test/automation accounts (test@, qa-, audit_, e2e-, auto_,
# u_<hex>, @aurem.test) before computing signup → repo → task → paid
# conversion rates. Returns the real-user funnel plus a top-10 recent
# signups breakdown so the founder can scan activation at a glance
# without an SQL shell.


async def _compute_activation_funnel() -> dict:
    """The actual aggregation body.  Pulled out of the route handler
    so the cache wrapper above can call it on a cold miss."""
    db = require_db()

    import re

    # Iter 356 — exclusion rules extracted to services/test_accounts.py
    # (shared with the public marketing stats). Same behaviour as before.
    from services.test_accounts import is_test_email as is_test

    all_users = await db.dev_users.find(
        {}, {"_id": 0, "user_id": 1, "email": 1,
             "tier": 1, "created_at": 1, "github": 1}
    ).to_list(2000)

    real_users = [u for u in all_users if not is_test(u.get("email"))]
    real_ids = {u["user_id"] for u in real_users if u.get("user_id")}

    # Step 2 — connected_github: any GitHub identity recorded on
    # dev_users (OAuth signup OR post-signup Connect via /github-oauth).
    # `github` is the nested doc — presence of `id`, `access_token`, or
    # `login` all count as "connected".
    github_uids = set()
    for u in real_users:
        gh = u.get("github") or {}
        if (gh.get("id") or gh.get("access_token") or gh.get("login")):
            uid = u.get("user_id")
            if uid:
                github_uids.add(uid)

    # Step 3 — added_project: at least one row in cto_projects.
    project_uids = set()
    cursor = db.cto_projects.find({}, {"_id": 0, "user_id": 1})
    async for p in cursor:
        uid = p.get("user_id")
        if uid in real_ids:
            project_uids.add(uid)

    # Step 4 — sent_message: at least one chat session with ≥1 turn.
    session_uids = set()
    cursor = db.chat_sessions.find(
        {"turns.0": {"$exists": True}},  # has at least 1 turn
        {"_id": 0, "user_id": 1},
    )
    async for s in cursor:
        uid = s.get("user_id")
        if uid in real_ids:
            session_uids.add(uid)

    # Step 5 — shipped_code: at least one task in `done` status.
    task_uids = set()
    cursor = db.cto_tasks.find(
        {"status": "done"}, {"_id": 0, "user_id": 1}
    )
    async for t in cursor:
        uid = t.get("user_id")
        if uid in real_ids:
            task_uids.add(uid)

    paying_tiers = {"starter", "pro", "team", "founder"}
    paying = [u for u in real_users
              if (u.get("tier") or "").lower() in paying_tiers]

    def pct(a: int, b: int) -> str:
        return f"{(a / max(b, 1)) * 100:.1f}%"

    def pct_num(a: int, b: int) -> float:
        # Funnel rate is bounded [0, 100]. When the previous step has
        # zero users we return 0 (instead of dividing by 1 and pretending
        # the rate is undefined). Capping at 100 means a "leaky" funnel
        # where users entered a later stage without crossing the prior
        # one displays as a full bar instead of >100 %.
        if b <= 0:
            return 0.0
        return round(min((a / b) * 100, 100), 1)

    # Iter 212m-3 — 5-step activation funnel with per-step conversion
    # rates. Biggest drop-off (largest delta between consecutive steps,
    # as %) is surfaced so the UI can highlight it red.
    n_signup = len(real_users)
    n_github = len(github_uids)
    n_proj   = len(project_uids)
    n_sess   = len(session_uids)
    n_task   = len(task_uids)

    funnel_steps = [
        {"key": "signed_up",       "label": "Signed up",        "count": n_signup},
        {"key": "connected_github","label": "Connected GitHub", "count": n_github},
        {"key": "added_project",   "label": "Added Project",    "count": n_proj},
        {"key": "sent_message",    "label": "Sent Message",     "count": n_sess},
        {"key": "shipped_code",    "label": "Shipped Code",     "count": n_task},
    ]

    # Compute conversion rate from previous step + absolute drop-off
    # (count_prev - count_current). Track which transition has the
    # largest drop so the UI can highlight it.
    biggest_drop_idx = -1
    biggest_drop_n = -1
    for i, step in enumerate(funnel_steps):
        if i == 0:
            step["pct_of_prev"] = 100.0
            step["drop_from_prev"] = 0
            continue
        prev_n = funnel_steps[i-1]["count"]
        cur_n  = step["count"]
        step["pct_of_prev"]    = pct_num(cur_n, prev_n)
        step["drop_from_prev"] = max(prev_n - cur_n, 0)
        if step["drop_from_prev"] > biggest_drop_n:
            biggest_drop_n = step["drop_from_prev"]
            biggest_drop_idx = i

    # Mark the biggest drop (if any users at all dropped off).
    for i, step in enumerate(funnel_steps):
        step["is_biggest_dropoff"] = (i == biggest_drop_idx and biggest_drop_n > 0)

    # Iter 212m-154 — `created_at` is a mix of int (epoch seconds) +
    # datetime in the production dev_users collection (different
    # signup paths wrote different shapes over the years).  Python's
    # sort can't compare those types, which used to crash this
    # endpoint cold-compute path (caught in the same iter when
    # switching to the SWR cache).  Normalise to a float epoch.
    def _ca_epoch(u: dict) -> float:
        v = u.get("created_at")
        if v is None:
            return 0.0
        if isinstance(v, (int, float)):
            return float(v)
        # datetime — including timezone-aware values
        try:
            return float(v.timestamp())
        except Exception:
            return 0.0

    recent = sorted(real_users, key=_ca_epoch, reverse=True)[:10]

    return {
        "ok": True,
        # Iter 212m-3 — 5-step funnel: the canonical shape going
        # forward. `funnel.signed_up/connected_repo/shipped_task/paying`
        # below stays for backward-compat with the old AdminOverview
        # render path.
        "funnel_steps": funnel_steps,
        "biggest_dropoff_idx": biggest_drop_idx if biggest_drop_n > 0 else None,
        "funnel": {
            "signed_up":         n_signup,
            "connected_github":  n_github,
            "added_project":     n_proj,
            "sent_message":      n_sess,
            "shipped_code":      n_task,
            # Backward-compat aliases (Iter 196 schema).
            "connected_repo":    n_proj,
            "shipped_task":      n_task,
            "paying":            len(paying),
        },
        "conversion_rates": {
            # New canonical 5-step rates.
            "signup_to_github":  pct(n_github, n_signup),
            "github_to_project": pct(n_proj,   n_github),
            "project_to_message":pct(n_sess,   n_proj),
            "message_to_ship":   pct(n_task,   n_sess),
            # Legacy aliases (Iter 196).
            "signup_to_repo":    pct(n_proj,   n_signup),
            "repo_to_task":      pct(n_task,   n_proj),
            "task_to_paid":      pct(len(paying), n_task),
        },
        "totals": {
            "all_users":         len(all_users),
            "test_users_excluded": len(all_users) - len(real_users),
        },
        "recent_signups": [
            {
                "email":    u.get("email"),
                "tier":     u.get("tier", "free"),
                "has_repo": u.get("user_id") in project_uids,
                "has_task": u.get("user_id") in task_uids,
                "joined":   u.get("created_at"),
            }
            for u in recent
        ],
    }


# ─── Iter 212m — User-patterns insights ────────────────────────────────
#
# GET /admin/insights/user-patterns
#
# Aggregates the `ora_patterns` collection (populated fire-and-forget
# by `services/ora_learning.py::extract_session_patterns` after every
# chat turn) into a founder-readable snapshot:
#   • top 10 hot_files across all users (file → user count)
#   • top stack_signals by frequency (e.g. fastapi=12, react=9, ...)
#   • total users with patterns
#   • total sessions tracked
#
# Returns empty buckets when the collection is empty / absent (e.g.
# before the first session is mined) so the UI card never crashes.


# ─────────────────────────────────────────────────────────────────────
# Iter 212h — Production Error Reporting
#
# Iter 358 — MOVED to routers/admin_public.py. The /errors/report sink
# must stay UNAUTHENTICATED (logged-out users hit console errors too),
# but this admin router is now gated at the router level
# (dependencies=[Depends(require_admin_dep)]). Keeping the public
# endpoint here would either break error reporting or punch a hole in
# the router gate. It lives on a separate un-gated router at the SAME
# URL (/admin/errors/report), so no frontend change was needed.
# ─────────────────────────────────────────────────────────────────────
from datetime import datetime, timezone


# ── Iter 212m-24 — House Rules (admin-defined ORA prompt) ───────────
# A single global system-prompt-style block that ORA reads BEFORE its
# own persona / tool catalog / project context, with individual
# green/red toggles per target (chat, advisor) and per mode (swift,
# pro, maxx). See services/house_rules.py for storage + injection.


# ── Iter 212m-187 — Robot Guide messages (admin-editable ORA welcome) ─
# Lets the admin change the ORA robot welcome wording shown on the
# /signup and /login windows. Public read lives at GET /auth/robot-guide.


_SCRIPT_RE = re.compile(r"<\s*/?\s*script[^>]*>", re.IGNORECASE)


# Iter 212m-28c — Admin debug endpoint for the repo_context timings
# collection (introduced in Iter 212m-28). Returns the most recent
# 20 samples so operators can spot-check per-phase latencies after a
# deploy without opening Atlas.
#
# NOTE: the router prefix is already "/admin" so this lands at
#   GET /api/aurem-dev/admin/debug/repo_context_timings
# matching the user's spec. ObjectId + datetime are coerced to JSON-
# safe shapes here because raw Mongo docs are NOT JSON-serializable
# (per project rule: no raw documents out of any endpoint).


# Iter 212m-29 — SEO core engine dry-run / commit endpoint (PR-1).
# Admin-only entry point so we can spot-check the engine against a
# real project from the preview without wiring the founder-offer UI
# (PR-2) or the GSC integration (PR-3, deferred) yet.
#
# POST /api/aurem-dev/admin/seo/run
#   body: {
#       project_id: str (required),
#       plan: "swift" | "pro" | "maxx" (default swift),
#       site_url:    str,
#       title:       str,
#       description: str,
#       og_image:    str,
#       author:      str,
#       dry_run:     bool (default True),
#       commit_message: str (default "chore(seo): aurem auto-fix"),
#   }
#
# Returns the SeoOptions result dict (patches summary + commit
# metadata when dry_run=False).


# Iter 212m-71 — admin cache introspection + flush.  Founders use this
# when they ship a data fix and need to see the impact immediately
# instead of waiting for the 60 s TTL to roll over.
from services.admin_analytics_cache import stats as _cache_stats


# ── Iter 212m-234 P0 — Manual re-trigger for the dev_users.created_at
# backfill sweep. The startup task in main.py auto-runs the same
# logic on every backend boot, but this endpoint lets the founder
# verify the fix post-deploy WITHOUT restarting the pod. Idempotent —
# if legacy rows have already been converted the second call is a
# ~1ms no-op.


# ── Iter 309 · Phase 0.2 — Loop metrics prod-impact probe ───────────
# Founder-gated, read-only aggregation over `loop_sessions.state` for
# the last 7 days AND the 7 days before that, so we can eyeball
# whether the FAILED ratio actually shifted after the Phase 0 loop-
# engine rewrite (heartbeats + periodic reaper + MAX_PHASE_RESTARTS
# reduced 2→1). If the ratio is flat, the test-only regressions are
# fixture-shape issues; if it climbed, we have a real prod bug and
# have to jump the queue.
#
# Iter 309-b (founder review 2026-07-26) — expanded to also return
# an explicit `data_source` block (db_name + host-hint + commit_sha
# + env label) so the founder can verify the card is actually
# reading prod Mongo, and a `failed_sample` list classifying each
# failed session as founder / admin / test / user so a 50% failure
# ratio on 14 resolved sessions can be triaged against WHO owned
# those sessions before treating the number as a live user-facing
# signal.


def _email_hint(email: str) -> str:
    """Redacted email — first 3 chars + domain — safe for admin UI.
    Full email would leak PII in screenshots; the hint is enough
    for the founder to recognise their own test accounts and to
    tell one anonymous user from another without collecting the
    full identifier into a log."""
    if not email or "@" not in email:
        return "(no email)"
    local, _, domain = email.partition("@")
    if len(local) <= 3:
        return f"{local}***@{domain}"
    return f"{local[:3]}***@{domain}"


# ── Iter 309 · Pre-Phase-1 — Loop LLM Token Metrics ────────────────
# Founder-gated, read-only aggregation over `ora_chat_usage` rows
# whose `route` starts with `loop.` (loop-originated LLM calls
# tagged by `services/loop_token_ledger.loop_call_context`).  Uses
# the SAME collection + indexes as `/admin/loop-metrics` so we don't
# double-store or double-index.  This is the cost baseline required
# before Phase 1 (Persistent Correction Rules) can measure the
# real cost delta of rules-injection.


# ────────────────────────────────────────────────────────────────────
# Iter 309 · Batch-2 aftermath — Read-only Loop Inspect endpoint.
#
# Founder incident (2026-07-26): a diagnostic-looking F12Badge button
# in the chat surface actually mutated state (sent a chat turn) and
# desynced the running-loop UI mid-live-test. Response: build a
# zero-mutation `/admin/inspect-loop/{loop_id}` view so future
# post-mortem inspection has NO risk of poking the loop.
#
# This endpoint aggregates three read-only sources:
#   1. `loop_sessions.find_one({loop_id})`           — the session doc
#   2. `loop_events.find({loop_id}).sort(-1).limit`  — last N events
#   3. sse_replay_buffer state (per-loop entry)      — Item 6 buffer
#
# Scope-limited per founder directive: NO writes, NO loop_engine.py
# imports, admin-only. Owner-scope also enforced (admin cannot inspect
# an arbitrary user's loop unless they own it OR are founder tier).
# ────────────────────────────────────────────────────────────────────


# ────────────────────────────────────────────────────────────────────
# Iter 309 · Speed Diagnostic — read-only per-phase wall-clock report.
# Founder's speed-diagnostic prompt (2026-07-26): aggregate the last
# N completed loops from loop_sessions + loop_events + ora_chat_usage
# to produce a real per-phase duration breakdown WITHOUT touching any
# runtime code path. Admin-only, zero writes.
# ────────────────────────────────────────────────────────────────────


# ────────────────────────────────────────────────────────────────────
# Iter 311 · Fix C — Scope-drift audit endpoint.
# Read-only aggregation over loop_events.kind == "scope_drift" so the
# founder can answer "did any other recent loops show unrelated file
# expansions?" without hand-inspecting each loop_id. Same pattern as
# /admin/speed-diagnostic. Admin-only, zero writes.
# ────────────────────────────────────────────────────────────────────


# ─── Session G · Cron-death simulation (env-gated, admin-only) ──────
# Founder-only shortcut for VISUAL verification of the /admin/architecture
# supervised-tasks tile: manually kill one supervised cron so the widget
# flips red without waiting for a real crash.
#
# Safety:
#   • Route lives on `/admin/*` — inherits `require_admin_dep` (JWT with
#     is_admin=true OR live-DB founder tier).
#   • Additionally env-gated on `AUREM_TEST_MODE=1` — production pods
#     never set the env, so the endpoint returns 404 on prod. This is a
#     preview/staging-only affordance.
#   • Kill only injects a postmortem row into `_DEAD` — the underlying
#     cron is still running normally. This is a DISPLAY simulation, not
#     a real termination, so a founder click never breaks a cron.


