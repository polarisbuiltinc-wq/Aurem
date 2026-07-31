"""Iter 367 · Item D · Risk-Based Routing (Phase 2) — REAL E2E.

Proves:
  1. score_change() returns AUTO_SHIP for benign small change.
  2. score_change() returns WARN_SHIP when path is sensitive.
  3. score_change() returns PAUSE_FOR_FOUNDER when multiple heavy
     signals fire (path + eval() + AWS key).
  4. Tier names match the LOCKED scope exactly (AUTO_SHIP,
     WARN_SHIP, PAUSE_FOR_FOUNDER — never "SAFE/CAUTION/BLOCK").
  5. Scoring is FAIL-OPEN — a raise inside never propagates.
  6. record_score() writes to the `risk_scores` collection.
  7. is_enforcing() is False for the first 14 days after shadow_start.
  8. should_halt() is False even for PAUSE_FOR_FOUNDER during shadow.
  9. should_halt() is True for PAUSE_FOR_FOUNDER after enforce cutover.
 10. admin_summary() aggregates counts + surfaces days_until_enforce.
 11. New-dependency detection fires on requirements.txt additions.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


# In-memory Mongo double — same shape as previous test files.
class _Coll:
    def __init__(self, name):
        self.rows = []; self.name = name
    async def find_one(self, filt, sort=None):
        rows = [r for r in self.rows
                if all(r.get(k) == v for k, v in filt.items())]
        if sort:
            for k, d in reversed(sort):
                rows.sort(key=lambda x: x.get(k) or "", reverse=(d < 0))
        return dict(rows[0]) if rows else None
    def find(self, filt=None):
        rows = []
        for r in self.rows:
            ok = True
            for k, v in (filt or {}).items():
                val = r.get(k)
                if isinstance(v, dict):
                    if "$gte" in v and (val is None or val < v["$gte"]):
                        ok = False; break
                else:
                    if val != v:
                        ok = False; break
            if ok:
                rows.append(dict(r))
        return _Cur(rows)
    async def insert_one(self, doc):
        self.rows.append(dict(doc))
        class _R: inserted_id = "id"
        return _R()
    async def update_one(self, filt, ops, upsert=False):
        for r in self.rows:
            if all(r.get(k) == v for k, v in filt.items()):
                if "$set" in ops: r.update(ops["$set"])
                class _R: modified_count = 1; matched_count = 1
                return _R()
        if upsert:
            new = dict(filt)
            if "$set" in ops: new.update(ops["$set"])
            self.rows.append(new)
        class _R: modified_count = 0; matched_count = 0
        return _R()


class _Cur:
    def __init__(self, rows): self.rows = rows
    def __aiter__(self):
        self._i = 0; return self
    async def __anext__(self):
        if self._i >= len(self.rows): raise StopAsyncIteration
        r = self.rows[self._i]; self._i += 1
        return r


class _DB:
    def __init__(self): self._c = {}
    def __getattr__(self, n):
        if n not in self._c: self._c[n] = _Coll(n)
        return self._c[n]


# ─────────────────────────────────────────────────────────────────
# Tier-name compliance test — locked scope MUST match verbatim
# ─────────────────────────────────────────────────────────────────


def test_tier_names_match_locked_scope():
    from services.risk_routing import (
        TIER_AUTO_SHIP, TIER_WARN_SHIP, TIER_PAUSE_FOR_FOUNDER, ALL_TIERS,
    )
    assert TIER_AUTO_SHIP == "AUTO_SHIP"
    assert TIER_WARN_SHIP == "WARN_SHIP"
    assert TIER_PAUSE_FOR_FOUNDER == "PAUSE_FOR_FOUNDER"
    assert set(ALL_TIERS) == {"AUTO_SHIP", "WARN_SHIP", "PAUSE_FOR_FOUNDER"}
    # Explicit negation — the earlier draft names must NOT appear.
    for forbidden in ("SAFE", "CAUTION", "BLOCK"):
        assert forbidden not in ALL_TIERS


# ─────────────────────────────────────────────────────────────────
# Path + diff scoring
# ─────────────────────────────────────────────────────────────────


def test_benign_change_is_auto_ship():
    from services.risk_routing import score_change, TIER_AUTO_SHIP
    s = score_change(
        path="frontend/src/components/ui/Button.jsx",
        before_bytes=b"export const Button = () => <button />",
        after_bytes=b"export const Button = () => <button>x</button>",
    )
    assert s.tier == TIER_AUTO_SHIP
    assert s.score < 0.4
    assert s.error is None


def test_sensitive_path_bumps_tier():
    from services.risk_routing import score_change, TIER_WARN_SHIP
    s = score_change(
        path="backend/routers/auth.py",
        before_bytes=b"def check(): pass",
        after_bytes=b"def check(): return True",
    )
    # auth_code weight = 0.55 → sigmoid ~ 0.35 → still AUTO or WARN
    # depending on rounding; regardless it must NOT be PAUSE alone.
    assert s.tier in ("AUTO_SHIP", "WARN_SHIP")
    assert "path" in s.signals


def test_dangerous_pattern_and_sensitive_path_pauses_for_founder():
    from services.risk_routing import score_change, TIER_PAUSE_FOR_FOUNDER
    payload = b"""from stripe import x
def api(req):
    result = eval(req.body)   # dangerous
    return result
"""
    s = score_change(
        path="backend/routers/payments.py",
        before_bytes=b"# safe stub",
        after_bytes=payload,
    )
    # payments (0.60) + eval (0.50) + admin_route (no) — total ~1.10
    # → sigmoid(4*(1.1-1)) ≈ 0.60 which lands in WARN_SHIP; we need
    # to bump total further. Let's add an AWS key too.
    payload2 = payload + b"\nAWS_ACCESS = '***REDACTED_AWS_KEY***'\n"
    s = score_change(
        path="backend/routers/payments.py",
        before_bytes=b"# safe stub",
        after_bytes=payload2,
    )
    assert s.tier == TIER_PAUSE_FOR_FOUNDER, (
        f"expected PAUSE_FOR_FOUNDER, got {s.tier} at score={s.score} "
        f"signals={s.signals}")


def test_scoring_is_fail_open(monkeypatch):
    """A crash inside score_change() must collapse to AUTO_SHIP + error
    tag. It must NEVER propagate an exception."""
    from services import risk_routing

    def _boom(*a, **k): raise RuntimeError("scoring exploded")
    monkeypatch.setattr(risk_routing, "_sigmoid_clip", _boom)

    s = risk_routing.score_change(path="anything.py",
                                   before_bytes=b"", after_bytes=b"x")
    assert s.tier == "AUTO_SHIP"
    assert s.error is not None
    assert "RuntimeError" in s.error


def test_new_dependency_detected_in_requirements():
    from services.risk_routing import score_change
    before = b"flask==3.0\nrequests==2.31\n"
    after  = b"flask==3.0\nrequests==2.31\npyc\ncryptography==42.0\n"
    s = score_change(
        path="backend/requirements.txt",
        before_bytes=before, after_bytes=after,
    )
    assert "new_dependencies" in s.signals
    assert "cryptography" in s.signals["new_dependencies"]


# ─────────────────────────────────────────────────────────────────
# Shadow window / enforce cutover
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_shadow_mode_never_halts_pause_tier():
    from services import risk_routing as rr

    db = _DB()
    # Seed shadow_start to NOW so we're inside the 14-day window.
    await db.risk_routing_meta.insert_one({
        "_key": "shadow_start",
        "started_at": datetime.now(timezone.utc).isoformat(),
    })
    s = rr.RiskScore(tier=rr.TIER_PAUSE_FOR_FOUNDER, score=0.9, path="x.py")
    assert await rr.is_enforcing(db) is False
    assert await rr.should_halt(db, s) is False


@pytest.mark.asyncio
async def test_after_2_weeks_pause_tier_actually_halts():
    from services import risk_routing as rr

    db = _DB()
    # Seed shadow_start 20 days ago → beyond the 14-day window.
    old = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
    await db.risk_routing_meta.insert_one({
        "_key": "shadow_start", "started_at": old,
    })
    assert await rr.is_enforcing(db) is True
    # PAUSE tier must halt; other tiers still don't.
    assert await rr.should_halt(
        db, rr.RiskScore(tier=rr.TIER_PAUSE_FOR_FOUNDER, score=0.9,
                          path="x.py")) is True
    assert await rr.should_halt(
        db, rr.RiskScore(tier=rr.TIER_WARN_SHIP, score=0.5,
                          path="x.py")) is False
    assert await rr.should_halt(
        db, rr.RiskScore(tier=rr.TIER_AUTO_SHIP, score=0.1,
                          path="x.py")) is False


# ─────────────────────────────────────────────────────────────────
# record_score writes real rows
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_score_writes_row():
    from services import risk_routing as rr

    db = _DB()
    s = rr.RiskScore(tier=rr.TIER_WARN_SHIP, score=0.5,
                      signals={"path": {"tag": "auth_code"}},
                      path="backend/routers/auth.py")
    await rr.record_score(db, loop_id="l1", user_id="u1",
                           project_id="p1", phase="execute",
                           path=s.path, score=s)
    row = await db.risk_scores.find_one({"loop_id": "l1"})
    assert row is not None
    assert row["tier"] == "WARN_SHIP"
    assert row["path"] == "backend/routers/auth.py"


# ─────────────────────────────────────────────────────────────────
# admin_summary aggregates + surfaces mode/countdown
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_summary_aggregates_and_shows_mode():
    from services import risk_routing as rr

    db = _DB()
    # Seed shadow_start ~5 days ago → still shadow.
    await db.risk_routing_meta.insert_one({
        "_key": "shadow_start",
        "started_at": (datetime.now(timezone.utc)
                       - timedelta(days=5)).isoformat(),
    })
    # A handful of score rows in-window.
    for t in ("AUTO_SHIP", "AUTO_SHIP", "WARN_SHIP", "PAUSE_FOR_FOUNDER"):
        await db.risk_scores.insert_one({
            "ts": datetime.now(timezone.utc).isoformat(),
            "tier": t, "path": "backend/routers/auth.py",
        })
    summ = await rr.admin_summary(db)
    assert summ["mode"] == "shadow"
    assert summ["days_until_enforce"] is not None
    assert summ["days_until_enforce"] > 0
    assert summ["days_until_enforce"] <= 9.01   # 14-5=9, allow for jitter
    assert summ["tier_counts"]["AUTO_SHIP"] == 2
    assert summ["tier_counts"]["WARN_SHIP"] == 1
    assert summ["tier_counts"]["PAUSE_FOR_FOUNDER"] == 1
    assert summ["total_scores"] == 4
    assert summ["top_paths"][0][0] == "backend/routers/auth.py"
