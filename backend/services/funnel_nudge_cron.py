"""
services/funnel_nudge_cron.py — stage-aware funnel nudge emails.

2026-08-20 — replaces the old single-message `onboarding_email.py`
nudge (which only knew "0 projects" and couldn't tell a user who
never touched GitHub apart from one like Luke West, who installed the
GitHub App with real repo access and then silently dropped off right
before the final "Continue" click).

Classifies every user into exactly ONE current funnel stage (the most
advanced one they're stuck at), and — once they've been stuck there
for 24h+ — sends ONE stage-specific email, never repeated for that
stage. Dedup + audit reuse the existing `onboarding_emails` collection
(campaign="funnel_stage_nudge") so admin tooling has one place to look.

Stages (waterfall — most-advanced progress wins):
    stage3_no_chat          — has a project, never sent a chat message
    stage2_project_pending  — GitHub connected (App install or legacy
                              OAuth link), zero projects  (Luke's case)
    stage1_github_started   — clicked "Connect GitHub" but never
                              finished (no install, no project)
    stage4_fully_inactive   — signed up, zero engagement since

Users who completed the funnel (sent a chat) are excluded entirely.
Unsubscribed emails (`email_unsubscribes`) are always skipped.

Public surface:
    eligible_users(db)                  → list[dict] (user + stage)
    send_stage_nudge(user, stage, ...)  → dict
    run_nudge_batch(db, dry_run=False)  → dict
    stage_counts(db)                    → dict (admin visibility)
    nudge_cron(interval_seconds)        → None (daily loop)
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

from cto_services.db import get_db

logger = logging.getLogger(__name__)

CAMPAIGN     = "funnel_stage_nudge"
STUCK_HOURS  = 24
PUBLIC_BASE  = os.environ.get("PUBLIC_APP_URL", "https://auremcto.com").rstrip("/")
SIGNOFF      = "— Tejinder Sandhu, Founder, Aurem"

STAGES = ("stage3_no_chat", "stage2_project_pending",
          "stage1_github_started", "stage4_fully_inactive")

SUBJECTS = {
    "stage4_fully_inactive":  "Your account is ready — connect GitHub to get started",
    "stage1_github_started":  "Pick up where you left off — finish connecting your repo",
    "stage2_project_pending": "One step left — pick a repo and click Continue to finish setup",
    "stage3_no_chat":         "Try your first task — ask ORA to fix or build something",
}

BODIES = {
    "stage4_fully_inactive": (
        "You signed up for Aurem but haven't started connecting a repo yet.\n\n"
        "Connect GitHub and you're mapped, indexed, and ready to ship in under "
        "2 minutes."
    ),
    "stage1_github_started": (
        "You started connecting GitHub but didn't finish — no repo is linked "
        "to your account yet.\n\n"
        "Head back to your dashboard, click Connect GitHub again, and pick a "
        "repo. It only takes 2 minutes."
    ),
    "stage2_project_pending": (
        "You already installed the GitHub App and granted repo access — "
        "nice, the hard part's done.\n\n"
        "One step left: open your dashboard, pick a repo from the list, and "
        "click Continue to finish connecting it."
    ),
    "stage3_no_chat": (
        "Your repo is connected and indexed — but you haven't sent ORA a "
        "task yet.\n\n"
        "Try your first task: ask ORA to fix a bug, add a small feature, or "
        "just explain a file. Takes seconds to see it work."
    ),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _created_at_dt(raw) -> Optional[datetime]:
    """Coerce dev_users.created_at (datetime / epoch / ISO string) → aware dt."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, (int, float)):
        secs = float(raw) / (1000.0 if raw > 10**12 else 1.0)
        return datetime.fromtimestamp(secs, tz=timezone.utc)
    if isinstance(raw, str):
        try:
            d = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _first_name(user: dict) -> str:
    name = (user.get("name") or "").strip()
    if name:
        return name.split()[0]
    email = (user.get("email") or "").strip()
    return (email.split("@", 1)[0] or "there").split(".", 1)[0]


def dashboard_url() -> str:
    return f"{PUBLIC_BASE}/dashboard"


def click_url(user_id: str, stage: str) -> str:
    """Tracked CTA — reuses the existing click-logger/redirector at
    `/onboarding/click` (routers/onboarding.py), extended with a
    `stage` param so clicks attribute to the exact stage email sent,
    not just the campaign. The endpoint itself decides the redirect
    target based on `stage` (wizard vs plain dashboard)."""
    from urllib.parse import quote
    return (
        f"{PUBLIC_BASE}/api/aurem-dev/onboarding/click"
        f"?uid={quote(user_id)}&c={CAMPAIGN}&stage={quote(stage)}"
    )


def render_text(user: dict, stage: str) -> str:
    from services.first50_campaign import unsub_url
    first = _first_name(user)
    body = BODIES[stage]
    cta = click_url(user.get("user_id", ""), stage)
    unsub = unsub_url(user.get("email", ""))
    return (
        f"Hey {first},\n\n{body}\n\n"
        f"Open your dashboard → {cta}\n\n"
        f"{SIGNOFF}\n\n"
        f"---\nDon't want these emails? Unsubscribe: {unsub}\n"
    )


def render_html(user: dict, stage: str) -> str:
    from services.first50_campaign import unsub_url
    first = _first_name(user)
    body = BODIES[stage].replace("\n\n", "<br><br>")
    cta = click_url(user.get("user_id", ""), stage)
    unsub = unsub_url(user.get("email", ""))
    return f"""<!doctype html>
<html><body style="margin:0;padding:0;background:#0b0b0b;color:#e8e8e8;
font-family:'Helvetica Neue',Arial,sans-serif;line-height:1.55;">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0"
         width="100%" style="background:#0b0b0b;padding:32px 16px;">
    <tr><td align="center">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0"
             width="560" style="max-width:560px;background:#141414;
                                 border:1px solid rgba(255,102,8,0.22);
                                 border-radius:12px;padding:32px;">
        <tr><td style="color:#e8e8e8;font-size:15px;">
          Hey {first},<br><br>
          {body}<br><br>
          <a href="{cta}"
             style="display:inline-block;padding:12px 22px;background:#ff6608;
                    color:#0b0b0b;text-decoration:none;font-weight:600;
                    border-radius:8px;font-size:14px;">
            Open your dashboard &rarr;
          </a><br><br>
          <span style="color:#aaa;font-size:13px;">{SIGNOFF}</span>
        </td></tr>
      </table>
 [43 lines shown. Remaining: lines 161-426 (266 lines). Use view_range parameter to continue.]
      <p style="color:#555;font-size:11px;margin-top:16px;">
        <a href="{unsub}" style="color:#555;">Unsubscribe</a> from these emails.
      </p>
    </td></tr>
  </table>
</body></html>"""


async def _resend_send(to_email: str, *, subject: str, text: str, html: str) -> tuple[bool, Optional[str]]:
    key = os.environ.get("RESEND_API_KEY") or ""
    if not key:
        return False, "RESEND_API_KEY not configured"
    sender = os.environ.get("RESEND_FROM_EMAIL") or "AUREM <ora@aurem.live>"
    try:
        from services.http import ext_request, ExternalCallError
        from services.email_reply_to import get_reply_to
        try:
            _rt = get_reply_to()
            r = await ext_request(
                "resend", "POST", "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "from": sender, "to": [to_email], "subject": subject,
                    "text": text, "html": html,
                    **({"reply_to": _rt} if _rt else {}),
                },
                raise_for_status=False,
            )
            if r.status_code in (200, 201, 202):
                return True, None
            return False, f"HTTP {r.status_code}: {r.text[:200]}"
        except ExternalCallError as e:
            return False, f"{e.dep}: {e}"
    except Exception as e:  # noqa: BLE001 — Resend is best-effort
        return False, f"{type(e).__name__}: {e}"


async def _is_unsubscribed(db, email: str) -> bool:
    from services.first50_campaign import is_unsubscribed
    return await is_unsubscribed(db, email)


async def _has_been_sent(db, user_id: str, stage: str) -> bool:
    doc = await db.onboarding_emails.find_one(
        {"user_id": user_id, "campaign": CAMPAIGN, "stage": stage}, {"_id": 1},
    )
    return doc is not None


async def _claim_send_slot(db, user_id: str, email: str, stage: str) -> Optional[object]:
    """Atomically claim the (user_id, campaign, stage) slot BEFORE
    sending. Returns the inserted `_id` on success, or None if another
    process already claimed it (real 2026-08-20 incident: a rolling-
    deploy cutover briefly ran 2 pod boots, both firing the cron's
    first tick immediately with no startup delay; the old check-then-
    act pattern — read "already sent?", THEN send, THEN write — raced,
    so ~30 real users got the same stage email twice. This insert
    relies on the `uniq_user_campaign_stage` unique index
    (services/db_indexes.py) to make the claim atomic across any
    number of concurrent processes, not just this one."""
    from pymongo.errors import DuplicateKeyError
    try:
        res = await db.onboarding_emails.insert_one({
            "user_id": user_id, "email": email, "campaign": CAMPAIGN,
            "stage": stage, "sent_at": _now(), "sent_ok": False,
            "error": "claimed, send in progress", "dry_run": False,
            "clicked_at": None, "click_count": 0,
        })
        return res.inserted_id
    except DuplicateKeyError:
        return None


async def _finalize_send(db, doc_id, sent_ok: bool, error: Optional[str]) -> None:
    try:
        await db.onboarding_emails.update_one(
            {"_id": doc_id},
            {"$set": {"sent_ok": bool(sent_ok), "error": error, "sent_at": _now()}},
        )
    except Exception as e:
        logger.warning("funnel_nudge finalize_send failed: %r", e)


async def classify_users(db) -> dict[str, list[dict]]:
    """Bucket every non-internal signed-up user into their current
    stage (or None if fully progressed / too new). Batched — no N+1."""
    candidates = await db.dev_users.find(
        {"email": {"$exists": True, "$ne": ""}},
        {"_id": 0, "user_id": 1, "email": 1, "name": 1, "created_at": 1,
         "first_chat_at": 1, "is_admin": 1, "is_unlimited": 1, "tier": 1,
         "github": 1},
    ).to_list(length=10_000)
    candidates = [
        u for u in candidates
        if not (u.get("is_admin") or u.get("is_unlimited") or u.get("tier") == "founder")
    ]
    ids = [u["user_id"] for u in candidates if u.get("user_id")]
    if not ids:
        return {s: [] for s in STAGES}

    users_with_project: dict[str, float] = {}
    cur = db.cto_projects.find({"user_id": {"$in": ids}}, {"_id": 0, "user_id": 1, "created_at": 1})
    async for p in cur:
        uid = p.get("user_id")
        if not uid:
            continue
        ts = p.get("created_at")
        ts = ts.timestamp() if hasattr(ts, "timestamp") else (ts or 0)
        if uid not in users_with_project or ts < users_with_project[uid]:
            users_with_project[uid] = ts

    active_installs: dict[str, float] = {}
    try:
        cur = db.github_installations.find(
            {"user_id": {"$in": ids}, "active": True},
            {"_id": 0, "user_id": 1, "created_at": 1},
        )
        async for row in cur:
            uid = row.get("user_id")
            if not uid:
                continue
            ts = row.get("created_at")
            ts = ts.timestamp() if hasattr(ts, "timestamp") else (ts or 0)
            if uid not in active_installs or ts < active_installs[uid]:
                active_installs[uid] = ts
    except Exception:
        pass

    github_started: dict[str, float] = {}
    try:
        cur = db.github_funnel_events.find(
            {"user_id": {"$in": ids}}, {"_id": 0, "user_id": 1, "ts_epoch": 1, "created_at": 1},
        )
        async for row in cur:
            uid = row.get("user_id")
            if not uid:
                continue
            ts = row.get("ts_epoch")
            if ts is None:
                ca = row.get("created_at")
                ts = ca.timestamp() if hasattr(ca, "timestamp") else 0
            if uid not in github_started or ts < github_started[uid]:
                github_started[uid] = ts
    except Exception:
        pass

    any_funnel_event: dict[str, float] = {}
    try:
        cur = db.funnel_events.find(
            {"user_id": {"$in": ids}, "event_type": {"$ne": "signup_completed"}},
            {"_id": 0, "user_id": 1, "ts_epoch": 1, "created_at": 1},
        )
        async for row in cur:
            uid = row.get("user_id")
            if not uid:
                continue
            ts = row.get("ts_epoch")
            if ts is None:
                ca = row.get("created_at")
                ts = ca.timestamp() if hasattr(ca, "timestamp") else 0
            if uid not in any_funnel_event or ts < any_funnel_event[uid]:
                any_funnel_event[uid] = ts
    except Exception:
        pass

    now_epoch = _now().timestamp()
    buckets: dict[str, list[dict]] = {s: [] for s in STAGES}
    for u in candidates:
        uid = u["user_id"]
        if u.get("first_chat_at"):
            continue  # funnel complete — no nudge needed

        has_project = uid in users_with_project
        has_github  = uid in active_installs or bool((u.get("github") or {}).get("login"))
        has_started = uid in github_started or uid in any_funnel_event

        if has_project:
            stuck_since = users_with_project[uid]
            stage = "stage3_no_chat"
        elif has_github:
            stuck_since = active_installs.get(uid) or github_started.get(uid) or 0
            stage = "stage2_project_pending"
        elif has_started:
            stuck_since = github_started.get(uid) or any_funnel_event.get(uid) or 0
            stage = "stage1_github_started"
        else:
            ca = _created_at_dt(u.get("created_at"))
            stuck_since = ca.timestamp() if ca else 0
            stage = "stage4_fully_inactive"

        if not stuck_since or (now_epoch - stuck_since) < STUCK_HOURS * 3600:
            continue  # not stuck long enough yet

        u["_stage"] = stage
        buckets[stage].append(u)
    return buckets


async def eligible_users(db) -> list[dict]:
    """Flat list across all 4 stages, minus already-sent / unsubscribed."""
    buckets = await classify_users(db)
    out: list[dict] = []
    for stage, users in buckets.items():
        for u in users:
            if await _has_been_sent(db, u["user_id"], stage):
                continue
            if await _is_unsubscribed(db, u.get("email", "")):
                continue
            out.append(u)
    return out


async def send_stage_nudge(user: dict, *, dry_run: bool = False) -> dict:
    db = get_db()
    stage = user.get("_stage")
    if db is None or stage not in SUBJECTS:
        return {"ok": False, "error": "invalid stage or db unavailable",
                "user_id": user.get("user_id")}
    subject = SUBJECTS[stage]
    text = render_text(user, stage)
    html = render_html(user, stage)
    if dry_run:
        return {"ok": True, "user_id": user.get("user_id"), "email": user.get("email"),
                "stage": stage, "dry_run": True,
                "preview": {"subject": subject, "text": text}}
    # Claim the slot BEFORE sending — see _claim_send_slot docstring.
    # If another process (e.g. an overlapping pod during a rolling
    # deploy) already claimed it, skip entirely: no Resend call.
    claim_id = await _claim_send_slot(db, user["user_id"], user["email"], stage)
    if claim_id is None:
        return {"ok": True, "user_id": user.get("user_id"), "email": user.get("email"),
                "stage": stage, "skipped": True, "reason": "already claimed"}
    sent_ok, err = await _resend_send(user["email"], subject=subject, text=text, html=html)
    await _finalize_send(db, claim_id, sent_ok, err)
    return {"ok": bool(sent_ok), "user_id": user.get("user_id"), "email": user.get("email"),
            "stage": stage, "error": err}


async def run_nudge_batch(db, *, dry_run: bool = False) -> dict:
    summary = {"ok": True, "dry_run": bool(dry_run), "sent": 0, "skipped": 0,
               "failed": 0, "recipients": [], "errors": []}
    cohort = await eligible_users(db)
    for u in cohort:
        res = await send_stage_nudge(u, dry_run=dry_run)
        summary["recipients"].append({
            "user_id": res.get("user_id"), "email": res.get("email"),
            "stage": res.get("stage"), "ok": bool(res.get("ok")), "dry_run": bool(dry_run),
            "skipped": bool(res.get("skipped")),
        })
        if res.get("skipped"):
            summary["skipped"] += 1
        elif res.get("ok"):
            summary["sent"] += 1
        else:
            summary["failed"] += 1
            if res.get("error"):
                summary["errors"].append(res["error"])
    return summary


async def stage_counts(db) -> dict:
    """Admin visibility — how many users are currently stuck at each
    stage (regardless of whether they've already been nudged), how
    many nudges have actually been sent per stage, and how many of
    those were clicked (real engagement, not just delivery)."""
    buckets = await classify_users(db)
    stuck = {s: len(users) for s, users in buckets.items()}
    sent: dict[str, int] = {s: 0 for s in STAGES}
    clicked: dict[str, int] = {s: 0 for s in STAGES}
    total_sent = 0
    total_clicked = 0
    try:
        pipeline = [
            {"$match": {"campaign": CAMPAIGN, "sent_ok": True}},
            {"$group": {
                "_id": "$stage", "n": {"$sum": 1},
                "n_clicked": {"$sum": {"$cond": [
                    {"$ifNull": ["$clicked_at", False]}, 1, 0,
                ]}},
            }},
        ]
        async for row in db.onboarding_emails.aggregate(pipeline):
            if row["_id"] in sent:
                sent[row["_id"]] = int(row["n"])
                clicked[row["_id"]] = int(row.get("n_clicked", 0))
                total_sent += int(row["n"])
                total_clicked += int(row.get("n_clicked", 0))
    except Exception:
        pass
    return {"stuck": stuck, "nudges_sent": sent, "nudges_sent_total": total_sent,
            "nudges_clicked": clicked, "nudges_clicked_total": total_clicked}


async def nudge_cron(interval_seconds: int = 86400, *, startup_delay_seconds: int = 120) -> None:
    """Daily loop. Idempotent — the atomic claim in `send_stage_nudge`
    gates every send, even across concurrent processes.

    2026-08-20 — `startup_delay_seconds` added after a real incident:
    a rolling-deploy cutover briefly ran 2 pod boots, both firing this
    loop's first tick IMMEDIATELY (no delay), sending ~30 real users
    the same nudge email twice within seconds (the old pod hadn't
    finished terminating before the new pod's first tick fired). The
    claim-based dedup above now makes that scenario safe even if it
    happens again, but the delay also avoids wasting a tick on a pod
    that's about to be replaced during the ~30-60s cutover window."""
    import asyncio
    await asyncio.sleep(startup_delay_seconds)
    while True:
        try:
            db = get_db()
            if db is not None:
                result = await run_nudge_batch(db, dry_run=False)
                if result["sent"] or result["failed"] or result["skipped"]:
                    logger.info("🪧 funnel_nudge cron — sent=%d failed=%d skipped=%d",
                                result["sent"], result["failed"], result["skipped"])
        except Exception as e:
            logger.warning("funnel_nudge_cron tick failed: %r", e)
        await asyncio.sleep(interval_seconds)
