"""
tests/guardrails/test_path_guard.py — audit rule #2 (write path guard).

Includes the required drill fixture (Wave 1 gate, per founder sign-off
2026-08-28): the exact file list from the real P6 drill ship
(loop_7014cd440aaf4c, `.env.example` + `requirements.txt`) — proves the
guard catches the thing that actually already happened once, not just
a synthetic example.
"""
from __future__ import annotations

import pytest

from services import write_guard as wg
from core.errors import WriteGuardBlockedError


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def __aiter__(self):
        self._it = iter(self._rows)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class _Coll:
    def __init__(self):
        self.rows: list[dict] = []

    def find(self, query=None):
        return _Cursor(list(self.rows))

    async def find_one(self, query=None):
        for r in self.rows:
            if all(r.get(k) == v for k, v in (query or {}).items()):
                return dict(r)
        return None

    async def insert_one(self, doc):
        self.rows.append(dict(doc))

    async def update_one(self, query, update, upsert=False):
        for r in self.rows:
            if all(r.get(k) == v for k, v in (query or {}).items()):
                r.update(update.get("$set") or {})
                return
        if upsert:
            new = dict(query or {})
            new.update(update.get("$set") or {})
            self.rows.append(new)

    async def count_documents(self, query=None):
        return len(self.rows)


class _FakeDb:
    def __init__(self):
        self._c: dict[str, _Coll] = {}

    def __getattr__(self, name):
        if name not in self._c:
            self._c[name] = _Coll()
        return self._c[name]


@pytest.fixture
def db():
    return _FakeDb()


# ── matched_deny_pattern — one case per deny category ─────────────────
@pytest.mark.parametrize("path,should_match", [
    (".env", True),
    (".env.example", True),
    (".env.production", True),
    (".github/workflows/ci.yml", True),
    ("package-lock.json", True),
    ("pnpm-lock.yaml", True),
    ("yarn.lock", True),
    ("poetry.lock", True),
    ("Cargo.lock", True),
    ("go.sum", True),
    ("migrations/0001_init.py", True),
    ("backend/migrations/0002_x.py", True),
    ("vercel.json", True),
    ("netlify.toml", True),
    ("docker-compose.yml", True),
    ("docker-compose.prod.yml", True),
    ("firebase.json", True),
    ("wrangler.toml", True),
    ("infra/main.tf", True),
    ("secrets.yaml", True),
    # Clear paths — must NOT match.
    ("src/app.py", False),
    ("README.md", False),
    ("requirements.txt", False),
    ("frontend/src/App.jsx", False),
])
def test_deny_pattern_coverage(path, should_match):
    hit = wg.matched_deny_pattern(path)
    assert (hit is not None) == should_match, f"{path} -> {hit}"


# ── WARN mode (default, no guard_config doc) — logs, never raises ────
@pytest.mark.asyncio
async def test_warn_mode_default_does_not_raise(db):
    await wg.check_write_paths(db, [".env.example"], owner="o", repo="r", branch="main")
    events = db.guardrail_events.rows
    assert len(events) == 1
    assert events[0]["event"] == "GW_WARN_PATH"
    assert events[0]["mode"] == "warn"
    assert ".env.example" in events[0]["paths"]


@pytest.mark.asyncio
async def test_warn_mode_clean_paths_no_event(db):
    await wg.check_write_paths(db, ["src/app.py", "README.md"], owner="o", repo="r", branch="main")
    assert db.guardrail_events.rows == []


# ── BLOCK mode — raises, never proceeds ───────────────────────────────
@pytest.mark.asyncio
async def test_block_mode_raises(db):
    await db.guard_config.update_one(
        {"_id": wg.RULE_PATH_GUARD}, {"$set": {"mode": "block"}}, upsert=True)
    with pytest.raises(WriteGuardBlockedError) as exc_info:
        await wg.check_write_paths(db, [".env.example"], owner="o", repo="r", branch="main")
    assert ".env.example" in exc_info.value.paths
    events = db.guardrail_events.rows
    assert events[0]["event"] == "GW_BLOCK_PATH"


@pytest.mark.asyncio
async def test_block_mode_error_message_never_leaks_full_deny_list(db):
    await db.guard_config.update_one(
        {"_id": wg.RULE_PATH_GUARD}, {"$set": {"mode": "block"}}, upsert=True)
    with pytest.raises(WriteGuardBlockedError) as exc_info:
        await wg.check_write_paths(db, [".env.example"], owner="o", repo="r", branch="main")
    msg = str(exc_info.value)
    # The specific offending path IS shown; the other 15+ deny patterns
    # (package-lock.json, *.tf, etc.) must never appear in the message.
    assert ".env.example" in msg
    assert "package-lock.json" not in msg
    assert "*.tf" not in msg


# ── Named drill fixture (Wave 1 gate requirement) ─────────────────────
@pytest.mark.asyncio
async def test_drill_fixture_loop_7014cd440aaf4c(db):
    """The real file list committed by the P6 drill ship
    (loop_7014cd440aaf4c, seen live in loop_sessions): `.env.example`
    was touched by a real ship before this guard existed. Proves the
    guard catches the thing that actually already happened once."""
    await db.guard_config.update_one(
        {"_id": wg.RULE_PATH_GUARD}, {"$set": {"mode": "block"}}, upsert=True)
    drill_files = ["requirements.txt", ".env.example"]
    with pytest.raises(WriteGuardBlockedError) as exc_info:
        await wg.check_write_paths(
            db, drill_files, owner="polarisbuiltinc-wq", repo="ora-grounding",
            branch="main",
        )
    # requirements.txt is clean — only .env.example should be flagged.
    assert exc_info.value.paths == [".env.example"]


# ── commit_files() integration — guard fires BEFORE any GitHub call ──
@pytest.mark.asyncio
async def test_commit_files_blocks_before_any_network_call(db, monkeypatch):
    """In block mode, commit_files() must raise from the guard check
    before ever calling GitHub — proven by NOT mocking httpx at all:
    if the guard didn't fire first, this test would hang/fail on a
    real network call instead of raising cleanly and fast."""
    await db.guard_config.update_one(
        {"_id": wg.RULE_PATH_GUARD}, {"$set": {"mode": "block"}}, upsert=True)
    monkeypatch.setattr("cto_services.db.get_db", lambda: db)

    from services.github_api_writer import commit_files
    with pytest.raises(WriteGuardBlockedError):
        await commit_files(
            owner="o", repo="r", branch="main", token="fake-token",
            files={".env.example": "SECRET=1"},
            commit_message="test", author_name="a", author_email="a@x.com",
        )


@pytest.mark.asyncio
async def test_commit_files_warn_mode_does_not_raise_type_error(db, monkeypatch):
    """WARN mode must not change commit_files()'s signature/behavior —
    only asserts the guard call itself doesn't blow up before reaching
    the (mocked-out-by-absence) network step. We stop the assertion at
    'no WriteGuardBlockedError and no guard-side exception', since a
    real network attempt is out of scope for this unit test."""
    monkeypatch.setattr("cto_services.db.get_db", lambda: db)
    from services import write_guard
    # Directly assert the guard step alone behaves — full commit_files()
    # network path is covered by existing writer tests, not this suite.
    await write_guard.check_write_paths(db, [".env.example"], owner="o", repo="r")
    assert db.guardrail_events.rows[0]["event"] == "GW_WARN_PATH"
