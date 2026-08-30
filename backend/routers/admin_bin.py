"""
routers/admin_bin.py — Iter 212m-171  (Admin Panel Rebuild — Sections 1-5+)

New admin surface introduced by the "AUREM Admin Panel Full Rebuild"
spec:

  Section 1: BIN Tracker  ─  /admin/bin/{bin_id}/projects
                             /admin/users/{bin_id}/tier
  Section 2: Feature Flags ─  /admin/feature-flags/{flag}/user-override  POST+DELETE
                             /admin/feature-flags-sync-env               (boot helper)
  Section 3: LLM Credits   ─  /admin/llm-credits
                             /admin/llm-credit-alert                     POST
  Section 5: Parliament    ─  /admin/parliament/live
  Boundary probes tile     ─  /admin/boundary-probes

Existing admin.py (3680 LOC) is UNTOUCHED — this file is mounted under
the same "/admin" prefix so paths compose naturally.

Reuse:
  • cto_services.auth._require_admin  — every endpoint is admin-gated
  • services.vault._decrypt_pat        — for PAT-validity check
  • services.house_rules               — for advisor_llm slugs
  • routers.cto_projects._decrypt_pat, _user_gh_token — via local import
"""
# arch: allow-http — BIN tracker external API probes (iter 212m-225)
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Header, HTTPException, Depends
from cto_services.auth import require_admin_dep as _require_admin_dep
from pydantic import BaseModel, Field
from services.http import ext_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["Admin / Rebuild"],
                   dependencies=[Depends(_require_admin_dep)])  # Iter 358 router-level gate


# ── Local admin guard (delegates to the shared one) ─────────────────


async def _require_admin(authorization: Optional[str]) -> dict:
    """Wraps the shared admin-required check.

    Iter 212m-230 — Delegates to `cto_services.auth.require_admin`
    (the canonical implementation) instead of the router-side
    `_require_admin` in admin.py.  Removes the last
    `admin_bin → admin → … → main → admin_bin` cycle that
    architecture_health has been flagging.
    """
    from cto_services.auth import require_admin as _svc_require_admin
    return await _svc_require_admin(authorization)


# ────────────────────────────────────────────────────────────────────
# SECTION 1 — BIN TRACKER
# ────────────────────────────────────────────────────────────────────


class TierChangeBody(BaseModel):
    tier: str = Field(..., pattern=r"^(free|starter|pro|team|founder)$")


@router.get("/bin/{bin_id}/projects")
async def bin_tracker_projects(
    bin_id: str,
    authorization: Optional[str] = Header(None),
):
    """List all projects for a BIN with live PAT validity + last activity."""
    await _require_admin(authorization)
    from cto_services.db import get_db
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB unavailable")

    # Verify user exists
    user = await db.dev_users.find_one({"user_id": bin_id})
    if not user:
        raise HTTPException(404, "BIN not found")

    projs = await db.cto_projects.find(
        {"user_id": bin_id},
        {"_id": 0, "project_id": 1, "name": 1, "github_owner": 1,
         "github_repo": 1, "branch": 1, "github_token": 1,
         "tasks_done": 1, "last_task": 1, "updated_at": 1, "created_at": 1,
         "auth_method": 1, "status": 1,
         "installation_id": 1, "user_id": 1},
    ).to_list(200)

    # Decrypt + probe each PAT in parallel (HEAD /repos/{o}/{r}).
    # 2026-02-11 · Phase 3b (Bug 2 fix) — get_repo_token unifies PAT
    # + github_app auth so App-installed projects show a real token
    # status in the admin probe instead of always "missing".
    # 2026-06 PAT-removal — App-only, no OAuth fallback.
    from services.pat_vault import get_repo_token_or_error

    async def _probe(p):
        pat, _auth_err, _ = await get_repo_token_or_error(p)
        pat_status = "missing"
        pat_last4 = None
        if pat:
            pat_last4 = pat[-4:]
            owner = (p.get("github_owner") or "").strip()
            repo = (p.get("github_repo") or "").strip()
            if owner and repo:
                try:
                    async with ext_client("github", timeout=httpx.Timeout(4.0)) as c:
                        r = await c.head(
                            f"https://api.github.com/repos/{owner}/{repo}",
                            headers={
                                "Authorization": f"Bearer {pat}",
                                "Accept": "application/vnd.github+json",
                            },
                        )
                    if r.status_code == 200:
                        pat_status = "valid"
                    elif r.status_code == 401:
                        pat_status = "invalid"
                    elif r.status_code == 404:
                        pat_status = "repo_not_found"
                    else:
                        pat_status = f"http_{r.status_code}"
                except Exception:
                    pat_status = "probe_error"
            else:
                pat_status = "no_repo"
        # Coerce datetime → iso
        for k in ("updated_at", "created_at", "last_task"):
            v = p.get(k)
            if hasattr(v, "isoformat"):
                p[k] = v.isoformat()
        return {**p, "pat_status": pat_status, "pat_last4": pat_last4,
                "github_token": None}   # never return ciphertext

    rows = []
    for p in projs:
        rows.append(await _probe(p))

    return {
        "ok": True,
        "bin_id": bin_id,
        "email": user.get("email"),
        "tier": user.get("tier", "free"),
        "is_admin": bool(user.get("is_admin")),
        "is_unlimited": bool(user.get("is_unlimited")),
        "project_count": len(rows),
        "projects": rows,
    }


@router.post("/users/{bin_id}/tier")
async def change_user_tier(
    bin_id: str,
    body: TierChangeBody,
    authorization: Optional[str] = Header(None),
):
    """Change a user's subscription tier."""
    admin = await _require_admin(authorization)
    from cto_services.db import get_db
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB unavailable")

    user = await db.dev_users.find_one({"user_id": bin_id})
    if not user:
        raise HTTPException(404, "BIN not found")

    prev_tier = user.get("tier", "free")
    await db.dev_users.update_one(
        {"user_id": bin_id},
        {"$set": {
            "tier": body.tier,
            "tier_updated_at": datetime.now(timezone.utc),
            "tier_updated_by": admin.get("user_id") or "admin",
        }},
    )
    # Audit trail
    try:
        await db.admin_audit.insert_one({
            "ts": datetime.now(timezone.utc),
            "actor": admin.get("email") or admin.get("user_id"),
            "action": "tier_change",
            "target_user_id": bin_id,
            "before": prev_tier,
            "after": body.tier,
        })
    except Exception:
        pass
    return {"ok": True, "bin_id": bin_id, "prev_tier": prev_tier,
            "new_tier": body.tier}


# ────────────────────────────────────────────────────────────────────
# SECTION 1b — GITHUB BULK REVOKE  (2026-08-30)
#
# Admin tool: bulk-select users/installations → force-uninstall the
# GitHub App for real, or (non-destructively) flag idle-but-working
# connections for re-engage follow-up.
#
# Built to fix a real near-miss: an admin almost revoked 27 of 28
# "never ran a task" users whose GitHub connection was in fact
# perfectly valid — only 1 was genuinely broken. `pat_status` must be
# visible and filterable BEFORE any selection happens, and the hard
# guard below is the second line of defense if a valid row still
# slips into a selection.
#
# STANDING GATE: real destructive use is OFF by default via the
# `github_bulk_revoke_live_verified` feature flag — the live drill-
# repo verify (U1-U6) has not been run against a real disposable
# installation yet (see /app/memory/GITHUB_BULK_REVOKE_DRILL_VERIFY.md).
# Flipping that flag ON is the founder's call, after that live test.
# ────────────────────────────────────────────────────────────────────

REVOKE_COOLDOWN_SECONDS = 30
_TERMINAL_LOOP_STATES = {"completed", "failed", "aborted", "expired"}


class GithubBulkRevokeBody(BaseModel):
    installation_ids: list[int] = Field(..., min_length=1, max_length=100)
    confirm_text: Optional[str] = None
    reason: Optional[str] = Field(None, max_length=500)
    dry_run: bool = False


class GithubFlagIdleBody(BaseModel):
    installation_ids: list[int] = Field(..., min_length=1, max_length=200)
    reason: Optional[str] = Field(None, max_length=500)


def _iso(v: Any) -> Any:
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, (int, float)):
        # Several collections (e.g. chat_sessions.updated_at) store epoch
        # seconds as a raw float, not a datetime — coerce so the frontend
        # always gets one consistent ISO string, never a bare number that
        # `new Date()` would misread as epoch MILLISECONDS.
        try:
            return datetime.fromtimestamp(v, tz=timezone.utc).isoformat()
        except (ValueError, OSError, OverflowError):
            return None
    return v


@router.get("/github/connections")
async def github_connections(
    authorization: Optional[str] = Header(None),
    view: str = "revokable",   # revokable | idle | all
):
    """Cross-user table of GitHub-App-connected projects, grouped by
    installation_id, for the bulk-revoke tool. Read-only, live-probes
    pat_status the same way BIN Tracker does (services.pat_vault.
    probe_pat_status) so the numbers here always match what an admin
    would see per-user in Section 1.

    Row classification:
      revokable — pat_status != "valid" (the ONLY ones eligible for
                  the destructive revoke action)
      idle      — pat_status == "valid" AND task_count == 0 (working
                  but unused — flag-for-re-engage candidate, NEVER
                  revoked by this tool)
      active    — pat_status == "valid" AND task_count > 0
    """
    await _require_admin(authorization)
    from cto_services.db import get_db
    from services import feature_flags as _ff
    from services.pat_vault import probe_pat_status

    db = get_db()
    if db is None:
        raise HTTPException(503, "DB unavailable")

    live_verified = await _ff.is_enabled("github_bulk_revoke_live_verified")

    projs = await db.cto_projects.find(
        {"github_owner": {"$exists": True, "$ne": None},
         "github_repo": {"$exists": True, "$ne": None},
         "auth_method": "github_app"},
        {"_id": 0, "project_id": 1, "github_owner": 1, "github_repo": 1,
         "auth_method": 1, "status": 1, "installation_id": 1,
         "user_id": 1, "tasks_done": 1, "re_engage_flagged": 1},
    ).to_list(2000)

    if not projs:
        return {"ok": True, "rows": [], "view": view, "live_verified": live_verified}

    user_ids = list({p.get("user_id") for p in projs if p.get("user_id")})
    project_ids = [p["project_id"] for p in projs if p.get("project_id")]

    users = await db.dev_users.find(
        {"user_id": {"$in": user_ids}}, {"_id": 0, "user_id": 1, "email": 1},
    ).to_list(len(user_ids) or 1)
    email_by_uid = {u["user_id"]: u.get("email") for u in users}

    last_sess_by_proj: dict[str, Any] = {}
    if project_ids:
        async for row in db.chat_sessions.aggregate([
            {"$match": {"project_id": {"$in": project_ids}}},
            {"$group": {"_id": "$project_id", "last": {"$max": "$updated_at"}}},
        ]):
            last_sess_by_proj[row["_id"]] = row.get("last")

    inflight_projects: set[str] = set()
    if project_ids:
        async for row in db.loop_sessions.find(
            {"project_id": {"$in": project_ids},
             "state": {"$nin": list(_TERMINAL_LOOP_STATES)}},
            {"_id": 0, "project_id": 1},
        ):
            if row.get("project_id"):
                inflight_projects.add(row["project_id"])

    sem = asyncio.Semaphore(10)

    async def _probe(p):
        async with sem:
            return await probe_pat_status(p)

    pat_results = await asyncio.gather(*[_probe(p) for p in projs])

    by_installation: dict[Any, dict] = {}
    for p, pat in zip(projs, pat_results):
        iid = p.get("installation_id")
        uid = p.get("user_id")
        row_key = iid if iid else f"no_install:{p['project_id']}"
        row = by_installation.setdefault(row_key, {
            "installation_id": iid,
            "email": email_by_uid.get(uid, uid),
            "user_id": uid,
            "auth_method": p.get("auth_method"),
            "repos": [],
            "pat_status": pat["pat_status"],
            "status": p.get("status"),
            "task_count": 0,
            "last_session_at": None,
            "in_flight_work": False,
            "re_engage_flagged": False,
        })
        repo_str = f"{p.get('github_owner')}/{p.get('github_repo')}"
        if repo_str not in row["repos"]:
            row["repos"].append(repo_str)
        row["task_count"] += int(p.get("tasks_done") or 0)
        ls = last_sess_by_proj.get(p["project_id"])
        if ls and (row["last_session_at"] is None or ls > row["last_session_at"]):
            row["last_session_at"] = ls
        if p["project_id"] in inflight_projects:
            row["in_flight_work"] = True
        if p.get("re_engage_flagged"):
            row["re_engage_flagged"] = True
        # Worst-status wins if one installation covers >1 repo.
        if pat["pat_status"] != "valid":
            row["pat_status"] = pat["pat_status"]

    rows = []
    for row in by_installation.values():
        row["repo"] = " · ".join(row.pop("repos"))
        row["last_session_at"] = _iso(row["last_session_at"])
        is_valid = row["pat_status"] == "valid"
        if is_valid and row["task_count"] == 0:
            row["classification"] = "idle"
        elif is_valid:
            row["classification"] = "active"
        else:
            row["classification"] = "revokable"
        row["revoke_eligible"] = bool(row["installation_id"]) and row["classification"] == "revokable"
        rows.append(row)

    if view in ("revokable", "idle"):
        rows = [r for r in rows if r["classification"] == view]
    # view == "all" -> no filter

    return {"ok": True, "rows": rows, "view": view, "live_verified": live_verified}


@router.post("/github/bulk-revoke")
async def github_bulk_revoke(
    body: GithubBulkRevokeBody,
    authorization: Optional[str] = Header(None),
):
    """Force-uninstall the GitHub App for the given installations —
    real GitHub call first, DB sync only on success/already-gone.
    `dry_run=true` returns the blast-radius preview (email/repo/
    pat_status per row) and makes ZERO GitHub calls / DB writes."""
    admin = await _require_admin(authorization)
    from cto_services.db import get_db
    from services import feature_flags as _ff
    from services.pat_vault import probe_pat_status

    db = get_db()
    if db is None:
        raise HTTPException(503, "DB unavailable")

    ids = list({int(i) for i in body.installation_ids})

    projs = await db.cto_projects.find(
        {"installation_id": {"$in": ids}, "auth_method": "github_app"},
        {"_id": 0, "project_id": 1, "github_owner": 1, "github_repo": 1,
         "installation_id": 1, "user_id": 1},
    ).to_list(2000)
    by_iid: dict[int, list[dict]] = {}
    for p in projs:
        by_iid.setdefault(int(p["installation_id"]), []).append(p)

    user_ids = list({p["user_id"] for p in projs if p.get("user_id")})
    users = await db.dev_users.find(
        {"user_id": {"$in": user_ids}}, {"_id": 0, "user_id": 1, "email": 1},
    ).to_list(len(user_ids) or 1)
    email_by_uid = {u["user_id"]: u.get("email") for u in users}

    preview = []
    valid_count = 0
    for iid in ids:
        member_projs = by_iid.get(iid) or []
        if not member_projs:
            preview.append({"installation_id": iid, "email": None,
                             "repo": None, "pat_status": "unknown_no_project"})
            continue
        statuses = await asyncio.gather(*[probe_pat_status(p) for p in member_projs])
        worst = "valid"
        for s in statuses:
            if s["pat_status"] != "valid":
                worst = s["pat_status"]
        if worst == "valid":
            valid_count += 1
        seen_repos: list[str] = []
        for p in member_projs:
            repo_str = f"{p['github_owner']}/{p['github_repo']}"
            if repo_str not in seen_repos:
                seen_repos.append(repo_str)
        preview.append({
            "installation_id": iid,
            "email": email_by_uid.get(member_projs[0].get("user_id")),
            "repo": " · ".join(seen_repos),
            "pat_status": worst,
        })

    if body.dry_run:
        return {"ok": True, "dry_run": True, "preview": preview,
                "valid_count": valid_count, "total": len(ids)}

    # ── HARD GUARD — server-side, cannot be bypassed from the client ──
    if valid_count > 0 and (body.confirm_text or "").strip().upper() != "REVOKE":
        raise HTTPException(400, detail={
            "error": "hard_guard_blocked",
            "message": (
                f"{valid_count} of your {len(ids)} selected installations "
                "have a WORKING GitHub connection. Revoking will break "
                "something that currently works for them. Retry with "
                "confirm_text=\"REVOKE\" to proceed."
            ),
            "valid_count": valid_count, "total": len(ids),
        })

    # ── Master kill-switch — OFF until the live drill-verify (U1-U6)
    #    is CONFIRMED against a real disposable installation. ────────
    if not await _ff.is_enabled("github_bulk_revoke_live_verified"):
        raise HTTPException(403, detail={
            "error": "live_verification_pending",
            "message": ("Live verification pending — this tool is not yet "
                        "cleared for use. See "
                        "GITHUB_BULK_REVOKE_DRILL_VERIFY.md; flip the "
                        "github_bulk_revoke_live_verified feature flag once "
                        "the real DELETE behavior has been confirmed."),
        })

    # ── Rate limit: 1 bulk-revoke batch per 30s per admin ────────────
    admin_key = admin.get("email") or admin.get("user_id") or "admin"
    last = await db.admin_audit.find_one(
        {"action": "bulk_github_revoke", "actor": admin_key},
        sort=[("ts", -1)],
    )
    if last and last.get("ts"):
        age = (datetime.now(timezone.utc) - last["ts"]).total_seconds()
        if age < REVOKE_COOLDOWN_SECONDS:
            raise HTTPException(429, detail=(
                f"Bulk-revoke rate limit — wait {int(REVOKE_COOLDOWN_SECONDS - age)}s "
                "before the next batch."
            ))

    from services.github_bulk_revoke import bulk_revoke as _bulk_revoke
    results = await _bulk_revoke(ids)

    now = datetime.now(timezone.utc)
    revoked, skipped, failed = [], [], []
    for r in results:
        iid = r["installation_id"]
        if r["outcome"] in ("deleted", "already_gone"):
            # Real GitHub call succeeded (or was already gone) — NOW
            # sync our own DB. Soft-revoke only: preserve the project
            # row + all its data (scans, findings, chat history).
            await db.github_installations.update_one(
                {"installation_id": int(iid)},
                {"$set": {"active": False, "revoked_at": now,
                          "revoked_by": admin_key, "updated_at": now}},
            )
            await db.cto_projects.update_many(
                {"installation_id": int(iid)},
                {"$set": {"installation_active": False, "status": "disconnected",
                          "installation_status_updated_at": now}},
            )
            (revoked if r["outcome"] == "deleted" else skipped).append(r)
        else:
            # GitHub call failed — do NOT touch our DB (the half-state
            # bug this tool exists to avoid).
            failed.append(r)

    audit_row = {
        "ts": now,
        "actor": admin_key,
        "action": "bulk_github_revoke",
        "installation_ids": ids,
        "reason": body.reason,
        "result": {"revoked": len(revoked), "skipped": len(skipped), "failed": len(failed)},
        "per_installation": results,
    }
    try:
        await db.admin_audit.insert_one(audit_row)
    except Exception as e:
        logger.warning("bulk_github_revoke audit write failed: %r", e)

    return {
        "ok": True,
        "revoked": revoked, "skipped": skipped, "failed": failed,
        "summary": (f"{len(revoked)} revoked, {len(failed)} failed, "
                    f"{len(skipped)} skipped (already uninstalled)"),
    }


@router.post("/github/flag-idle")
async def github_flag_idle(
    body: GithubFlagIdleBody,
    authorization: Optional[str] = Header(None),
):
    """Non-destructive: flags idle-but-working connections for a
    re-engage follow-up. Makes ZERO GitHub calls and revokes nothing —
    this is the safe action for the 27-of-28 case."""
    admin = await _require_admin(authorization)
    from cto_services.db import get_db
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB unavailable")

    ids = list({int(i) for i in body.installation_ids})
    now = datetime.now(timezone.utc)
    admin_key = admin.get("email") or admin.get("user_id") or "admin"
    res = await db.cto_projects.update_many(
        {"installation_id": {"$in": ids}},
        {"$set": {"re_engage_flagged": True, "re_engage_flagged_at": now,
                  "re_engage_flagged_by": admin_key,
                  "re_engage_reason": body.reason}},
    )
    try:
        await db.admin_audit.insert_one({
            "ts": now, "actor": admin_key, "action": "flag_idle_reengage",
            "installation_ids": ids, "reason": body.reason,
            "matched": res.modified_count,
        })
    except Exception:
        pass
    return {"ok": True, "flagged": res.modified_count}


# ────────────────────────────────────────────────────────────────────
# SECTION 2 — FEATURE FLAGS (user overrides + env sync)
# ────────────────────────────────────────────────────────────────────


class UserOverrideBody(BaseModel):
    bin_id: str
    value: bool


@router.post("/feature-flags/{flag}/user-override")
async def flag_user_override_set(
    flag: str,
    body: UserOverrideBody,
    authorization: Optional[str] = Header(None),
):
    """Set a per-user override for a feature flag."""
    admin = await _require_admin(authorization)
    from cto_services.db import get_db
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB unavailable")

    now = datetime.now(timezone.utc)
    # feature_flags.user_overrides is a dict {bin_id: {value, set_by, ts}}
    await db.feature_flags.update_one(
        {"flag": flag},
        {"$set": {
            f"user_overrides.{body.bin_id}": {
                "value":  bool(body.value),
                "set_by": admin.get("user_id") or "admin",
                "set_at": now,
            },
        }},
        upsert=True,
    )
    return {"ok": True, "flag": flag, "bin_id": body.bin_id,
            "value": body.value}


@router.delete("/feature-flags/{flag}/user-override/{bin_id}")
async def flag_user_override_remove(
    flag: str,
    bin_id: str,
    authorization: Optional[str] = Header(None),
):
    """Remove a per-user override for a feature flag."""
    await _require_admin(authorization)
    from cto_services.db import get_db
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB unavailable")
    await db.feature_flags.update_one(
        {"flag": flag},
        {"$unset": {f"user_overrides.{bin_id}": ""}},
    )
    return {"ok": True, "flag": flag, "bin_id": bin_id, "removed": True}


# ────────────────────────────────────────────────────────────────────
# SECTION 3 — LLM CREDIT MONITOR
# ────────────────────────────────────────────────────────────────────


class CreditAlertBody(BaseModel):
    threshold: float = Field(gt=0, le=1000)


# 60s in-process cache for OpenRouter balance (their API is rate-limited).
_OR_CREDIT_CACHE: dict = {"data": None, "ts": 0.0}


async def _fetch_openrouter_balance() -> dict:
    """Return {ok, balance_usd, currency, limit, used, raw} from OR key."""
    now = time.time()
    if _OR_CREDIT_CACHE["data"] and (now - _OR_CREDIT_CACHE["ts"]) < 60:
        return _OR_CREDIT_CACHE["data"]

    key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("EMERGENT_LLM_KEY")
    if not key:
        out = {"ok": False, "error": "no_api_key"}
        _OR_CREDIT_CACHE.update(data=out, ts=now)
        return out
    try:
        async with ext_client("openrouter", timeout=httpx.Timeout(6.0)) as c:
            r = await c.get(
                "https://openrouter.ai/api/v1/credits",
                headers={"Authorization": f"Bearer {key}"},
            )
        if r.status_code != 200:
            out = {"ok": False, "error": f"http_{r.status_code}",
                   "detail": r.text[:200]}
        else:
            j = r.json() or {}
            data = j.get("data") or {}
            total = float(data.get("total_credits") or 0)
            used = float(data.get("total_usage") or 0)
            out = {
                "ok": True,
                "balance_usd": round(total - used, 4),
                "total_credits": total,
                "total_usage": used,
                "currency": "USD",
            }
    except Exception as e:
        out = {"ok": False, "error": "exception", "detail": str(e)[:200]}
    _OR_CREDIT_CACHE.update(data=out, ts=now)
    return out


@router.get("/llm-credits")
async def llm_credits(
    authorization: Optional[str] = Header(None),
):
    """Consolidated view: OpenRouter balance + provider status + circuit
    breaker state + LongCat live flag + linters missing."""
    await _require_admin(authorization)
    from cto_services.db import get_db

    openrouter = await _fetch_openrouter_balance()

    # LongCat status — flag imported lazily so hot-reload picks up changes.
    try:
        from services import llm as _llm
        longcat_live = bool(getattr(_llm, "LONGCAT_LIVE", False))
    except Exception:
        longcat_live = False

    # Circuit-breaker state — heuristic: if there's a live LLM breaker
    # module use it, else "unknown".
    breaker_state = "unknown"
    try:
        from services.llm_circuit_breaker import get_breaker_state
        breaker_state = get_breaker_state()
    except Exception:
        pass

    # Linters missing (from Iter 212m-166 boot probe on app.state)
    # Iter 212m-230 — Read from services.app_state instead of
    # `from main import app` which formed a routers → main → routers
    # cycle that architecture_health has been flagging.
    linters_missing: list[str] = []
    try:
        from services.app_state import get_state as _svc_get_state
        linters_missing = list(_svc_get_state("loop_linters_missing", []) or [])
    except Exception:
        pass

    # Iter 212m-190 (Directive Session 2 · Part B) — Full-Scan health.
    # Extends the existing linter degraded surface to also reflect
    # scanner availability. If Bug Hunt or HTTP-headers or Docker CIS
    # errored on the last run, `full_scan_health.status == "degraded"`
    # so the dashboard shows an honest "not full coverage" state
    # rather than claiming green.
    full_scan_health: dict = {"status": "unknown"}
    try:
        from services.loop_full_scan import get_full_scan_health
        full_scan_health = get_full_scan_health()
    except Exception:
        pass

    # Threshold from settings collection
    threshold = 5.0
    db = get_db()
    if db is not None:
        try:
            s = await db.settings.find_one({"_id": "llm_credit_alert"})
            if s and "threshold" in s:
                threshold = float(s["threshold"])
        except Exception:
            pass

    providers = [
        {"id": "openrouter", "label": "OpenRouter",
         "status": "ok" if openrouter.get("ok") else "error",
         "balance_usd": openrouter.get("balance_usd"),
         "detail": openrouter.get("error") or ""},
        {"id": "longcat", "label": "LongCat (Council A primary)",
         "status": "ok" if longcat_live else "fallback",
         "detail": "" if longcat_live else "Falling back to GLM-5.2"},
        {"id": "deepseek", "label": "DeepSeek (Council C)",
         "status": "ok",  # routed through OR
         "detail": "via OpenRouter"},
        {"id": "glm52", "label": "GLM-5.2 (Council B + Advisor)",
         "status": "ok", "detail": "via OpenRouter"},
        {"id": "claude", "label": "Claude Sonnet 4.5 (CEO Judge)",
         "status": "ok", "detail": "via OpenRouter"},
        {"id": "groq", "label": "Groq Llama-3.3-70B (rescue)",
         "status": "ok", "detail": "Free tier, rate-limited"},
    ]
    return {
        "ok": True,
        "providers": providers,
        "openrouter": openrouter,
        "longcat_live": longcat_live,
        "circuit_breaker": breaker_state,
        "linters_missing": linters_missing,
        "full_scan_health": full_scan_health,
        "threshold_usd": threshold,
        "last_checked": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/llm-credit-alert")
async def llm_credit_alert_set(
    body: CreditAlertBody,
    authorization: Optional[str] = Header(None),
):
    """Persist the alert threshold that a background job compares against."""
    admin = await _require_admin(authorization)
    from cto_services.db import get_db
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB unavailable")
    await db.settings.update_one(
        {"_id": "llm_credit_alert"},
        {"$set": {
            "threshold": float(body.threshold),
            "updated_at": datetime.now(timezone.utc),
            "updated_by": admin.get("user_id") or "admin",
        }},
        upsert=True,
    )
    return {"ok": True, "threshold": body.threshold}


# ────────────────────────────────────────────────────────────────────
# SECTION 5 — PARLIAMENT LIVE
# ────────────────────────────────────────────────────────────────────


@router.get("/parliament/live")
async def parliament_live(
    authorization: Optional[str] = Header(None),
    window_hours: int = 24,
):
    """Per-council live state — model in use + call counts + LongCat flag."""
    await _require_admin(authorization)
    from cto_services.db import get_db
    db = get_db()

    since = datetime.now(timezone.utc)
    from datetime import timedelta
    cutoff = since - timedelta(hours=max(1, min(168, window_hours)))

    # Read from parliament_log if it exists, else zeros.
    counts_by_council = {"A": 0, "B": 0, "C": 0, "CEO": 0}
    rescues = 0
    if db is not None:
        try:
            pipeline = [
                {"$match": {"timestamp": {"$gte": cutoff}}},
                {"$group": {"_id": "$council", "n": {"$sum": 1}}},
            ]
            async for row in db.parliament_log.aggregate(pipeline):
                key = str(row.get("_id") or "").upper()
                if key in counts_by_council:
                    counts_by_council[key] += int(row.get("n") or 0)
            rescues = await db.parliament_log.count_documents(
                {"timestamp": {"$gte": cutoff}, "event": "rescue"},
            )
        except Exception as e:
            logger.debug("parliament/live aggregate failed: %r", e)

    try:
        from services import llm as _llm
        longcat_live = bool(getattr(_llm, "LONGCAT_LIVE", False))
    except Exception:
        longcat_live = False

    return {
        "ok": True,
        "window_hours": window_hours,
        "councils": [
            {"id": "A", "label": "Council A (Code Fix)",
             "model_primary": "LongCat" if longcat_live else "GLM-5.2 (LongCat down)",
             "model_fallback": "GLM-5.2",
             "calls": counts_by_council["A"]},
            {"id": "B", "label": "Council B (Analysis)",
             "model_primary": "GLM-5.2",
             "model_fallback": "—",
             "calls": counts_by_council["B"]},
            {"id": "C", "label": "Council C (Writing)",
             "model_primary": "DeepSeek V3",
             "model_fallback": "—",
             "calls": counts_by_council["C"]},
            {"id": "CEO", "label": "CEO Judge",
             "model_primary": "Claude Sonnet 4.5",
             "model_fallback": "GLM-5.2",
             "calls": counts_by_council["CEO"],
             "rescues": rescues},
        ],
        "longcat_live": longcat_live,
    }


# ────────────────────────────────────────────────────────────────────
# BOUNDARY PROBES TILE (Iter 212m-171)
# ────────────────────────────────────────────────────────────────────


@router.get("/boundary-probes")
async def boundary_probes(
    authorization: Optional[str] = Header(None),
    window_hours: int = 24,
):
    """Count of ORA boundary violations in the window.  Reads from
    admin_audit / audit_log entries with error_class=ora_boundary_violation
    OR from the in-memory counter."""
    await _require_admin(authorization)
    from cto_services.db import get_db
    db = get_db()
    if db is None:
        return {"ok": True, "count_today": 0, "count_window": 0,
                "window_hours": window_hours}

    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, min(168, window_hours)))
    day_cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    count_window = 0
    count_today = 0
    try:
        count_window = await db.audit_log.count_documents({
            "ts": {"$gte": cutoff},
            "event": "ora_boundary_violation",
        })
        count_today = await db.audit_log.count_documents({
            "ts": {"$gte": day_cutoff},
            "event": "ora_boundary_violation",
        })
    except Exception as e:
        logger.debug("boundary_probes aggregate failed: %r", e)

    return {
        "ok": True,
        "count_today": count_today,
        "count_window": count_window,
        "window_hours": window_hours,
    }


__all__ = ["router"]
