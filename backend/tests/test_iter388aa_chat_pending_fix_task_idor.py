"""test_iter388aa_chat_pending_fix_task_idor.py — Iter 388-aa (2026-02-14).

Regression net for the Tier-1 IDOR audit finding on `chat.py:1636+`.

Background
----------
The Mode-D redirect path used to clear `pending_fix_task` on a session
row without asserting caller ownership in the Mongo filter:

    _sess = await _db.chat_sessions.find_one(
        {"session_id": body.session_id},              # NO user_id
        {"_id": 0, "pending_fix_task": 1, "user_id": 1},
    )
    if _pending and (not _sess.get("user_id") or _sess.get("user_id") == user_id):
        # legacy row → allowed even without ownership

Two failure modes were possible:
- P3: attacker who knows another user's session_id could $unset that
  user's `pending_fix_task` flag (deprecated field, cosmetic damage).
- Rule 4 anti-pattern: ownership enforced in a code branch, not the query.

This test asserts (via source inspection — the actual code path is
inside a live SSE loop that is not unit-testable in isolation) that:

1. The `find_one` for `pending_fix_task` filters by both `session_id`
   AND `user_id`.
2. The `update_one` that `$unset`s the flag filters by both keys too.
3. The legacy "row has no user_id → allow" branch is removed.
"""
from __future__ import annotations

import re
from pathlib import Path


# 2026-09-08 StreamState refactor — the Mode-D pending_fix_task guard
# moved from stream.py's inline _worker() into worker.py's
# `_mode_d_fast_path()` (mechanical move, same source lines).
CHAT_ROUTER = Path(__file__).resolve().parent.parent / "routers" / "chat" / "worker.py"


def _extract_pending_fix_block() -> str:
    src = CHAT_ROUTER.read_text()
    # Anchor: from `is_fix_confirmation` guard down to the next
    # function boundary (`_mode_d_fast_path` closing → `async def
    # _mode_broadcast`'s "Decide A/B/C/D" docstring line).
    m = re.search(
        r"if body\.session_id and is_fix_confirmation.*?(?=\n\s*(?:#\s*)?[\"']*\s*Decide A/B/C/D)",
        src, re.DOTALL,
    )
    assert m, "Could not locate Mode-D pending_fix_task guard block"
    return m.group(0)


def test_find_one_filters_by_session_and_user_id():
    block = _extract_pending_fix_block()
    # First find_one must filter on both keys.
    find_one_match = re.search(
        r"chat_sessions\.find_one\(\s*\{[^}]*\}", block,
    )
    assert find_one_match, "chat_sessions.find_one call not found in block"
    filter_str = find_one_match.group(0)
    assert '"session_id":' in filter_str, filter_str
    assert '"user_id":'    in filter_str, filter_str


def test_update_one_filters_by_session_and_user_id():
    block = _extract_pending_fix_block()
    update_match = re.search(
        r"chat_sessions\.update_one\(\s*\{[^}]*\}", block,
    )
    assert update_match, "chat_sessions.update_one call not found in block"
    filter_str = update_match.group(0)
    assert '"session_id":' in filter_str, filter_str
    assert '"user_id":'    in filter_str, filter_str


def test_no_legacy_no_user_id_allowance():
    block = _extract_pending_fix_block()
    # The old code checked `not _sess.get("user_id")` as an OR fallback.
    # That branch is what let a legacy row be mutated by any caller.
    assert 'not _sess.get("user_id")' not in block, (
        "Legacy 'row has no user_id → allow' branch is still present. "
        "Iter 388-aa removed it — this test flags any reintroduction."
    )


def test_projection_no_longer_leaks_user_id():
    """Cosmetic — after ownership moved into the filter, the projection
    no longer needs to fetch `user_id` back to the caller."""
    block = _extract_pending_fix_block()
    # Look at the projection dict of the find_one call.
    proj_match = re.search(
        r"chat_sessions\.find_one\(\s*\{[^}]*\},\s*(\{[^}]*\})", block,
    )
    assert proj_match, "Could not extract projection dict"
    proj = proj_match.group(1)
    assert '"pending_fix_task":' in proj
    # user_id in projection is redundant now — the query already
    # guarantees ownership. Not a security issue, just noise. Assert it
    # is NOT present so drift is caught.
    assert '"user_id":' not in proj, (
        "After Iter 388-aa the ownership check moved to the query filter, "
        "so `user_id` in the projection is redundant. Remove it."
    )
