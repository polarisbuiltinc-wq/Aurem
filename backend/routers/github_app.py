"""
routers/github_app.py — Phase 2 · GitHub App install flow

Endpoints (mounted at /api/aurem-dev/github/app/*):

  GET    /github/app/install                — user kickoff (JWT-auth)
  GET    /github/app/callback               — GitHub post-install redirect
                                              (unauth, state-token validated)
  POST   /github/app/webhook                — GitHub POSTs deliveries
                                              (unauth, HMAC-signature-validated)
  GET    /github/app/installations          — list my active installs (JWT-auth)
  DELETE /github/app/installations/{id}     — user disconnect (JWT-auth)

Race semantics:
  Webhook `installation.created` and `/callback` can arrive in either
  order. Both use `find_one_and_update(upsert=True)` with `$set` for
  mutable fields and `$setOnInsert` for immutable ones. Callback
  additionally sets `user_id` (webhook never has it). Result is
  idempotent: whichever arrives first creates the row; the other
  fills in missing fields.

Idempotency:
  Every webhook delivery is deduped by `X-GitHub-Delivery` UUID via
  the `webhook_deliveries` collection (TTL 7d). Repeat deliveries
  return {ok:true, deduped:true} without side-effects.

Security:
  * Callback state token is single-use (atomic
    find_one_and_update({used:false},{$set:{used:true}})).
  * Webhook signature is verified BEFORE any body parsing.
  * DELETE returns 404 (not 403) on cross-user ownership violations
    so installation_id cannot be enumerated.
  * Installation access tokens are never persisted — Phase 1.1 property
    preserved. This router only handles metadata + installation IDs.
"""
# arch: allow-http — direct calls to api.github.com (App metadata,
# installation details, revoke). Uses services/github_app.py primitives
# for JWT signing and installation-token handling.
from __future__ import annotations

import logging
import secrets
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from cto_services.auth import current_dev
from cto_services.db import get_db, require_db
from services import github_app as _ga
from services.github_app_config import is_configured as _app_configured
from routers.github_funnel import track_server_side as _funnel_track

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/github/app", tags=["GitHub App"])

# State row TTL — user must complete the install within this window.
_STATE_TTL_SECONDS = 15 * 60

# Webhook delivery dedupe collection retention.
_WEBHOOK_DELIVERY_TTL_SECONDS = 7 * 24 * 60 * 60

# Frontend deep-links (relative — the caller's browser resolves against
# the domain they're currently on, so multi-domain deploys just work).
_BRIDGE_PATH             = "/api/aurem-dev/github/app/installed"
_DEEP_LINK_SUCCESS       = _BRIDGE_PATH + "?status=success&install_id={iid}"
_DEEP_LINK_ERR_STATE     = _BRIDGE_PATH + "?status=err&err=invalid_state"
_DEEP_LINK_ERR_PROBE     = _BRIDGE_PATH + "?status=err&err=github_probe_failed"
_DEEP_LINK_APP_PENDING   = _BRIDGE_PATH + "?status=pending"


# ═════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════

def _now_utc_epoch() -> float:
    return datetime.now(timezone.utc).timestamp()


async def _fetch_installation_meta(installation_id: int) -> dict:
    """`GET /app/installations/{id}` with App-JWT. Returns the full
    installation payload (account, target_type, permissions, events,
    repository_selection, ...)."""
    headers = {
        "Authorization":        f"Bearer {_ga.app_jwt()}",
        "Accept":               "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent":           "aurem-github-app/1.0",
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(
            f"https://api.github.com/app/installations/{installation_id}",
            headers=headers,
        )
    r.raise_for_status()
    return r.json() or {}


def _slim_repo(repo: dict) -> dict:
    return {
        "id":             repo.get("id"),
        "full_name":      repo.get("full_name"),
        "private":        repo.get("private"),
        "default_branch": repo.get("default_branch"),
    }


def _dashboard_url(request: Request, tail: str) -> str:
    """Resolve `tail` against the current request's origin so any
    domain (preview pod, auremcto.com, custom) works. Falls back to
    `tail` alone if headers are unavailable — that's a valid
    same-origin redirect from GitHub's perspective too."""
    try:
        proto = request.headers.get("x-forwarded-proto") or "https"
        host = request.headers.get("x-forwarded-host") or request.headers.get("host")
        if host:
            return f"{proto}://{host}{tail}"
    except Exception:
        pass
    import os as _os
    base = (_os.getenv("APP_URL") or "").rstrip("/")
    return f"{base}{tail}" if base else tail


async def _upsert_installation(
    db, installation_id: int, meta: dict,
    *, user_id: Optional[str] = None,
    delivery_id: Optional[str] = None,
    repositories: Optional[list[dict]] = None,
) -> dict:
    """Idempotent upsert into `github_installations`.

    * `$setOnInsert`: `installed_at` (immutable install-time epoch)
    * `$set`: everything mutable (auth data, active flag, updated_at,
              optionally user_id / repositories / last_webhook_delivery)
    """
    now = _now_utc_epoch()
    account = meta.get("account") or {}
    set_doc: dict = {
        "installation_id":       installation_id,
        "github_login":          account.get("login") or "",
        "github_account_type":   account.get("type") or "",
        "github_account_id":     account.get("id"),
        "target_type":           meta.get("target_type") or "",
        "repository_selection":  meta.get("repository_selection") or "",
        "permissions":           meta.get("permissions") or {},
        "events":                meta.get("events") or [],
        "active":                True,
        "suspended_at":          None,
        "deleted_at":            None,
        "updated_at":            now,
    }
    if user_id:
        set_doc["user_id"]   = user_id
        set_doc["linked_at"] = now
    if repositories is not None:
        set_doc["repositories"] = repositories
    if delivery_id:
        set_doc["last_webhook_delivery"] = delivery_id

    return await db.github_installations.find_one_and_update(
        {"installation_id": installation_id},
        {
            "$set":         set_doc,
            "$setOnInsert": {"installed_at": now},
        },
        upsert=True,
        return_document=True,     # pymongo ReturnDocument.AFTER equivalent
    )


async def _cascade_project_active(
    db, installation_id: int, active: bool,
) -> None:
    """Soft-toggle `installation_active` on every `cto_projects` row
    referencing this installation. Rows are NEVER deleted here — user
    still owns their project data across suspensions."""
    try:
        await db.cto_projects.update_many(
            {"installation_id": installation_id},
            {"$set": {
                "installation_active": active,
                "installation_status_updated_at": _now_utc_epoch(),
            }},
        )
    except Exception as e:                                        # noqa: BLE001
        logger.warning(
            "cto_projects installation_active cascade failed for iid=%s: %r",
            installation_id, e,
        )


# ═════════════════════════════════════════════════════════════════════
# 1. GET /install — user kickoff
# ═════════════════════════════════════════════════════════════════════

@router.get("/install")
async def install_kickoff(
    request: Request,
    authorization: Optional[str] = Header(None),
    auth: Optional[str] = Query(None),
    fs: Optional[str] = Query(default=None),
):
    """Authenticated redirect to GitHub's install page.

    Mint a single-use state token bound to `current_dev.user_id`,
    persist it in `oauth_states`, and 302 to
    `github.com/apps/<slug>/installations/new?state=<>`.

    Query params:
      auth  — legacy pass-through for `?auth=<jwt>` navigations (some
              chrome-less flows can't set Authorization header). Also
              used by the Phase 4 wizard popup, which can't set the
              header on a `window.open()` navigation.
      fs    — optional funnel session_id to stitch client + server events.
    """
    if not _app_configured():
        # 2026-08-24 — lifespan-hydration race: fall back to a one-shot
        # DB read before failing closed (testing-agent-observed 499s in
        # the first second after a restart).
        from services.github_app_config import ensure_configured_from_db
        if not await ensure_configured_from_db(require_db()):
            raise HTTPException(503, "github_app_not_configured")

    if not authorization and auth:
        authorization = f"Bearer {auth}"
    user = await current_dev(authorization)

    state = f"gha:{user['user_id']}:{secrets.token_urlsafe(24)}"
    db = require_db()
    await db.oauth_states.insert_one({
        "state":       state,
        "kind":        "github_app_install",
        "mode":        "github_app_install",
        "user_id":     user["user_id"],
        "used":        False,
        "created_at":  datetime.now(timezone.utc),
        "expires_at":  datetime.now(timezone.utc).timestamp() + _STATE_TTL_SECONDS,
        "ts":          time.time(),
        "funnel_session": fs,
    })

    await _funnel_track(
        "app_install_redirect", source="wizard",
        session_id=fs, user_id=user["user_id"],
        meta={"state_prefix": "gha"},
    )

    return RedirectResponse(url=_ga.install_url(state=state), status_code=302)


# ═════════════════════════════════════════════════════════════════════
# 2. GET /callback — GitHub returns after install
# ═════════════════════════════════════════════════════════════════════

@router.get("/callback")
async def install_callback(
    request: Request,
    installation_id: Optional[int] = Query(default=None),
    setup_action:    Optional[str] = Query(default=None),
    state:           Optional[str] = Query(default=None),
    code:            Optional[str] = Query(default=None),  # ignored — OAuth-during-install unused for now
):
    """GitHub's redirect after an install / update / request action.

    Handles four `setup_action` values that GitHub sends:
      • "install" / "update" → active install; upsert + link + redirect success
      • "request"            → user needs org-admin approval → soft banner
      • (missing)            → treat as "install" (older GitHub responses)
    """
    # ── Branch A: user requested App on an org they can't self-install to ──
    if setup_action == "request":
        return RedirectResponse(
            url=_dashboard_url(request, _DEEP_LINK_APP_PENDING),
            status_code=302,
        )

    if not installation_id:
        # Malformed callback — send them home softly.
        return RedirectResponse(
            url=_dashboard_url(request, _DEEP_LINK_ERR_STATE),
            status_code=302,
        )

    db = require_db()

    # ── State validation (atomic single-use) ──────────────────────────
    state_row = None
    if state:
        state_row = await db.oauth_states.find_one_and_update(
            {
                "state":       state,
                "kind":        "github_app_install",
                "used":        False,
                "expires_at":  {"$gt": _now_utc_epoch()},
            },
            {"$set": {"used": True, "used_at": _now_utc_epoch()}},
        )

    _recovered_user_id = None
    if state and state_row is None:
        # State row missing/expired/already-used — this used to be a
        # dead end: the webhook still records `installation.created`
        # with user_id:null, and the user's wizard silently reverts to
        # the connect CTA with zero feedback (2026-08-27 founder report
        # — real-world GitHub App install that never linked). FIX: our
        # own state string is `gha:<user_id>:<random 24-byte token>` —
        # the random suffix makes it unforgeable, so on TTL-expiry or a
        # benign double-fire (GitHub occasionally redirects the
        # callback twice) it is safe to recover `user_id` from the
        # string itself rather than dropping the link entirely. Still
        # a hard 401/redirect-to-error below if the string doesn't even
        # match our own format (can't recover a user_id from nothing —
        # that's a genuinely malformed/forged state).
        if state.startswith("gha:"):
            _parts = state.split(":", 2)
            if len(_parts) == 3 and _parts[1]:
                _recovered_user_id = _parts[1]
        logger.warning(
            "GH_CONNECT_STATE_INVALID installation_id=%s state_prefix=%s "
            "recovered_user_id=%s (row missing/expired/used — see comment)",
            installation_id, state[:8], _recovered_user_id,
        )
        if _recovered_user_id is None:
            return RedirectResponse(
                url=_dashboard_url(request, _DEEP_LINK_ERR_STATE),
                status_code=302,
            )

    user_id_to_link = (state_row or {}).get("user_id") or _recovered_user_id
    funnel_session  = (state_row or {}).get("funnel_session")

    # ── Fetch installation metadata from GitHub ───────────────────────
    try:
        meta = await _fetch_installation_meta(installation_id)
    except Exception as e:                                        # noqa: BLE001
        logger.error(
            "github_app callback: /app/installations/%s fetch failed: %r",
            installation_id, e,
        )
        # Onboarding Step 4 · S-A (2026-08-26) — attributable failure
        # (user_id_to_link is known here, from the validated state
        # row) → real funnel signal instead of a silent redirect.
        if user_id_to_link:
            from services.signup_guards import emit_connect_repo_install_failed
            await emit_connect_repo_install_failed(
                db, user_id=user_id_to_link, error=repr(e)[:300],
            )
        return RedirectResponse(
            url=_dashboard_url(request, _DEEP_LINK_ERR_PROBE),
            status_code=302,
        )

    # ── Fetch installation repos (paginated inside service) ───────────
    try:
        repos_full = await _ga.list_installation_repos(int(installation_id))
        repos_slim = [_slim_repo(r) for r in repos_full]
    except Exception as e:                                        # noqa: BLE001
        # Non-fatal — row is still linked, wizard will fetch repos
        # again when the user opens the picker.
        logger.warning(
            "github_app callback: list_installation_repos(%s) failed: %r",
            installation_id, e,
        )
        repos_slim = None

    # ── Upsert installation row (idempotent, race-safe) ───────────────
    await _upsert_installation(
        db, int(installation_id), meta,
        user_id=user_id_to_link,
        repositories=repos_slim,
    )
    if _recovered_user_id:
        logger.info(
            "GH_CONNECT_STATE_RECOVERED installation_id=%s user_id=%s "
            "— linked successfully despite expired/used state row",
            installation_id, _recovered_user_id,
        )

    # ── Restore cascade — if a prior suspend/delete was recorded,
    #     re-enabling the install should re-enable the projects too.
    await _cascade_project_active(db, int(installation_id), active=True)

    # ── Funnel event ──────────────────────────────────────────────────
    await _funnel_track(
        "app_installed", source="wizard",
        session_id=funnel_session, user_id=user_id_to_link,
        meta={
            "installation_id":       installation_id,
            "account_login":         (meta.get("account") or {}).get("login"),
            "repository_selection":  meta.get("repository_selection"),
            "repo_count":            len(repos_slim) if repos_slim else 0,
        },
    )
    # 2026-08-27 · Journey Watch Phase 0 — server-truth mirror of
    # `app_install_granted`. The client also fires this on its own
    # status poll, but that poll dies if the user closes the tab right
    # after granting — this server-side callback fire never misses it.
    await _funnel_track(
        "app_install_granted", source="wizard",
        session_id=funnel_session, user_id=user_id_to_link,
        meta={"installation_id": installation_id},
    )

    return RedirectResponse(
        url=_dashboard_url(
            request,
            _DEEP_LINK_SUCCESS.format(iid=installation_id),
        ),
        status_code=302,
    )


# ═════════════════════════════════════════════════════════════════════
# 3. POST /webhook — GitHub deliveries
# ═════════════════════════════════════════════════════════════════════

@router.post("/webhook")
async def install_webhook(request: Request):
    """Signature-validated webhook receiver.

    Contract:
      * `raw_body = await request.body()` FIRST — signature is computed
        over exact bytes, not a re-serialised parse.
      * HMAC verified against configured webhook_secret.
      * Dedupes on `X-GitHub-Delivery` header via `webhook_deliveries`.
      * Every event returns 200 on success (GitHub retries on 5xx —
        we only 5xx on infra failure, never on unknown events).
    """
    raw_body = await request.body()
    sig_header = request.headers.get("x-hub-signature-256")
    if not _ga.verify_webhook_signature(raw_body, sig_header):
        # Never leak reason (missing header vs. bad hash vs. not
        # configured) — always the same 401 to prevent probing.
        raise HTTPException(401, "invalid_signature")

    event = request.headers.get("x-github-event") or ""
    delivery_id = request.headers.get("x-github-delivery") or ""

    # Parse body only after signature is verified.
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, "invalid_json")

    db = require_db()

    # ── Idempotency: dedupe on delivery_id ────────────────────────────
    if delivery_id:
        try:
            existing = await db.webhook_deliveries.find_one({"_id": delivery_id})
            if existing:
                return {"ok": True, "deduped": True, "delivery_id": delivery_id}
            await db.webhook_deliveries.insert_one({
                "_id":            delivery_id,
                "event":          event,
                "action":         payload.get("action"),
                "installation":   ((payload.get("installation") or {}).get("id")),
                "received_at":    _now_utc_epoch(),
            })
        except Exception as e:                                    # noqa: BLE001
            # Duplicate-key from a racing worker → also a dedupe hit.
            if "duplicate key" in str(e).lower() or "E11000" in str(e):
                return {"ok": True, "deduped": True, "delivery_id": delivery_id}
            # Any other DB error — log but continue (webhook write
            # matters more than the dedupe log).
            logger.warning(
                "webhook_deliveries write failed (continuing): %r", e,
            )

    action = payload.get("action") or ""
    installation = payload.get("installation") or {}
    installation_id = installation.get("id")

    # ── Event dispatch ────────────────────────────────────────────────
    try:
        if event == "installation" and action == "created":
            if installation_id:
                # Try to fetch fresh repo list; payload already
                # contains `repositories` for `created` events so
                # prefer that (avoids a GitHub API call inside the
                # webhook path — must return <10s to avoid GitHub
                # marking us as slow).
                payload_repos = payload.get("repositories") or []
                repos_slim = [
                    {
                        "id":             r.get("id"),
                        "full_name":      (r.get("full_name")
                                           or f"{(installation.get('account') or {}).get('login','')}/{r.get('name','')}".strip("/")),
                        "private":        r.get("private"),
                        # `default_branch` not in webhook payload; leave None
                        # — wizard hits list_installation_repos() for fresh data.
                        "default_branch": None,
                    }
                    for r in payload_repos
                ]
                await _upsert_installation(
                    db, int(installation_id), installation,
                    delivery_id=delivery_id,
                    repositories=repos_slim or None,
                )

        elif event == "installation" and action == "deleted":
            if installation_id:
                await db.github_installations.update_one(
                    {"installation_id": int(installation_id)},
                    {"$set": {
                        "active":     False,
                        "deleted_at": _now_utc_epoch(),
                        "updated_at": _now_utc_epoch(),
                        "last_webhook_delivery": delivery_id,
                    }},
                )
                await _cascade_project_active(db, int(installation_id), active=False)

        elif event == "installation" and action == "suspend":
            if installation_id:
                await db.github_installations.update_one(
                    {"installation_id": int(installation_id)},
                    {"$set": {
                        "active":       False,
                        "suspended_at": _now_utc_epoch(),
                        "updated_at":   _now_utc_epoch(),
                        "last_webhook_delivery": delivery_id,
                    }},
                )
                await _cascade_project_active(db, int(installation_id), active=False)

        elif event == "installation" and action == "unsuspend":
            if installation_id:
                await db.github_installations.update_one(
                    {"installation_id": int(installation_id)},
                    {"$set": {
                        "active":       True,
                        "suspended_at": None,
                        "updated_at":   _now_utc_epoch(),
                        "last_webhook_delivery": delivery_id,
                    }},
                )
                await _cascade_project_active(db, int(installation_id), active=True)

        elif event == "installation_repositories" and action == "added":
            if installation_id:
                added = payload.get("repositories_added") or []
                added_slim = [
                    {"id": r.get("id"), "full_name": r.get("full_name"),
                     "private": r.get("private"), "default_branch": None}
                    for r in added
                ]
                # Merge into existing cached repos. Use $addToSet
                # per-entry via aggregation-style update is awkward
                # in Motor without pipeline; safest = read-modify-write
                # keyed by id to avoid duplicates.
                current_row = await db.github_installations.find_one(
                    {"installation_id": int(installation_id)}
                )
                existing_repos = ((current_row or {}).get("repositories") or [])
                existing_ids = {r.get("id") for r in existing_repos}
                merged = existing_repos + [
                    r for r in added_slim if r["id"] not in existing_ids
                ]
                await db.github_installations.update_one(
                    {"installation_id": int(installation_id)},
                    {"$set": {
                        "repositories": merged,
                        "updated_at":   _now_utc_epoch(),
                        "last_webhook_delivery": delivery_id,
                    }},
                )

        elif event == "installation_repositories" and action == "removed":
            if installation_id:
                removed = payload.get("repositories_removed") or []
                removed_ids = {r.get("id") for r in removed}
                current_row = await db.github_installations.find_one(
                    {"installation_id": int(installation_id)}
                )
                existing_repos = ((current_row or {}).get("repositories") or [])
                filtered = [
                    r for r in existing_repos if r.get("id") not in removed_ids
                ]
                await db.github_installations.update_one(
                    {"installation_id": int(installation_id)},
                    {"$set": {
                        "repositories": filtered,
                        "updated_at":   _now_utc_epoch(),
                        "last_webhook_delivery": delivery_id,
                    }},
                )
                # Soft-disable cto_projects for the removed repos —
                # safer than deleting; user re-adds the repo to the
                # installation to reactivate.
                removed_full_names = {r.get("full_name") for r in removed if r.get("full_name")}
                if removed_full_names:
                    try:
                        # Match on github_owner/github_repo composite —
                        # cto_projects stores them separately.
                        for full in removed_full_names:
                            if "/" not in full:
                                continue
                            owner, repo = full.split("/", 1)
                            await db.cto_projects.update_many(
                                {
                                    "installation_id": int(installation_id),
                                    "github_owner":    owner,
                                    "github_repo":     repo,
                                },
                                {"$set": {
                                    "installation_active":              False,
                                    "installation_status_updated_at":   _now_utc_epoch(),
                                }},
                            )
                    except Exception as e:                        # noqa: BLE001
                        logger.warning(
                            "cto_projects repo-removed cascade failed: %r", e,
                        )

        elif event == "meta" and action == "deleted":
            # Catastrophic: the App itself was deleted from GitHub.
            # Mass-mark every installation of this App as inactive.
            try:
                await db.github_installations.update_many(
                    {"active": True},
                    {"$set": {
                        "active":     False,
                        "deleted_at": _now_utc_epoch(),
                        "updated_at": _now_utc_epoch(),
                        "meta_deleted": True,
                    }},
                )
                # Cascade to all cto_projects across all installations.
                await db.cto_projects.update_many(
                    {"installation_id": {"$exists": True, "$ne": None}},
                    {"$set": {
                        "installation_active": False,
                        "installation_status_updated_at": _now_utc_epoch(),
                    }},
                )
                logger.error(
                    "🚨 GitHub App META DELETE received — every installation "
                    "marked inactive. Founder must re-register the App.",
                )
            except Exception as e:                                # noqa: BLE001
                logger.error("meta.deleted cascade failed: %r", e)

        else:
            # Unknown event — log and 200. GitHub adds new events
            # over time; we never 4xx here so deliveries aren't
            # retried forever.
            logger.info(
                "github_app webhook: unhandled event=%s action=%s delivery=%s",
                event, action, delivery_id,
            )

    except HTTPException:
        raise
    except Exception as e:                                        # noqa: BLE001
        # Real infra failure — 500 so GitHub retries.
        logger.error(
            "github_app webhook dispatch failed event=%s delivery=%s: %r",
            event, delivery_id, e,
        )
        raise HTTPException(500, "dispatch_failed")

    return {
        "ok":          True,
        "event":       event,
        "action":      action,
        "delivery_id": delivery_id,
    }


# ═════════════════════════════════════════════════════════════════════
# 3b. GET /installed — popup ↔ parent handshake bridge (Phase 4)
# ═════════════════════════════════════════════════════════════════════

_BRIDGE_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Aurem · GitHub App installed</title>
<style>
  html,body{margin:0;padding:0;background:#0b0b0d;color:#e5e5e5;
    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
    height:100%;display:flex;align-items:center;justify-content:center}
  .box{max-width:420px;text-align:center;padding:32px;line-height:1.6}
  .ok{color:#6dd4a1}.err{color:#ff6b6b}.hint{color:#71717a;font-size:12px;margin-top:16px}
</style></head><body>
<div class="box">
  <div id="msg">Finishing up…</div>
  <div class="hint">You can close this window.</div>
</div>
<script>
(function() {
  var qs = new URLSearchParams(window.location.search);
  var status = qs.get('status') || 'err';
  var installId = qs.get('install_id') || null;
  var err = qs.get('err') || null;

  var msg = document.getElementById('msg');
  if (status === 'success') {
    msg.innerHTML = '<span class="ok">\u2713 GitHub App installed</span>';
  } else if (status === 'pending') {
    msg.innerHTML = 'Installation is pending your org admin\\'s approval.';
  } else {
    msg.innerHTML = '<span class="err">Install did not complete</span>' +
      (err ? '<div class="hint">Reason: ' + err + '</div>' : '');
  }

  var payload = { type: 'aurem-app-installed', status: status,
                  install_id: installId ? Number(installId) : null,
                  err: err };

  // 1. Popup case — postMessage back to opener, then close self.
  if (window.opener && !window.opener.closed) {
    try { window.opener.postMessage(payload, '*'); } catch (e) {}
    setTimeout(function(){ try { window.close(); } catch (e) {} }, 400);
    return;
  }

  // 2. Non-popup fallback — meta-refresh to /dashboard so the user
  //    still lands somewhere sensible.
  var tail = '';
  if (status === 'success' && installId) {
    tail = '?flow=connect-repo&install=success&install_id=' + installId;
  } else if (status === 'pending') {
    tail = '?flow=connect-repo&app_pending=1';
  } else {
    tail = '?flow=connect-repo&err=' + (err || 'unknown');
  }
  setTimeout(function(){ window.location.replace('/dashboard' + tail); }, 1200);
})();
</script>
</body></html>"""


@router.get("/installed")
async def install_bridge(
    status: Optional[str] = Query(default="err"),
    install_id: Optional[int] = Query(default=None),
    err: Optional[str] = Query(default=None),
):
    """Tiny HTML bridge served after `/callback` completes.

    Detects `window.opener` — if the browser is a popup opened by the
    wizard, it posts an `aurem-app-installed` message back to the
    parent and closes itself. Otherwise it meta-refreshes to
    `/dashboard?flow=connect-repo&…` so the user still lands on the
    right screen.

    Query params (all optional):
      status      — "success" | "pending" | "err"
      install_id  — populated on success
      err         — reason on failure
    """
    from fastapi.responses import HTMLResponse
    return HTMLResponse(_BRIDGE_HTML)


# ═════════════════════════════════════════════════════════════════════
# 4. GET /installations — list mine
# ═════════════════════════════════════════════════════════════════════

@router.get("/installations")
async def list_my_installations(
    authorization: Optional[str] = Header(None),
):
    """Return every active installation linked to the current user.

    Wizard picker in Phase 4 uses this to render the "which repo?"
    step after the install completes.
    """
    user = await current_dev(authorization)
    db = require_db()

    rows = await db.github_installations.find(
        {"user_id": user["user_id"], "active": True},
    ).sort("installed_at", -1).to_list(length=100)

    return {
        "installations": [
            {
                "installation_id":      row.get("installation_id"),
                "github_login":         row.get("github_login"),
                "github_account_type":  row.get("github_account_type"),
                "target_type":          row.get("target_type"),
                "repository_selection": row.get("repository_selection"),
                "repositories":         row.get("repositories") or [],
                "installed_at":         row.get("installed_at"),
                "linked_at":            row.get("linked_at"),
            }
            for row in rows
        ],
    }


# ═════════════════════════════════════════════════════════════════════
# 4a. GET /status — 2026-08 hardening (F4-github-connect · I4b).
#
# THE authoritative live-status endpoint the connect investigation
# found missing. Both wizards previously guessed "did the connect
# finish?" from either a postMessage OR "installation count went up" —
# the count check can never detect "repo added to an EXISTING
# installation" (the common case after the first connect), and if the
# initial repo-list fetch failed even once, the row stayed cached with
# 0 repos FOREVER (confirmed live in this exact DB: installation
# 152797252 has 0 cached repos today).
#
# Self-healing short-TTL cache: if the cached `repositories` is <10s
# old AND non-empty, trust it (bounds live GitHub calls to ~1 per 10s
# even though the frontend polls every ~2.5s — rate-limit safe). If
# it's stale OR EMPTY, live-refetch from GitHub and persist — this is
# exactly what self-heals a poisoned 0-repo row. On a live-fetch
# failure, DO NOT persist the empty result (so the next poll retries
# immediately) and return state=error with a plain-language reason.
# ═════════════════════════════════════════════════════════════════════
_STATUS_CACHE_TTL_S = 10


@router.get("/status")
async def connect_status(authorization: Optional[str] = Header(None)):
    user = await current_dev(authorization)
    db = require_db()

    rows = await db.github_installations.find(
        {"user_id": user["user_id"], "active": True},
    ).to_list(length=50)

    if not rows:
        return {
            "installation_active": False,
            "installations": [],
            "repos": [],
            "connected_repo": None,
            "state": "pending",
            "error": None,
        }

    now = time.time()
    any_error: Optional[str] = None
    out_installs = []
    for row in rows:
        iid = row.get("installation_id")
        cached_repos = row.get("repositories")
        updated_at = row.get("updated_at") or 0
        fresh = (
            isinstance(cached_repos, list) and len(cached_repos) > 0
            and (now - updated_at) < _STATUS_CACHE_TTL_S
        )
        if fresh:
            repos = cached_repos
        else:
            try:
                fetched = await _ga.list_installation_repos(int(iid))
                repos = [_slim_repo(r) for r in fetched]
                await db.github_installations.update_one(
                    {"installation_id": iid},
                    {"$set": {"repositories": repos, "updated_at": now}},
                )
            except Exception as e:
                logger.warning(
                    "[status] live repo fetch failed for install %s: %r",
                    iid, e,
                )
                # Do NOT overwrite/long-cache the failure — keep whatever
                # was cached (even if empty) and let the NEXT poll retry.
                repos = cached_repos if isinstance(cached_repos, list) else []
                if not repos:
                    any_error = "github_fetch_failed"
        out_installs.append({
            "installation_id": iid,
            "github_login":    row.get("github_login"),
            "repositories":    repos,
        })

    all_repos = [
        {**r, "installation_id": inst["installation_id"],
         "github_login": inst["github_login"]}
        for inst in out_installs for r in inst["repositories"]
    ]
    connected_repo = all_repos[0]["full_name"] if len(all_repos) == 1 else None

    if all_repos:
        state = "connected"
    elif any_error:
        state = "error"
    else:
        state = "pending"

    return {
        "installation_active": True,
        "installations": out_installs,
        "repos": all_repos,
        "connected_repo": connected_repo,
        "state": state,
        "error": (
            "Couldn't verify your GitHub repos just now — retrying "
            "automatically."
        ) if any_error else None,
    }


# ═════════════════════════════════════════════════════════════════════
# 4b. GET /installations/health — Settings + revoked-banner CTA
# ═════════════════════════════════════════════════════════════════════
#
# 2026-08-20 — founder-approved: distinguish "App installation
# suspended/removed" (whole-App-level, fixed on GitHub's own settings
# page) from "per-repo access revoked" (fixed by the wizard's
# reconnect flow). Unlike `list_my_installations` above — which stays
# `active: True`-only on purpose so the wizard/repo-picker flows never
# regress — this returns EVERY installation row for the user,
# including suspended/deleted ones, so the UI can render an accurate
# status. Reads the `suspended_at`/`deleted_at` fields the webhook
# handler already maintains (installation/suspend, /unsuspend,
# /deleted events) — no live GitHub API polling.
@router.get("/installations/health")
async def installations_health(
    authorization: Optional[str] = Header(None),
):
    user = await current_dev(authorization)
    db = require_db()

    rows = await db.github_installations.find(
        {"user_id": user["user_id"]},
    ).sort("updated_at", -1).to_list(length=100)

    def _status(row: dict) -> str:
        if row.get("deleted_at"):
            return "deleted"
        if row.get("suspended_at"):
            return "suspended"
        return "active"

    return {
        "installations": [
            {
                "installation_id": row.get("installation_id"),
                "github_login":    row.get("github_login"),
                "status":          _status(row),
                "suspended_at":    row.get("suspended_at"),
                "deleted_at":      row.get("deleted_at"),
                "repo_count":      len(row.get("repositories") or []),
            }
            for row in rows
        ],
    }


# ═════════════════════════════════════════════════════════════════════
# 5. DELETE /installations/{id} — user disconnect
# ═════════════════════════════════════════════════════════════════════

@router.delete("/installations/{installation_id}")
async def disconnect_installation(
    installation_id: int,
    authorization: Optional[str] = Header(None),
):
    """User-initiated disconnect. Revokes on GitHub, marks local row
    inactive, cascades cto_projects.installation_active=false.

    Returns 404 (not 403) on cross-user attempts so installation_id
    cannot be enumerated across accounts.
    """
    user = await current_dev(authorization)
    db = require_db()

    row = await db.github_installations.find_one({
        "installation_id": int(installation_id),
        "user_id":         user["user_id"],
    })
    if not row:
        # Either doesn't exist or belongs to someone else — same
        # response either way (enum-proof).
        raise HTTPException(404, "installation_not_found")

    # Idempotent: if already revoked/inactive, skip the GitHub API call
    # and just report success.
    if row.get("active"):
        try:
            await _ga.revoke_installation(int(installation_id))
        except Exception as e:                                    # noqa: BLE001
            logger.warning(
                "github_app: revoke_installation(%s) failed at GitHub side: %r "
                "(marking local row inactive anyway)",
                installation_id, e,
            )

    await db.github_installations.update_one(
        {"installation_id": int(installation_id)},
        {"$set": {
            "active":     False,
            "revoked_at": _now_utc_epoch(),
            "updated_at": _now_utc_epoch(),
        }},
    )
    await _cascade_project_active(db, int(installation_id), active=False)

    return {"ok": True, "revoked_installation_id": int(installation_id)}
