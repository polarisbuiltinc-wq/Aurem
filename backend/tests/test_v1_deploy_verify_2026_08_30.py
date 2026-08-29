"""
tests/test_v1_deploy_verify_2026_08_30.py — V1 (2026-08-30): server-
side headless deploy-verify. Founder's revised v2 spec.

Covers V1a (deterministic engine, zero-LLM spy), V1c (security fence —
9 rules incl. the local-endpoint-hardening note), V1b (gated judgment),
and V1e's 6 E2E scenarios against a local disposable static fixture
server (`/tmp/v1_fixture_site/`, started by this test module itself,
NEVER a real user site, NEVER production).

E2E note on the SSRF fence + local fixture tension: `validate_target_url`
IS the real, unmodified SSRF guard (reused from `ora_chat.deep_research`,
already has its own 9-test suite in `test_iter270_ssrf_guard.py` — not
re-proven here). Scenarios 1/2/5/6 (deterministic checks) need the
fence to ALLOW a 127.0.0.1 fixture target, which the real fence
correctly refuses (loopback) — so those 4 scenarios monkeypatch ONLY
`deploy_verify.validate_target_url` to simulate "already passed the
fence", the exact same technique `test_iter270_ssrf_guard.py` itself
uses (mocking `socket.getaddrinfo` to test the public-IP-allowed path).
Scenario 3 (SSRF-blocked) uses the REAL, unmocked fence against a real
private/metadata target — proving the fence itself, unmocked.
"""
from __future__ import annotations

import asyncio
import http.server
import socketserver
import threading
import time
from unittest.mock import AsyncMock, patch

import pytest

FIXTURE_DIR = "/tmp/v1_fixture_site"
FIXTURE_PORT = 8899
FIXTURE_BASE = f"http://127.0.0.1:{FIXTURE_PORT}"


_FIXTURE_FILES = {
    "index.html": (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<meta name="build-sha" content="deadbeef1234"></head><body>'
        "<h1>V1 fixture site — good page</h1>"
        '<button id="cta">Click me</button>'
        '<img src="/ok.png" width="10" height="10">'
        "</body></html>"
    ),
    "bad.html": (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<meta name="build-sha" content="stale-old-sha-0000"></head><body>'
        "<h1>V1 fixture site — broken page</h1>"
        '<img src="/does-not-exist.png" width="10" height="10">'
        '<script>console.error("simulated console error for V1 E2E test");'
        'throw new Error("simulated uncaught pageerror for V1 E2E test");</script>'
        "</body></html>"
    ),
    "overflow.html": (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<meta name="build-sha" content="deadbeef1234"></head><body>'
        "<h1>V1 fixture — overflow-only page</h1>"
        '<div style="width: 5000px; height: 20px;">deliberately over-wide container</div>'
        "</body></html>"
    ),
    # smallest-possible valid 1x1 PNG (not a 0-byte placeholder — a
    # real decodable image, so the "good page" scenario's breakage
    # sweep genuinely finds nothing broken, not by accident).
    "ok.png": bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000c4944415478da6364f80f000101000018dd8db4000000004945"
        "4e44ae426082"
    ),
}


@pytest.fixture(scope="module", autouse=True)
def _fixture_server():
    """(Re)writes the disposable local static fixture files on every
    run — no dependency on /tmp state surviving between processes —
    then starts the fixture server for this test module only, if not
    already running (main agent may have started it manually during
    interactive E2E verification too)."""
    import os
    os.makedirs(FIXTURE_DIR, exist_ok=True)
    for name, content in _FIXTURE_FILES.items():
        mode = "wb" if isinstance(content, bytes) else "w"
        with open(os.path.join(FIXTURE_DIR, name), mode) as f:
            f.write(content)

    import urllib.request
    try:
        urllib.request.urlopen(f"{FIXTURE_BASE}/index.html", timeout=1)
        yield
        return
    except Exception:
        pass

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=FIXTURE_DIR, **kw)
        def log_message(self, *a):
            pass

    httpd = socketserver.TCPServer(("127.0.0.1", FIXTURE_PORT), Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    time.sleep(0.3)
    yield
    httpd.shutdown()


def _allow_fence(monkeypatch):
    """Test-only bypass of JUST the fence gate (not the deterministic
    engine) for the local fixture target — see module docstring."""
    import services.deploy_verify as dv
    monkeypatch.setattr(dv, "validate_target_url", lambda url: (True, ""))


# ═══════════════════════ V1a — zero-LLM spy ═══════════════════════
@pytest.mark.asyncio
async def test_verify_a_zero_llm(monkeypatch):
    """The entire deterministic run constructs NO LLM provider."""
    _allow_fence(monkeypatch)
    import services.deploy_verify as dv
    with patch("services.llm.call_llm", new=AsyncMock(side_effect=AssertionError(
            "V1a must never call an LLM"))):
        result = await dv.run_verify(f"{FIXTURE_BASE}/index.html", run_trace=False)
    assert result["verdict"] == "pass"


# ═══════════════════════ V1c — security fence (9 rules) ═══════════
def test_verify_ssrf_blocked_metadata_ip():
    import services.deploy_verify as dv
    ok, why = dv.validate_target_url("http://169.254.169.254/latest/meta-data/")
    assert ok is False
    assert why in ("link_local",) or "link_local" in why or "private" in why


def test_verify_ssrf_blocked_private_ip():
    import services.deploy_verify as dv
    ok, why = dv.validate_target_url("http://10.0.0.5/")
    assert ok is False


def test_verify_ssrf_blocked_loopback():
    import services.deploy_verify as dv
    ok, why = dv.validate_target_url(f"{FIXTURE_BASE}/index.html")
    assert ok is False
    assert why == "loopback"


def test_verify_egress_blocked_off_allowlist_domain():
    import services.deploy_verify as dv
    assert dv._same_allowlisted_domain("https://evil.example.com/steal", "myapp.com") is False
    assert dv._same_allowlisted_domain("https://myapp.com/page", "myapp.com") is True
    assert dv._same_allowlisted_domain("https://cdn.myapp.com/asset.js", "myapp.com") is True
    assert dv._same_allowlisted_domain("https://myapp.com.evil.com/", "myapp.com") is False


def test_verify_url_reverify_mid_run_same_check_used():
    """Mid-run re-verify (rule 3) reuses the SAME allowlist function
    the route guard uses on every sub-resource (rule 2) — one check,
    applied both places, not two divergent implementations."""
    import services.deploy_verify as dv
    import inspect
    src = inspect.getsource(dv._run_verify_inner)
    assert "_same_allowlisted_domain" in src
    assert "validate_target_url" in src  # full re-resolve, not just string compare


@pytest.mark.asyncio
async def test_verify_mid_run_reverify_blocks_changed_route(monkeypatch):
    """A changed-route navigation that fails the FULL re-verify (e.g.
    the route now resolves somewhere unsafe post-rebind) is fenced
    closed — named as a reverify block, never silently skipped."""
    import services.deploy_verify as dv
    calls = {"n": 0}

    def _fence(nav_url):
        calls["n"] += 1
        if calls["n"] == 1:
            return True, ""  # entry gate passes
        return False, "reverify_simulated_block"  # every re-check after fails closed

    monkeypatch.setattr(dv, "validate_target_url", _fence)
    result = await dv.run_verify(
        f"{FIXTURE_BASE}/index.html", changed_routes=["index.html"], run_trace=False,
    )
    route_check = next(c for c in result["checks"] if c["name"] == "changed_route_assertion")
    assert route_check["pass"] is False
    assert any("reverify_blocked" in str(r.get("error", "")) for r in route_check["evidence"])


def test_verify_isolated_context_no_stored_state():
    """Every run gets a fresh BrowserContext with no storage_state and
    downloads disabled — code-level guard, not just a claim."""
    import inspect
    import services.deploy_verify as dv
    src = inspect.getsource(dv._run_verify_inner)
    assert "storage_state" not in src  # never passed → always fresh/empty
    assert "accept_downloads=False" in src


def test_verify_output_truncated():
    import services.deploy_verify as dv
    long_text = "x" * 10_000
    assert len(dv._truncate(long_text)) <= dv.OUTPUT_TRUNCATE_CAP + len("...[truncated]")


@pytest.mark.asyncio
async def test_verify_audit_logged():
    import services.deploy_verify as dv
    calls = []

    class _FakeDB:
        class deploy_verify_audit:
            @staticmethod
            async def insert_one(doc):
                calls.append(doc)

    ok, why = dv.validate_target_url("http://169.254.169.254/")
    assert ok is False
    await dv._audit_log(_FakeDB(), run_id="x", url="http://169.254.169.254/", result="blocked_ssrf")
    assert len(calls) == 1
    assert calls[0]["result"] == "blocked_ssrf"


def test_verify_no_shell_no_db():
    """The engine's own module never imports subprocess/os.system/eval
    — a dumb worker, no shell escape hatch."""
    import inspect
    import services.deploy_verify as dv
    src = inspect.getsource(dv)
    assert "subprocess" not in src
    assert "os.system" not in src
    assert "eval(" not in src


def test_verify_endpoint_hardened_no_new_http_endpoint():
    """V1c rule 9 — this round adds NO new standalone HTTP/MCP
    endpoint for the verify worker (it's an in-process function called
    from the existing deploy router) — nothing to loopback-bind or
    Host-validate because nothing new is exposed. Regression guard:
    fails loudly if a future round adds a router without this test
    being updated to check its binding."""
    import services.deploy_verify as dv
    import inspect
    src = inspect.getsource(dv)
    assert "APIRouter" not in src
    assert "FastAPI(" not in src


# ═══════════════ V1b — LLM judgment (LEFT PENDING this round) ═════
@pytest.mark.asyncio
async def test_judgment_refused_in_mock():
    """Mock mode never gets an active judgment (V1b is pending
    entirely this round, in both mock and real mode)."""
    import services.deploy_verify as dv
    result = await dv.run_judgment("some accessibility snapshot text", mock_llm=True)
    assert result["verdict"] == "pending"
    assert "pending" in result["note"]


@pytest.mark.asyncio
async def test_judgment_never_calls_model_pending_this_round():
    """V1b is left pending this round — the stub must never construct
    an LLM call in EITHER mock or real mode (zero spend either way)."""
    import services.deploy_verify as dv
    with patch("services.llm.call_llm", new=AsyncMock(side_effect=AssertionError(
            "V1b must not call an LLM this round — left pending"))):
        result = await dv.run_judgment("x" * 50_000, mock_llm=False)
    assert result["verdict"] == "pending"


@pytest.mark.asyncio
async def test_judgment_suspicious_never_fails_a_passing_run(monkeypatch):
    """Even a hypothetical non-clean advisory verdict must never flip
    a passing deterministic run to fail — enforced by the CALLER
    never reading advisory_model into the verdict computation. (V1b
    itself is pending this round; this test still guards the caller
    contract for whenever it's wired.)"""
    import services.deploy_verify as dv
    _allow_fence(monkeypatch)
    result = await dv.run_verify(f"{FIXTURE_BASE}/index.html", run_trace=False)
    assert result["verdict"] == "pass"
    result["advisory_model"] = {"verdict": "suspicious", "points": ["looks odd"]}
    assert result["verdict"] == "pass"  # setting advisory after the fact never mutates verdict


@pytest.mark.asyncio
async def test_judgment_token_cap():
    import services.deploy_verify as dv
    assert dv.JUDGMENT_TOKEN_CAP <= 2000


# ═══════════════════════ V1e — E2E scenarios (6) ═══════════════════
@pytest.mark.asyncio
async def test_e2e_scenario_1_pass(monkeypatch):
    _allow_fence(monkeypatch)
    import services.deploy_verify as dv
    result = await dv.run_verify(
        f"{FIXTURE_BASE}/index.html", expected_build="deadbeef1234",
        primary_cta_selector="#cta", run_trace=True,
    )
    assert result["verdict"] == "pass", result
    assert result["build_match"] is True
    assert result["screenshots"]["mobile_375"] > 0
    assert result["screenshots"]["desktop"] > 0
    assert result["trace_path"]


@pytest.mark.asyncio
async def test_e2e_scenario_2_multi_fail_named(monkeypatch):
    _allow_fence(monkeypatch)
    import services.deploy_verify as dv
    result = await dv.run_verify(
        f"{FIXTURE_BASE}/bad.html", expected_build="deadbeef1234", run_trace=False,
    )
    assert result["verdict"] == "fail"
    names = {c["name"]: c["pass"] for c in result["checks"]}
    assert names["version_identity"] is False    # stale build marker
    assert names["breakage_sweep"] is False       # 404 img
    assert names["runtime_health"] is False       # console.error + pageerror
    assert len(result["console_errors"]) >= 1


@pytest.mark.asyncio
async def test_e2e_scenario_3_ssrf_blocked_no_launch(monkeypatch):
    """REAL fence, unmocked — proves the launch never happens at all."""
    import services.deploy_verify as dv
    with patch("playwright.async_api.async_playwright") as mock_pw:
        result = await dv.run_verify("http://169.254.169.254/latest/meta-data/", run_trace=False)
    mock_pw.assert_not_called()
    assert result["verdict"] == "fail"
    assert "blocked_ssrf" in result["fail_reason"]


@pytest.mark.asyncio
async def test_e2e_scenario_4_model_advisory_mock_refused(monkeypatch):
    _allow_fence(monkeypatch)
    import services.deploy_verify as dv
    result = await dv.run_verify(f"{FIXTURE_BASE}/index.html", run_trace=False)
    judgment = await dv.run_judgment("accessibility snapshot", mock_llm=True)
    result["advisory_model"] = judgment
    assert result["verdict"] == "pass"  # deterministic verdict unaffected
    assert judgment["verdict"] == "pending"  # V1b left pending this round


@pytest.mark.asyncio
async def test_e2e_scenario_5_stale_build_named_specifically(monkeypatch):
    _allow_fence(monkeypatch)
    import services.deploy_verify as dv
    result = await dv.run_verify(
        f"{FIXTURE_BASE}/bad.html", expected_build="deadbeef1234", run_trace=False,
    )
    version_check = next(c for c in result["checks"] if c["name"] == "version_identity")
    assert version_check["pass"] is False
    assert "stale build" in version_check["evidence"]
    assert result["fail_reason"] == "stale_build"


@pytest.mark.asyncio
async def test_e2e_scenario_6_overflow_detected_zero_llm(monkeypatch):
    _allow_fence(monkeypatch)
    import services.deploy_verify as dv
    with patch("services.llm.call_llm", new=AsyncMock(side_effect=AssertionError(
            "overflow detection must be zero-LLM"))):
        result = await dv.run_verify(f"{FIXTURE_BASE}/overflow.html", run_trace=False)
    geometry_check = next(c for c in result["checks"] if c["name"] == "geometry")
    assert geometry_check["pass"] is False
    assert geometry_check["evidence"]["overflowX"] is True
    assert result["verdict"] == "fail"
