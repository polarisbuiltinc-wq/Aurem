"""tests/test_receipt_dual_shot_2026_08_30.py — receipt UI polish
(2026-08-30): the Deploy panel's receipt view exposes BOTH the
full-page and viewport screenshots (V1's `fullpage_receipt_key`,
already persisted by V1d — see test_v1d_deploy_verify_wiring) via
`/log/{run_id}` and `/runs/{run_id}/receipt?variant=`, so the
frontend can render them side by side ("Full page" / "Viewport")."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException


class _FakeDeployRuns:
    def __init__(self, doc):
        self._doc = doc

    async def find_one(self, filt, proj=None):
        return self._doc


class _FakeDB:
    def __init__(self, doc):
        self.aurem_cto_deploy_runs = _FakeDeployRuns(doc)


@pytest.mark.asyncio
async def test_log_endpoint_exposes_fullpage_receipt_key(monkeypatch):
    import routers.deploy as deploy_mod

    doc = {
        "run_id": "r1", "status": "ok", "exit_code": 0, "head_sha": "abc",
        "verified": True, "verify_note": None, "verify_url": "https://x.test",
        "receipt_key": "viewport-key", "output": [],
        "verify_engine": {"fullpage_receipt_key": "fullpage-key"},
        "started_at": "t0", "finished_at": "t1",
    }
    db = _FakeDB(doc)
    monkeypatch.setattr(deploy_mod, "require_db", lambda: db)
    monkeypatch.setattr(deploy_mod, "current_dev", AsyncMock(return_value={"user_id": "u1"}))

    out = await deploy_mod.get_log("r1", since=0, authorization="Bearer t")
    assert out["receipt_key"] == "viewport-key"
    assert out["fullpage_receipt_key"] == "fullpage-key"


@pytest.mark.asyncio
async def test_receipt_shows_both_shots(monkeypatch):
    """t_receipt_shows_both_shots — GET /runs/{id}/receipt fetches the
    viewport key by default, and the fullpage key when
    variant=fullpage, so the frontend can request+render BOTH images
    side by side."""
    import routers.deploy as deploy_mod

    doc = {
        "receipt_key": "viewport-key",
        "verify_engine": {"fullpage_receipt_key": "fullpage-key"},
    }
    db = _FakeDB(doc)
    monkeypatch.setattr(deploy_mod, "require_db", lambda: db)
    monkeypatch.setattr(deploy_mod, "current_dev", AsyncMock(return_value={"user_id": "u1"}))

    fetched_keys = []

    async def _fake_fetch_receipt(key):
        fetched_keys.append(key)
        return b"fake-jpeg-bytes"

    monkeypatch.setattr("services.preview_capture.fetch_receipt", _fake_fetch_receipt)

    viewport_resp = await deploy_mod.get_run_receipt("r1", variant="viewport", authorization="Bearer t")
    fullpage_resp = await deploy_mod.get_run_receipt("r1", variant="fullpage", authorization="Bearer t")

    assert fetched_keys == ["viewport-key", "fullpage-key"]
    assert viewport_resp.media_type == "image/jpeg"
    assert fullpage_resp.media_type == "image/jpeg"


@pytest.mark.asyncio
async def test_receipt_fullpage_404_when_missing(monkeypatch):
    """Old runs (pre-2026-08-30) have no fullpage_receipt_key — the
    variant=fullpage request 404s cleanly instead of falling back to
    the viewport image (never silently substitute)."""
    import routers.deploy as deploy_mod

    doc = {"receipt_key": "viewport-key", "verify_engine": {}}
    db = _FakeDB(doc)
    monkeypatch.setattr(deploy_mod, "require_db", lambda: db)
    monkeypatch.setattr(deploy_mod, "current_dev", AsyncMock(return_value={"user_id": "u1"}))

    with pytest.raises(HTTPException) as exc:
        await deploy_mod.get_run_receipt("r1", variant="fullpage", authorization="Bearer t")
    assert exc.value.status_code == 404
