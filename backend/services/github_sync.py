"""services/github_sync.py — Guard 8 (partial): GitHub repo sync detection.

ONE check, ONE data source, shown in two places (Admin Overview build
badge + the /admin/qa REGRESSION GUARDS row when that section ships).

Compares the currently-live build's commit SHA (routers/version.py)
against GitHub main's HEAD via the GitHub API. "Save to GitHub" is a
user-only platform action, so the repo silently goes stale unless
something watches it — this is that something.

States:
  not_wired — GITHUB_ACTIONS_TOKEN / GITHUB_REPO env missing
  in_sync   — GitHub main contains the deployed commit
  behind    — deployed commit missing from GitHub (repo outdated);
              critical=True when the gap exceeds 48h → escalated into
              the existing topup_alerts banner (RED), auto-resolved
              when back in sync.
  error     — GitHub API unreachable/bad token (never raises upstream)
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
import uuid
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

_CACHE: dict = {"ts": 0.0, "data": None}
CACHE_TTL_S = 600
CRITICAL_GAP_HOURS = 48
_GH_API = "https://api.github.com"


def _cfg() -> tuple[str, str]:
    token = (os.environ.get("GITHUB_ACTIONS_TOKEN")
             or os.environ.get("GITHUB_TOKEN") or "").strip()
    repo = (os.environ.get("GITHUB_REPO") or "").strip()
    return token, repo


def _parse_ts(v: str) -> datetime | None:
    try:
        s = str(v).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _local_commits_behind(github_head_sha: str) -> int | None:
    """Preview-only best effort: count workspace commits GitHub lacks.
    On prod there is no .git — returns None (UI falls back to hours)."""
    try:
        out = subprocess.run(
            ["git", "rev-list", "--count", f"{github_head_sha}..HEAD"],
            cwd="/app", capture_output=True, text=True, timeout=5)
        if out.returncode == 0:
            return int(out.stdout.strip())
    except Exception:
        pass
    return None


async def _compute(token: str, repo: str, build_sha: str, built_at: str) -> dict:
    headers = {"Authorization": f"Bearer {token}",
               "Accept": "application/vnd.github+json"}
    async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
        r = await client.get(f"{_GH_API}/repos/{repo}/commits/main",
                             headers=headers)
        r.raise_for_status()
        head = r.json()
        head_sha = head.get("sha") or ""
        head_date = ((head.get("commit") or {}).get("committer") or {}).get("date") or ""
        base = {
            "github_head": head_sha[:12],
            "github_head_date": head_date,
            "build_sha": build_sha,
        }
        if head_sha.startswith(build_sha) or (build_sha and build_sha.startswith(head_sha[:12])):
            return {**base, "status": "in_sync", "critical": False}

        # Does GitHub have the deployed commit at all?
        r2 = await client.get(f"{_GH_API}/repos/{repo}/commits/{build_sha}",
                              headers=headers)
        if r2.status_code == 200:
            r3 = await client.get(
                f"{_GH_API}/repos/{repo}/compare/{build_sha}...{head_sha}",
                headers=headers)
            if r3.status_code == 200 and r3.json().get("status") in ("ahead", "identical"):
                return {**base, "status": "in_sync", "critical": False,
                        "github_ahead_by": r3.json().get("ahead_by", 0)}

        # GitHub is missing the deployed commit → repo outdated.
        gap_hours = None
        b, h = _parse_ts(built_at), _parse_ts(head_date)
        if b and h:
            gap_hours = round(max(0.0, (b - h).total_seconds() / 3600), 1)
        return {
            **base,
            "status": "behind",
            "commits_behind": _local_commits_behind(head_sha),
            "gap_hours": gap_hours,
            "critical": bool(gap_hours is not None and gap_hours > CRITICAL_GAP_HOURS),
        }


async def _sync_alert(db, data: dict) -> None:
    """Escalate/resolve via the EXISTING topup_alerts banner engine
    (same collection + row shape as integration alerts). Best-effort.

    2026-08-20 — found while wiring the "error" branch: a topup_alerts
    insert_one alone does NOT actually email anyone. `email_sent:
    False` only ever flips to True via `topup_alerts.email_and_mark`,
    which is only called from `daily_digest.py`'s OWN snapshot cycle
    (integration_health.run_all_probes results) — github_sync was
    never part of that snapshot, so the "behind/critical" banner had
    the exact same silent-email gap the founder was worried about for
    "error". Fixed both branches the same way: call
    founder_alerts.send_founder_alert() directly — it's built exactly
    for this ("can be called directly from any guard-critical path")
    and has its own 6h dedup, independent of the banner row."""
    try:
        now = time.time()
        if data.get("status") == "behind" and data.get("critical"):
            day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            key = f"github_sync::critical::{day}"
            behind = data.get("commits_behind")
            gap = data.get("gap_hours")
            summary = ("GitHub repo outdated — deployed build "
                       f"{data.get('build_sha','?')} is not on GitHub main "
                       f"({f'{behind} commits' if behind is not None else f'{gap}h'} behind).")
            existing = await db.topup_alerts.find_one({"alert_key": key}, {"_id": 1})
            if existing:
                await db.topup_alerts.update_one(
                    {"alert_key": key},
                    {"$set": {"last_seen": now, "status": "active",
                              "summary": summary[:300]},
                     "$inc": {"seen_count": 1}})
            else:
                await db.topup_alerts.insert_one({
                    "alert_id": f"al_{uuid.uuid4().hex[:10]}",
                    "alert_key": key,
                    "integration_id": "github_sync",
                    "integration_name": "GitHub sync (Guard 8)",
                    "severity": "critical",
                    "summary": summary[:300],
                    "detail": ("Workspace/production has moved past the last "
                               "'Save to GitHub' push for over "
                               f"{CRITICAL_GAP_HOURS}h."),
                    "fix_hint": "Click 'Save to GitHub' in the Emergent chat input.",
                    "day_key": day,
                    "first_seen": now, "last_seen": now, "seen_count": 1,
                    "status": "active", "email_sent": False,
                })
            await _founder_email(db, "github_sync::behind", "GitHub repo outdated (Guard 8)",
                                 summary, guard="G8")
        elif data.get("status") == "error":
            day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            key = f"github_sync::error::{day}"
            summary = ("GitHub sync check is failing — Guard 8 is blind until "
                       f"this is fixed. Error: {data.get('error', 'unknown')[:150]}")
            existing = await db.topup_alerts.find_one({"alert_key": key}, {"_id": 1})
            if existing:
                await db.topup_alerts.update_one(
                    {"alert_key": key},
                    {"$set": {"last_seen": now, "status": "active",
                              "summary": summary[:300]},
                     "$inc": {"seen_count": 1}})
            else:
                await db.topup_alerts.insert_one({
                    "alert_id": f"al_{uuid.uuid4().hex[:10]}",
                    "alert_key": key,
                    "integration_id": "github_sync",
                    "integration_name": "GitHub sync (Guard 8)",
                    "severity": "critical",
                    "summary": summary[:300],
                    "detail": data.get("error", "")[:500],
                    "fix_hint": ("Check GITHUB_ACTIONS_TOKEN hasn't expired — "
                                 "fine-grained PATs don't auto-renew. Generate a "
                                 "new one and update backend/.env."),
                    "day_key": day,
                    "first_seen": now, "last_seen": now, "seen_count": 1,
                    "status": "active", "email_sent": False,
                })
            await _founder_email(db, "github_sync::error", "GitHub sync check failing (Guard 8)",
                                 summary, guard="G8")
        elif data.get("status") == "in_sync":
            await db.topup_alerts.update_many(
                {"integration_id": "github_sync", "status": "active"},
                {"$set": {"status": "resolved", "resolved_at": now,
                          "resolved_by": "auto_github_sync"}})
    except Exception as e:
        logger.warning("github_sync alert upsert failed: %r", e)


async def _founder_email(db, source_key: str, title: str, detail: str, *, guard: str) -> None:
    """Best-effort direct email — never let a Resend hiccup break the
    sync check itself."""
    try:
        from services.founder_alerts import send_founder_alert
        await send_founder_alert(db, source_key=source_key, title=title,
                                  detail=detail, level="critical", guard=guard)
    except Exception as e:
        logger.warning("github_sync founder_email failed: %r", e)


async def get_github_sync(build_sha: str, built_at: str, db=None) -> dict:
    """Single shared entry point (Overview badge + QA guards row)."""
    now = time.time()
    if _CACHE["data"] is not None and now - _CACHE["ts"] < CACHE_TTL_S:
        return _CACHE["data"]
    token, repo = _cfg()
    if not token or not repo:
        data = {"status": "not_wired", "critical": False,
                "detail": "Set GITHUB_ACTIONS_TOKEN + GITHUB_REPO env vars."}
    else:
        try:
            data = await _compute(token, repo, build_sha, built_at)
        except Exception as e:
            logger.warning("github_sync check failed: %r", e)
            data = {"status": "error", "critical": False, "error": str(e)[:200]}
    data["checked_at"] = int(now)
    _CACHE.update(ts=now, data=data)
    if db is not None:
        await _sync_alert(db, data)
    return data
