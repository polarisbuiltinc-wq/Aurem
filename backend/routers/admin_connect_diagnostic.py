"""
routers/admin_connect_diagnostic.py — Connect-flow investigation (2026-09-01)

READ-ONLY, admin-gated diagnostic for a SINGLE user_id. Built so the
founder can deploy this to PRODUCTION and run it for each of the 4
named users, then paste the JSON output back for diagnosis — no
production credential ever needs to enter this chat.

Pulls exactly the state named in the investigation brief:
  * github_installations row(s) for the user — installation_id,
    user_id (set/null), repo_ids, granted/denied-relevant timestamps,
    status.
  * cto_projects rows for the user — count + created_at for each, so
    "any created in the failure window" is answerable at a glance.
  * oauth_states rows (kind=github_app_install) for the user — state
    token ISSUED (created_at) vs RECEIVED/consumed (used_at), so a
    callback that never fired is visible as `used=false`.
  * funnel_events (connect_repo_install_failed / first_scan_* —
    Onboarding Step 4 signals) — surfaces any exception the callback
    hit, even though the user saw nothing.
  * The ORDERED combined event stream: github_funnel_events (client +
    server stage instrumentation) + webhook_deliveries (matched by any
    installation_id found for this user) + funnel_events, merged and
    sorted by timestamp — event type, timestamp, event/delivery id,
    installation_id, all in one list so ordering tells real churn
    apart from a bug.

NO WRITES. NO MUTATION. Scoped to exactly the user_id passed in — not
a mass dump. Same admin gate as every other admin-only endpoint
(routers/self_bugs_admin.py, routers/admin_ops_config.py, ...).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Header

from cto_services.auth import require_admin
from cto_services.db import require_db

router = APIRouter(prefix="/admin/connect-diagnostic", tags=["Admin — Connect Flow"])


def _iso(v):
    """Best-effort epoch-or-datetime -> ISO string, for readability in
    the pasted JSON output. Leaves non-time values untouched."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        try:
            return datetime.fromtimestamp(v, tz=timezone.utc).isoformat()
        except (ValueError, OSError):
            return v
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v.isoformat()
    return v


@router.get("/{user_id}")
async def connect_flow_diagnostic(
    user_id: str,
    authorization: Optional[str] = Header(None),
):
    await require_admin(authorization)
    db = require_db()

    # ── 1. github_installations rows for this user ────────────────────
    install_rows = await db.github_installations.find(
        {"user_id": user_id},
    ).sort("installed_at", -1).to_list(length=50)
    installations = [
        {
            "installation_id":      r.get("installation_id"),
            "user_id":              r.get("user_id"),
            "github_login":         r.get("github_login"),
            "target_type":          r.get("target_type"),
            "repository_selection": r.get("repository_selection"),
            "repo_ids":             [
                rr.get("id") for rr in (r.get("repositories") or [])
            ],
            "repo_full_names":      [
                rr.get("full_name") for rr in (r.get("repositories") or [])
            ],
            "active":               r.get("active"),
            "installed_at":         _iso(r.get("installed_at")),
            "linked_at":            _iso(r.get("linked_at")),
            "updated_at":           _iso(r.get("updated_at")),
            "suspended_at":         _iso(r.get("suspended_at")),
            "deleted_at":           _iso(r.get("deleted_at")),
            "revoked_at":           _iso(r.get("revoked_at")),
            "last_webhook_delivery": r.get("last_webhook_delivery"),
        }
        for r in install_rows
    ]
    installation_ids = [
        i["installation_id"] for i in installations if i["installation_id"]
    ]

    # ── 2. cto_projects rows for this user ─────────────────────────────
    project_rows = await db.cto_projects.find(
        {"user_id": user_id},
    ).sort("created_at", -1).to_list(length=100)
    projects = [
        {
            "project_id":            r.get("project_id"),
            "github_owner":          r.get("github_owner"),
            "github_repo":           r.get("github_repo"),
            "installation_id":       r.get("installation_id"),
            "installation_active":   r.get("installation_active"),
            "created_at":            _iso(r.get("created_at")),
        }
        for r in project_rows
    ]

    # ── 3. oauth_states (github_app_install) — issued vs received ─────
    state_rows = await db.oauth_states.find(
        {"user_id": user_id, "kind": "github_app_install"},
    ).sort("created_at", -1).to_list(length=50)
    oauth_states = [
        {
            "state_prefix":  (r.get("state") or "")[:12] + "…",  # never full token
            "issued_at":     _iso(r.get("created_at")),
            "expires_at":    _iso(r.get("expires_at")),
            "used":          bool(r.get("used")),
            "used_at":       _iso(r.get("used_at")),
            "funnel_session": r.get("funnel_session"),
        }
        for r in state_rows
    ]

    # ── 4. funnel_events (install-failed / first-scan onboarding) ─────
    funnel_evt_rows = await db.funnel_events.find(
        {"user_id": user_id},
    ).sort("ts_epoch", 1).to_list(length=200)
    funnel_events = [
        {
            "event_type": r.get("event_type"),
            "metadata":   r.get("metadata"),
            "ts":         _iso(r.get("ts_epoch")),
        }
        for r in funnel_evt_rows
    ]

    # ── 5. Ordered combined stream: github_funnel_events + webhooks ───
    gh_funnel_rows = await db.github_funnel_events.find(
        {"user_id": user_id},
    ).sort("ts", 1).to_list(length=300)

    webhook_rows = []
    if installation_ids:
        webhook_rows = await db.webhook_deliveries.find(
            {"installation": {"$in": installation_ids}},
        ).sort("received_at", 1).to_list(length=300)

    combined = []
    for r in gh_funnel_rows:
        combined.append({
            "kind":            "funnel",
            "event_type":      r.get("stage"),
            "origin":          r.get("origin"),      # "client" | "server"
            "source":          r.get("source"),
            "event_id":        r.get("event_id"),
            "installation_id": (r.get("meta") or {}).get("installation_id"),
            "meta":            r.get("meta"),
            "ts":              _iso(r.get("ts")),
            "_sort_ts":        r.get("ts") or 0,
        })
    for r in webhook_rows:
        combined.append({
            "kind":            "webhook",
            "event_type":      r.get("event"),
            "action":          r.get("action"),
            "delivery_id":     r.get("_id"),
            "installation_id": r.get("installation"),
            "ts":              _iso(r.get("received_at")),
            "_sort_ts":        r.get("received_at") or 0,
        })
    for r in funnel_evt_rows:
        combined.append({
            "kind":            "signup_funnel",
            "event_type":      r.get("event_type"),
            "meta":            r.get("metadata"),
            "ts":              _iso(r.get("ts_epoch")),
            "_sort_ts":        r.get("ts_epoch") or 0,
        })
    combined.sort(key=lambda e: e["_sort_ts"])
    for e in combined:
        e.pop("_sort_ts", None)

    return {
        "user_id":                user_id,
        "github_installations":   installations,
        "projects": {
            "count": len(projects),
            "rows":  projects,
        },
        "oauth_states":           oauth_states,
        "funnel_events":          funnel_events,
        "ordered_event_stream":   combined,
    }
