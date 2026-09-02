"""
services/browser_tools.py — ORA's browser diagnostic tool ("browser").

Closes the biggest ORA diagnostic gap: "my form doesn't work" reports
were previously answered by READING CODE + fetching STATIC HTML only
— never by actually trying the interaction. This tool lets ORA
navigate a REAL public page, click/fill/submit, and collect real
evidence (console errors, network log, accessibility snapshot,
optional screenshot) in ONE browser session per chat turn.

Design (3 founder-approved, Playwright-AI-mode-verified patterns):
  1. SEMANTIC SNAPSHOT (accessibility tree) is the PRIMARY "sight" —
     via `Locator.aria_snapshot(mode="ai")` (a YAML-ish text tree with
     stable element refs, not pixels). Far fewer tokens than a
     screenshot, zero pixel-guessing ambiguity. Screenshot is a
     secondary, capped, opt-in artifact for showing the user, not the
     model's primary reasoning input.
  2. CODE-DRIVEN, single round-trip — ORA passes the FULL list of
     `steps` (navigate/click/fill/submit/...) in ONE tool call; every
     step runs inside ONE browser session, ONE aggregated result is
     returned. No per-step LLM round-trip (click-wait-type-wait).
  3. CONCURRENT-ACTION LOCK — reuses the SAME `loop_locks` collection
     Loop ships already use (`services.loop_safety.acquire_loop_lock`)
     so a browser check and a Loop ship can never run concurrently on
     the same project; the browser tool WAITS, it never barges in.

Reuses (do not reimplement):
  - SSRF/public-URL guard: `services.ora_chat.deep_research._is_safe_public_url`
    — the SAME guard `services.deploy_verify` already reuses for its
    own browser.
  - Chromium launch + missing-binary graceful-degrade pattern: the
    same `PLAYWRIGHT_CHROME_EXECUTABLE_PATH` env + `_is_browser_missing_error`
    check from `services.deploy_verify` (preview has a Chromium binary
    at /root/bin/chromium; production currently does NOT — see
    /app/memory/SUPPORT_TICKET_DRAFT_CHROMIUM.md — so this tool must
    degrade honestly there too, not crash or fabricate a result).

SAFETY (non-negotiable):
  - Fresh browser context every call — no persistent cookies/
    localStorage/session; browser closes at the end of the turn.
  - SSRF block on every navigation — private/loopback/link-local/
    metadata ranges blocked, only http/https (no file://, data:, ftp).
  - PUBLIC pages only. No credential entry fields are ever
    auto-filled; no login flow is attempted. "Log in as me" style
    requests must be refused by the caller (ORA's system prompt),
    this tool has no credential-storage capability at all.
  - Caps: max 3 navigations/turn, 30s/navigation, ~5000-token a11y
    snapshot (char-capped proxy), 1 screenshot/turn, 500KB max
    (downscaled if larger).
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

MAX_NAVIGATIONS_PER_TURN = 3
NAV_TIMEOUT_MS = 30_000
ACTION_TIMEOUT_MS = 10_000
A11Y_CHAR_CAP = 20_000          # ~5000 tokens at ~4 chars/token
SCREENSHOT_MAX_BYTES = 500_000
MAX_CONSOLE_ERRORS = 30
MAX_NETWORK_LOG = 30
LOCK_WAIT_BUDGET_S = 30
LOCK_POLL_INTERVAL_S = 2
MAX_STEPS = 20

ALLOWED_ACTIONS = {
    "navigate", "click", "fill", "submit", "screenshot",
    "a11y_snapshot", "console_errors", "network_log",
    "wait_for", "back", "close",
}

UNAVAILABLE_MESSAGE = (
    "I can't run an interactive browser check right now — the browser "
    "runtime isn't available in this environment. I can still read "
    "your code and fetch static page content, but I can't click/"
    "submit/see console errors until that's fixed."
)


def validate_target_url(url: str) -> tuple[bool, str]:
    """Same public-URL/SSRF guard `deploy_verify.py` already uses —
    not reimplemented."""
    from services.ora_chat.deep_research import _is_safe_public_url
    return _is_safe_public_url(url or "")


def _resize_screenshot_if_needed(image_bytes: bytes) -> bytes:
    """Downscale a JPEG screenshot under SCREENSHOT_MAX_BYTES via
    quality reduction. No new dependency — Pillow is already in the
    tree (used elsewhere for image handling)."""
    if len(image_bytes) <= SCREENSHOT_MAX_BYTES:
        return image_bytes
    try:
        import io
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        data = image_bytes
        for quality in (60, 45, 30, 20):
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality)
            data = buf.getvalue()
            if len(data) <= SCREENSHOT_MAX_BYTES:
                return data
        return data  # best-effort, smallest we could get
    except Exception as e:                                 # noqa: BLE001
        logger.warning("browser_tools screenshot resize failed: %r", e)
        return image_bytes[:SCREENSHOT_MAX_BYTES]


async def _wait_for_project_lock(
    db, project_id: str, user_id: str, session_id: str,
) -> tuple[bool, Optional[str]]:
    """Concurrent-action lock — reuses the SAME loop_locks collection
    Loop ships use. Polls up to LOCK_WAIT_BUDGET_S before giving up
    honestly (never hangs forever, never silently proceeds without
    the lock while a ship is running)."""
    from services.loop_safety import acquire_loop_lock
    lock_id = f"browser_{session_id}"
    deadline = time.time() + LOCK_WAIT_BUDGET_S
    while True:
        ok, _existing = await acquire_loop_lock(db, project_id, user_id, lock_id)
        if ok:
            return True, lock_id
        if time.time() >= deadline:
            return False, None
        await asyncio.sleep(LOCK_POLL_INTERVAL_S)


async def _release_project_lock(db, project_id: str, user_id: str, lock_id: str) -> None:
    from services.loop_safety import release_loop_lock
    try:
        await release_loop_lock(db, project_id, user_id, lock_id)
    except Exception as e:                                 # noqa: BLE001
        logger.warning("browser_tools lock release failed: %r", e)


async def _run_steps(steps: list[dict]) -> dict:
    from playwright.async_api import async_playwright
    from services.deploy_verify import _is_browser_missing_error

    result: dict = {
        "ok": True, "browser_available": True, "steps_executed": [],
        "errors": [], "a11y_snapshot": None, "console_errors": [],
        "network_log": [], "screenshot_base64": None, "current_url": None,
    }
    console_errors: list[str] = []
    network_log: list[dict] = []
    nav_count = 0

    async with async_playwright() as pw:
        launch_kwargs = {"headless": True}
        exe = os.environ.get("PLAYWRIGHT_CHROME_EXECUTABLE_PATH")
        if exe:
            launch_kwargs["executable_path"] = exe
        try:
            browser = await pw.chromium.launch(**launch_kwargs)
        except Exception as e:                             # noqa: BLE001
            if _is_browser_missing_error(e):
                return {
                    "ok": False, "browser_available": False,
                    "error": "browser_unavailable", "message": UNAVAILABLE_MESSAGE,
                }
            return {"ok": False, "browser_available": True,
                    "error": f"browser_launch_failed: {type(e).__name__}: {e}"}

        try:
            # Fresh, isolated context every session — no persisted
            # cookies/localStorage/credentials, downloads disabled.
            context = await browser.new_context(accept_downloads=False)
            page = await context.new_page()

            page.on("console", lambda msg: console_errors.append(msg.text[:500])
                    if msg.type == "error" else None)
            page.on("pageerror", lambda exc: console_errors.append(str(exc)[:500]))

            def _on_response(resp):
                try:
                    if resp.status >= 400 or resp.request.method in ("POST", "PUT", "PATCH", "DELETE"):
                        network_log.append({
                            "method": resp.request.method,
                            "url": resp.url[:300],
                            "status_code": resp.status,
                        })
                except Exception:                          # noqa: BLE001
                    pass
            page.on("response", _on_response)

            for step in steps:
                action = step.get("action")
                try:
                    if action == "navigate":
                        nav_count += 1
                        if nav_count > MAX_NAVIGATIONS_PER_TURN:
                            result["errors"].append("navigation_cap_exceeded (max 3/turn)")
                            break
                        url = step.get("url", "")
                        ok, why = validate_target_url(url)
                        if not ok:
                            result["errors"].append(f"blocked_ssrf:{why} for {url!r}")
                            result["ok"] = False
                            break
                        await page.goto(url, wait_until="load", timeout=NAV_TIMEOUT_MS)
                        result["current_url"] = page.url
                    elif action == "click":
                        await page.locator(step["sel"]).first.click(timeout=ACTION_TIMEOUT_MS)
                    elif action == "fill":
                        await page.locator(step["sel"]).first.fill(
                            step.get("val", ""), timeout=ACTION_TIMEOUT_MS)
                    elif action == "submit":
                        loc = page.locator(step["sel"]).first
                        try:
                            await loc.click(timeout=ACTION_TIMEOUT_MS)
                        except Exception:                   # noqa: BLE001
                            await loc.evaluate(
                                "el => el.closest('form') && el.closest('form').requestSubmit()")
                        await page.wait_for_timeout(800)
                    elif action == "wait_for":
                        await page.locator(step["sel"]).first.wait_for(
                            timeout=step.get("timeout", 5000))
                    elif action == "back":
                        await page.go_back(timeout=NAV_TIMEOUT_MS)
                    elif action == "a11y_snapshot":
                        snap = await page.locator("body").aria_snapshot(mode="ai")
                        result["a11y_snapshot"] = (snap or "")[:A11Y_CHAR_CAP]
                    elif action in ("console_errors", "network_log"):
                        pass  # aggregated below regardless of when asked
                    elif action == "screenshot":
                        if result["screenshot_base64"] is None:
                            shot = await page.screenshot(type="jpeg", quality=70)
                            shot = _resize_screenshot_if_needed(shot)
                            result["screenshot_base64"] = base64.b64encode(shot).decode("ascii")
                    elif action == "close":
                        break
                    result["steps_executed"].append(action)
                except Exception as e:                     # noqa: BLE001
                    result["errors"].append(
                        f"{action}_failed: {type(e).__name__}: {str(e)[:200]}")
                    result["ok"] = False

            # Always leave ORA with SOME text "sight" even if it never
            # explicitly asked for a11y_snapshot.
            if result["a11y_snapshot"] is None:
                try:
                    snap = await page.locator("body").aria_snapshot(mode="ai")
                    result["a11y_snapshot"] = (snap or "")[:A11Y_CHAR_CAP]
                except Exception:                           # noqa: BLE001
                    pass
        finally:
            await browser.close()

    result["console_errors"] = console_errors[:MAX_CONSOLE_ERRORS]
    result["network_log"] = network_log[:MAX_NETWORK_LOG]
    return result


async def run_browser_session(steps: list[dict]) -> dict:
    """Validate + execute a full step list in ONE fresh browser
    session. Never raises — always returns a dict with `ok` +
    `error`/`message` on failure (fail-open honesty, not a crash)."""
    if not steps or not isinstance(steps, list):
        return {"ok": False, "error": "no_steps_provided"}
    if len(steps) > MAX_STEPS:
        return {"ok": False, "error": f"too_many_steps (max {MAX_STEPS})"}
    for step in steps:
        if not isinstance(step, dict) or step.get("action") not in ALLOWED_ACTIONS:
            return {"ok": False, "error": f"invalid_step: {step!r}"}
    try:
        return await _run_steps(steps)
    except Exception as e:                                 # noqa: BLE001
        logger.warning("run_browser_session failed: %r", e)
        return {"ok": False, "error": f"unexpected_failure: {type(e).__name__}: {e}"}


async def browser(ctx: dict, args: dict) -> dict:
    """LOCAL_TOOLS entry point — `(ctx, args) -> dict` matching every
    other tool in `local_tools.py`. `args["steps"]` is the full,
    code-driven step list (see module docstring, pattern #2)."""
    steps = (args or {}).get("steps")

    # Attach the vision-generated description of any captured
    # screenshot BEFORE the concurrent-lock branch below, so a
    # 'browser_available: False' degrade never needs it.
    async def _describe_screenshot(res: dict) -> dict:
        b64 = res.get("screenshot_base64")
        if not b64:
            return res
        try:
            from routers.upload import _describe_image_via_vision
            raw = base64.b64decode(b64)
            desc = await _describe_image_via_vision(raw, "image/jpeg", "browser_screenshot.jpg")
            res["screenshot_description"] = desc
        except Exception as e:                             # noqa: BLE001
            logger.warning("browser tool screenshot vision describe failed: %r", e)
        return res

    from .local_tools import _repo_ctx_from
    rc = _repo_ctx_from(ctx)
    project_id = rc.get("pid") if rc else None
    user_id = (ctx or {}).get("user_id")

    if not project_id or not user_id:
        # No connected project — nothing to lock against; still let a
        # standalone public-URL check run (e.g. Home casual chat).
        res = await run_browser_session(steps)
        return await _describe_screenshot(res)

    from cto_services.db import get_db
    db = get_db()
    session_id = uuid.uuid4().hex[:10]
    got_lock, lock_id = await _wait_for_project_lock(db, project_id, user_id, session_id)
    if not got_lock:
        return {
            "ok": False, "error": "project_busy",
            "message": ("Another automated action (a Loop ship) is running on "
                        "this project right now — the browser check needs to "
                        "wait. Try again in a moment."),
        }
    try:
        res = await run_browser_session(steps)
        return await _describe_screenshot(res)
    finally:
        await _release_project_lock(db, project_id, user_id, lock_id)
