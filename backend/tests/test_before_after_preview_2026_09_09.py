"""
tests/test_before_after_preview_2026_09_09.py

Founder feature request: "show me the real edited page, same colors,
before vs after". Extends the EXISTING "After Fix" screenshot system
(services/preview_capture.py) with a matching "before" screenshot,
captured automatically the moment a task is submitted (fire-and-forget,
before any code change lands). Uses a real server-side screenshot
(not a raw iframe) — most real hosts send X-Frame-Options/CSP headers
that block their site from loading inside anyone else's iframe.
"""
from __future__ import annotations

import pytest

from services.preview_capture import capture_before_snapshot_for_task


class _FakeUpdateResult:
    pass


class _FakeTasksCollection:
    def __init__(self):
        self.updates = []

    async def update_one(self, filt, update):
        self.updates.append((filt, update))
        return _FakeUpdateResult()


class _FakeDB:
    def __init__(self):
        self.cto_tasks = _FakeTasksCollection()


@pytest.mark.asyncio
async def test_capture_before_snapshot_noop_without_preview_url():
    db = _FakeDB()
    await capture_before_snapshot_for_task(db, "p1", "u1", "t1", "")
    assert db.cto_tasks.updates == []


@pytest.mark.asyncio
async def test_capture_before_snapshot_noop_on_capture_failure(monkeypatch):
    db = _FakeDB()

    async def _fake_capture(url, device="phone"):
        return None

    monkeypatch.setattr("services.preview_capture.capture_screenshot", _fake_capture)
    await capture_before_snapshot_for_task(db, "p1", "u1", "t1", "https://example.com")
    assert db.cto_tasks.updates == [], "must not write anything on capture failure — honest, no fake receipt"


@pytest.mark.asyncio
async def test_capture_before_snapshot_stores_receipt_on_success(monkeypatch):
    db = _FakeDB()

    async def _fake_capture(url, device="phone"):
        assert url == "https://example.com/"
        return b"fake-jpeg-bytes"

    async def _fake_upload(image_bytes, key_suffix):
        assert image_bytes == b"fake-jpeg-bytes"
        assert key_suffix == "p1/before-t1.jpg"
        return f"deploy-receipts/{key_suffix}"

    monkeypatch.setattr("services.preview_capture.capture_screenshot", _fake_capture)
    monkeypatch.setattr("services.preview_capture.upload_receipt", _fake_upload)

    await capture_before_snapshot_for_task(db, "p1", "u1", "t1", "https://example.com/")

    assert len(db.cto_tasks.updates) == 1
    filt, update = db.cto_tasks.updates[0]
    assert filt == {"task_id": "t1"}
    assert update == {"$set": {"before_receipts": {"/": "deploy-receipts/p1/before-t1.jpg"}}}


@pytest.mark.asyncio
async def test_capture_before_snapshot_never_raises_on_db_error(monkeypatch):
    """Fire-and-forget must never blow up the caller (task submission
    already responded 200 by the time this background task runs)."""
    class _BrokenTasksCollection:
        async def update_one(self, *a, **kw):
            raise RuntimeError("db down")

    class _BrokenDB:
        cto_tasks = _BrokenTasksCollection()

    async def _fake_capture(url, device="phone"):
        return b"fake-jpeg-bytes"

    async def _fake_upload(image_bytes, key_suffix):
        return "deploy-receipts/x"

    monkeypatch.setattr("services.preview_capture.capture_screenshot", _fake_capture)
    monkeypatch.setattr("services.preview_capture.upload_receipt", _fake_upload)

    await capture_before_snapshot_for_task(_BrokenDB(), "p1", "u1", "t1", "https://example.com")
