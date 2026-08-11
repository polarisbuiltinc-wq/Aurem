"""admin_ops_config.py — Ops surfaces: cache, feature-flags, integrations, sentry, settings, GitHub-App config.

Extracted from routers/admin.py during Phase 2 architecture split (2026-02-11).
Contains 26 handler(s)/helper(s):

  GET/POST /admin/settings
  GET  /admin/cache/stats             POST /admin/cache/purge
  GET  /admin/cache/analytics-stats   POST /admin/cache/analytics-invalidate
  GET  /admin/feature-flags           POST /admin/feature-flags/{flag}/toggle
  POST /admin/feature-flags
  GET  /admin/integrations/health     POST /admin/integrations/refresh
  POST /admin/sentry/test             GET  /admin/db-health
  GET/PUT /admin/house-rules          GET/PUT /admin/robot-guide
  GET/POST /admin/github-app-config   GET /admin/github-app-diagnostics
  GET  /admin/debug/repo_context_timings
  POST /admin/dev/kill-supervised-task/{name}
  POST /admin/dev/clear-supervised-postmortem/{name}

Every handler + helper is COPIED VERBATIM from the pre-split admin.py.
"""
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
    prefix="/admin", tags=["Admin-ops-config"],
    dependencies=[Depends(require_admin_dep)],
)

from routers._admin_common import _require_admin  # noqa: E402
# 2026-02-11 · Phase 2 split fix — helper still lives in pre-split
# admin.py stub. Re-import so handlers resolve it at runtime.
from routers.admin import _github_app_live_probe  # noqa: E402


@router.get("/settings")
async def get_settings(authorization: Optional[str] = Header(None)):
    await _require_admin(authorization)
    db = require_db()
    doc = await db.cto_settings.find_one({"_id": "global"}, {"_id": 0})
    return doc or {
        "token_limits": {"free": 10000, "pro": 50000, "team": 100000},
        "pricing": {"free": 0, "pro": 29, "team": 99},
    }


@router.post("/settings")
async def save_settings(
    request: Request,
    authorization: Optional[str] = Header(None),
):
    await _require_admin(authorization)
    db = require_db()
    body = await request.json()
    body["updated_at"] = time.time()
    await db.cto_settings.update_one(
        {"_id": "global"}, {"$set": body}, upsert=True,
    )
    return {"ok": True}


@router.get("/cache/stats")
async def cache_stats(
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """Iter 140 — in-memory route cache observability. Returns the
    configured routes and currently-live entries with their remaining
    TTLs. Helps validate cache hit rates from the admin dashboard
    without scraping logs."""
    await _require_admin(authorization)
    from services.route_cache import _CACHE, ROUTE_CONFIG
    import time as _t
    now = _t.time()
    entries = []
    for key, (expires_at, status, body, _ctype) in list(_CACHE.items()):
        ttl_remaining = max(0, expires_at - now)
        entries.append({
            "key": key[:80],
            "ttl_remaining_s": round(ttl_remaining, 1),
            "size_bytes": len(body),
            "status": status,
        })
    return {
        "ok": True,
        "cached_routes": len(ROUTE_CONFIG),
        "live_entries": len(entries),
        "entries": sorted(entries, key=lambda x: -x["ttl_remaining_s"]),
    }


@router.get("/feature-flags")
async def list_feature_flags(
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """List all feature flags and their status."""
    await _require_admin(authorization)
    from services.feature_flags import get_all_flags as _get_all_flags
    flags = await _get_all_flags()
    return {"ok": True, "flags": flags}


@router.post("/feature-flags/{flag}/toggle")
async def toggle_feature_flag(
    flag: str,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """Flip a feature flag's `enabled` boolean."""
    await _require_admin(authorization)
    db = require_db()
    doc = await db.feature_flags.find_one({"flag": flag})
    if not doc:
        raise HTTPException(404, f"Flag '{flag}' not found")
    new_state = not doc.get("enabled", False)
    await db.feature_flags.update_one(
        {"flag": flag}, {"$set": {"enabled": new_state}}
    )
    from services.feature_flags import invalidate_cache as _ff_invalidate
    _ff_invalidate()
    return {"ok": True, "flag": flag, "enabled": new_state}


@router.post("/feature-flags")
async def create_feature_flag(
    body: dict,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """Create or update a feature flag (idempotent upsert)."""
    await _require_admin(authorization)
    db = require_db()
    flag = (body.get("flag") or "").strip()
    if not flag:
        raise HTTPException(400, "flag name required")
    await db.feature_flags.update_one(
        {"flag": flag},
        {"$set": {
            "flag": flag,
            "enabled": bool(body.get("enabled", False)),
            "tier_allowlist": list(body.get("tier_allowlist") or []),
            "user_allowlist": list(body.get("user_allowlist") or []),
            "description": str(body.get("description") or ""),
        }},
        upsert=True,
    )
    from services.feature_flags import invalidate_cache as _ff_invalidate
    _ff_invalidate()
    return {"ok": True, "flag": flag}


@router.post("/sentry/test")
async def sentry_test(authorization: Optional[str] = Header(None)):
    await _require_admin(authorization)
    import os as _os
    if not _os.environ.get("SENTRY_DSN", "").strip():
        return {"ok": False, "active": False,
                "message": "SENTRY_DSN not set — add it to backend env and restart."}
    try:
        import sentry_sdk
        sentry_sdk.capture_message(
            "AUREM Sentry test — if you see this, monitoring is live ✓",
            level="info",
        )
        # Also fire a captured exception
        try:
            raise RuntimeError("AUREM Sentry test exception (intentional)")
        except RuntimeError as _re:
            sentry_sdk.capture_exception(_re)
        return {"ok": True, "active": True,
                "message": "Sent test event + exception to Sentry. Check the Issues tab."}
    except Exception as e:
        return {"ok": False, "active": False, "error": str(e)}


@router.post("/cache/purge")
async def purge_caches(
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """Real, fully-wired cache purge — admin-only.

    Clears:
      1. Cloudflare edge cache  (if CLOUDFLARE_API_TOKEN + ZONE_ID set)
      2. In-memory `lru_cache` of skill_context_injector
      3. MongoDB TTL caches: repo_context_cache, github_issues_cache,
         codebase_index_cache (collections used as caches; safe to drop
         rows — they self-rebuild on next read).

    Returns a structured report so the UI can show exactly what landed.
    The frontend then performs its own client-side step (unregister SWs,
    `caches.delete()`, hard reload).
    """
    import os
    import httpx
    from services.http import ext_client
    await _require_admin(authorization)

    report = {
        "cloudflare": {"status": "skipped", "detail": "CLOUDFLARE_API_TOKEN / ZONE_ID not set"},
        "lru_cache": {"status": "skipped", "detail": ""},
        "mongo_caches": {},
    }

    # 1. Cloudflare edge purge — only if env configured
    cf_token = os.environ.get("CLOUDFLARE_API_TOKEN")
    cf_zone = os.environ.get("CLOUDFLARE_ZONE_ID")
    if cf_token and cf_zone:
        try:
            async with ext_client("cloudflare", timeout=httpx.Timeout(10.0)) as client:
                resp = await client.post(
                    f"https://api.cloudflare.com/client/v4/zones/{cf_zone}/purge_cache",
                    headers={
                        "Authorization": f"Bearer {cf_token}",
                        "Content-Type": "application/json",
                    },
                    json={"purge_everything": True},
                )
                cf_body = resp.json()
                if resp.status_code == 200 and cf_body.get("success"):
                    report["cloudflare"] = {
                        "status": "ok",
                        "detail": "Purge_everything fired — edge cache will refill on next request.",
                    }
                else:
                    report["cloudflare"] = {
                        "status": "error",
                        "detail": str(cf_body.get("errors") or cf_body)[:300],
                    }
        except Exception as e:
            report["cloudflare"] = {"status": "error", "detail": str(e)[:300]}

    # 2. In-memory lru_cache on skill injector
    try:
        from services.skill_context_injector import _load_skill
        _load_skill.cache_clear()
        report["lru_cache"] = {
            "status": "ok",
            "detail": "skill_context_injector._load_skill lru_cache cleared",
        }
    except Exception as e:
        report["lru_cache"] = {"status": "error", "detail": str(e)[:300]}

    # 3. Mongo TTL caches — drop docs so the next read repopulates
    db = get_db()
    if db is not None:
        for coll_name in (
            "repo_context_cache",
            "github_issues_cache",
            "codebase_index_cache",
        ):
            try:
                r = await db[coll_name].delete_many({})
                report["mongo_caches"][coll_name] = {
                    "status": "ok", "deleted": r.deleted_count,
                }
            except Exception as e:
                report["mongo_caches"][coll_name] = {
                    "status": "error", "detail": str(e)[:200],
                }
    else:
        report["mongo_caches"] = {"status": "skipped", "detail": "no DB"}

    return {"ok": True, "report": report}


@router.get("/integrations/health")
async def integrations_health(
    authorization: Optional[str] = Header(None),
):
    """Return the latest cached snapshot of every integration probe.
    If no snapshot exists yet, run all probes inline (slow first hit)."""
    await _require_admin(authorization)
    db = require_db()
    snap = await db.integration_health.find_one(
        {"_id": "latest"}, {"_id": 0}
    )
    if not snap:
        # Cold start — probe immediately so the founder sees real data.
        from services.integration_health import run_all_probes, summary_counts
        results = await run_all_probes()
        snap = {
            "results":      results,
            "summary":      summary_counts(results),
            "generated_at": time.time(),
            "trigger":      "cold_start",
        }
        await db.integration_health.update_one(
            {"_id": "latest"},
            {"$set": snap},
            upsert=True,
        )
    return snap


@router.post("/integrations/refresh")
async def integrations_refresh(
    authorization: Optional[str] = Header(None),
):
    """Force-re-probe every integration NOW. Founder-only — each call
    actually hits all the external APIs."""
    await _require_admin(authorization)
    from services.integration_health import run_all_probes, summary_counts
    results = await run_all_probes()
    snap = {
        "results":      results,
        "summary":      summary_counts(results),
        "generated_at": time.time(),
        "trigger":      "manual",
    }
    db = require_db()
    await db.integration_health.update_one(
        {"_id": "latest"},
        {"$set": snap},
        upsert=True,
    )
    # Iter 212m-17 — process new top-up alerts inline so the founder
    # gets an immediate email when a refresh surfaces a broken probe
    # (instead of waiting for the next daily cron at 06:00 UTC).
    try:
        from services.topup_alerts import process_snapshot
        alert_result = await process_snapshot(db, snap)
        snap["alerts_processed"] = alert_result
    except Exception as e:
        logger.warning(f"topup_alerts on manual refresh: {e!r}")
    # Iter 212m-16 — return the fresh snapshot so the admin UI can
    # render the result without a second roundtrip to /integrations/health.
    return snap


@router.get("/db-health")
async def db_health(authorization: Optional[str] = Header(None)):
    """Live DB health snapshot — verifies all required collections are
    materialised + the documented indexes exist. Reads the bootstrap
    state from the last init_prod_collections() run + re-checks the
    current collection set right now."""
    await _require_admin(authorization)
    from scripts.init_prod_collections import (
        get_last_bootstrap, _BOOTSTRAP_SPEC,
    )
    db = get_db()
    required = [name for name, _ in _BOOTSTRAP_SPEC]
    present: list[str] = []
    missing: list[str] = list(required)
    indexes_ok = True
    if db is not None:
        try:
            existing = set(await db.list_collection_names())
            present = [n for n in required if n in existing]
            missing = [n for n in required if n not in existing]
            # Spot-check: each required collection should have at least
            # one secondary index (beyond the default _id one). If any
            # collection has only _id, the boot script didn't run cleanly.
            for name, idx_specs in _BOOTSTRAP_SPEC:
                if name not in existing or not idx_specs:
                    continue
                idx = await db[name].list_indexes().to_list(length=50)
                if len(idx) < 1 + 1:  # _id_ + at least one user index
                    indexes_ok = False
                    break
        except Exception as e:
            return {
                "ok": False,
                "collections_present": 0,
                "last_bootstrap": None,
                "missing": required,
                "indexes_ok": False,
                "error": str(e)[:200],
            }
    last = get_last_bootstrap()
    return {
        "ok": True,
        "collections_present": len(present),
        "collections_expected": len(required),
        "last_bootstrap":        (last or {}).get("ts"),
        "last_bootstrap_summary": {
            "created":      (last or {}).get("created", []),
            "indexed_count": len((last or {}).get("indexed", [])),
            "errors":       (last or {}).get("errors", []),
        },
        "missing":    missing,
        "indexes_ok": indexes_ok and not missing,
    }

    db = get_db()
    from services.vanguard_audit import recent_blocks
    return {"rows": await recent_blocks(db, limit=max(1, min(limit, 200)))}


@router.get("/github-app-config")
async def admin_get_github_app_config(
    authorization: Optional[str] = Header(None),
):
    """Return the current GitHub App config state.

    NEVER echoes the raw private key or webhook secret back to the
    client — only presence flags + last-6 fingerprint of the key and
    an on-demand live probe (`GET /app`) so the admin knows whether
    the current credentials still authenticate against GitHub.
    """
    await _require_admin(authorization)
    from services.github_app_config import (
        get_runtime_github_app_config, REQUIRED_FIELDS,
    )

    db = require_db()

    # Defensive re-hydration in case this worker missed the boot-time
    # load (fresh pod, race with a prior POST on another worker).
    row = None
    try:
        row = await db.admin_settings.find_one({"_id": "github_app_config"})
        if row:
            from services.github_app_config import set_runtime_github_app_config
            set_runtime_github_app_config({
                f: row.get(f) for f in REQUIRED_FIELDS
            })
    except Exception as e:
        logger.warning("admin/github-app-config GET: DB lookup failed: %r", e)

    runtime = get_runtime_github_app_config()
    configured = bool(runtime)

    # Presence-only summary (no secrets).
    summary = {
        "configured":      configured,
        "app_id":          runtime.get("app_id") or "",
        "app_slug":        runtime.get("app_slug") or "",
        "install_url":     (
            f"https://github.com/apps/{runtime['app_slug']}/installations/new"
            if runtime.get("app_slug") else ""
        ),
        "private_key_last6":   (runtime.get("private_key") or "").strip()[-6:]
                                if runtime.get("private_key") else "",
        "webhook_secret_last4": (runtime.get("webhook_secret") or "").strip()[-4:]
                                 if runtime.get("webhook_secret") else "",
        "last_updated": (row or {}).get("updated_at"),
        "updated_by":   (row or {}).get("updated_by"),
    }

    # Live probe — sign a JWT with the stored private key, call
    # `GET /app` on GitHub. Any RS256/PEM issue surfaces as "invalid".
    if configured:
        try:
            live = await _github_app_live_probe(
                runtime["app_id"], runtime["private_key"],
            )
            summary["live"] = live
        except Exception as e:                                  # noqa: BLE001
            summary["live"] = {
                "ok": False,
                "error": f"{type(e).__name__}: {e}"[:200],
            }
    else:
        summary["live"] = {"ok": False, "error": "not configured"}

    return summary


class GitHubAppConfigBody(BaseModel):
    """Every field is required and validated together. Partial writes
    are refused so the runtime cache never lands in a half-set state."""
    app_id:         str
    app_slug:       str
    private_key:    str  # full PEM, `-----BEGIN … -----END …-----` block
    webhook_secret: str


@router.post("/github-app-config")
async def admin_set_github_app_config(
    body: GitHubAppConfigBody,
    authorization: Optional[str] = Header(None),
):
    """Validate + persist the GitHub App credentials. Every field is
    checked non-empty; the PEM is functionally tested against GitHub
    (`GET /app` with a freshly-minted App JWT) BEFORE the row is
    written. Hot-swaps into the runtime cache on success.

    The webhook_secret is stored verbatim but not probed here (only
    the future webhook handler exercises it). It is required non-empty
    so `is_configured()` becomes a single boolean truth.
    """
    user = await _require_admin(authorization)

    from services.github_app_config import (
        set_runtime_github_app_config, REQUIRED_FIELDS,
    )

    # Normalise
    app_id         = (body.app_id or "").strip()
    app_slug       = (body.app_slug or "").strip().lower()
    private_key    = (body.private_key or "").strip()
    webhook_secret = (body.webhook_secret or "").strip()

    # Basic shape checks BEFORE the live probe so obvious mistakes
    # fail fast without hitting GitHub.
    errors: dict = {}
    if not app_id.isdigit():
        errors["app_id"] = "App ID must be numeric (see GitHub App settings header)."
    if not app_slug or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,38}", app_slug):
        errors["app_slug"] = ("App slug must be the lowercase kebab-case name "
                              "from the App's public URL (github.com/apps/<slug>).")
    if "BEGIN" not in private_key or "PRIVATE KEY" not in private_key:
        errors["private_key"] = "Private key must be a full PEM block (BEGIN/END PRIVATE KEY)."
    if len(webhook_secret) < 8:
        errors["webhook_secret"] = "Webhook secret must be at least 8 characters."

    if errors:
        raise HTTPException(400, {
            "error":   "invalid_input",
            "details": errors,
            "message": ("Fix the highlighted field(s) and retry. All four fields "
                        "are required — partial configs are refused so the "
                        "GitHub App integration cannot half-enable."),
        })

    # Live probe against GitHub — proves the PEM matches this App ID.
    probe = await _github_app_live_probe(app_id, private_key)
    if not probe.get("ok"):
        raise HTTPException(400, {
            "error":   "github_probe_failed",
            "message": ("Refusing to persist — GitHub rejected the App JWT signed "
                        "with this private key. " + probe.get("error", "")),
        })
    # Extra sanity: the ID returned by GitHub must match the pasted ID.
    if str(probe.get("app_id") or "") != app_id:
        raise HTTPException(400, {
            "error":   "app_id_mismatch",
            "message": (f"Pasted App ID `{app_id}` but GitHub returned App ID "
                        f"`{probe.get('app_id')}` for this private key — the key "
                        "belongs to a different App."),
        })
    # Slug sanity — GitHub is canonical for the slug; correct silently
    # if the admin pasted a variant with different casing/hyphens.
    canonical_slug = (probe.get("app_slug") or "").lower()
    if canonical_slug and canonical_slug != app_slug:
        logger.info(
            "admin/github-app-config: correcting slug '%s' → '%s' (from GitHub /app)",
            app_slug, canonical_slug,
        )
        app_slug = canonical_slug

    # Persist. All-or-nothing on the doc — a POST always overwrites the
    # full 4-field set (mirrors the Stripe pattern).
    db = require_db()
    doc = {
        "_id":            "github_app_config",
        "app_id":         app_id,
        "app_slug":       app_slug,
        "private_key":    private_key,
        "webhook_secret": webhook_secret,
        "updated_at":     time.time(),
        "updated_by":     user.get("email") or user.get("user_id"),
    }
    try:
        await db.admin_settings.update_one(
            {"_id": "github_app_config"}, {"$set": doc}, upsert=True,
        )
    except Exception as e:
        logger.error("admin/github-app-config POST: DB save failed: %r", e)
        raise HTTPException(500, f"DB persistence failed: {e}")

    # Hot-swap into runtime cache on THIS worker. Other workers pick
    # up the same doc on their next request that reads it (or on
    # their next boot via main.py lifespan hydrator).
    set_runtime_github_app_config({f: doc.get(f) for f in REQUIRED_FIELDS})
    logger.info(
        "🐙 GitHub App config hot-swapped by admin=%s app_id=%s slug=%s",
        user.get("email"), app_id, app_slug,
    )

    return {
        "ok":         True,
        "message":    ("GitHub App credentials validated against GitHub and saved. "
                       "Every future request in this worker uses these; other "
                       "workers hydrate on their next boot or admin GET."),
        "app_id":     app_id,
        "app_slug":   app_slug,
        "install_url": f"https://github.com/apps/{app_slug}/installations/new",
        "live":       probe,
    }


@router.get("/github-app-diagnostics")
async def admin_github_app_diagnostics(
    authorization: Optional[str] = Header(None),
):
    """End-to-end health probe of services/github_app.py against real
    GitHub. Read-only; safe to spam. Returns a per-step summary so a
    partial failure clearly points at the exact hop that broke."""
    await _require_admin(authorization)

    from services import github_app as _ga
    from services.github_app_config import is_configured

    result: dict = {
        "configured":       is_configured(),
        "steps":            [],
        "install_url":      None,
        "installations":    [],
        "sample_repos":     None,
    }

    if not result["configured"]:
        result["steps"].append({
            "step":  "config_check",
            "ok":    False,
            "error": "GitHub App is not configured yet — paste credentials via the admin card first.",
        })
        return result

    # STEP 1 — mint App JWT (RS256 sign)
    try:
        jwt_token = _ga.app_jwt()
        result["steps"].append({
            "step":       "app_jwt",
            "ok":         True,
            "jwt_prefix": jwt_token[:20] + "…",
            "jwt_length": len(jwt_token),
        })
    except Exception as e:                                       # noqa: BLE001
        result["steps"].append({
            "step":  "app_jwt",
            "ok":    False,
            "error": f"{type(e).__name__}: {e}",
        })
        return result

    # STEP 2 — install URL build
    try:
        result["install_url"] = _ga.install_url()
        result["steps"].append({"step": "install_url", "ok": True})
    except Exception as e:                                       # noqa: BLE001
        result["steps"].append({
            "step":  "install_url",
            "ok":    False,
            "error": f"{type(e).__name__}: {e}",
        })

    # STEP 3 — list installations via App JWT
    try:
        installs = await _ga.list_installations()
        summary = [
            {
                "id":             inst.get("id"),
                "account_login":  ((inst.get("account") or {}).get("login") or ""),
                "account_type":   ((inst.get("account") or {}).get("type") or ""),
                "target_type":    inst.get("target_type"),
                "repository_selection": inst.get("repository_selection"),
                "created_at":     inst.get("created_at"),
                "suspended_at":   inst.get("suspended_at"),
            }
            for inst in installs
        ]
        result["installations"] = summary
        result["steps"].append({
            "step":  "list_installations",
            "ok":    True,
            "count": len(installs),
        })
    except Exception as e:                                       # noqa: BLE001
        result["steps"].append({
            "step":  "list_installations",
            "ok":    False,
            "error": f"{type(e).__name__}: {e}",
        })
        return result

    # STEP 4 — if any installation exists, prove token minting + repo listing
    if result["installations"]:
        first_id = result["installations"][0]["id"]
        try:
            token, expires_at = await _ga.get_installation_token(int(first_id))
            result["steps"].append({
                "step":              "get_installation_token",
                "ok":                True,
                "installation_id":   first_id,
                "token_prefix":      token[:8] + "…",
                "expires_at_epoch":  expires_at,
                "expires_in_seconds": int(expires_at - time.time()),
            })
        except Exception as e:                                   # noqa: BLE001
            result["steps"].append({
                "step":  "get_installation_token",
                "ok":    False,
                "error": f"{type(e).__name__}: {e}",
            })
            return result

        try:
            repos = await _ga.list_installation_repos(int(first_id))
            result["sample_repos"] = [
                {
                    "id":            r.get("id"),
                    "full_name":     r.get("full_name"),
                    "private":       r.get("private"),
                    "default_branch": r.get("default_branch"),
                }
                for r in repos[:5]                               # cap payload
            ]
            result["steps"].append({
                "step":  "list_installation_repos",
                "ok":    True,
                "count": len(repos),
            })
        except Exception as e:                                   # noqa: BLE001
            result["steps"].append({
                "step":  "list_installation_repos",
                "ok":    False,
                "error": f"{type(e).__name__}: {e}",
            })
    else:
        result["steps"].append({
            "step":  "get_installation_token",
            "ok":    "skipped",
            "note":  "No installations yet — install the App on any account to exercise this step.",
        })

    result["all_green"] = all(
        (s.get("ok") is True or s.get("ok") == "skipped")
        for s in result["steps"]
    )
    return result


class HouseRulesPayload(BaseModel):
    prompt:           str = ""
    enabled_chat:     bool = False
    enabled_advisor:  bool = False
    enabled_swift:    bool = False
    enabled_pro:      bool = False
    enabled_maxx:     bool = False
    # Iter 212m-53 — Ask Advisor dedicated slot (separate prompt +
    # LLM selector). See services/house_rules.py::ADVISOR_LLM_CHOICES
    # for the valid llm ids; out-of-range values get clamped to
    # `glm-5.2` server-side so the admin UI can't poison the field.
    advisor_prompt:          str = ""
    advisor_prompt_enabled:  bool = False
    advisor_llm:             str = "glm-5.2"
    # Iter 212m-171 — CHAT prompt slot + model / temperature / max_tokens
    # overrides for both chat and advisor.
    chat_prompt:             str = ""
    chat_prompt_enabled:     bool = False
    chat_model:              str = ""      # empty → orchestrator picks
    chat_temperature:        float = 0.2
    chat_max_tokens:         int = 4000
    advisor_temperature:     float = 0.2
    advisor_max_tokens:      int = 2500


@router.get("/house-rules")
async def admin_house_rules_read(authorization: Optional[str] = Header(None)):
    """Return the current house-rules doc + LLM choice list. Admin-only."""
    await _require_admin(authorization)
    from services.house_rules import get_house_rules_doc, ADVISOR_LLM_CHOICES
    doc = await get_house_rules_doc()
    # Mongo's _id is fine to return as the literal "singleton" string.
    # datetime → iso for JSON.
    ua = doc.get("updated_at")
    if hasattr(ua, "isoformat"):
        doc = {**doc, "updated_at": ua.isoformat()}
    # Iter 212m-53 — bundle the LLM choices so the admin UI doesn't
    # have to hard-code them. Single source of truth = house_rules.py.
    return {**doc, "advisor_llm_choices": ADVISOR_LLM_CHOICES}


@router.put("/house-rules")
async def admin_house_rules_write(
    payload: HouseRulesPayload,
    authorization: Optional[str] = Header(None),
):
    """Persist the house-rules doc. Admin-only.

    Validates and writes the singleton document; invalidates the
    in-process cache so the next chat turn picks up the new rules.
    """
    admin = await _require_admin(authorization)
    from services.house_rules import set_house_rules_doc
    try:
        doc = await set_house_rules_doc(
            payload.model_dump(), by_user_id=admin.get("user_id") or "",
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return {"ok": True, "house_rules": doc}


class RobotGuidePayload(BaseModel):
    signup_message: str = ""
    login_message:  str = ""


@router.get("/robot-guide")
async def admin_robot_guide_read(authorization: Optional[str] = Header(None)):
    """Return the current robot-guide messages. Admin-only."""
    await _require_admin(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    doc = await db.ui_settings.find_one({"_id": "robot_guide"}, {"_id": 0}) or {}
    ua = doc.get("updated_at")
    if hasattr(ua, "isoformat"):
        doc = {**doc, "updated_at": ua.isoformat()}
    return {
        "signup_message": doc.get("signup_message") or "",
        "login_message":  doc.get("login_message") or "",
        "updated_at":     doc.get("updated_at"),
        "updated_by":     doc.get("updated_by") or "",
    }


@router.put("/robot-guide")
async def admin_robot_guide_write(
    payload: RobotGuidePayload,
    authorization: Optional[str] = Header(None),
):
    """Persist the robot-guide messages. Admin-only.

    Basic HTML (<strong>, <em>, ora-arrow span) is allowed since the
    message renders through the RobotGuide component; <script> tags are
    stripped defensively.
    """
    admin = await _require_admin(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    signup_msg = _SCRIPT_RE.sub("", payload.signup_message).strip()[:600]
    login_msg  = _SCRIPT_RE.sub("", payload.login_message).strip()[:600]
    now = datetime.now(timezone.utc)
    await db.ui_settings.update_one(
        {"_id": "robot_guide"},
        {"$set": {
            "signup_message": signup_msg,
            "login_message":  login_msg,
            "updated_at":     now,
            "updated_by":     admin.get("user_id") or "",
        }},
        upsert=True,
    )
    return {
        "ok": True,
        "signup_message": signup_msg,
        "login_message":  login_msg,
        "updated_at":     now.isoformat(),
    }


@router.get("/debug/repo_context_timings")
async def admin_debug_repo_context_timings(
    authorization: Optional[str] = Header(None),
):
    """Return the 20 most recent `repo_context_timings` samples.

    Admin-only. JSON-safe: `_id` becomes a string, `ts` becomes an ISO
    string. Surfaces the per-phase millisecond breakdown
    (`tree_fetch_ms`, `rescue_ms`, `inline_ms`, `cache_hit_ms`,
    `total_ms`) plus `files_inlined` and `cold_path` so operators can
    see at a glance whether the parallel fetch fix is working.
    """
    await _require_admin(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    docs = await db.repo_context_timings.find().sort("ts", -1).limit(20).to_list(20)
    timings: list[dict] = []
    for d in docs:
        d["_id"] = str(d.get("_id")) if d.get("_id") is not None else None
        ts = d.get("ts")
        if hasattr(ts, "isoformat"):
            d["ts"] = ts.isoformat()
        timings.append(d)
    return {"timings": timings, "count": len(timings)}


@router.get("/cache/analytics-stats")
async def admin_cache_stats(
    authorization: Optional[str] = Header(None),
):
    await _require_admin(authorization)
    return _cache_stats()


@router.post("/cache/analytics-invalidate")
async def admin_cache_invalidate(
    body: dict, authorization: Optional[str] = Header(None),
):
    """`{"key": "admin:activation_funnel:v1"}` flushes one key.
    `{}` flushes everything.  Always returns the number dropped."""
    await _require_admin(authorization)
    key = (body or {}).get("key")
    dropped = _cache_invalidate(key)
    return {"ok": True, "dropped": dropped, "key": key}


@router.post("/dev/kill-supervised-task/{name}")
async def kill_supervised_task_for_ui_test(
    name: str,
    reason: str = "exception",
    authorization: Optional[str] = Header(None),
):
    """Simulate a dead supervised task for UI verification.

    Args:
        name:   The supervised-task name (must match one currently in
                `supervised_tasks._SUPERVISED`). Case-sensitive.
        reason: `"exception"` (default) or `"silent_completion"` —
                shapes the postmortem row so the widget renders the
                matching red-state description.

    Returns:
        `{"ok": True, "simulated_dead": <postmortem_row>}` on success.
        `404` if the task name isn't currently supervised.
        `404` if `AUREM_TEST_MODE` is not `"1"` (endpoint effectively
        does not exist on production).
    """
    if os.getenv("AUREM_TEST_MODE") != "1":
        # Behave as 404 on production so the endpoint's existence itself
        # is invisible.  A founder poking around a prod admin panel gets
        # a clean "endpoint not found" rather than a "you don't have the
        # right env var" leak.
        raise HTTPException(404, "Not Found")

    from services import supervised_tasks
    import time as _time
    from datetime import datetime, timezone

    # Confirm the caller is actually the founder (extra belt-and-braces
    # even though require_admin_dep already gated the route).
    _ = await _require_admin(authorization)

    reg = supervised_tasks._SUPERVISED
    dead = supervised_tasks._DEAD
    if name not in reg and name not in dead:
        raise HTTPException(
            404,
            f"Supervised task '{name}' not found. "
            f"Current supervised: {sorted(reg.keys())}",
        )

    now = _time.time()
    iso = datetime.fromtimestamp(now, tz=timezone.utc).isoformat()
    if reason == "silent_completion":
        row = {
            "died_at":     now,
            "died_at_iso": iso,
            "reason":      "silent_completion",
            "exc_type":    None,
            "exc_msg":     None,
            "simulated":   True,
        }
    else:
        row = {
            "died_at":     now,
            "died_at_iso": iso,
            "reason":      "exception",
            "exc_type":    "SimulatedTaskDeath",
            "exc_msg":     f"Founder-triggered UI simulation for task '{name}'",
            "simulated":   True,
        }

    supervised_tasks._DEAD[name] = row
    return {"ok": True, "simulated_dead": row, "name": name}


@router.post("/dev/clear-supervised-postmortem/{name}")
async def clear_supervised_postmortem_for_ui_test(
    name: str,
    authorization: Optional[str] = Header(None),
):
    """Companion of `/dev/kill-supervised-task` — clears a postmortem
    row so the founder can confirm the widget flips back to green.
    Same env + admin gates as the kill endpoint."""
    if os.getenv("AUREM_TEST_MODE") != "1":
        raise HTTPException(404, "Not Found")
    from services import supervised_tasks
    _ = await _require_admin(authorization)
    removed = supervised_tasks._DEAD.pop(name, None)
    return {"ok": True, "cleared": removed is not None, "name": name}
