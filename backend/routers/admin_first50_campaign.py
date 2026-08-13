"""
routers/admin_first50_campaign.py — Admin-gated endpoints for the
First-50 drip campaign. Dry-run, render preview, dispatch, and stats.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Depends
from fastapi.responses import HTMLResponse

from cto_services.auth import require_admin_dep
from cto_services.db import get_db, require_db
from routers._admin_common import _require_admin
from services.first50_campaign import (
    STAGE_HOURS, eligible_for_stage, render_stage, send_one,
    _resend_send,
)

logger = logging.getLogger(__name__)
# Iter 388x · G21 finding — router-level admin gate (defense-in-depth)
# added to match the pattern used by admin_support.py / admin_qa.py etc.
# Individual handlers keep their inline `await _require_admin(...)` calls
# too (harmless redundancy — belt AND suspenders).
router = APIRouter(prefix="/admin/first50-campaign",
                    tags=["Admin First-50 campaign"],
                    dependencies=[Depends(require_admin_dep)])


async def _promo_remaining() -> Optional[int]:
    db = get_db()
    if db is None:
        return None
    doc = await db.promo_first50_state.find_one({"_id": "global"})
    if not doc:
        return None
    return max(0, int(doc.get("total", 50)) - int(doc.get("spots_claimed", 0)))


@router.get("/preview")
async def preview(stage: int, limit: int = 500,
                   authorization: Optional[str] = Header(None)):
    """Dry-run: return the exact user list + skip reasons for a stage."""
    await _require_admin(authorization)
    if stage not in STAGE_HOURS:
        raise HTTPException(400, f"stage must be one of {list(STAGE_HOURS)}")
    db = require_db()
    users = await eligible_for_stage(db, stage, limit=limit)
    will_send = [u for u in users if u.get("_will_send")]
    return {
        "stage":          stage,
        "total_evaluated": len(users),
        "will_send":      len(will_send),
        "skipped":        len(users) - len(will_send),
        "promo_remaining_now": await _promo_remaining(),
        "users": [
            {"user_id": u["user_id"], "email": u["email"],
             "name": u.get("name"),
             "will_send": u.get("_will_send"),
             "skip_reason": u.get("_skip_reason"),
             "email_verified": u.get("email_verified"),
             "promo_first50_claimed": u.get("promo_first50_claimed")}
            for u in users
        ],
    }


@router.get("/render", response_class=HTMLResponse)
async def render_email(stage: int, user_id: str = "",
                        authorization: Optional[str] = Header(None)):
    """Render the exact HTML that would be sent to `user_id` at `stage`.
    Used for founder-review before real send."""
    await _require_admin(authorization)
    if stage not in STAGE_HOURS:
        raise HTTPException(400, f"stage must be one of {list(STAGE_HOURS)}")
    db = require_db()
    if not user_id:
        # Use founder's own record as the preview
        u = await db.dev_users.find_one({"tier": "founder"},
                                         {"_id": 0, "user_id": 1, "email": 1,
                                          "name": 1, "pro_expires_at": 1})
    else:
        u = await db.dev_users.find_one({"user_id": user_id},
                                         {"_id": 0, "user_id": 1, "email": 1,
                                          "name": 1, "pro_expires_at": 1})
    if not u:
        raise HTTPException(404, "user not found")
    subject, text, html = render_stage(stage, u,
                                        promo_remaining=await _promo_remaining())
    # Wrap the raw HTML in a preview shell so subject + plaintext are visible
    return HTMLResponse(
        f'<!doctype html><html><head><meta charset="utf-8">'
        f'<title>Preview: stage {stage} → {u.get("email")}</title></head>'
        f'<body style="font-family:-apple-system,sans-serif;background:#f5f5f5;'
        f'margin:0;padding:24px">'
        f'<div style="max-width:640px;margin:0 auto">'
        f'<div style="background:#111;color:#eee;padding:12px 16px;'
        f'border-radius:8px 8px 0 0;font-family:monospace;font-size:12px">'
        f'<div>TO:      {u.get("email")}</div>'
        f'<div>SUBJECT: {subject}</div>'
        f'<div>STAGE:   {stage}</div></div>'
        f'<div style="background:#fff;border-radius:0 0 8px 8px;padding:0;'
        f'border:1px solid #ddd">{html}</div>'
        f'<details style="margin-top:16px"><summary style="cursor:pointer;'
        f'color:#666;font-size:12px">View plain-text version</summary>'
        f'<pre style="background:#fff;padding:16px;border-radius:8px;'
        f'border:1px solid #ddd;white-space:pre-wrap;font-size:12px;'
        f'font-family:monospace">{text}</pre></details>'
        f'</div></body></html>'
    )


@router.post("/dispatch")
async def dispatch(stage: int, limit: int = 5, dry_run: bool = True,
                    to_emails: Optional[str] = None,
                    authorization: Optional[str] = Header(None)):
    """Actually send stage N.

    Two modes:
      1) Normal (default): send to up to `limit` eligible users from DB filter.
      2) Override: if `to_emails=a@x.com,b@y.com` is passed, bypass DB
         filtering entirely and forcibly send stage N to those exact
         recipients. Skips ALL stop-conditions (unsub / already_sent /
         task_created) and does NOT record to first50_campaign_state,
         so it's safe for founder inbox review without polluting prod
         campaign state. Uses the real dev_users row for personalization
         when the email exists in DB; falls back to a synthetic user dict
         otherwise.

    Defaults to dry_run=true (safety). Set dry_run=false to actually send."""
    await _require_admin(authorization)
    if stage not in STAGE_HOURS:
        raise HTTPException(400, f"stage must be one of {list(STAGE_HOURS)}")
    db = require_db()
    remaining = await _promo_remaining()

    # ── Override path: send to explicit list, bypass ALL guards ─────────
    if to_emails:
        emails = [e.strip().lower() for e in to_emails.split(",") if e.strip()]
        if not emails:
            raise HTTPException(400, "to_emails was provided but empty")
        results = []
        for em in emails:
            # Try to find a real dev_users row so name/pro_expires_at render right
            u = await db.dev_users.find_one(
                {"email": em},
                {"_id": 0, "user_id": 1, "email": 1, "name": 1,
                 "pro_expires_at": 1},
            ) or {"user_id": f"override-{em}", "email": em,
                  "name": em.split("@")[0]}
            subject, text, html = render_stage(stage, u, promo_remaining=remaining)
            if dry_run:
                results.append({"email": em, "ok": True, "dry_run": True,
                                "subject": subject, "text_len": len(text),
                                "html_len": len(html)})
                continue
            ok, err, mid = await _resend_send(em, subject=subject,
                                              text=text, html=html)
            results.append({"email": em, "ok": ok, "error": err,
                            "message_id": mid, "subject": subject})
        return {"stage": stage, "dry_run": dry_run, "mode": "to_emails_override",
                "attempted": len(emails), "results": results}

    # ── Normal path: eligible users from DB filter ──────────────────────
    users = await eligible_for_stage(db, stage, limit=limit * 3)
    to_send = [u for u in users if u.get("_will_send")][:limit]

    results = []
    for u in to_send:
        r = await send_one(u, stage, dry_run=dry_run, promo_remaining=remaining)
        results.append({"user_id": u["user_id"], "email": u["email"], **r})
    return {"stage": stage, "dry_run": dry_run, "mode": "eligible_filter",
            "attempted": len(to_send), "results": results}


@router.get("/state")
async def campaign_state(authorization: Optional[str] = Header(None)):
    """Aggregate stats on the campaign so far."""
    await _require_admin(authorization)
    db = require_db()
    pipeline = [
        {"$unwind": "$stage_sent"},
        {"$group": {"_id": "$stage_sent", "n": {"$sum": 1}}},
    ]
    per_stage = {}
    async for row in db.first50_campaign_state.aggregate(pipeline):
        per_stage[str(row["_id"])] = row["n"]
    unsub_count  = await db.email_unsubscribes.count_documents({})
    state_total  = await db.first50_campaign_state.count_documents({})
    return {
        "sent_by_stage":       per_stage,
        "users_in_sequence":   state_total,
        "unsubscribes_total":  unsub_count,
    }


@router.get("/second-list")
async def second_list(authorization: Optional[str] = Header(None)):
    """Founder Q2 from the brief: users who ARE verified but never got the
    promo (verified before it launched OR after it sold out). Returned as a
    plain list so founder can decide manually per-user."""
    await _require_admin(authorization)
    db = require_db()
    users = []
    cursor = db.dev_users.find(
        {"email_verified": True, "promo_first50_claimed": {"$ne": True}},
        {"_id": 0, "user_id": 1, "email": 1, "name": 1, "tier": 1,
         "created_at": 1, "tokens_granted": 1},
    ).limit(500)
    async for u in cursor:
        users.append(u)
    return {"count": len(users), "users": users}
