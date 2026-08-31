"""
routers/visibility.py — Visibility Kit (SEO+GEO+AEO) Phase B API (spec §5).

GET  /visibility/catalog                       → 7-item catalog
GET  /visibility/projects/{pid}/state           → score + per-item status
PUT  /visibility/projects/{pid}/bot-policy      → save training-bot choices
POST /visibility/projects/{pid}/apply           → apply engine (task-quota gated)
POST /visibility/applications/{id}/revert       → close PR + delete branch
"""
from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel

from cto_services.auth import current_dev
from cto_services.db import get_db

router = APIRouter(prefix="/visibility", tags=["Visibility Kit"])

# 2026-08-31 — Apply's plan-tier paywall (Pro+) is REMOVED for a
# limited promotional period: every tier, including Free, can Apply.
# It is NOT unmetered — it draws from the SAME monthly task quota as
# every other fix tool (services/scan_fix_quota.py), 1 apply = 1 task,
# deducted only on a successful PR open. No separate Kit price exists.


@router.get("/catalog")
async def get_catalog():
    db = get_db()
    items = await db.visibility_items.find({}, {"_id": 0}).sort("sort", 1).to_list(20)
    return {"ok": True, "items": items}


@router.get("/projects/{project_id}/state")
async def get_state(project_id: str, authorization: str = Header(None)):
    user = await current_dev(authorization)
    db = get_db()
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user["user_id"]}, {"_id": 0},
    )
    if not proj:
        raise HTTPException(404, "project_not_found_or_not_yours")

    items = await db.visibility_items.find({}, {"_id": 0}).sort("sort", 1).to_list(20)
    states = {
        s["item_id"]: s
        async for s in db.visibility_state.find({"project_id": project_id}, {"_id": 0})
    }
    rows = []
    total_weight = sum(i["weight"] for i in items)
    earned = 0.0
    for item in items:
        st = states.get(item["key"], {})
        status = st.get("status", "missing")
        if status in ("pr_merged", "live"):
            earned += item["weight"]
        elif status == "pr_created":
            earned += item["weight"] / 2  # §3 SCORE formula
        rows.append({
            "key": item["key"], "name": item["name"], "what_why": item["what_why"],
            "weight": item["weight"], "mode": item["mode"],
            "status": status, "detected_framework": st.get("detected_framework"),
            "detail": st.get("detail") or {},
        })
    score = round(100 * earned / total_weight) if total_weight else 0

    policy = await db.visibility_bot_policies.find_one(
        {"project_id": project_id}, {"_id": 0},
    )
    from services.feature_flags import is_enabled as _kit_flag_enabled
    apply_enabled = await _kit_flag_enabled("kit_apply_enabled", user_id=user["user_id"])
    return {
        "ok": True, "score": score, "items": rows,
        "bot_policy": (policy or {}).get("training_choice", {}),
        "apply_enabled": apply_enabled,
        "apply_disabled_reason": (
            None if apply_enabled else
            "Apply available once ship-via-PR (R9) is proven live on production."
        ),
        # 2026-08-31 — no separate Kit price exists; free for a limited
        # promotional period on every plan. Applying still draws 1 task
        # from your existing monthly quota (services/scan_fix_quota.py).
        "pricing_note": (
            "Free to use for a limited time — Apply still uses 1 task "
            "from your plan's regular monthly quota, no extra charge."
        ),
    }


class BotPolicyBody(BaseModel):
    training_choice: dict[str, str]


@router.put("/projects/{project_id}/bot-policy")
async def set_bot_policy(project_id: str, body: BotPolicyBody, authorization: str = Header(None)):
    user = await current_dev(authorization)
    db = get_db()
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user["user_id"]}, {"_id": 0, "project_id": 1},
    )
    if not proj:
        raise HTTPException(404, "project_not_found_or_not_yours")
    await db.visibility_bot_policies.update_one(
        {"project_id": project_id},
        {"$set": {"training_choice": body.training_choice, "updated_at": time.time()}},
        upsert=True,
    )
    return {"ok": True}


class ApplyBody(BaseModel):
    items: list[str]
    force: bool = False


@router.post("/projects/{project_id}/apply")
async def apply_kit(project_id: str, body: ApplyBody, authorization: str = Header(None)):
    user = await current_dev(authorization)
    db = get_db()
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user["user_id"]}, {"_id": 0},
    )
    if not proj:
        raise HTTPException(404, "project_not_found_or_not_yours")

    # 2026-08-30 — R9-gate. Apply opens a real PR against the user's repo
    # via the SAME ship-via-PR path R9 governs. Master kill-switch, DEFAULT
    # OFF, independent of the billing-tier gate below — flipped ON only
    # once R9 is proven live on production (a real ship, PR->merge->Live,
    # not just the flag being on). See LOOP-STATE.md R9 carry-forward note.
    from services.feature_flags import is_enabled as _kit_flag_enabled
    if not await _kit_flag_enabled("kit_apply_enabled", user_id=user["user_id"]):
        raise HTTPException(403, {
            "error": "apply_not_yet_enabled",
            "message": "Apply available once ship-via-PR (R9) is proven live on production.",
        })

    # 2026-08-31 — quota gate replaces the old Pro-tier paywall. Free
    # for a limited time: every tier can apply, but it still costs 1
    # task from the SAME monthly quota chat/scan fixes use.
    from services.scan_fix_quota import assert_can_fix, record_scan_fixes
    await assert_can_fix(user, "visibility-kit", count=1)

    from services.pat_vault import get_repo_token_or_error
    token, _auth_err, _auth_detail = await get_repo_token_or_error(proj)
    if not token:
        raise HTTPException(403, f"GitHub App auth failed ({_auth_err}): {_auth_detail}")

    policy_doc = await db.visibility_bot_policies.find_one(
        {"project_id": project_id}, {"_id": 0, "training_choice": 1},
    )
    bot_policy = (policy_doc or {}).get("training_choice", {})

    from services.visibility.apply import apply_visibility_kit
    result = await apply_visibility_kit(
        db, project=proj, requested_items=body.items, token=token,
        scan_urls=proj.get("scan_urls") or [], site_meta=proj.get("site_meta") or {},
        bot_policy=bot_policy, force=body.force,
    )
    # Deduct ONLY on a real successful PR open — never pre-deduct, and
    # never charge for a request that failed/found nothing to apply.
    if result.get("ok"):
        await record_scan_fixes(user["user_id"], "visibility-kit", count=1)
    return result


@router.post("/applications/{application_id}/revert")
async def revert_application(application_id: str, authorization: str = Header(None)):
    user = await current_dev(authorization)
    db = get_db()
    from bson import ObjectId
    try:
        app_doc = await db.visibility_applications.find_one({"_id": ObjectId(application_id)})
    except Exception:                                       # noqa: BLE001
        app_doc = None
    if not app_doc:
        raise HTTPException(404, "application_not_found")
    proj = await db.cto_projects.find_one(
        {"project_id": app_doc["project_id"], "user_id": user["user_id"]}, {"_id": 0},
    )
    if not proj:
        raise HTTPException(404, "project_not_found_or_not_yours")

    from services.pat_vault import get_repo_token_or_error
    token, _auth_err, _auth_detail = await get_repo_token_or_error(proj)
    if not token:
        raise HTTPException(403, f"GitHub App auth failed ({_auth_err}): {_auth_detail}")

    from services.loop_safety import close_and_retract
    owner = proj.get("github_owner") or proj.get("owner")
    repo = proj.get("github_repo") or proj.get("repo")
    result = await close_and_retract(
        owner=owner, repo=repo, pr_number=app_doc.get("pr_number"),
        branch=app_doc["branch"], token=token,
    )
    ok = bool(result.get("pr_closed")) and bool(result.get("branch_deleted"))
    await db.visibility_applications.update_one(
        {"_id": app_doc["_id"]}, {"$set": {"status": "reverted", "merged_at": None}},
    )
    await db.visibility_state.update_many(
        {"project_id": app_doc["project_id"], "item_id": {"$in": app_doc.get("items", [])}},
        {"$set": {"status": "missing", "updated_at": time.time()}},
    )
    return {"ok": ok, "branch_deleted": bool(result.get("branch_deleted"))}
