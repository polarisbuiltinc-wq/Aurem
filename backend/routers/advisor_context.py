"""
routers/advisor_context.py — Iter 212m-209 / 212m-210 (RBAC tier split)

Read-only aggregation endpoint that powers the Ask Advisor panel's
project-scoped answers.  Returns a single JSON blob per project_id
combining live signals from up to four sources:

    Tier 1 (all authenticated users)
    --------------------------------
    1. `findings`         — cto_open_findings row counts by severity
    2. `quota`            — the current user's monthly token quota row

    Tier 2 (founders only — is_admin | tier=="founder" | FOUNDER_EMAILS)
    -------------------------------------------------------------------
    3. `council`          — latest council_health_probes row
    4. `deploy_sync`      — self /version vs prod /version comparison

For non-founders `council` and `deploy_sync` are entirely omitted
from the response (not just nulled) so no infra fingerprints leak.
The response carries `role: "founder" | "user"` so both the frontend
morning-brief pill and the server-side LLM prompt injector can shape
their output accordingly.

Design contract
---------------
• Pure read.  No side effects, no LLM calls, no tool loop.
• Project scoped — 404 if the caller doesn't own the project.
• EVERY tier-1 field can be `None`; consumers (LLM + morning brief)
  must handle missing data by saying "yeh data abhi available nahi
  hai" and MUST NOT guess.
• Sub-second by design: 2–4 count/find operations, no aggregation
  pipelines.
"""

# arch: allow-http — Ad-hoc GitHub fetches for Advisor snapshots (iter 212m-225)
from __future__ import annotations
import os
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Header, HTTPException

from routers.auth import current_dev
from cto_services.db import require_db
from services.usage import is_founder_email
from services.advisor_open_prs import fetch_open_prs

router = APIRouter(prefix="/api/aurem-dev", tags=["advisor"])

# Same PROD_ORIGIN AdminSystemHealth uses for deploy-sync detection.
PROD_ORIGIN = os.environ.get("AUREM_PROD_ORIGIN", "https://auremcto.com")
_SELF_COMMIT = None  # lazy — filled from local /version on first call


async def _self_commit() -> Optional[str]:
    """Read our own commit_sha via the version router state.

    Kept lazy + best-effort — if the version router isn't mounted (very
    early boot) we just return None and the deploy-sync card degrades
    gracefully.
    """
    global _SELF_COMMIT
    if _SELF_COMMIT is not None:
        return _SELF_COMMIT
    try:
        from routers.version import _COMMIT_SHA as _local
        _SELF_COMMIT = _local
    except Exception:
        _SELF_COMMIT = None
    return _SELF_COMMIT


@router.get("/advisor/context")
async def get_advisor_context(
    project_id: str,
    authorization: Optional[str] = Header(None),
) -> dict:
    me = await current_dev(authorization)
    user_id = me["user_id"]
    db = require_db()

    # ── Founder gate ───────────────────────────────────────────
    # Council-status and deploy-sync are infra fingerprints; only
    # founders see them.  Same rule the admin gate + advisor LLM
    # prompt use elsewhere so we stay consistent.
    is_founder = bool(
        me.get("is_admin")
        or me.get("tier") == "founder"
        or is_founder_email(me.get("email"))
    )

    # ── Project ownership + name ────────────────────────────────
    proj = await db["cto_projects"].find_one(
        {"project_id": project_id, "user_id": user_id},
        {"_id": 0, "project_id": 1, "name": 1, "github_owner": 1, "github_repo": 1},
    )
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    proj_name = (
        proj.get("name")
        or (f"{proj.get('github_owner')}/{proj.get('github_repo')}"
            if proj.get("github_owner") else project_id)
    )

    # ── 1. Findings ─── project_id + user_id BOTH filtered, hard limit
    findings = {"total": 0, "p0": 0, "p1": 0, "p2": 0, "error": None}
    try:
        _filter_base = {"project_id": project_id, "user_id": user_id, "status": {"$ne": "resolved"}}
        findings["total"] = await db["cto_open_findings"].count_documents(
            _filter_base, maxTimeMS=1500, limit=5000,
        )
        for sev, key in [("P0", "p0"), ("P1", "p1"), ("P2", "p2")]:
            findings[key] = await db["cto_open_findings"].count_documents(
                {**_filter_base, "severity": sev}, maxTimeMS=1500, limit=5000,
            )
    except Exception as e:
        findings["error"] = str(e)[:80]

    # ── 2. Council A latest probe — ALLOWLIST projection ───────
    # Founder-only.  Non-founders never touch this collection.
    council = None
    if is_founder:
        council = {"live": None, "primary_actual": None, "primary_intended": None,
                   "last_probe": None, "error": None}
        try:
            row = await db["council_health_probes"].find_one(
                {"council": "A"},
                projection={"_id": 0, "live": 1, "primary_actual": 1,
                            "primary_intended": 1, "last_probe": 1, "created_at": 1},
                sort=[("_id", -1)],
                max_time_ms=1500,
            )
            if row:
                council["live"] = row.get("live")
                council["primary_actual"] = row.get("primary_actual")
                council["primary_intended"] = row.get("primary_intended")
                lp = row.get("last_probe") or row.get("created_at")
                if lp:
                    council["last_probe"] = lp.isoformat() if hasattr(lp, "isoformat") else str(lp)
        except Exception as e:
            council["error"] = str(e)[:80]

    # ── 3. Deploy sync — same as before ────────────────────────
    # Founder-only.  Non-founders never learn our commit SHA.
    deploy_sync = None
    if is_founder:
        deploy_sync = {"self_sha": None, "prod_sha": None, "in_sync": None, "error": None}
        try:
            deploy_sync["self_sha"] = await _self_commit()
            try:
                async with httpx.AsyncClient(timeout=4.0) as cx:
                    r = await cx.get(f"{PROD_ORIGIN}/api/aurem-dev/version")
                    if r.status_code == 200:
                        deploy_sync["prod_sha"] = (r.json() or {}).get("commit_sha")
            except Exception as _e:
                deploy_sync["error"] = f"prod fetch failed: {str(_e)[:60]}"
            if deploy_sync["self_sha"] and deploy_sync["prod_sha"]:
                if deploy_sync["self_sha"] == "unknown" or deploy_sync["prod_sha"] == "unknown":
                    deploy_sync["in_sync"] = None
                else:
                    deploy_sync["in_sync"] = (deploy_sync["self_sha"] == deploy_sync["prod_sha"])
        except Exception as e:
            deploy_sync["error"] = str(e)[:80]

    # ── 4. Quota — ALLOWLIST projection (no api_key, no secrets) ─
    quota = {"tokens_used": None, "tokens_limit": None, "period": None, "error": None}
    try:
        q = await db["dev_user_quota"].find_one(
            {"user_id": user_id},
            projection={"_id": 0, "tokens_used": 1, "tokens_limit": 1,
                        "monthly_limit": 1, "period": 1, "month": 1},
            max_time_ms=1500,
        )
        if q:
            quota["tokens_used"] = q.get("tokens_used")
            quota["tokens_limit"] = q.get("tokens_limit") or q.get("monthly_limit")
            quota["period"] = q.get("period") or q.get("month")
    except Exception as e:
        quota["error"] = str(e)[:80]

    # ── 5. Recent tasks (Iter 388j · Bug 3 fix) ─────────────────
    # Advisor previously had ZERO run-state data — so "Diagnose failed
    # run" fell through to screenshot vision, which described stale
    # scrollback as "current state". Fetch the last 5 tasks for this
    # project so the LLM can name the actual latest failure (or say
    # "no recent failures" honestly).
    recent_tasks: dict = {"items": [], "error": None}
    try:
        cursor = db["cto_tasks"].find(
            {"project_id": project_id, "user_id": user_id},
            projection={
                "_id": 0, "task_id": 1, "status": 1, "task": 1,
                "result": 1, "commit_sha": 1, "error": 1,
                "created_at": 1, "completed_at": 1, "tokens_used": 1,
            },
            sort=[("created_at", -1)],
            max_time_ms=1500,
        ).limit(5)
        rows = await cursor.to_list(5)
        for r in rows:
            recent_tasks["items"].append({
                "task_id":      (r.get("task_id") or "")[:36],
                "status":       r.get("status"),
                "summary":      (r.get("task") or "")[:140],
                "result":       (r.get("result") or "")[:200],
                "sha":          (r.get("commit_sha") or "")[:12],
                "error":        (r.get("error") or "")[:200],
                "created_at":   r.get("created_at"),
                "completed_at": r.get("completed_at"),
                "tokens_used": r.get("tokens_used"),
            })
    except Exception as e:
        recent_tasks["error"] = str(e)[:80]

    # ── 6. Open PRs (Iter 388j · Bug 4 fix) ─────────────────────
    # Advisor "Summarize open PRs" chip was silently hanging because
    # there was no GitHub data source at all — the LLM either froze
    # or fell back to screenshot description. Fetch open PRs via
    # unauthenticated GitHub API (public repos work; private repos
    # will 404 gracefully). Hard 4s timeout so a slow GitHub can't
    # stall the advisor.
    _gh_owner = proj.get("github_owner")
    _gh_repo  = proj.get("github_repo")
    open_prs = await fetch_open_prs(_gh_owner, _gh_repo)

    # ── 7. Token breakdown (Iter 388j · Bug 5 fix) ──────────────
    # "Token breakdown" chip previously described SCREENSHOT UI
    # elements because advisor_context only exposed the aggregate
    # tokens_used integer. Provide the top 5 recent tasks' token
    # counts + project-scoped monthly total so the LLM has real
    # numbers to answer with.
    token_breakdown: dict = {
        "recent_task_tokens": [], "project_month_total": 0,
        "user_month_total": None, "error": None,
    }
    try:
        _month_start_dt = datetime.now(timezone.utc).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0,
        )
        _month_start = _month_start_dt.timestamp()
        # Per-task recent-5 tokens (already fetched above, reuse).
        for item in recent_tasks["items"]:
            if item.get("tokens_used") is not None:
                token_breakdown["recent_task_tokens"].append({
                    "task_id":     item["task_id"][:8],
                    "summary":     item["summary"][:80],
                    "status":      item["status"],
                    "tokens_used": item["tokens_used"],
                })
        # Project-scoped month total (sum across THIS project only).
        agg = await db["cto_tasks"].aggregate([
            {"$match": {
                "project_id": project_id,
                "user_id":    user_id,
                "status":     "done",
                "created_at": {"$gte": _month_start},
            }},
            {"$group": {"_id": None, "total": {"$sum": "$tokens_used"}}},
        ], maxTimeMS=1500).to_list(1)
        if agg:
            token_breakdown["project_month_total"] = int(agg[0].get("total") or 0)
        # User-scoped month total (all projects).
        agg2 = await db["cto_tasks"].aggregate([
            {"$match": {
                "user_id":    user_id,
                "status":     "done",
                "created_at": {"$gte": _month_start},
            }},
            {"$group": {"_id": None, "total": {"$sum": "$tokens_used"}}},
        ], maxTimeMS=1500).to_list(1)
        if agg2:
            token_breakdown["user_month_total"] = int(agg2[0].get("total") or 0)
    except Exception as e:
        token_breakdown["error"] = str(e)[:80]

    resp: dict = {
        "project_id":   project_id,
        "project_name": proj_name,
        "role":         "founder" if is_founder else "user",
        "findings":     findings,
        "quota":        quota,
        # Iter 388j — three new blocks for the Advisor chip buttons.
        # Each block carries an `error` field so the LLM can honestly
        # say "yeh data abhi available nahi hai" when a source is
        # unreachable, rather than fabricating from the screenshot.
        "recent_tasks":    recent_tasks,
        "open_prs":        open_prs,
        "token_breakdown": token_breakdown,
        "checked_at":   datetime.now(timezone.utc).isoformat(),
    }
    # Tier-2 keys only appear for founders — non-founders never see
    # these fields on the wire (no `null`, no empty dict).  This
    # keeps infra fingerprints out of standard-user browsers even
    # if a rogue extension logs the network tab.
    if is_founder:
        resp["council"]     = council
        resp["deploy_sync"] = deploy_sync
    return resp
