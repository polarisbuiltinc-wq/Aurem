"""tests/test_c1_browser_free_fallback_2026_08_30.py — C1: graceful
no-browser degrade for `deploy_verify.run_verify` / `web_inspect`'s
snapshot fetch. Triggered ONLY when Playwright's Chromium executable
is missing at launch (the exact failure the founder saw in
production: "chromium executable doesn't exist at /root/bin/chromium").
Any OTHER launch failure still hard-fails exactly as before — this
suite proves both branches, plus the full `run_web_inspect` flow using
the browser-free raw-HTML fallback.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _missing_chromium_exc() -> Exception:
    return Exception(
        "BrowserType.launch: Executable doesn't exist at "
        "/root/bin/chromium\nLooks like Playwright was just installed "
        "or updated."
    )


def _mock_async_playwright(launch_side_effect: Exception):
    """Same patch target/shape as the existing
    `test_e2e_scenario_3_ssrf_blocked_no_launch` — patches the module
    Playwright itself is imported from, so both deploy_verify.py's and
    web_inspect.py's local `from playwright.async_api import
    async_playwright` pick it up."""
    pw_instance = MagicMock()
    pw_instance.chromium.launch = AsyncMock(side_effect=launch_side_effect)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=pw_instance)
    cm.__aexit__ = AsyncMock(return_value=None)
    return patch("playwright.async_api.async_playwright", return_value=cm)


class _FakeHttpxResponse:
    def __init__(self, status_code=200, text="<html><h1>Hello</h1></html>"):
        self.status_code = status_code
        self.text = text


class _FakeHttpxClient:
    def __init__(self, response=None, raise_exc=None):
        self._response = response or _FakeHttpxResponse()
        self._raise_exc = raise_exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    async def get(self, url):
        if self._raise_exc:
            raise self._raise_exc
        return self._response


# ═════════════ deploy_verify.run_verify — browser missing ═════════════
@pytest.mark.asyncio
async def test_run_verify_degrades_when_chromium_missing(monkeypatch):
    """C1 — missing-Chromium launch error -> verdict='degraded',
    browser_available=False, a labeled fallback check, never a bare
    'fail' that looks like a normal broken page."""
    import services.deploy_verify as dv

    monkeypatch.setattr(dv, "validate_target_url", lambda url: (True, ""))
    fake_client = _FakeHttpxClient(_FakeHttpxResponse(200, "<html><h1>ok</h1></html>"))
    with _mock_async_playwright(_missing_chromium_exc()), \
         patch("httpx.AsyncClient", return_value=fake_client):
        result = await dv.run_verify("https://example.com", run_trace=False)

    assert result["verdict"] == "degraded"
    assert result["browser_available"] is False
    assert result["fail_reason"] == "browser_unavailable"
    names = {c["name"]: c for c in result["checks"]}
    assert names["reachability_fallback"]["pass"] is True
    assert "browser-free fallback" in names["reachability_fallback"]["evidence"]
    assert names["content_signal_fallback"]["pass"] is True
    assert "Chromium is not installed" in result["what_happened"]
    # 2026-08-31 — the RAW launch error (with the exact attempted path
    # Playwright tried) must be surfaced, not just the exception type,
    # so a stale env-var path vs a wrong default path can be told apart
    # from the response alone, no build-log digging required.
    assert result["chromium_launch_error"] == str(_missing_chromium_exc())[:400]
    assert "/root/bin/chromium" in result["chromium_launch_error"]
    assert "/root/bin/chromium" in result["what_happened"]
    assert "/root/bin/chromium" in names["reachability_fallback"]["evidence"]


@pytest.mark.asyncio
async def test_run_verify_degraded_fallback_request_itself_fails(monkeypatch):
    """C1 — Chromium missing AND the httpx fallback request also
    fails: still `degraded` (never silently reported as a normal
    'fail'), with the fallback check explicitly failed."""
    import services.deploy_verify as dv

    monkeypatch.setattr(dv, "validate_target_url", lambda url: (True, ""))
    fake_client = _FakeHttpxClient(raise_exc=ConnectionError("refused"))
    with _mock_async_playwright(_missing_chromium_exc()), \
         patch("httpx.AsyncClient", return_value=fake_client):
        result = await dv.run_verify("https://example.com", run_trace=False)

    assert result["verdict"] == "degraded"
    assert result["browser_available"] is False
    names = {c["name"]: c for c in result["checks"]}
    assert names["reachability_fallback"]["pass"] is False


@pytest.mark.asyncio
async def test_run_verify_other_launch_errors_still_hard_fail(monkeypatch):
    """C1 boundary — a launch failure that is NOT 'missing Chromium'
    (e.g. a sandbox/permission error) must still hard-fail exactly as
    before this change. Proves the degrade path is narrowly scoped."""
    import services.deploy_verify as dv

    monkeypatch.setattr(dv, "validate_target_url", lambda url: (True, ""))
    with _mock_async_playwright(Exception("Target page crashed unexpectedly")):
        result = await dv.run_verify("https://example.com", run_trace=False)

    assert result["verdict"] == "fail"
    assert result["browser_available"] is True  # untouched — not a browser-missing case
    assert result["fail_reason"].startswith("verify_engine_error:")


def test_is_browser_missing_error_matches_founder_reported_message():
    """C1 — exact regression guard for the production error string the
    founder reported: 'chromium executable doesn't exist at
    /root/bin/chromium'."""
    import services.deploy_verify as dv

    assert dv._is_browser_missing_error(
        Exception("chromium executable doesn't exist at /root/bin/chromium"))
    assert not dv._is_browser_missing_error(Exception("Target page crashed"))
    assert not dv._is_browser_missing_error(TimeoutError("navigation timeout"))


# ═════════════ web_inspect snapshot fetch — browser missing ═════════════
@pytest.mark.asyncio
async def test_fetch_snapshot_degrades_when_chromium_missing():
    """C1 — web_inspect's own launch site degrades the same way: raw
    HTML via httpx instead of a rendered innerText snapshot, no
    screenshot, `browser_available=False`."""
    import services.web_inspect as wi

    fake_client = _FakeHttpxClient(_FakeHttpxResponse(200, "<html><body>raw html</body></html>"))
    with _mock_async_playwright(_missing_chromium_exc()), \
         patch("httpx.AsyncClient", return_value=fake_client):
        out = await wi._fetch_snapshot_and_screenshot_meta("https://example.com", "example.com")

    assert out["browser_available"] is False
    assert out["degraded_reason"] and out["degraded_reason"].startswith("chromium_unavailable:")
    assert "/root/bin/chromium" in out["degraded_reason"]  # exact attempted path, not just the type
    assert out["screenshot_meta"] is None
    assert "raw html" in out["snapshot"]
    assert out["error"] is None


@pytest.mark.asyncio
async def test_run_web_inspect_full_flow_degraded_still_answers(monkeypatch):
    """C1 — end-to-end: Chromium missing, run_web_inspect still gets an
    advisory answer (using the httpx raw-HTML fallback as its
    snapshot) and plainly surfaces browser_available=False /
    degraded_reason on the output — never silently pretends a full
    rendered inspection happened."""
    import services.web_inspect as wi
    import services.deploy_verify as dv

    monkeypatch.setattr(dv, "validate_target_url", lambda url: (True, ""))

    captured_user_msg = {}

    async def _fake_call_openrouter_model(model, system, user, **kw):
        captured_user_msg["user"] = user
        return "Advisory answer from the raw-HTML fallback."

    class _FakeAudit:
        async def insert_one(self, doc):
            pass

    class _FakeDB:
        deploy_verify_audit = _FakeAudit()

    fake_client = _FakeHttpxClient(_FakeHttpxResponse(200, "<html><h1>Hi</h1></html>"))
    with _mock_async_playwright(_missing_chromium_exc()), \
         patch("httpx.AsyncClient", return_value=fake_client), \
         patch("services.llm.openrouter_client.call_openrouter_model",
               _fake_call_openrouter_model), \
         patch("services.llm_usd_cap.assert_within_usd_cap", AsyncMock()), \
         patch("services.llm_usd_cap.record_usd_spend", AsyncMock()), \
         patch("services.ora_chat.cost_tracker.log_call", AsyncMock()):
        out = await wi.run_web_inspect("https://example.com", "what is this?",
                                        db=_FakeDB(), user_id="admin1")

    assert out["ok"] is True
    assert out["answer"] == "Advisory answer from the raw-HTML fallback."
    assert out["browser_available"] is False
    assert out["degraded_reason"].startswith("chromium_unavailable:")
    # 2026-08-31 — ORA (the LLM) must actually SEE the exact launch
    # error + attempted path via a trusted system note, so a founder
    # asking "check homepage" gets the real path back, not a bare
    # "browser_unavailable" with nothing to act on.
    assert "SYSTEM NOTE" in captured_user_msg["user"]
    assert "/root/bin/chromium" in captured_user_msg["user"]
