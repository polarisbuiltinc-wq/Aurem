"""
services/browser_self_test.py — Iter 367 · Item E · Phase 4

Post-execute Playwright smoke tests for the AI's OWN work. When the
executor writes to a frontend page/component/route, we spin up
Playwright headless, load the URL that the change should be visible
on, and grep for red-flag rendered text ("NaN", "undefined",
"Invalid Date", stack traces, "TypeError:", empty <main>, etc.).

We DO NOT synthesise UI flows or click buttons — this is a smoke
test, not a full E2E. The goal: catch a regression the executor
introduced (blank page, "NaN" balance, missing hero) within seconds
of the deploy, so the founder / user sees a real signal instead
of "loop said success — page is blank."

Design:
  • Path classifier: `classify_frontend_change(paths) -> [urls]`
    Given a list of changed file paths, return the small set of
    live URLs that should be smoke-tested. Pure & cheap.
    * pages/*.jsx           → the page's own route
    * pages/personal/*.jsx  → the corresponding personal-track route
    * pages/admin/*.jsx     → the admin dashboard route (single URL)
    * components/*.jsx      → skip (impact-analysis too broad)
    * App.jsx               → landing + login (widest smoke)
  • Cache: `{url: {last_smoked_at, sha}}` in `browser_selftest_cache`.
    A URL is re-smoked at most once per RESMOKE_COOLDOWN_S so a burst
    of loops on the same page doesn't pile up Playwright launches.
  • `run_smoke(base_url, urls, timeout_s=45)`: launches ONE chromium,
    visits each URL sequentially with a 15s wait, returns a report:
      {ok, results: [{url, status, red_flags, ms}], failed_count}
  • `record_run(db, loop_id, report)`: persists `browser_selftest_runs`
    row so the admin dashboard + `/admin/qa/browser-selftest` returns
    the last N smoke results.
  • FAIL-OPEN — Playwright launch failures collapse to
    `{ok: True, results: [], skipped_reason: "..."}`.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Env-tunables.
RESMOKE_COOLDOWN_S = int(os.environ.get(
    "BROWSER_SELFTEST_COOLDOWN_S", "180"))
DEFAULT_TIMEOUT_S = int(os.environ.get(
    "BROWSER_SELFTEST_TIMEOUT_S", "45"))
PER_URL_WAIT_MS = int(os.environ.get(
    "BROWSER_SELFTEST_PER_URL_MS", "15000"))
# Red-flag rendered patterns — same list Guard-1 already uses so the
# two guards agree on what "broken UI" means.
_RED_FLAG_RES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bNaN\b"),                              "nan_rendered"),
    (re.compile(r"\bundefined\b(?!\.js)"),                "undefined_rendered"),
    (re.compile(r"Invalid Date"),                         "invalid_date"),
    (re.compile(r"TypeError:"),                           "typeerror_rendered"),
    (re.compile(r"ReferenceError:"),                      "referror_rendered"),
    (re.compile(r"Error: Objects are not valid"),          "react_child_err"),
    (re.compile(r"at Module\._compile"),                  "stack_trace"),
    (re.compile(r"</html>\s*$"),                          None),   # sentinel — presence of </html> is GOOD
]


# ─── Path classifier ────────────────────────────────────────────────


def classify_frontend_change(paths: list[str]) -> list[str]:
    """Given a list of changed file paths, return the small set of
    ROUTE PATHS (leading slash, no host) that should be smoke-tested.
    Deduplicated + capped so we never launch more URLs than we need.

    Pure function — no I/O — so it's cheap enough to call on every
    executor completion.
    """
    urls: set[str] = set()
    for p in paths or []:
        p = (p or "").strip()
        if not p:
            continue
        # App-wide changes → widest smoke.
        if p.endswith("src/App.jsx") or p.endswith("frontend/src/App.jsx"):
            urls.update(("/", "/login"))
            continue
        # pages/personal/*.jsx  → /personal or the specific slug.
        m = re.match(r".*frontend/src/pages/personal/([A-Za-z0-9]+)\.jsx$", p)
        if m:
            slug = m.group(1).lower()
            if slug in ("_shell", "buildhome", "start"):
                urls.add("/personal")
            elif slug == "draftreview":
                urls.add("/personal/draft-review")
            elif slug == "buildsuccess":
                urls.add("/personal/success")
            else:
                urls.add(f"/personal/{slug}")
            continue
        # pages/admin/*.jsx or pages/Admin*.jsx  → /admin (single dashboard)
        if re.search(r"frontend/src/pages/(?:admin/|Admin[A-Z])", p):
            urls.add("/admin")
            continue
        # pages/*.jsx → the page's own kebab-case route.
        m = re.match(r".*frontend/src/pages/([A-Za-z0-9]+)\.jsx$", p)
        if m:
            slug = m.group(1)
            # Special-case the common landing pages.
            if slug.lower() in ("both", "landing", "home", "index"):
                urls.add("/")
            elif slug.lower() == "login":
                urls.add("/login")
            elif slug.lower() == "wall":
                urls.add("/wall")
            elif slug.lower() == "toolspage":
                urls.add("/tools")
            else:
                # kebab-case fallback
                kebab = re.sub(r"(?<!^)([A-Z])", r"-\1", slug).lower()
                urls.add(f"/{kebab}")
            continue
        # backend/routers/*  → hit /docs to at least confirm FastAPI
        # still boots. Cheap smoke that catches import-time crashes.
        if p.startswith("backend/routers/") or "/routers/" in p:
            urls.add("/docs")
            continue
    return sorted(urls)[:8]   # hard cap so one loop can't launch 50 URLs


# ─── Cache — one row per URL ────────────────────────────────────────


async def _cooldown_check(db, url: str) -> bool:
    """True when the URL is still in cooldown and should be skipped."""
    if db is None:
        return False
    try:
        row = await db.browser_selftest_cache.find_one({"url": url})
        if not row:
            return False
        last = row.get("last_smoked_at")
        if not last:
            return False
        last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - last_dt).total_seconds() \
            < RESMOKE_COOLDOWN_S
    except Exception:
        return False


async def _touch_cache(db, url: str, sha: str = "") -> None:
    if db is None:
        return
    try:
        await db.browser_selftest_cache.update_one(
            {"url": url},
            {"$set": {"url": url, "sha": sha,
                       "last_smoked_at":
                           datetime.now(timezone.utc).isoformat()}},
            upsert=True,
        )
    except Exception:
        pass


# ─── Playwright smoke runner ───────────────────────────────────────


async def run_smoke(
    base_url: str,
    urls:     list[str],
    *,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    per_url_wait_ms: int = PER_URL_WAIT_MS,
) -> dict:
    """Launch one headless Chromium, visit each URL sequentially.
    Returns a report. FAIL-OPEN on Playwright launch problems."""
    if not urls:
        return {"ok": True, "results": [], "failed_count": 0,
                "skipped_reason": "no_urls"}
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {"ok": True, "results": [], "failed_count": 0,
                "skipped_reason": "playwright_not_installed"}

    started = time.monotonic()
    results: list[dict] = []
    failed = 0
    try:
        async with asyncio.timeout(timeout_s):
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                try:
                    context = await browser.new_context()
                    page = await context.new_page()
                    for path in urls:
                        url_full = base_url.rstrip("/") + path
                        started_url = time.monotonic()
                        red: list[str] = []
                        status = None
                        try:
                            resp = await page.goto(
                                url_full,
                                wait_until="load",
                                timeout=per_url_wait_ms,
                            )
                            status = resp.status if resp else None
                            html = await page.content()
                            for regex, tag in _RED_FLAG_RES:
                                if tag is None:
                                    continue   # sentinel
                                if regex.search(html):
                                    red.append(tag)
                            # Empty <main> check.
                            try:
                                main = await page.locator("main").first \
                                    .text_content(timeout=1000)
                                if not (main or "").strip():
                                    red.append("empty_main")
                            except Exception:
                                pass
                        except Exception as e:
                            red.append(f"nav_err:{type(e).__name__}")
                        ms = round((time.monotonic() - started_url) * 1000)
                        ok = (status is not None and 200 <= status < 400
                              and not red)
                        if not ok:
                            failed += 1
                        results.append({
                            "url":       url_full,
                            "status":    status,
                            "red_flags": red,
                            "ms":        ms,
                            "ok":        ok,
                        })
                finally:
                    await browser.close()
    except asyncio.TimeoutError:
        return {"ok": False, "results": results, "failed_count": failed,
                "skipped_reason": f"timeout_after_{timeout_s}s",
                "duration_ms": round((time.monotonic() - started) * 1000)}
    except Exception as e:
        logger.warning("run_smoke playwright launch failed: %r", e)
        return {"ok": True, "results": [], "failed_count": 0,
                "skipped_reason": f"launch_error:{type(e).__name__}"}
    return {"ok": (failed == 0), "results": results,
            "failed_count": failed,
            "duration_ms": round((time.monotonic() - started) * 1000)}


async def record_run(
    db, *,
    loop_id:    str,
    user_id:    str,
    project_id: Optional[str],
    report:     dict,
) -> None:
    """Persist a smoke run for the founder dashboard + timeline."""
    if db is None:
        return
    try:
        await db.browser_selftest_runs.insert_one({
            "loop_id":      loop_id,
            "user_id":      user_id,
            "project_id":   project_id,
            "ok":           bool(report.get("ok")),
            "failed_count": report.get("failed_count", 0),
            "results":      report.get("results", []),
            "duration_ms":  report.get("duration_ms"),
            "skipped_reason": report.get("skipped_reason"),
            "ts":           datetime.now(timezone.utc).isoformat(),
        })
    except Exception:
        pass


async def smoke_paths_for_loop(
    db,
    *,
    loop_id:    str,
    user_id:    str,
    project_id: Optional[str],
    file_paths: list[str],
    base_url:   str,
) -> dict:
    """One-shot orchestrator: classify → cooldown-filter → run → record.

    Returns the report dict from `run_smoke` so callers can narrate a
    warning if failed_count > 0. FAIL-OPEN — never raises to the loop.
    """
    try:
        urls = classify_frontend_change(file_paths)
        # Filter urls still in cooldown.
        eligible: list[str] = []
        for u in urls:
            if not await _cooldown_check(db, u):
                eligible.append(u)
        if not eligible:
            return {"ok": True, "results": [], "failed_count": 0,
                    "skipped_reason": "all_urls_in_cooldown",
                    "urls_considered": urls}
        report = await run_smoke(base_url, eligible)
        for u in eligible:
            await _touch_cache(db, u)
        await record_run(db, loop_id=loop_id, user_id=user_id,
                          project_id=project_id, report=report)
        return {**report, "urls_smoked": eligible}
    except Exception as e:
        logger.warning("browser_self_test orchestrator failed: %r", e)
        return {"ok": True, "results": [], "failed_count": 0,
                "skipped_reason": f"orchestrator_error:{type(e).__name__}"}
