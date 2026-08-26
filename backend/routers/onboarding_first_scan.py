"""routers/onboarding_first_scan.py — Onboarding Step 4 · S-B (2026-08-26).

Endpoints for the first-scan results card. Auth-gated, user-scoped
(every read/write is filtered by the caller's own user_id).

  GET  /onboarding/first-scan/status?project_id=   — poll for results
  POST /onboarding/first-scan/viewed               — findings_viewed
  POST /onboarding/first-scan/apply                — "Fix all N for me"

Does NOT go through `routers/founder_offer.py`'s claim/confirm flow
(see services/onboarding_first_scan.py docstring — decoupled from the
promotional spot counter per founder decision (c))."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from cto_services.auth import current_dev
from cto_services.db import get_db
from services.onboarding_first_scan import is_still_scanning_slow
from services.feature_flags import is_enabled

router = APIRouter(prefix="/onboarding/first-scan", tags=["Onboarding Step 4"])

# Build Prompt v4 · Phase A — WorkCard rollout flag. Default OFF; ON only
# for the allowlisted test account until the read-back/idempotency fix is
# proven stable (D2/D3). See services/feature_flags.py for the schema.
_WORKCARD_FLAG = "workcard_first_scan"


async def _owned_project_or_404(db, user_id: str, project_id: str) -> dict:
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user_id})
    if not proj:
        raise HTTPException(404, "project not found")
    return proj


@router.get("/status")
async def get_first_scan_status(
    project_id: str, authorization: Optional[str] = Header(None),
) -> dict:
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "database unavailable")
    await _owned_project_or_404(db, user["user_id"], project_id)

    workcard_enabled = await is_enabled(_WORKCARD_FLAG, user_id=user["user_id"])

    row = await db.first_scan_results.find_one({"project_id": project_id})
    if not row:
        # No scan was ever triggered for this project (e.g. it's not
        # the user's first repo — S-B "second repo" edge case).
        return {"status": "skipped", "workcard_enabled": workcard_enabled}

    status = row.get("status", "scanning")
    resp: dict = {"status": status, "workcard_enabled": workcard_enabled}
    if status == "scanning" and is_still_scanning_slow(row.get("started_at")):
        resp["status"] = "still_scanning"
        resp["message"] = "Still scanning — this is a large repo. You can start chatting in the meantime."
    elif status == "ready":
        resp["cards"] = row.get("cards", [])
        resp["more_count"] = row.get("more_count", 0)
        resp["findings_count"] = row.get("findings_count", 0)
        resp["scan_duration_ms"] = row.get("scan_duration_ms")
        # Phase A read-back fix (BUILD PROMPT v4 §3): a previously-applied
        # fix is already saved on this row — surface it so a reload shows
        # "already fixed" instead of reverting to the unfixed findings card.
        if row.get("commit_sha"):
            resp["commit_sha"] = row.get("commit_sha")
            resp["commit_url"] = row.get("commit_url")
            resp["files_fixed"] = row.get("files_fixed")
            fixed_at = row.get("fix_applied_at")
            resp["fix_applied_at"] = fixed_at.isoformat() if fixed_at else None
    elif status == "error":
        resp["message"] = "I couldn't scan your repo right now, but you can still ask me to build or fix anything."
    return resp


class _ViewedBody(BaseModel):
    project_id: str


@router.post("/viewed")
async def mark_first_scan_viewed(
    body: _ViewedBody, authorization: Optional[str] = Header(None),
) -> dict:
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "database unavailable")
    proj = await _owned_project_or_404(db, user["user_id"], body.project_id)

    row = await db.first_scan_results.find_one({"project_id": body.project_id})
    if row and not row.get("findings_viewed_at"):
        await db.first_scan_results.update_one(
            {"project_id": body.project_id},
            {"$set": {"findings_viewed_at": datetime.now(timezone.utc)}},
        )
        from services.signup_guards import emit_first_scan_findings_viewed
        await emit_first_scan_findings_viewed(
            db, user_id=user["user_id"], project_id=body.project_id)
    return {"ok": True}


class _ApplyBody(BaseModel):
    project_id: str


@router.post("/apply")
async def apply_first_scan_fixes(
    body: _ApplyBody, authorization: Optional[str] = Header(None),
) -> dict:
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "database unavailable")
    await _owned_project_or_404(db, user["user_id"], body.project_id)

    # Phase A idempotency (BUILD PROMPT v4 §4) — reuses the exact atomic
    # claim shape as the loop ship-claim precedent (loop_engine.py:3296-3320):
    # this is a mutating, repo-writing POST, so a disabled client button
    # alone can't stop a double-commit (double-click, retry, two tabs).
    # Only the winner of this claim runs run_seo_fixes; every other caller
    # gets the already-committed result back instead of re-running it.
    claim = await db.first_scan_results.find_one_and_update(
        {
            "project_id": body.project_id,
            "fix_claimed_at": {"$exists": False},
            "commit_sha": {"$exists": False},
        },
        {"$set": {"fix_claimed_at": datetime.now(timezone.utc)}},
    )
    if claim is None:
        existing = await db.first_scan_results.find_one(
            {"project_id": body.project_id}) or {}
        if existing.get("commit_sha"):
            return {
                "ok": True,
                "commit_sha": existing.get("commit_sha"),
                "commit_url": existing.get("commit_url"),
                "files_fixed": existing.get("files_fixed", 0),
                "already_applied": True,
            }
        raise HTTPException(
            409, "A fix is already being applied for this project — "
            "please wait a moment and refresh.")

    from services.signup_guards import emit_first_scan_fix_clicked
    await emit_first_scan_fix_clicked(
        db, user_id=user["user_id"], project_id=body.project_id)

    from services.onboarding_first_scan import _alt_provider, _default_title_description
    proj = await db.cto_projects.find_one({"project_id": body.project_id}) or {}
    title, description = _default_title_description(proj)
    from services.seo.orchestrator import SeoOptions, run_seo_fixes
    try:
        result = await run_seo_fixes(
            user_id=user["user_id"], project_id=body.project_id,
            options=SeoOptions(plan="swift", dry_run=False,
                               title=title, description=description,
                               alt_provider=_alt_provider,
                               commit_message="chore(seo): AUREM first-scan fix"),
        )
    except Exception:
        # Release the claim so a retry is possible — a crashed attempt
        # must not permanently lock the user out of ever fixing this.
        await db.first_scan_results.update_one(
            {"project_id": body.project_id}, {"$unset": {"fix_claimed_at": ""}})
        raise

    if not result.get("ok") or not result.get("committed"):
        await db.first_scan_results.update_one(
            {"project_id": body.project_id}, {"$unset": {"fix_claimed_at": ""}})
        raise HTTPException(
            502,
            "Couldn't commit the fix right now — you can still ask me to "
            "build or fix anything in chat.",
        )
    await db.first_scan_results.update_one(
        {"project_id": body.project_id},
        {"$set": {"fix_applied_at": datetime.now(timezone.utc),
                  "commit_sha": result.get("commit_sha"),
                  "commit_url": result.get("commit_url"),
                  "files_fixed": result.get("patch_count", 0)}},
    )
    return {
        "ok": True,
        "commit_sha": result.get("commit_sha"),
        "commit_url": result.get("commit_url"),
        "files_fixed": result.get("patch_count", 0),
    }
