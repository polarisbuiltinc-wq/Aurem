"""Iter 367 · Item C · REAL E2E — Persistent Correction Rules
14-day auto-graduation.

Proves:
  1. New rule created via add_rule() has shadow_started_at + graduated_at=None.
  2. Rule with hits >= 1 and age < 14 days is NOT graduated by the sweep.
  3. Rule with hits >= 1 and age >= 14 days IS graduated by the sweep,
     with graduated_reason='auto_14day_hits'.
  4. Rule with 0 hits is never graduated regardless of age.
  5. Inactive (user-disabled) rule never auto-graduates.
  6. Legacy Phase-1 rule (no shadow_started_at field) is skipped.
  7. Sweep is idempotent — running it twice doesn't re-promote.
  8. Once graduated, is_rule_effectively_enforced() returns True even when
     project_enforce=False (so loop_engine injects it into the executor).
  9. Non-graduated shadow rule with project_enforce=False → not enforced.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest


# In-memory Mongo double — enough surface for correction_rules.
class _Coll:
    def __init__(self, name):
        self.name = name
        self.rows = []
    async def find_one(self, filt, projection=None, sort=None):
        for r in self.rows:
            if all(r.get(k) == v for k, v in filt.items()):
                return dict(r)
        return None
    def find(self, filt=None, projection=None):
        rows = [dict(r) for r in self.rows
                if all(r.get(k) == v for k, v in (filt or {}).items())]
        return _Cursor(rows)
    async def insert_one(self, doc):
        self.rows.append(dict(doc))
        class _R: inserted_id = "id"
        return _R()
    async def update_one(self, filt, ops, upsert=False):
        modified = 0
        for r in self.rows:
            if all(r.get(k) == v for k, v in filt.items()):
                if "$set" in ops:
                    r.update(ops["$set"])
                    modified = 1
                if "$inc" in ops:
                    for k, v in ops["$inc"].items():
                        r[k] = r.get(k, 0) + v
                        modified = 1
                break
        else:
            if upsert:
                new = dict(filt); 
                if "$set" in ops: new.update(ops["$set"])
                self.rows.append(new); modified = 1
        class _R: matched_count = modified; modified_count = modified
        return _R()
    async def delete_one(self, filt):
        for i, r in enumerate(self.rows):
            if all(r.get(k) == v for k, v in filt.items()):
                del self.rows[i]
                class _R: deleted_count = 1
                return _R()
        class _R: deleted_count = 0
        return _R()
    async def count_documents(self, filt=None):
        return sum(1 for r in self.rows
                   if all(r.get(k) == v for k, v in (filt or {}).items()))


class _Cursor:
    def __init__(self, rows): self.rows = rows
    def sort(self, k, d=1):
        self.rows.sort(key=lambda x: x.get(k) or "", reverse=(d < 0))
        return self
    async def to_list(self, length=100): return self.rows[:length]
    def __aiter__(self):
        self._i = 0; return self
    async def __anext__(self):
        if self._i >= len(self.rows): raise StopAsyncIteration
        r = self.rows[self._i]; self._i += 1
        return r


class _DB:
    def __init__(self):
        self._colls = {}
    def __getattr__(self, n):
        if n not in self._colls:
            self._colls[n] = _Coll(n)
        return self._colls[n]


def _iso_ago(days: float) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.isoformat()


# ─────────────────────────────────────────────────────────────────
# Test 1 — add_rule stamps shadow_started_at + graduated_at=None
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_rule_stamps_shadow_fields():
    from services.correction_rules import add_rule

    db = _DB()
    res = await add_rule(db, "u1", "p1", "always use tabs, never spaces")
    assert res["ok"] is True
    r = res["rule"]
    assert r["shadow_started_at"] is not None
    assert r["graduated_at"] is None
    assert r["graduated_reason"] is None
    assert r["hits"] == 0
    assert r["active"] is True


# ─────────────────────────────────────────────────────────────────
# Test 2 — Young rule (5 days old, 3 hits) NOT graduated
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_young_rule_not_graduated():
    from services.correction_rules import graduate_shadow_eligible_rules

    db = _DB()
    db.correction_rules.rows.append({
        "rule_id": "r_young", "user_id": "u1", "project_id": "p1",
        "instruction": "no spaces", "hits": 3, "active": True,
        "shadow_started_at": _iso_ago(5), "graduated_at": None,
    })
    res = await graduate_shadow_eligible_rules(db)
    assert res["promoted"] == 0
    assert res["eligible_count"] == 0
    # DB unchanged
    r = await db.correction_rules.find_one({"rule_id": "r_young"})
    assert r["graduated_at"] is None


# ─────────────────────────────────────────────────────────────────
# Test 3 — Old + hit rule IS graduated (the happy path)
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_old_rule_with_hits_is_graduated():
    from services.correction_rules import graduate_shadow_eligible_rules

    db = _DB()
    db.correction_rules.rows.append({
        "rule_id": "r_veteran", "user_id": "u1", "project_id": "p1",
        "instruction": "prefer functional style",
        "hits": 5, "active": True,
        "shadow_started_at": _iso_ago(20),   # 20 days > 14 threshold
        "graduated_at": None,
    })
    res = await graduate_shadow_eligible_rules(db)
    assert res["promoted"] == 1
    assert res["eligible_count"] == 1
    assert res["rules"][0]["rule_id"] == "r_veteran"
    r = await db.correction_rules.find_one({"rule_id": "r_veteran"})
    assert r["graduated_at"] is not None
    assert r["graduated_reason"] == "auto_14day_hits"


# ─────────────────────────────────────────────────────────────────
# Test 4 — Zero-hits rule NEVER graduates
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_zero_hits_rule_not_graduated():
    from services.correction_rules import graduate_shadow_eligible_rules

    db = _DB()
    db.correction_rules.rows.append({
        "rule_id": "r_dusty", "user_id": "u1", "project_id": "p1",
        "instruction": "reject `any` type", "hits": 0, "active": True,
        "shadow_started_at": _iso_ago(100),  # very old — but no hits
        "graduated_at": None,
    })
    res = await graduate_shadow_eligible_rules(db)
    assert res["promoted"] == 0


# ─────────────────────────────────────────────────────────────────
# Test 5 — Inactive rule NEVER graduates
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_inactive_rule_not_graduated():
    from services.correction_rules import graduate_shadow_eligible_rules

    db = _DB()
    db.correction_rules.rows.append({
        "rule_id": "r_disabled", "user_id": "u1", "project_id": "p1",
        "instruction": "disabled rule", "hits": 5, "active": False,
        "shadow_started_at": _iso_ago(30), "graduated_at": None,
    })
    res = await graduate_shadow_eligible_rules(db)
    assert res["promoted"] == 0


# ─────────────────────────────────────────────────────────────────
# Test 6 — Legacy Phase-1 rule (missing shadow_started_at) is skipped
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_legacy_rule_without_shadow_start_skipped():
    from services.correction_rules import graduate_shadow_eligible_rules

    db = _DB()
    db.correction_rules.rows.append({
        "rule_id": "r_legacy", "user_id": "u1", "project_id": "p1",
        "instruction": "phase 1 legacy", "hits": 5, "active": True,
        # shadow_started_at intentionally missing (Phase 1 shape)
        "graduated_at": None,
    })
    res = await graduate_shadow_eligible_rules(db)
    assert res["promoted"] == 0


# ─────────────────────────────────────────────────────────────────
# Test 7 — Idempotent — re-running the sweep doesn't re-promote
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sweep_is_idempotent():
    from services.correction_rules import graduate_shadow_eligible_rules

    db = _DB()
    db.correction_rules.rows.append({
        "rule_id": "r_idem", "user_id": "u1", "project_id": "p1",
        "instruction": "idempotent test", "hits": 1, "active": True,
        "shadow_started_at": _iso_ago(15), "graduated_at": None,
    })
    r1 = await graduate_shadow_eligible_rules(db)
    r2 = await graduate_shadow_eligible_rules(db)
    assert r1["promoted"] == 1
    assert r2["promoted"] == 0
    assert r2["eligible_count"] == 0


# ─────────────────────────────────────────────────────────────────
# Test 8 — Graduated rule is effectively enforced regardless of
#          project global toggle → loop_engine WILL inject it.
# ─────────────────────────────────────────────────────────────────


def test_graduated_rule_is_effectively_enforced():
    from services.correction_rules import is_rule_effectively_enforced

    graduated_rule = {"rule_id": "g", "graduated_at": "2026-01-01T00:00:00+00:00"}
    shadow_rule    = {"rule_id": "s", "graduated_at": None}

    # Graduated rule → enforced regardless of project toggle
    assert is_rule_effectively_enforced(graduated_rule, False) is True
    assert is_rule_effectively_enforced(graduated_rule, True)  is True
    # Shadow rule → only enforced when project toggle is on
    assert is_rule_effectively_enforced(shadow_rule, False) is False
    assert is_rule_effectively_enforced(shadow_rule, True)  is True


# ─────────────────────────────────────────────────────────────────
# Test 9 — dry_run reports eligibles without writing
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dry_run_reports_without_writing():
    from services.correction_rules import graduate_shadow_eligible_rules

    db = _DB()
    db.correction_rules.rows.append({
        "rule_id": "r_dry", "user_id": "u1", "project_id": "p1",
        "instruction": "dry-run", "hits": 2, "active": True,
        "shadow_started_at": _iso_ago(20), "graduated_at": None,
    })
    res = await graduate_shadow_eligible_rules(db, dry_run=True)
    assert res["eligible_count"] == 1
    assert res["promoted"] == 0
    r = await db.correction_rules.find_one({"rule_id": "r_dry"})
    assert r["graduated_at"] is None   # not actually written


# ─────────────────────────────────────────────────────────────────
# Test 10 — rule_report surfaces new fields for the founder UI
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rule_report_includes_graduation_columns():
    from services.correction_rules import add_rule, rule_report

    db = _DB()
    await add_rule(db, "u1", "p1", "test rule")
    rep = await rule_report(db, "u1", "p1")
    assert rep["rule_count"] == 1
    row = rep["rules"][0]
    assert "shadow_started_at" in row
    assert "graduated_at" in row
    assert "graduated_reason" in row
    assert "days_in_shadow" in row
