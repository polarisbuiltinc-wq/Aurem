"""
tests/test_v1d_deploy_verify_wiring_2026_08_30.py — V1d (2026-08-30):
wires the V1 deterministic verify engine into the EXISTING deploy
receipt path (`routers/deploy.py::_verify_and_capture`), the trust
events trail, and the notification bell — additive to the pre-existing
shallow httpx-reachability check (S3-D4), never replacing it.

Uses a lightweight in-memory fake db (no real Mongo needed) + monkeypatches
`services.deploy_verify.run_verify` directly (the engine itself already
has its own 22-test suite — this file only proves the WIRING, not the
engine's checks again)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


class _FakeCursorNone:
    async def find_one(self, *a, **kw):
        return None


class _FakeDeployRuns:
    def __init__(self):
        self.updates = []

    async def find_one(self, *a, **kw):
        return None

    async def update_one(self, filt, update):
        self.updates.append(update.get("$set", {}))


class _FakeTrustEvents:
    def __init__(self):
        self.inserted = []

    async def insert_one(self, doc):
        self.inserted.append(doc)


class _FakeNotifications:
    def __init__(self):
        self.inserted = []

    async def insert_one(self, doc):
        self.inserted.append(doc)


class _FakeDB:
    def __init__(self):
        self.cto_projects = _FakeCursorNone()
        self.aurem_cto_deploy_runs = _FakeDeployRuns()
        self.trust_surface_events = _FakeTrustEvents()
        self.notifications = _FakeNotifications()


class _FakeHTTPResp:
    status_code = 200


@pytest.mark.asyncio
async def test_verify_engine_wired_pass_no_bell(monkeypatch):
    """A passing engine run persists `verify_engine.verdict='pass'`,
    fires `verify_started`+`verify_passed` trust events, and does NOT
    ring the bell (only failures are persistent-notified)."""
    import routers.deploy as deploy_mod

    db = _FakeDB()
    monkeypatch.setattr(deploy_mod, "require_db", lambda: db)

    async def _fake_httpx_get(self, url):
        return _FakeHTTPResp()

    async def _fake_run_verify(url, **kw):
        return {"run_id": "x", "url": url, "verdict": "pass", "build_match": None,
                "checks": [{"name": "reachability", "pass": True, "evidence": "HTTP 200, TTFB 50ms"}],
                "console_errors": [], "fail_reason": None, "what_happened": "All checks passed.",
                "duration_ms": 500, "_raw_screenshots": {}}

    with patch("httpx.AsyncClient.get", new=_fake_httpx_get), \
         patch("services.preview_capture.capture_screenshot", new=AsyncMock(return_value=None)), \
         patch("services.deploy_verify.run_verify", new=_fake_run_verify):
        await deploy_mod._verify_and_capture(
            "u1", "run1", "p1", {"verify_url": "https://example.com"},
        )

    verify_engine_sets = [u for u in db.aurem_cto_deploy_runs.updates if "verify_engine" in u]
    assert len(verify_engine_sets) == 1
    assert verify_engine_sets[0]["verify_engine"]["verdict"] == "pass"
    trust_kinds = [d["kind"] for d in db.trust_surface_events.inserted]
    assert "verify_started" in trust_kinds
    assert "verify_passed" in trust_kinds
    assert "verify_failed" not in trust_kinds
    assert db.notifications.inserted == []


@pytest.mark.asyncio
async def test_verify_engine_wired_persists_fullpage_receipt_key(monkeypatch):
    """Full-page screenshot upgrade (2026-08-30) — when the engine
    returns a `fullpage` raw screenshot, it gets its OWN receipt key
    (separate from the viewport `receipt_key`), plus the lazy-load
    caveat note, both persisted onto `verify_engine`."""
    import routers.deploy as deploy_mod

    db = _FakeDB()
    monkeypatch.setattr(deploy_mod, "require_db", lambda: db)

    upload_calls = []

    async def _fake_upload_receipt(image_bytes, key):
        upload_calls.append(key)
        return f"uploaded::{key}"

    async def _fake_httpx_get(self, url):
        return _FakeHTTPResp()

    async def _fake_run_verify(url, **kw):
        return {
            "run_id": "x", "url": url, "verdict": "pass", "build_match": None,
            "checks": [{"name": "reachability", "pass": True, "evidence": "HTTP 200, TTFB 50ms"}],
            "console_errors": [], "fail_reason": None, "what_happened": "All checks passed.",
            "duration_ms": 500,
            "lazy_load_note": "Full-page shot captures rendered content; scroll-triggered lazy elements may not appear.",
            "_raw_screenshots": {"mobile_375": b"mobilebytes", "desktop": b"desktopbytes", "fullpage": b"fullpagebytes"},
        }

    with patch("httpx.AsyncClient.get", new=_fake_httpx_get), \
         patch("services.preview_capture.capture_screenshot", new=AsyncMock(return_value=None)), \
         patch("services.preview_capture.upload_receipt", new=_fake_upload_receipt), \
         patch("services.deploy_verify.run_verify", new=_fake_run_verify):
        await deploy_mod._verify_and_capture(
            "u1", "run_fp", "p1", {"verify_url": "https://example.com"},
        )

    verify_engine_sets = [u["verify_engine"] for u in db.aurem_cto_deploy_runs.updates if "verify_engine" in u]
    assert len(verify_engine_sets) == 1
    ve = verify_engine_sets[0]
    assert ve["receipt_key"] == "uploaded::deploy-runs/run_fp-verify-engine.jpg"
    assert ve["fullpage_receipt_key"] == "uploaded::deploy-runs/run_fp-verify-engine-fullpage.jpg"
    assert "scroll-triggered lazy elements" in ve["lazy_load_note"]
    assert any("fullpage" in k for k in upload_calls)


@pytest.mark.asyncio
async def test_verify_engine_wired_fail_rings_persistent_bell(monkeypatch):
    """A failing engine run fires `verify_failed` and emits a
    PERSISTENT `verify_failed` bell notification (per V1d spec)."""
    import routers.deploy as deploy_mod
    from services.notifications import PERSISTENT_TYPES

    assert "verify_failed" in PERSISTENT_TYPES

    db = _FakeDB()
    monkeypatch.setattr(deploy_mod, "require_db", lambda: db)

    async def _fake_httpx_get(self, url):
        return _FakeHTTPResp()

    async def _fake_run_verify(url, **kw):
        return {"run_id": "x", "url": url, "verdict": "fail", "build_match": False,
                "checks": [{"name": "version_identity", "pass": False, "evidence": "stale build"}],
                "console_errors": [], "fail_reason": "stale_build",
                "what_happened": "Failed: version_identity — stale build",
                "duration_ms": 700, "_raw_screenshots": {}}

    with patch("httpx.AsyncClient.get", new=_fake_httpx_get), \
         patch("services.preview_capture.capture_screenshot", new=AsyncMock(return_value=None)), \
         patch("services.deploy_verify.run_verify", new=_fake_run_verify):
        await deploy_mod._verify_and_capture(
            "u1", "run2", "p1", {"verify_url": "https://example.com"},
        )

    trust_kinds = [d["kind"] for d in db.trust_surface_events.inserted]
    assert "verify_failed" in trust_kinds
    assert len(db.notifications.inserted) == 1
    notif = db.notifications.inserted[0]
    assert notif["type"] == "verify_failed"
    assert notif["persistent"] is True
    assert "stale build" in notif["text"]
