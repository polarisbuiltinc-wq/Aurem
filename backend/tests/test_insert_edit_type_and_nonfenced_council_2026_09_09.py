"""
tests/test_insert_edit_type_and_nonfenced_council_2026_09_09.py

Founder-repro regression (fresh account, ReRootsBeauty/ReRoots-,
reproduced twice independently): Council mode proposed adding a
clickable `tel:` link at a specific line ("add X after Y" — an
ADDITION, not a replacement) with no ```aurem-handoff fence (Council
mode emits "pure Markdown, no fenced code blocks" by design). Saying
"go" afterwards hit the dead NO_PENDING_ACTIONABLE_MESSAGE because:
  1. `extract_deterministic_edit()` only recognized "from X to Y"
     replacements — an addition could never match.
  2. `propose_from_turn()` only even attempted extraction when a
     literal ```aurem-handoff fence was present in the reply — which
     Council-mode replies never contain.

Root-caused via direct code read (services/actions/pending_action.py),
not a guess. Fix: added a symmetric `type_="insert"` edit class, and
removed the fence-only gate so extraction runs on every final reply
(CBR-1 is preserved — both extractors still require the extracted
value to be uniquely present in the REAL live file before anything
reaches AWAITING_CONFIRM).
"""
from __future__ import annotations

import os
import uuid

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from cto_services.db import set_db
from services.actions.pending_action import (
    STATUS_AWAITING_CONFIRM,
    PROVIDER_EXECUTOR, PROVIDER_NO_PENDING,
    NO_PENDING_ACTIONABLE_MESSAGE,
    propose_action, propose_from_turn, resolve_confirm,
    get_active_actions, extract_deterministic_insert,
)

MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME", "aurem_dev")
BIN_CTX = {"token": "fake-token"}


def _ensure_db():
    if not MONGO_URL:
        return None
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    set_db(db)
    return db


def _sid() -> str:
    return f"insert-test-{uuid.uuid4()}"


async def _cleanup(db, *session_ids):
    if db is None:
        return
    await db.pending_actions.delete_many({"session_id": {"$in": list(session_ids)}})


# ── extractor unit tests ─────────────────────────────────────────────
def test_extract_deterministic_insert_content_then_anchor_phrasing():
    text = (
        'I\'ll add `<a href="tel:+15551234567">Call us</a>` right after '
        '`<p class="hours-badge">Mon-Fri 9-5</p>` in `index.html`.'
    )
    result = extract_deterministic_insert(text)
    assert result == {
        "path": "index.html",
        "anchor": '<p class="hours-badge">Mon-Fri 9-5</p>',
        "content": '<a href="tel:+15551234567">Call us</a>',
    }


def test_extract_deterministic_insert_anchor_then_content_phrasing():
    text = (
        'Right after `<p class="hours-badge">Mon-Fri 9-5</p>`, I\'ll add '
        '`<a href="tel:+15551234567">Call us</a>` in `index.html`.'
    )
    result = extract_deterministic_insert(text)
    assert result == {
        "path": "index.html",
        "anchor": '<p class="hours-badge">Mon-Fri 9-5</p>',
        "content": '<a href="tel:+15551234567">Call us</a>',
    }


def test_extract_deterministic_insert_returns_none_when_unclear():
    assert extract_deterministic_insert("I'll add a phone link somewhere soon.") is None


def test_extract_deterministic_insert_returns_none_without_file_path():
    assert extract_deterministic_insert(
        'I\'ll add `<a href="tel:1">Call</a>` right after `<p>hi</p>`.'
    ) is None


# ── t_council_addition_becomes_actionable_end_to_end (the exact repro) ─
@pytest.mark.asyncio
async def test_t_council_addition_without_fence_becomes_actionable(monkeypatch):
    db = _ensure_db()
    if db is None:
        pytest.skip("no live Mongo connection in this environment")
    session_id = _sid()
    original_file = '<p class="hours-badge">Mon-Fri 9-5</p>'
    written = {}

    async def _fake_read(ctx, args):
        return {"ok": True, "content": original_file}

    async def _fake_write(ctx, args):
        written["content"] = args["content"]
        return {"ok": True, "sha": "abc123", "html_url": "https://github.com/x/y/commit/abc123"}

    monkeypatch.setattr("services.local_tools.read_repo_file", _fake_read)
    monkeypatch.setattr("services.local_tools.write_repo_file", _fake_write)

    try:
        # Council mode's exact shape: no ```aurem-handoff fence anywhere,
        # provider is NOT "edit-tier-upgrade-offer" — this used to fall
        # straight through to "no action created", ever.
        await propose_from_turn(
            db, session_id=session_id, user_id="u1", project_id="p1",
            provider="aurem-council",
            assistant_reply=(
                'Sure — I\'ll add `<a href="tel:+15551234567">Call us</a>` right after '
                '`<p class="hours-badge">Mon-Fri 9-5</p>` in `index.html`.'
            ),
            bin_ctx=BIN_CTX,
        )
        active = await get_active_actions(db, session_id=session_id, user_id="u1")
        assert len(active) == 1, "a clean addition proposal must become actionable, fence or not"
        assert active[0]["type"] == "insert"

        result = await resolve_confirm(
            db, session_id=session_id, user_id="u1", project_id="p1",
            prompt="go", user={"user_id": "u1"}, bin_ctx=BIN_CTX,
        )
        assert result["provider"] == PROVIDER_EXECUTOR
        assert result["content"] != NO_PENDING_ACTIONABLE_MESSAGE
        assert 'tel:+15551234567' in written["content"]
        assert '<p class="hours-badge">Mon-Fri 9-5</p>' in written["content"]
    finally:
        await _cleanup(db, session_id)


@pytest.mark.asyncio
async def test_t_replace_edit_without_fence_now_also_actionable(monkeypatch):
    """The fence-gate removal helps plain replace-type prose too — a
    non-Council mode that never happens to emit the fence for a simple
    single-file text swap should now also become actionable."""
    db = _ensure_db()
    if db is None:
        pytest.skip("no live Mongo connection in this environment")
    session_id = _sid()

    async def _fake_read(ctx, args):
        return {"ok": True, "content": "9am-5pm"}

    monkeypatch.setattr("services.local_tools.read_repo_file", _fake_read)

    try:
        await propose_from_turn(
            db, session_id=session_id, user_id="u1", project_id="p1",
            provider="aurem-agentic",
            assistant_reply="I'll change the hours from '9am-5pm' to '9am-6pm' in `src/Hours.jsx`.",
            bin_ctx=BIN_CTX,
        )
        active = await get_active_actions(db, session_id=session_id, user_id="u1")
        assert len(active) == 1
        assert active[0]["status"] == STATUS_AWAITING_CONFIRM
        assert active[0]["type"] == "edit"
    finally:
        await _cleanup(db, session_id)


@pytest.mark.asyncio
async def test_t_vague_addition_prose_still_not_actionable():
    """CBR-1 preserved: prose that merely gestures at an addition
    without a clean, unique anchor+content+path never becomes
    actionable — same contract as the existing replace-type guard."""
    db = _ensure_db()
    if db is None:
        pytest.skip("no live Mongo connection in this environment")
    session_id = _sid()
    try:
        await propose_from_turn(
            db, session_id=session_id, user_id="u1", project_id="p1",
            provider="aurem-council",
            assistant_reply="I'll add a phone link to the page somewhere soon.",
            bin_ctx=BIN_CTX,
        )
        active = await get_active_actions(db, session_id=session_id, user_id="u1")
        assert active == [], "vague addition prose must never become actionable"
    finally:
        await _cleanup(db, session_id)


@pytest.mark.asyncio
async def test_t_confirm_no_pending_still_honest_after_fence_gate_removal():
    """Sanity: the CBR-4 honest dead-end message still fires correctly
    (unchanged) when there's genuinely nothing pending."""
    db = _ensure_db()
    if db is None:
        pytest.skip("no live Mongo connection in this environment")
    session_id = _sid()
    try:
        result = await resolve_confirm(
            db, session_id=session_id, user_id="u1", project_id="p1",
            prompt="go", user={"user_id": "u1"}, bin_ctx=None,
        )
        assert result["provider"] == PROVIDER_NO_PENDING
        assert result["content"] == NO_PENDING_ACTIONABLE_MESSAGE
    finally:
        await _cleanup(db, session_id)


@pytest.mark.asyncio
async def test_t_unrelated_new_turn_cancels_stale_pending_insert(monkeypatch):
    """A follow-up turn that isn't itself concretizable (e.g. a big
    multi-file aurem-handoff brief) now also cancels a stale pending
    action left over from an earlier turn, instead of leaving it
    dangling indefinitely — avoids a user later confirming into a
    completely different, forgotten proposal."""
    db = _ensure_db()
    if db is None:
        pytest.skip("no live Mongo connection in this environment")
    session_id = _sid()
    try:
        await propose_action(
            db, session_id=session_id, user_id="u1", project_id="p1",
            type_="insert",
            raw_payload={"path": "index.html", "anchor": "<p>hi</p>", "content": "<a>x</a>"},
            ctx=None,
        )
        # propose_action alone (no repo ctx) fails validation and is
        # immediately CANCELLED — use get_active_actions to prove it's
        # truly empty before the next assertion is meaningful.
        active_before = await get_active_actions(db, session_id=session_id, user_id="u1")
        assert active_before == []

        # Now prove the cancellation branch itself, with a REAL active
        # action this time.
        async def _fake_read(ctx, args):
            return {"ok": True, "content": "<p>hi</p>"}
        monkeypatch.setattr("services.local_tools.read_repo_file", _fake_read)

        await propose_from_turn(
            db, session_id=session_id, user_id="u1", project_id="p1",
            provider="aurem-council",
            assistant_reply='I\'ll add `<a>x</a>` right after `<p>hi</p>` in `index.html`.',
            bin_ctx=BIN_CTX,
        )
        active = await get_active_actions(db, session_id=session_id, user_id="u1")
        assert len(active) == 1

        await propose_from_turn(
            db, session_id=session_id, user_id="u1", project_id="p1",
            provider="aurem-agentic",
            assistant_reply="```aurem-handoff\nA big multi-file rewrite, no clean triple here.\n```",
            bin_ctx=BIN_CTX,
        )
        remaining = await get_active_actions(db, session_id=session_id, user_id="u1")
        assert remaining == [], "stale pending action must be cancelled by a new unrelated turn"
    finally:
        await _cleanup(db, session_id)
