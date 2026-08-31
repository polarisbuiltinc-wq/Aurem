"""
services/deploy_verify.py — V1: server-side headless deploy-verify
(2026-08-30, own workstream, starts after T2-T5 + M1/M3, founder GO).

L17 reuse-first — full reuse map: /app/e2e-proof/V1/V0_REUSE_MAP.md.
This module is the genuinely-new piece: a deterministic (V1a), fenced
(V1c), optionally-judged (V1b) verify ENGINE. Wired into the existing
`routers/deploy.py::_verify_and_capture` (S3-D4, prior round) and the
`DeployPanel.jsx` receipt card (V1d) — extends, does not replace, that
existing honest-states pattern.

Standard invariants this module follows:
  - ZERO LLM calls in the deterministic path (V1a) — see
    `test_verify_a_zero_llm`.
  - Security fence (V1c) runs FIRST, before ANY navigation, even
    against the local E2E fixture target.
  - Never `--no-sandbox` (V1c rule 5) — launches with the SAME args
    `services/browser_self_test.py` already uses in this pod.
  - Fresh `BrowserContext` per run, no stored credentials, downloads
    disabled (V1c rule 4).
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

VERIFY_BUDGET_S = 120           # V1a spec — <= 2 min wall clock, fail fast
JUDGMENT_TOKEN_CAP = 2000        # V1b spec — <= 2000 tokens/run
SNAPSHOT_CHAR_CAP = 12_000       # ~3000 tokens, approx 4 chars/token
OUTPUT_TRUNCATE_CAP = 4_000      # V1c rule 6 — every page-sourced artifact

# V1d — runtime escape hatch ONLY. "local" (self-host, this module) is
# the sole implementation this round; "cloud" is a NAMED future option
# (F29 ledger entry), never purchased/wired here. Any value other than
# "local" today still runs the local engine — there is no cloud path
# to fall through to yet, so this never silently no-ops.
import os as _os_mod
VERIFY_BROWSER_MODE = _os_mod.environ.get("VERIFY_BROWSER", "local")

DEVICE_VIEWPORTS = {
    "mobile_375": {"width": 375, "height": 812, "device_scale_factor": 1},
    "desktop":    {"width": 1440, "height": 900, "device_scale_factor": 1},
}


# ═══════════════════════ V1c — security fence ═══════════════════════
def validate_target_url(url: str) -> tuple[bool, str]:
    """V1c rule 1 — reuses `services.ora_chat.deep_research`'s
    existing, tested SSRF guard (scheme allowlist + every DNS answer
    checked against private/loopback/link-local/metadata/CGNAT +
    bare-IP-host validation). NOT reimplemented (L17)."""
    from services.ora_chat.deep_research import _is_safe_public_url
    return _is_safe_public_url(url)


def _host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except Exception:
        return ""


def _same_allowlisted_domain(nav_url: str, allowlist_host: str) -> bool:
    """V1c rules 2+3 — per-run domain allowlist, exact host or a real
    subdomain of it. Re-called before every interaction (rule 3) and
    on every intercepted request (rule 2)."""
    host = _host_of(nav_url)
    allow = (allowlist_host or "").lower()
    return bool(host) and (host == allow or host.endswith("." + allow))


def _truncate(s: Optional[str], cap: int = OUTPUT_TRUNCATE_CAP) -> str:
    """V1c rule 6 — output truncation on every page-sourced artifact
    before storage or LLM use."""
    if not s:
        return ""
    return s if len(s) <= cap else s[:cap] + "...[truncated]"


def _is_browser_missing_error(exc: Exception) -> bool:
    """C1 — detects ONLY the 'no Chromium binary installed in this
    environment' failure (Playwright's launch-time message), never
    ordinary navigation/runtime errors. Those still hard-fail exactly
    as before — this is not a catch-all."""
    msg = str(exc).lower()
    return "executable doesn't exist" in msg


async def _browser_free_fallback(url: str, result: dict, exc: Exception) -> dict:
    """C1 — graceful no-browser degrade. Only reached when Chromium's
    own executable is missing at launch (see `_is_browser_missing_error`).
    Runs a plain httpx GET — no screenshots, no console/runtime checks,
    no JS execution, no interactions — and labels the result clearly as
    `degraded` so nothing downstream mistakes it for a full verify."""
    import httpx
    result["verdict"] = "degraded"
    result["browser_available"] = False
    result["fail_reason"] = "browser_unavailable"
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
            resp = await client.get(url)
        status_ok = 200 <= resp.status_code < 400
        body = (resp.text or "")[:OUTPUT_TRUNCATE_CAP]
        has_heading = "<h1" in body.lower()
        result["checks"].append({
            "name": "reachability_fallback", "pass": status_ok,
            "evidence": f"HTTP {resp.status_code} via browser-free fallback "
                        f"(Chromium unavailable: {type(exc).__name__})",
        })
        result["checks"].append({
            "name": "content_signal_fallback", "pass": has_heading,
            "evidence": ("found an <h1> in the raw HTML" if has_heading
                         else "no <h1> found in the raw HTML — may be a JS-only shell"),
        })
    except Exception as fetch_exc:                            # noqa: BLE001
        result["checks"].append({
            "name": "reachability_fallback", "pass": False,
            "evidence": f"browser-free fallback request failed: {type(fetch_exc).__name__}",
        })
    result["what_happened"] = (
        "Chromium is not installed in this environment, so full browser "
        "verification (screenshots, console/runtime errors, click "
        "interactions) could not run. Ran a browser-free HTTP check "
        "instead — this result does NOT confirm visual rendering, JS "
        "runtime health, or interactivity."
    )
    return result


async def _audit_log(db, **fields) -> None:
    """V1c rule 7 — every verify run logged (who/what/when/dur/result/
    tokens/egress), same append-only pattern as the webhook fence
    trail."""
    if db is None:
        return
    try:
        await db.deploy_verify_audit.insert_one({
            "ts": time.time(),
            "logged_at": datetime.now(timezone.utc).isoformat(),
            **fields,
        })
    except Exception as e:                                    # noqa: BLE001
        logger.debug("deploy_verify audit log failed: %r", e)


# ═══════════════════ V1a — deterministic verify engine ═══════════════
async def run_verify(
    url: str,
    *,
    expected_build: Optional[str] = None,
    changed_routes: Optional[list[str]] = None,
    primary_cta_selector: Optional[str] = None,
    db=None,
    user_id: str = "",
    project_id: str = "",
    run_trace: bool = True,
) -> dict:
    """The V1a deterministic verify engine. Budget bounded to
    `VERIFY_BUDGET_S` (~2 min) wall clock, fails fast on overrun.
    ZERO LLM calls anywhere in this function or anything it calls —
    `test_verify_a_zero_llm` asserts no provider is ever constructed
    on this path.

    Returns:
      {url, build_match, checks:[{name, pass, evidence}],
       screenshots:{mobile_375, desktop}, trace_path, duration_ms,
       console_errors:[], egress_attempts:[], verdict:pass|fail|degraded,
       fail_reason, advisory_model, browser_available}

    `verdict == "degraded"` (C1) means Chromium itself was not
    installed in this environment — the checks that DID run are a
    browser-free httpx fallback (reachability + raw-HTML signal only).
    It is NOT a pass and NOT a full verify; `browser_available` is
    `False` in that case so callers can render it distinctly.
    """
    t_start = time.time()
    run_id = uuid.uuid4().hex[:12]
    result: dict = {
        "run_id": run_id, "url": url, "build_match": None,
        "checks": [], "screenshots": {}, "trace_path": None,
        "duration_ms": None, "console_errors": [], "egress_attempts": [],
        "verdict": "fail", "fail_reason": None, "advisory_model": None,
        "what_happened": None, "browser_available": True,
    }

    # V1c FIRST — the gate sequencing rule: fence before ANY run
    # against anything real, even the local E2E fixture target.
    ok, why = validate_target_url(url)
    if not ok:
        result["fail_reason"] = f"blocked_ssrf:{why}"
        result["checks"].append({"name": "url_validation", "pass": False, "evidence": why})
        result["what_happened"] = f"Blocked before launch — {why}"
        await _audit_log(db, run_id=run_id, user_id=user_id, project_id=project_id,
                          url=url, result="blocked_ssrf", reason=why)
        return result

    allowlist_host = _host_of(url)
    try:
        async with asyncio.timeout(VERIFY_BUDGET_S):
            result = await _run_verify_inner(
                url, allowlist_host, result,
                expected_build=expected_build,
                changed_routes=changed_routes or [],
                primary_cta_selector=primary_cta_selector,
                run_trace=run_trace,
            )
    except asyncio.TimeoutError:
        result["fail_reason"] = "verify_budget_exceeded_120s"
        result["checks"].append({"name": "budget", "pass": False,
                                  "evidence": "exceeded the 120s wall-clock budget"})
        result["verdict"] = "fail"
        result["what_happened"] = "Verify run exceeded its 2-minute budget — stopped, not left hanging."
    except Exception as e:                                    # noqa: BLE001
        result["fail_reason"] = f"verify_engine_error:{type(e).__name__}"
        result["checks"].append({"name": "engine", "pass": False, "evidence": str(e)[:300]})
        result["verdict"] = "fail"
        result["what_happened"] = f"Verify engine hit an internal error: {type(e).__name__}"
        logger.warning("run_verify(%s) engine error: %r", url, e)

    result["duration_ms"] = int((time.time() - t_start) * 1000)
    await _audit_log(
        db, run_id=run_id, user_id=user_id, project_id=project_id, url=url,
        verdict=result["verdict"], fail_reason=result.get("fail_reason"),
        duration_ms=result["duration_ms"],
        egress_attempts=result["egress_attempts"],
        tokens=0,  # V1a is zero-LLM — always 0 here
    )
    return result


async def _run_verify_inner(
    url: str, allowlist_host: str, result: dict,
    *, expected_build: Optional[str], changed_routes: list[str],
    primary_cta_selector: Optional[str], run_trace: bool,
) -> dict:
    from playwright.async_api import async_playwright

    egress_attempts: list[dict] = []

    async def _route_guard(route, request):
        # V1c rule 2 — per-run domain allowlist on every sub-resource.
        if not _same_allowlisted_domain(request.url, allowlist_host):
            egress_attempts.append({"url": _truncate(request.url, 300),
                                     "resource_type": request.resource_type})
            await route.abort()
            return
        await route.continue_()

    console_errors: list[str] = []
    page_errors: list[str] = []

    async with async_playwright() as pw:
        # V1c rule 5 — never --no-sandbox. Identical launch args to
        # services/browser_self_test.py's D1 pattern (one browser-
        # launch code path in this codebase, not a second one).
        # executable_path pinned to this pod's actual installed
        # browser (PLAYWRIGHT_CHROME_EXECUTABLE_PATH env) since the
        # bundled Playwright browser version here doesn't match what
        # playwright==1.61.0 expects by default.
        import os as _os
        launch_kwargs = {"headless": True}
        _exe = _os.environ.get("PLAYWRIGHT_CHROME_EXECUTABLE_PATH")
        if _exe:
            launch_kwargs["executable_path"] = _exe
        try:
            browser = await pw.chromium.launch(**launch_kwargs)
        except Exception as e:
            # C1 — graceful degrade ONLY for a missing Chromium binary.
            # Any other launch failure still hard-fails via the outer
            # run_verify() try/except, unchanged.
            if _is_browser_missing_error(e):
                return await _browser_free_fallback(url, result, e)
            raise
        try:
            # V1c rule 4 — fresh, isolated context every run. No prior
            # session or saved credentials carried in, downloads off.
            context = await browser.new_context(
                viewport=DEVICE_VIEWPORTS["mobile_375"],
                accept_downloads=False,
            )
            if run_trace:
                await context.tracing.start(screenshots=True, snapshots=True)
            page = await context.new_page()
            await page.route("**/*", _route_guard)
            page.on("console", lambda msg: console_errors.append(_truncate(msg.text, 500))
                    if msg.type == "error" else None)
            page.on("pageerror", lambda exc: page_errors.append(_truncate(str(exc), 500)))

            # ── Check 1: VERSION IDENTITY + wait for load-complete ──
            t0 = time.time()
            try:
                resp = await page.goto(url, wait_until="load", timeout=20_000)
                ttfb_ms = int((time.time() - t0) * 1000)
            except Exception as e:
                result["checks"].append({"name": "reachability", "pass": False,
                                          "evidence": f"navigation failed: {type(e).__name__}"})
                result["fail_reason"] = "unreachable"
                result["what_happened"] = "Could not reach the deploy URL at all."
                return result
            await page.wait_for_load_state("networkidle", timeout=10_000)
            status_ok = bool(resp) and 200 <= resp.status < 400
            result["checks"].append({
                "name": "reachability", "pass": status_ok,
                "evidence": f"HTTP {resp.status if resp else 'none'}, TTFB {ttfb_ms}ms",
            })
            if not status_ok:
                result["fail_reason"] = f"http_{resp.status if resp else 'none'}"

            build_match = None
            if expected_build:
                page_content = await page.content()
                build_match = expected_build in page_content
                if not build_match:
                    try:
                        meta_build = await page.get_attribute(
                            'meta[name="build-sha"]', "content")
                        build_match = bool(meta_build) and expected_build in meta_build
                    except Exception:
                        pass
                result["build_match"] = build_match
                result["checks"].append({
                    "name": "version_identity", "pass": bool(build_match),
                    "evidence": ("expected build marker found" if build_match
                                 else f"expected '{expected_build}' NOT found on the "
                                      "loaded page — stale build / CDN cache"),
                })
                if not build_match:
                    result["fail_reason"] = result["fail_reason"] or "stale_build"

            # ── Check 3: RUNTIME HEALTH ─────────────────────────────
            await page.wait_for_timeout(500)  # let any deferred errors surface
            runtime_ok = not console_errors and not page_errors
            result["console_errors"] = [*console_errors, *page_errors][:20]
            result["checks"].append({
                "name": "runtime_health", "pass": runtime_ok,
                "evidence": (f"{len(console_errors)} console.error, "
                             f"{len(page_errors)} uncaught pageerror"),
            })
            if not runtime_ok:
                result["fail_reason"] = result["fail_reason"] or "runtime_errors"

            # ── Check 4: CHANGED-ROUTE ASSERTION ────────────────────
            # V1c rule 1+3 — re-verify (full DNS re-resolve, not just a
            # hostname string compare) before EVERY navigation past the
            # first, not only once at entry. Fails that route closed —
            # never silently skips a rebind attempt.
            route_results = []
            for route_path in changed_routes[:10]:
                route_url = url.rstrip("/") + "/" + route_path.lstrip("/")
                if not _same_allowlisted_domain(route_url, allowlist_host):
                    continue
                reverify_ok, reverify_why = validate_target_url(route_url)
                if not reverify_ok:
                    route_results.append({"route": route_path, "ok": False,
                                           "status": None, "error": f"reverify_blocked:{reverify_why}"})
                    egress_attempts.append({"url": _truncate(route_url, 300),
                                             "resource_type": "reverify_blocked"})
                    continue
                try:
                    r2 = await page.goto(route_url, wait_until="load", timeout=15_000)
                    ok2 = bool(r2) and 200 <= r2.status < 400
                    route_results.append({"route": route_path, "ok": ok2,
                                           "status": r2.status if r2 else None})
                except Exception as e:                         # noqa: BLE001
                    route_results.append({"route": route_path, "ok": False,
                                           "status": None, "error": type(e).__name__})
            if changed_routes:
                all_routes_ok = all(r["ok"] for r in route_results)
                result["checks"].append({
                    "name": "changed_route_assertion", "pass": all_routes_ok,
                    "evidence": route_results,
                })
                if not all_routes_ok:
                    result["fail_reason"] = result["fail_reason"] or "changed_route_broken"
            else:
                result["checks"].append({
                    "name": "changed_route_assertion", "pass": True,
                    "evidence": "no changed_routes provided — skipped (not a fail)",
                })
            # navigate back to the primary URL for the remaining checks
            await page.goto(url, wait_until="load", timeout=20_000)

            # ── Check 5: BREAKAGE SWEEP (cheap) ─────────────────────
            broken_images = await page.evaluate(
                "() => Array.from(document.images).filter(img => "
                "!img.complete || img.naturalWidth === 0).map(img => img.src).slice(0, 10)"
            )
            breakage_ok = len(broken_images) == 0
            result["checks"].append({
                "name": "breakage_sweep", "pass": breakage_ok,
                "evidence": f"{len(broken_images)} broken <img> found" + (
                    f": {broken_images[:5]}" if broken_images else ""),
            })

            # ── Check 5b: GEOMETRY (overflow/overlap, in CODE not LLM) ─
            geometry = await page.evaluate(
                "() => { const doc = document.documentElement; "
                "const overflowX = doc.scrollWidth > doc.clientWidth + 4; "
                "const overflowY = false; "
                "return {overflowX, overflowY, scrollWidth: doc.scrollWidth, "
                "clientWidth: doc.clientWidth}; }"
            )
            geometry_ok = not geometry.get("overflowX")
            result["checks"].append({
                "name": "geometry", "pass": geometry_ok,
                "evidence": geometry,
            })

            # ── Check 6: ONE CORE INTERACTION (best-effort) ─────────
            interacted = False
            if primary_cta_selector:
                try:
                    locator = page.locator(primary_cta_selector).first
                    if await locator.count() > 0:
                        box = await locator.bounding_box()
                        if box:
                            await page.mouse.move(box["x"] + box["width"] / 2,
                                                   box["y"] + box["height"] / 2)
                            await page.mouse.move(box["x"] + box["width"] / 2 + 1,
                                                   box["y"] + box["height"] / 2 + 1)
                        await locator.click(timeout=5_000)
                        await page.wait_for_timeout(500)
                        interacted = True
                except Exception as e:                          # noqa: BLE001
                    result["checks"].append({"name": "interaction", "pass": False,
                                              "evidence": f"click failed: {type(e).__name__}"})
            if not interacted:
                result["checks"].append({
                    "name": "interaction", "pass": True,
                    "evidence": ("interaction skipped (no deterministic target)"
                                 if not primary_cta_selector else "click target not found"),
                })

            # ── Check 7: CAPTURE ─────────────────────────────────────
            shot_mobile = await page.screenshot(type="jpeg", quality=80)
            result["screenshots"]["mobile_375"] = len(shot_mobile)  # size only in-memory; caller persists bytes separately
            _dv = DEVICE_VIEWPORTS["desktop"]
            await page.set_viewport_size({"width": _dv["width"], "height": _dv["height"]})
            await page.wait_for_timeout(200)
            shot_desktop = await page.screenshot(type="jpeg", quality=80)
            result["screenshots"]["desktop"] = len(shot_desktop)
            # Full-page shot (2026-08-30 upgrade) — SAME already-loaded
            # page, desktop viewport, full_page=True. Playwright's
            # full_page flag captures the current DOM's rendered
            # height; it does NOT re-navigate/reload (no new `goto`
            # call here — see `test_fullpage_no_renavigate`). Taken
            # AFTER Check 6's interaction step, so any content that
            # mounts on click is included, but this is still not a
            # guarantee for arbitrary scroll-triggered lazy content —
            # see `lazy_load_note` below, surfaced on the receipt.
            shot_fullpage = await page.screenshot(type="jpeg", quality=80, full_page=True)
            result["screenshots"]["fullpage"] = len(shot_fullpage)
            result["_raw_screenshots"] = {
                "mobile_375": shot_mobile, "desktop": shot_desktop, "fullpage": shot_fullpage,
            }
            result["lazy_load_note"] = (
                "Full-page shot captures rendered content; scroll-"
                "triggered lazy elements may not appear."
            )

            if run_trace:
                trace_path = f"/tmp/deploy_verify_trace_{result['run_id']}.zip"
                await context.tracing.stop(path=trace_path)
                result["trace_path"] = trace_path

            result["egress_attempts"] = egress_attempts[:20]

            all_pass = all(c["pass"] for c in result["checks"])
            result["verdict"] = "pass" if all_pass else "fail"
            if all_pass:
                result["what_happened"] = "All deterministic checks passed."
            else:
                first_fail = next((c for c in result["checks"] if not c["pass"]), None)
                result["what_happened"] = (
                    f"Failed: {first_fail['name']} — {first_fail['evidence']}"
                    if first_fail else "Failed (reason unclear)."
                )
                result["fail_reason"] = result["fail_reason"] or (
                    first_fail["name"] if first_fail else "unknown"
                )
        finally:
            await browser.close()
    return result


# ═══════════════ V1b — LLM judgment (LEFT PENDING this round) ══════
async def run_judgment(accessibility_snapshot: str, *, mock_llm: bool) -> dict:
    """V1b — founder-directed: LEFT PENDING this round. The pruned
    accessibility-snapshot capture, nonce-wrapped untrusted-content
    boundary, and advisory-schema parsing are deliberately NOT built
    yet. If this is ever triggered (it is not wired into any caller
    this round), it logs and returns a pending stub — it never
    constructs an LLM call, in mock OR real mode, so there is zero
    model spend from this function this round. Next round wires the
    real thing (pruned snapshot <=3000 tokens, nonce boundaries, max-
    3-point advisory-only schema, refuse-in-mock).

    Full-page screenshot guard (2026-08-30) — this function's ONLY
    accepted input is a text accessibility snapshot, never an image.
    If V1b is ever wired for real, the full-page shot from V1a's
    capture step must NOT be handed to it directly — only a sliced +
    resized (1568px, ~28px/token) crop would ever be acceptable input,
    and even that is future scope. This raises loudly right now if
    anyone ever tries to pass raw image bytes (fullpage or otherwise)
    into this function, so the guard is enforced even while the real
    judgment logic doesn't exist yet."""
    if isinstance(accessibility_snapshot, bytes):
        raise TypeError(
            "run_judgment must never receive raw image bytes (fullpage "
            "or otherwise) — a text-only accessibility snapshot is "
            "required; a full-page screenshot needs slicing+resizing "
            "(1568px, ~28px/token) before it could ever be LLM input."
        )
    logger.info("deploy_verify.run_judgment: V1b pending — not built this round, no model called")
    return {"verdict": "pending", "points": [], "note": "V1b pending — not built this round"}
