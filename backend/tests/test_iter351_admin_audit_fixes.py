"""Iter 351 — admin audit fixes locks.

1. Stale-alert auto-resolve: healthy probe resolves ALL active alerts
   for that integration (not just today's day_key).
2. QA counts build-manifest fallback for prod pods without tests/.
3. Stripe probe self-heal awareness (source lock).
"""
import json
import os
import time
import uuid

import pytest
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

_HEALTH_SRC = open(os.path.join(
    os.path.dirname(__file__), "..", "services",
    "integration_health.py")).read()


def _db():
    import motor.motor_asyncio
    cli = motor.motor_asyncio.AsyncIOMotorClient(os.environ["MONGO_URL"])
    return cli[os.environ["DB_NAME"]]


# ── 1. Stale alert auto-resolve across days ──────────────────────────
@pytest.mark.asyncio
async def test_healthy_probe_resolves_yesterdays_alert():
    from services.topup_alerts import upsert_alerts_from_snapshot
    db = _db()
    iid = f"fc_test_{uuid.uuid4().hex[:6]}"
    # Plant a stuck alert from YESTERDAY (old day_key).
    await db.topup_alerts.insert_one({
        "alert_id": f"al_{uuid.uuid4().hex[:10]}",
        "alert_key": f"{iid}::critical::2020-01-01",
        "integration_id": iid,
        "integration_name": "Firecrawl (test)",
        "severity": "critical",
        "day_key": "2020-01-01",
        "first_seen": time.time() - 86400,
        "last_seen": time.time() - 86400,
        "status": "active",
    })
    # Today's snapshot says the integration is healthy (status ok →
    # classify() returns None → resolve branch).
    snap = {"generated_at": time.time(), "results": [{
        "id": iid, "name": "Firecrawl (test)", "status": "ok",
        "summary": "scraped fine",
    }]}
    await upsert_alerts_from_snapshot(db, snap)
    doc = await db.topup_alerts.find_one({"integration_id": iid}, {"_id": 0})
    assert doc["status"] == "resolved", (
        "yesterday's active alert must auto-resolve on a healthy probe")
    assert doc.get("resolved_by") == "auto_probe"
    await db.topup_alerts.delete_many({"integration_id": iid})


# ── 2. QA counts manifest fallback ───────────────────────────────────
def test_counts_fallback_to_manifest_when_tests_missing(monkeypatch, tmp_path):
    import routers.admin_qa as aq
    # Simulate a prod pod: no backend/tests dir under APP_ROOT, but a
    # committed qa_manifest.json exists.
    (tmp_path / "backend").mkdir()
    manifest = {
        "generated_at": 123.0,
        "test_counts": {
            "backend_pytest": {"files": 420, "tests": 3647},
            "frontend_vitest": {"files": 38, "tests": 218},
        },
        "grand_total_tests": 3879,
    }
    (tmp_path / "backend" / "qa_manifest.json").write_text(
        json.dumps(manifest))
    monkeypatch.setattr(aq, "_APP_ROOT", tmp_path)
    out = aq._harvest_counts()
    assert out["source"] == "build_manifest"
    assert out["backend_pytest"]["tests"] == 3647
    assert out["grand_total_tests"] == 3879


def test_counts_live_fs_on_preview():
    import routers.admin_qa as aq
    out = aq._harvest_counts()
    assert out["source"] == "live_fs"
    assert out["backend_pytest"]["files"] > 300
    assert out["grand_total_tests"] > 3000


def test_manifest_file_exists_and_fresh_shape():
    p = os.path.join(os.path.dirname(__file__), "..", "qa_manifest.json")
    assert os.path.exists(p), "backend/qa_manifest.json must be committed"
    m = json.loads(open(p).read())
    assert m["test_counts"]["backend_pytest"]["tests"] > 3000
    assert m["grand_total_tests"] > 3000


# ── 3. Stripe probe self-heal awareness (source locks) ───────────────
def test_stripe_probe_checks_selfheal_before_broken():
    assert "_match_discovered_price" in _HEALTH_SRC
    assert "checkout SELF-HEALS" in _HEALTH_SRC
    # broken verdict must only fire for unhealed IDs
    assert "NOT covered by checkout self-heal" in _HEALTH_SRC


def test_alert_resolve_has_no_day_key_filter():
    src = open(os.path.join(
        os.path.dirname(__file__), "..", "services",
        "topup_alerts.py")).read()
    resolve_block = src.split("if not severity:")[1].split("continue")[0]
    assert '"day_key"' not in resolve_block, (
        "healthy-probe resolve must NOT be scoped to today's day_key")
    assert '"resolved_by": "auto_probe"' in resolve_block


# ── 4. /version built_at normalization (Invalid Date fix) ────────────
def test_built_at_never_has_offset_plus_z():
    from routers.version import _read_built_at
    ts = _read_built_at()
    assert not (ts.endswith("Z") and "+" in ts), (
        f"built_at {ts!r} carries BOTH an offset and a trailing Z — "
        "JS renders that as Invalid Date")
    from datetime import datetime
    datetime.fromisoformat(ts)  # must parse cleanly


# ── 5. loop-metrics owner classification uses user_id field ─────────
def test_loop_metrics_lookup_by_user_id_field():
    src = open(os.path.join(
        os.path.dirname(__file__), "..", "routers", "admin.py")).read()
    assert 'dev_users.find_one(\n                    {"user_id": uid}' in src
    assert 'doc.get("phase") or doc.get("current_phase")' in src
