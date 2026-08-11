"""Tests for services/http/client.py — the shared HTTP wrapper.

Covers the four contracts the wrapper promises:
  1. 2xx passes through with the raw httpx.Response
  2. 5xx / 408 / 429 retries then raises ExternalCallError
  3. 4xx (non-retriable) raises ExternalCallError immediately
  4. Network error surfaces as ExternalCallError with cause preserved

Uses respx to mock httpx so no real network calls.
"""
from __future__ import annotations

import asyncio
import pytest
import httpx
import respx

from services.http import ext_request, ExternalCallError
from services.retry_guard import get_breaker


@pytest.fixture(autouse=True)
def _reset_breakers():
    # Fresh breaker state per test so a prior test can't push a dep
    # into OPEN and pollute the next assertion.
    for dep in ("github", "resend", "vercel"):
        br = get_breaker(dep)
        br.state = "closed"
        br.consecutive_fails = 0
        br.opened_at = 0.0
    yield


@pytest.mark.asyncio
@respx.mock
async def test_happy_path_returns_response():
    respx.get("https://api.example.com/ok").respond(
        200, json={"ok": True},
    )
    r = await ext_request("github", "GET", "https://api.example.com/ok",
                          max_retries=0)
    assert r.status_code == 200
    assert r.json() == {"ok": True}


@pytest.mark.asyncio
@respx.mock
async def test_5xx_retries_then_raises_external_call_error():
    # Every call returns 502
    respx.get("https://api.example.com/flaky").respond(502, text="upstream fail")
    with pytest.raises(ExternalCallError) as exc_info:
        await ext_request("github", "GET", "https://api.example.com/flaky",
                          max_retries=2)
    err = exc_info.value
    assert err.dep == "github"
    assert err.status == 502
    assert err.method == "GET"
    assert "flaky" in err.url
    assert "upstream fail" in (err.body_snippet or "")


@pytest.mark.asyncio
@respx.mock
async def test_4xx_no_retry_immediate_error():
    respx.post("https://api.example.com/bad").respond(400, text="bad input")
    with pytest.raises(ExternalCallError) as exc_info:
        await ext_request("resend", "POST", "https://api.example.com/bad",
                          json={"x": 1}, max_retries=3)
    err = exc_info.value
    assert err.status == 400
    # Assert the call was NOT retried — respx should have recorded exactly one call.
    assert respx.routes[0].call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_network_error_wraps_as_external_call_error():
    respx.get("https://api.example.com/dead").mock(
        side_effect=httpx.ConnectError("connection refused"),
    )
    with pytest.raises(ExternalCallError) as exc_info:
        await ext_request("vercel", "GET", "https://api.example.com/dead",
                          max_retries=1)
    err = exc_info.value
    assert err.dep == "vercel"
    assert err.status is None
    assert "connection refused" in str(err).lower() or "connecterror" in str(err).lower()
    assert isinstance(err.__cause__, httpx.ConnectError)


@pytest.mark.asyncio
@respx.mock
async def test_raise_for_status_false_returns_error_response():
    respx.get("https://api.example.com/soft-fail").respond(404, text="nope")
    r = await ext_request("github", "GET", "https://api.example.com/soft-fail",
                          max_retries=0, raise_for_status=False)
    assert r.status_code == 404
    assert r.text == "nope"


@pytest.mark.asyncio
@respx.mock
async def test_request_id_header_is_injected():
    captured = {}

    def _capture(request: httpx.Request):
        captured["rid"] = request.headers.get("X-Request-ID")
        return httpx.Response(200, json={"ok": True})

    respx.get("https://api.example.com/rid").mock(side_effect=_capture)
    r = await ext_request("github", "GET", "https://api.example.com/rid",
                          max_retries=0)
    assert r.status_code == 200
    assert captured["rid"] and len(captured["rid"]) >= 16


@pytest.mark.asyncio
@respx.mock
async def test_caller_headers_are_preserved():
    captured = {}

    def _capture(request: httpx.Request):
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(200)

    respx.post("https://api.example.com/auth").mock(side_effect=_capture)
    await ext_request(
        "resend", "POST", "https://api.example.com/auth",
        headers={"Authorization": "Bearer xyz"},
        max_retries=0,
    )
    assert captured["auth"] == "Bearer xyz"
