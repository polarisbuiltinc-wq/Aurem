"""Iter 367 · STEP 0 audit fix — rollback fake-success bug proof.

Before this fix:
  - POST /rollback/revert-last-ship inserted into db.rollback_trigger and
    returned 200. No consumer of rollback_trigger existed. Endpoint
    lied about success — no revert was ever created.
  - services.rollback_manager.execute_rollback did the same thing.
  - Also filtered loop_outcomes on `shipped: True` which no doc has,
    so /rollback/candidates always returned [] (even for users with
    real ships).

After this fix:
  - Both endpoints resolve the loop context (loop_id + project + PAT)
    then fire services.loop_rollback.run_rollback as a background task,
    which calls the real github_api_writer.revert_commit.
  - No rollback_trigger writes anywhere.
  - Endpoints return ok:False with a real reason when context missing.

These tests monkeypatch gh_api_revert (same pattern as the existing
loop_rollback tests) so we can prove the plumbing without hitting
GitHub. The wiring is what was broken — the underlying revert code
was fine.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest


# ─────────────────────────────────────────────────────────────────────
# Fixtures — in-memory Mongo double sharp enough to drive the routers.
# ─────────────────────────────────────────────────────────────────────


class _MemColl:
    """Tiny in-memory Mongo collection with async find/find_one/update_one."""
    def __init__(self, name: str, rows: list | None = None):
        self.name = name
        self.rows = list(rows or [])
        # For asserting we NEVER touched a collection we shouldn't.
        self.insert_calls = []

    async def find_one(self, filt=None, projection=None, sort=None):
        filt = filt or {}
        candidates = [r for r in self.rows if _match(r, filt)]
        if sort:
            for key, direction in reversed(sort):
                candidates.sort(key=lambda x: x.get(key) or "",
                                reverse=(direction < 0))
        return dict(candidates[0]) if candidates else None

    def find(self, filt=None, projection=None):
        filt = filt or {}
        rows = [dict(r) for r in self.rows if _match(r, filt)]
        return _MemCursor(rows)

    async def update_one(self, filt, ops, upsert=False):
        for r in self.rows:
            if _match(r, filt):
                if "$set" in ops:
                    r.update(ops["$set"])
                return _Result(1)
        if upsert:
            new = dict(filt)
            if "$set" in ops:
                new.update(ops["$set"])
            self.rows.append(new)
            return _Result(1)
        return _Result(0)

    async def insert_one(self, doc):
        self.insert_calls.append(dict(doc))
        self.rows.append(dict(doc))
        return _Result(1)

    async def count_documents(self, filt=None):
        return sum(1 for r in self.rows if _match(r, filt or {}))


class _MemCursor:
    def __init__(self, rows):
        self.rows = rows
    def sort(self, key, direction):
        self.rows.sort(key=lambda x: x.get(key) or "",
                       reverse=(direction < 0))
        return self
    def limit(self, n):
        self.rows = self.rows[:n]
        return self
    def __aiter__(self):
        self._i = 0
        return self
    async def __anext__(self):
        if self._i >= len(self.rows):
            raise StopAsyncIteration
        r = self.rows[self._i]; self._i += 1
        return r


class _Result:
    def __init__(self, n):
        self.matched_count = n
        self.modified_count = n
        self.inserted_id = "fake_id"


def _match(row: dict, filt: dict) -> bool:
    """Best-effort mini-matcher: eq, $ne, $regex prefix, $in, $exists."""
    for k, v in (filt or {}).items():
        val = row.get(k)
        if isinstance(v, dict):
            if "$ne" in v and val == v["$ne"]:
                return False
            if "$in" in v and val not in v["$in"]:
                return False
            if "$exists" in v:
                if v["$exists"] and k not in row:
                    return False
                if not v["$exists"] and k in row:
                    return False
            if "$regex" in v:
                import re
                if not re.search(v["$regex"], str(val or "")):
                    return False
            if "$gte" in v and (val is None or val < v["$gte"]):
                return False
        else:
            if val != v:
                return False
    return True


class _MemDB:
    def __init__(self):
        self._colls = {}
    def __getattr__(self, name):
        if name not in self._colls:
            self._colls[name] = _MemColl(name)
        return self._colls[name]
    def __getitem__(self, name):
        return getattr(self, name)


# ─────────────────────────────────────────────────────────────────────
# Test: STEP 0 · Fix #1 — /rollback/revert-last-ship
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_revert_last_ship_calls_real_run_rollback(monkeypatch):
    """The endpoint must:
      1. Find the latest unreverted loop_outcomes for the user.
      2. Resolve project + PAT.
      3. Set loop_sessions.rollback_status="queued".
      4. Schedule run_rollback in BackgroundTasks.
      5. NOT write to rollback_trigger.
    """
    from routers import user_rollback
    from fastapi import BackgroundTasks

    db = _MemDB()
    # Seed a shipped loop for user "u1"
    db.loop_outcomes.rows.append({
        "loop_id":    "l1",
        "user_id":    "u1",
        "project_id": "p1",
        "commit_sha": "abc1234deadbeef1234567890",
        "shipped_at": "2026-01-15T10:00:00+00:00",
        "reverted":   False,
    })
    # Seed session so idempotence checks pass
    db.loop_sessions.rows.append({
        "loop_id":  "l1",
        "user_id":  "u1",
        "state":    "completed",
        "context":  {"commit": {"full_sha": "abc1234deadbeef1234567890"}},
    })
    # Seed project + PAT
    db.cto_projects.rows.append({
        "project_id":     "p1",
        "user_id":        "u1",
        "github_owner":   "acme",
        "github_repo":    "widgets",
        "branch":         "main",
        "github_token":   "enc:PAT",
    })

    # Auth stub
    async def _dev(_auth):
        return {"user_id": "u1", "tier": "pro", "is_admin": False}
    monkeypatch.setattr(
        "routers.user_rollback.current_dev", _dev
    )
    monkeypatch.setattr(
        "routers.user_rollback.require_db", lambda: db
    )
    # PAT decrypt stub.
    # 2026-08-23 audit fix — production's real call path is
    # `services.pat_vault.get_repo_token()` → its OWN module-level
    # `_decrypt_pat`, not `routers.cto_projects._decrypt_pat` (that's a
    # separate wrapper that re-imports pat_vault's function fresh on
    # every call — patching it never touched what `get_repo_token`
    # actually calls). Patch the real target.
    async def _decrypt_pat(uid, enc):
        return "ghp_REAL_TOKEN"
    async def _user_gh_token(uid):
        return None
    monkeypatch.setattr(
        "services.pat_vault._decrypt_pat", _decrypt_pat
    )
    monkeypatch.setattr(
        "routers.cto_projects._user_gh_token", _user_gh_token
    )
    # Capture what run_rollback is called with.
    # 2026-08-23 audit fix — the production background-task call site
    # (routers/user_rollback.py) imports `run_rollback_bg`, a
    # module-level `safe_bg(run_rollback)` wrapper created at import
    # time (services/loop_rollback.py:332). That wrapper closed over
    # the ORIGINAL `run_rollback` function object, so patching the
    # `run_rollback` name here never touched what actually runs —
    # this test was silently making a real, unmocked GitHub API call
    # (401 against the fake "acme/widgets" repo) instead of exercising
    # the stub. Patch the wrapper that's actually invoked instead.
    calls = {}
    async def _fake_run(**kwargs):
        calls.update(kwargs)
    monkeypatch.setattr(
        "services.loop_rollback.run_rollback_bg", _fake_run
    )

    bg = BackgroundTasks()
    result = await user_rollback.revert_last_ship(bg=bg, authorization="x")

    # Assertions
    assert result["ok"] is True
    assert result["loop_id"] == "l1"
    assert result["commit_sha"] == "abc1234deadbeef1234567890"
    assert result["rollback_status"] == "queued"

    # loop_sessions was updated with rollback_status=queued
    sess = await db.loop_sessions.find_one({"loop_id": "l1"})
    assert sess["rollback_status"] == "queued"
    assert sess["rollback_commit_sha"] == "abc1234deadbeef1234567890"

    # rollback_trigger collection must have ZERO inserts
    assert db.rollback_trigger.insert_calls == [], (
        "rollback_trigger should never be written to anymore")

    # BackgroundTasks queue has one task
    assert len(bg.tasks) == 1

    # Execute the background task synchronously to prove wiring
    await bg.tasks[0]()
    assert calls.get("loop_id") == "l1"
    assert calls.get("user_token") == "ghp_REAL_TOKEN"
    assert calls.get("commit_sha") == "abc1234deadbeef1234567890"


@pytest.mark.asyncio
async def test_revert_last_ship_returns_404_when_no_outcome(monkeypatch):
    """If loop_outcomes has nothing for the user, we return 404 —
    NOT a fake 'queued' status."""
    from routers import user_rollback
    from fastapi import BackgroundTasks, HTTPException

    db = _MemDB()  # empty

    async def _dev(_auth):
        return {"user_id": "u_empty", "tier": "pro", "is_admin": False}
    monkeypatch.setattr("routers.user_rollback.current_dev", _dev)
    monkeypatch.setattr("routers.user_rollback.require_db", lambda: db)

    with pytest.raises(HTTPException) as exc:
        await user_rollback.revert_last_ship(bg=BackgroundTasks(),
                                              authorization="x")
    assert exc.value.status_code == 404
    assert "no_recent_ship" in str(exc.value.detail)


# ─────────────────────────────────────────────────────────────────────
# Test: STEP 0 · Fix #2 — services.rollback_manager.execute_rollback
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_rollback_resolves_sha_to_loop_and_fires(monkeypatch):
    """execute_rollback should look up the target_sha in loop_outcomes,
    NOT write to rollback_trigger, and schedule run_rollback."""
    from services import rollback_manager
    from fastapi import BackgroundTasks

    db = _MemDB()
    db.loop_outcomes.rows.append({
        "loop_id":    "l2",
        "user_id":    "u2",
        "project_id": "p2",
        "commit_sha": "deadbeef1234567890abcdef",
        "reverted":   False,
        "shipped_at": "2026-01-20T10:00:00+00:00",
    })
    db.loop_sessions.rows.append({
        "loop_id": "l2", "user_id": "u2",
    })
    db.cto_projects.rows.append({
        "project_id":   "p2", "user_id": "u2",
        "github_owner": "acme", "github_repo": "z", "branch": "main",
        "github_token": "enc:PAT2",
    })

    # 2026-08-23 audit fix — see the identical fix + comment on
    # test_revert_last_ship_calls_real_run_rollback above: patch
    # pat_vault's own `_decrypt_pat`, the actual target `get_repo_token`
    # calls, not the separate `routers.cto_projects` wrapper.
    async def _decrypt_pat(uid, enc):
        return "ghp_TOKEN2"
    async def _user_gh_token(uid):
        return None
    monkeypatch.setattr(
        "services.pat_vault._decrypt_pat", _decrypt_pat
    )
    monkeypatch.setattr(
        "routers.cto_projects._user_gh_token", _user_gh_token
    )
    # 2026-08-23 audit fix — same stale-mock-target issue as
    # test_revert_last_ship_calls_real_run_rollback above: production
    # calls the `run_rollback_bg` wrapper, not `run_rollback` directly.
    calls = {}
    async def _fake_run(**kwargs):
        calls.update(kwargs)
    monkeypatch.setattr(
        "services.loop_rollback.run_rollback_bg", _fake_run
    )
    # No-op founder_alerts to avoid Resend calls
    async def _noop_alert(*a, **k):
        return None
    monkeypatch.setattr(
        "services.founder_alerts.send_founder_alert", _noop_alert
    )

    bg = BackgroundTasks()
    result = await rollback_manager.execute_rollback(
        db,
        target_sha="deadbeef",   # short SHA — resolver uses $regex prefix
        triggered_by="founder@aurem",
        reason="regression on prod",
        bg=bg,
    )
    assert result["ok"] is True
    assert result["loop_id"] == "l2"
    assert result["rollback_status"] == "queued"

    sess = await db.loop_sessions.find_one({"loop_id": "l2"})
    assert sess["rollback_status"] == "queued"
    assert sess["rollback_triggered_by"].startswith("admin_g12:")

    # rollback_trigger collection must have ZERO inserts anymore
    assert db.rollback_trigger.insert_calls == [], (
        "rollback_trigger should be dead — no writes anywhere")

    assert len(bg.tasks) == 1
    await bg.tasks[0]()
    assert calls.get("loop_id") == "l2"
    assert calls.get("user_token") == "ghp_TOKEN2"


@pytest.mark.asyncio
async def test_execute_rollback_returns_reason_on_unknown_sha(monkeypatch):
    """Unknown target_sha → ok:False with reason:sha_not_shipped.
    NO writes to rollback_trigger. NO fake queued status."""
    from services import rollback_manager
    from fastapi import BackgroundTasks

    db = _MemDB()   # no loop_outcomes rows

    result = await rollback_manager.execute_rollback(
        db,
        target_sha="cafebabe",
        triggered_by="founder",
        reason="",
        bg=BackgroundTasks(),
    )
    assert result["ok"] is False
    assert result["reason"] == "sha_not_shipped"
    assert db.rollback_trigger.insert_calls == []


# ─────────────────────────────────────────────────────────────────────
# Test: /rollback/candidates now returns rows (was buggy `shipped:True`)
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rollback_candidates_returns_unreverted_ships(monkeypatch):
    """Before the fix, this endpoint filtered `{shipped:True}` which
    no loop_outcomes doc ever has, so it always returned []. After
    the fix, we filter on `{reverted: {$ne: True}}` and it works."""
    from routers import user_rollback

    db = _MemDB()
    db.loop_outcomes.rows.extend([
        {"loop_id": "l_a", "user_id": "u1", "project_id": "p1",
         "commit_sha": "aaa", "shipped_at": "2026-01-10T00:00:00+00:00",
         "reverted": False, "file_paths": ["a.py"]},
        {"loop_id": "l_b", "user_id": "u1", "project_id": "p1",
         "commit_sha": "bbb", "shipped_at": "2026-01-11T00:00:00+00:00",
         "reverted": True, "file_paths": ["b.py"]},   # excluded (reverted)
        {"loop_id": "l_c", "user_id": "u1", "project_id": "p2",
         "commit_sha": "ccc", "shipped_at": "2026-01-12T00:00:00+00:00",
         "reverted": False, "file_paths": ["c.py", "d.py"]},
        {"loop_id": "l_d", "user_id": "u2",  # different user — excluded
         "project_id": "p3", "commit_sha": "ddd",
         "shipped_at": "2026-01-13T00:00:00+00:00",
         "reverted": False, "file_paths": []},
    ])

    async def _dev(_auth):
        return {"user_id": "u1", "tier": "pro", "is_admin": False}
    monkeypatch.setattr("routers.user_rollback.current_dev", _dev)
    monkeypatch.setattr("routers.user_rollback.require_db", lambda: db)

    result = await user_rollback.rollback_candidates(authorization="x")
    shas = [c["commit_sha"] for c in result["candidates"]]
    # l_a and l_c belong to u1, unreverted. l_b reverted → excluded.
    # l_d different user → excluded.
    assert set(shas) == {"aaa", "ccc"}
    assert result["count"] == 2
    # Most-recent first
    assert result["candidates"][0]["commit_sha"] == "ccc"
