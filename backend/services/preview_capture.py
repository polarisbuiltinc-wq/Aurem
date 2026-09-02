"""
services/preview_capture.py — Trust Surfaces Round (S0-S5), 2026-08-29.

L17 reuse-first: this file does NOT launch a second browser or open a
second storage system.
  - Screenshot capture reuses the exact Playwright launch pattern
    already proven in `services/browser_self_test.py::run_smoke`
    (same library, same headless-Chromium launch args) — one
    browser-launch code path in this codebase, not two. Playwright is
    already an installed dependency (requirements.txt) — zero new
    deps (L14).
  - Receipt storage reuses `services/db_backup.py`'s existing,
    already-credentialed Cloudflare R2 (S3-compatible boto3) client
    factory, under a new `deploy-receipts/` key prefix instead of
    `mongo/` — no new bucket, no new storage system, no new deps.

Genuinely new in this file (nothing pre-existing covers it):
  - `classify_user_repo_change()` — a small route-guessing classifier
    for ARBITRARY connected user repos. `browser_self_test.
    classify_frontend_change()` is scoped to AUREM's OWN page routes
    only and cannot be reused for a customer's repo of unknown shape.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

DEVICE_VIEWPORTS = {
    "phone":   {"width": 375,  "height": 812},
    "tablet":  {"width": 768,  "height": 1024},
    "desktop": {"width": 1440, "height": 900},
}
RECEIPT_PREFIX = "deploy-receipts/"
RECEIPT_RETENTION_DAYS = 30
CAPTURE_TIMEOUT_MS = 20000


async def capture_screenshot(url: str, device: str = "phone") -> Optional[bytes]:
    """Navigate to `url` at the given device viewport, return JPEG
    bytes (q80). None on ANY failure — fail-open; callers must render
    an honest "capture unavailable" state, never fabricate an image."""
    if not url:
        return None
    viewport = DEVICE_VIEWPORTS.get(device, DEVICE_VIEWPORTS["phone"])
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return None
    try:
        async with asyncio.timeout(CAPTURE_TIMEOUT_MS / 1000 + 8):
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                try:
                    context = await browser.new_context(viewport=viewport)
                    page = await context.new_page()
                    await page.goto(url, wait_until="load", timeout=CAPTURE_TIMEOUT_MS)
                    await page.wait_for_timeout(400)
                    return await page.screenshot(type="jpeg", quality=80)
                finally:
                    await browser.close()
    except Exception as e:
        logger.warning("capture_screenshot failed for %s: %r", url, e)
        return None


async def capture_before_snapshot_for_task(
    db, project_id: str, user_id: str, task_id: str, preview_url: str,
) -> None:
    """2026-09 — "Before/After" live-preview feature. Fired
    fire-and-forget the moment a task is SUBMITTED (before any code
    change lands), so the "After Fix" tab can show a real Before vs.
    After of the actual live site (same screenshot mechanism as
    `capture_preview_route` — real colors/styling, and works even
    when the host sets X-Frame-Options/CSP blocking a raw iframe).
    Only ever captures route "/" (the common case — most single-file
    edits touch the homepage); honest no-op on any failure, never
    blocks or fails the task submission itself."""
    base = (preview_url or "").strip().rstrip("/")
    if not base:
        return
    try:
        image = await capture_screenshot(base + "/", "phone")
        if not image:
            return
        key = await upload_receipt(image, f"{project_id}/before-{task_id}.jpg")
        if not key:
            return
        await db.cto_tasks.update_one(
            {"task_id": task_id}, {"$set": {"before_receipts": {"/": key}}},
        )
    except Exception as e:                                 # noqa: BLE001
        logger.warning("capture_before_snapshot_for_task failed for %s: %r", task_id, e)


def _r2_client():
    # Reuse — not reimplement — db_backup.py's credentialed R2 client.
    from services.db_backup import _r2_client as _base_client
    return _base_client()


async def upload_receipt(image_bytes: bytes, key_suffix: str) -> Optional[str]:
    """Upload a JPEG receipt under `deploy-receipts/{key_suffix}` in
    the SAME R2 bucket db_backup.py already uses. Returns the R2 key
    (never a public/presigned URL — receipts are only ever served
    back through our own authenticated proxy endpoint) or None."""
    if not image_bytes:
        return None
    key = f"{RECEIPT_PREFIX}{key_suffix}"
    try:
        def _put():
            client = _r2_client()
            client.put_object(
                Bucket=os.environ["R2_BUCKET"], Key=key,
                Body=image_bytes, ContentType="image/jpeg",
            )
        await asyncio.to_thread(_put)
        return key
    except Exception as e:
        logger.warning("upload_receipt failed: %r", e)
        return None


async def fetch_receipt(key: str) -> Optional[bytes]:
    """Fetch receipt bytes back from R2. None on miss/failure."""
    if not key or not key.startswith(RECEIPT_PREFIX):
        return None
    try:
        def _get():
            client = _r2_client()
            obj = client.get_object(Bucket=os.environ["R2_BUCKET"], Key=key)
            return obj["Body"].read()
        return await asyncio.to_thread(_get)
    except Exception as e:
        logger.debug("fetch_receipt miss for %s: %r", key, e)
        return None


async def cleanup_old_receipts(retention_days: int = RECEIPT_RETENTION_DAYS) -> int:
    """Delete receipt objects older than `retention_days`. Returns
    count deleted. Same list+delete pattern as db_backup.py's
    `_prune_old`, different prefix."""
    try:
        def _prune():
            client = _r2_client()
            bucket = os.environ["R2_BUCKET"]
            cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
            deleted = 0
            paginator = client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket, Prefix=RECEIPT_PREFIX):
                batch = [
                    {"Key": o["Key"]} for o in page.get("Contents", [])
                    if o["LastModified"] < cutoff
                ]
                if batch:
                    client.delete_objects(Bucket=bucket, Delete={"Objects": batch})
                    deleted += len(batch)
            return deleted
        return await asyncio.to_thread(_prune)
    except Exception as e:
        logger.warning("cleanup_old_receipts failed: %r", e)
        return 0


# ─── Generic changed-file → route classifier (arbitrary user repos) ──

_ROUTE_PATTERNS = [
    re.compile(r".*(?:^|/)src/pages/([A-Za-z0-9_-]+)\.(?:jsx?|tsx?)$"),
    re.compile(r".*(?:^|/)pages/([A-Za-z0-9_-]+)\.(?:jsx?|tsx?)$"),
    re.compile(r".*(?:^|/)app/([A-Za-z0-9_-]+)/page\.(?:jsx?|tsx?)$"),
    re.compile(r".*(?:^|/)src/routes/([A-Za-z0-9_-]+)\.(?:jsx?|tsx?)$"),
]
_INDEX_SLUGS = {"index", "home", "app", "_app", "layout"}


def classify_user_repo_change(paths: list[str]) -> list[str]:
    """Given changed file paths from an ARBITRARY connected user repo
    (unknown framework), guess the small set of routes likely
    affected. Pure, deterministic, 0-LLM (L16). Falls back to "/" so
    the caller always has at least one page to show — capped at 5."""
    routes: set[str] = set()
    for p in paths or []:
        p = (p or "").strip().replace("\\", "/")
        if not p:
            continue
        matched = False
        for rx in _ROUTE_PATTERNS:
            m = rx.match(p)
            if m:
                slug = m.group(1).lower()
                routes.add("/" if slug in _INDEX_SLUGS else f"/{slug}")
                matched = True
                break
        if not matched and re.search(r"(?:^|/)(index|home)\.(?:html|jsx?|tsx?)$", p, re.IGNORECASE):
            routes.add("/")
    if not routes:
        routes.add("/")
    return sorted(routes)[:5]


# ─── S2 "What changed" classifier ────────────────────────────────────

_SERVER_PATH_MARKERS = (
    "backend/", "server/", "api/", "/migrations/", "/db/", "database/",
)
_SERVER_PATH_EXTS = (".sql",)
_UI_PATH_MARKERS = (
    "frontend/", "/pages/", "/components/", "/routes/", "src/app/",
)


def classify_changed_file(path: str) -> str:
    """Deterministic (0-LLM) per-file classification for S2's "What
    changed" summary. One of: server | ui | other."""
    p = (path or "").strip().replace("\\", "/").lower()
    if p.startswith(_SERVER_PATH_MARKERS) or any(m in p for m in _SERVER_PATH_MARKERS) \
            or p.endswith(_SERVER_PATH_EXTS):
        return "server"
    if p.startswith(_UI_PATH_MARKERS) or any(m in p for m in _UI_PATH_MARKERS):
        return "ui"
    return "other"


def summarise_change_classification(paths: list[str]) -> dict:
    """Returns {n_files, has_server, has_ui, headline} — headline is
    the exact deterministic sentence S2 shows first, and NEVER hides
    a server-side change even when UI files also changed."""
    paths = paths or []
    kinds = [classify_changed_file(p) for p in paths]
    has_server = "server" in kinds
    has_ui = "ui" in kinds
    n = len(paths)
    if n == 0:
        headline = "No changes yet."
    elif has_server and has_ui:
        headline = f"{n} files changed — customer-facing pages AND server & data (server files touched)."
    elif has_server:
        headline = f"{n} files changed — server & data (server files touched)."
    elif has_ui:
        headline = f"{n} files changed — customer-facing pages."
    else:
        headline = f"{n} files changed."
    return {"n_files": n, "has_server": has_server, "has_ui": has_ui, "headline": headline}


# ─── S1-P4 URL auto-detect ────────────────────────────────────────────

def detect_live_url_from_config(filename: str, content: str) -> str:
    """Deterministic (0-LLM) best-effort extraction of a live-site URL
    from one repo config file's content. Returns "" when nothing
    found — caller falls back to the existing manual AddLiveSiteModal
    (S1-P4 spec: never invents a URL, only surfaces a real one)."""
    if not content:
        return ""
    name = (filename or "").lower()
    url_rx = re.compile(r"https?://[^\s\"'<>]+")
    if name == "package.json":
        try:
            data = json.loads(content)
            hp = (data.get("homepage") or "").strip()
            if url_rx.match(hp):
                return hp.rstrip("/")
        except Exception:
            pass
        return ""
    if name == "vercel.json":
        try:
            data = json.loads(content)
            alias = data.get("alias")
            first = alias[0] if isinstance(alias, list) and alias else (alias if isinstance(alias, str) else "")
            first = (first or "").strip()
            if first:
                return first if first.startswith("http") else f"https://{first}"
        except Exception:
            pass
        return ""
    if name == "netlify.toml":
        m = url_rx.search(content)
        if m:
            return m.group(0).rstrip("/\"'")
        return ""
    return ""
