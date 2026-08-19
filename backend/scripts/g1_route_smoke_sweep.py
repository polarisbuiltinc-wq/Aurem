"""
scripts/g1_route_smoke_sweep.py — G1 · Playwright route smoke sweep

Real Playwright-driven crawl of every public + authenticated route.
Fail per page on:
  - HTTP non-200
  - visible "NaN"/"undefined"/"Invalid Date"/"Cloudflare could not parse"
  - raw Python stack-trace fragments in DOM text
  - empty <main> content (rendered blank)

Runs from CI + prod cron 30-min (via .github/workflows).
Persists to `synthetic_checks` collection so `/admin/qa` shows the
last run + drift over time.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone

# Runtime-optional Playwright — CI installs, local dev may not.
try:
    from playwright.async_api import async_playwright
except ImportError:
    print("[g1] Playwright not installed — install with "
          "`pip install playwright && playwright install chromium`")
    sys.exit(2)

PUBLIC_ROUTES = ("/", "/pricing", "/signup", "/login", "/wall", "/tools", "/docs")
AUTH_ROUTES   = ("/dashboard", "/settings", "/projects",
                  "/integrations", "/deploy", "/tokens", "/analytics",
                  "/admin", "/admin/qa", "/admin/funnel")

# Substrings that should NEVER appear in rendered text.
BAD_SUBSTRS = (
    "NaN", "undefined", "Invalid Date",
    "Cloudflare could not parse",
    "Traceback (most recent call last)",
)


async def _crawl_one(page, url: str, auth: bool = False) -> dict:
    row: dict = {
        "url":       url,
        "auth":      auth,
        "status":    None,
        "ok":        False,
        "findings":  [],
        "empty_main": False,
        "ms":        None,
    }
    try:
        resp = await page.goto(url, wait_until="networkidle", timeout=30000)
        row["status"] = resp.status if resp else None
        if not resp or resp.status >= 400:
            row["findings"].append(f"status_{resp.status if resp else 'none'}")
        try:
            body = await page.evaluate("() => document.body?.innerText || ''")
            main_txt = await page.evaluate(
                "() => document.querySelector('main')?.innerText || ''"
            )
        except Exception:
            body = ""; main_txt = ""
        if not (main_txt.strip()):
            # Some routes are `<div id="root">` only; check body length instead.
            if len(body.strip()) < 40:
                row["empty_main"] = True
                row["findings"].append("empty_main")
        for bad in BAD_SUBSTRS:
            if bad in body:
                row["findings"].append(f"bad_substr:{bad}")
        row["ok"] = row["status"] == 200 and not row["findings"]
    except Exception as e:
        row["findings"].append(f"exception:{str(e)[:200]}")
    return row


async def sweep(base_url: str) -> dict:
    started = datetime.now(timezone.utc)
    all_rows: list[dict] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            context = await browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent="aurem-g1-sweep",
            )
            page = await context.new_page()
            for path in PUBLIC_ROUTES:
                all_rows.append(await _crawl_one(page, base_url + path))
            # Auth routes optional — need cookies/JWT the CI job seeds.
            auth_token = os.environ.get("G1_AUTH_JWT", "")
            if auth_token:
                await context.add_init_script(
                    f"localStorage.setItem('aurem_token', '{auth_token}');"
                )
                for path in AUTH_ROUTES:
                    all_rows.append(await _crawl_one(
                        page, base_url + path, auth=True,
                    ))
        finally:
            await browser.close()
    finished = datetime.now(timezone.utc)
    fails = [r for r in all_rows if not r["ok"]]
    return {
        "started_at":  started.isoformat(),
        "finished_at": finished.isoformat(),
        "base_url":    base_url,
        "total":       len(all_rows),
        "failed":      len(fails),
        "results":     all_rows,
    }


async def _persist_result(result: dict) -> None:
    """2026-08-19 · G21-audit fix — the docstring always claimed this
    persists to `synthetic_checks` but the write was never actually
    implemented, so `/admin/qa` + the health-checks aggregator could
    never show anything but gray. Best-effort: a scan that ran fine
    but failed to write to Mongo should still exit with the real
    pass/fail code (CI cares about the exit code, not the DB write)."""
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        print("[g1] MONGO_URL/DB_NAME not set — skipping result persistence")
        return
    try:
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(mongo_url)
        await client[db_name].synthetic_checks.insert_one({
            "kind":         "g1_route_sweep",
            "finished_at":  datetime.now(timezone.utc),
            "base_url":     result["base_url"],
            "total":        result["total"],
            "failed":       result["failed"],
            "results":      result["results"],
        })
        client.close()
    except Exception as e:
        print(f"[g1] WARN persistence failed: {e}")


async def main_async(base_url: str, out_json: str | None) -> int:
    result = await sweep(base_url)
    if out_json:
        with open(out_json, "w") as fh:
            json.dump(result, fh, indent=2)
    await _persist_result(result)
    print(f"[g1] {result['failed']} failure(s) / {result['total']} routes.")
    for r in result["results"]:
        mark = "✅" if r["ok"] else "❌"
        find = ",".join(r["findings"]) if r["findings"] else "-"
        print(f"  {mark} {r.get('status','?')} {r['url']}  {find}")
    return 0 if result["failed"] == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url",
                    default=os.environ.get("G1_BASE_URL", "https://auremcto.com"))
    ap.add_argument("--out-json", default=None)
    args = ap.parse_args()
    return asyncio.run(main_async(args.base_url, args.out_json))


if __name__ == "__main__":
    sys.exit(main())
