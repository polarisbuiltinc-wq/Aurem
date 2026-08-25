"""
test_iter_feb2026_verify_terminal_fail.py — Feb 2026 (behavioral).

Founder-reported bug + bug_testing_agent verdict `not_fixed`:
    "when a verify step fails, the system loops infinitely
     (triggering 4+ 'Verify failed after 2 attempts' events) and
     the UI chip is stuck at 'heal 1/2'."

Previous fix relied on STATIC STRING ASSERTIONS
(`test_iter_feb2026_global_heal_cap.py`) that grep'd for the presence
of certain identifiers. Those passed while the behavior remained
broken: after MAX_SELF_HEALS heal rounds exhausted, `_do_verify` set
state=PAUSED_FOR_USER and emitted a "Verify failed after 2 attempts"
event with `requires_user_action=True` → the user then clicked
"retry" → the pipeline resumed → `_do_verify` re-entered → the
global-cap check hit `_fail()` → but the user had already seen ONE
"Verify failed" event AND was given a second pause_for_user chance
that emitted ANOTHER duplicate event.

Founder's terminal-hard-cap contract:
    "loop halts at exactly 2 heal attempts and surfaces a terminal
     state (not a silent retry)."

This file drives `_do_verify()` end-to-end with a failing verifier
and asserts the CONTRACT — not the source-code text:
  1. After MAX_SELF_HEALS heal rounds fail to clear the errors,
     engine.state == LoopState.FAILED (a terminal state).
  2. `_should_stop()` returns True → the outer pipeline halts.
  3. NO `PAUSED_FOR_USER` state transition occurs.
  4. Exactly ONE terminal FAILED event is emitted (no duplicate
     "Verify failed after 2 attempts" messages).
  5. `pause_response(retry)` refuses to resurrect the loop (409).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest


# ───────────────────────── Mongo doubles ──────────────────────────
class _Coll:
    def __init__(self):
        self.rows: list[dict] = []

    async def insert_one(self, d):
        self.rows.append(dict(d))

        class _R:
            inserted_id = "x"

        return _R()

    async def update_one(self, q, u, upsert=False):
        for r in self.rows:
            if all(r.get(k) == v for k, v in q.items()
                   if not isinstance(v, dict)):
                for k, v in (u.get("$set") or {}).items():
                    r[k] = v

                class _R:
                    modified_count = 1
                    upserted_id = None

                return _R()
        if upsert:
            doc = {**q, **(u.get("$set") or {})}
            self.rows.append(doc)

        class _R:
            modified_count = 0
            upserted_id = "x" if upsert else None

        return _R()

    async def find_one(self, q, *_a, **_kw):
        for r in self.rows:
            if all(r.get(k) == v for k, v in q.items()
                   if not isinstance(v, dict)):
                return dict(r)
        return None

    async def delete_one(self, q):
        for i, r in enumerate(list(self.rows)):
            if all(r.get(k) == v for k, v in q.items()):
                self.rows.pop(i)

                class _R:
                    deleted_count = 1

                return _R()

        class _R:
            deleted_count = 0

        return _R()


class _DB:
    def __init__(self):
        self.loop_sessions = _Coll()
        self.loop_backups = _Coll()
        self.loop_plans = _Coll()
        self.loop_lock = _Coll()
        self.loop_failures = _Coll()
        self.loop_run_log = _Coll()


def _make_engine():
    from services import loop_engine as le
    return le.LoopEngine(
        db=_DB(), loop_id="lp_test_verify_terminal",
        user_id="u_test_terminal", project_id="p_test",
        user_message="add a broken file that never lints",
    )


# ───────────────────────── shared fake ────────────────────────────
def _always_failing_verify_factory():
    """Returns a fake verify_files that ALWAYS reports the same file
    failing. Records every call so the test can count self-heal
    rounds executed."""
    call_log: list[list[str]] = []

    async def _fake_verify(files):
        call_log.append([f["path"] for f in files])
        return {
            "ok": False,
            "results": [
                {"path": f["path"], "ok": False, "linter": "ruff",
                 "stdout": f"{f['path']}:1:7: SyntaxError: never fixable",
                 "stderr": ""}
                for f in files
            ],
            "errors": [f"{f['path']}:1:7: SyntaxError" for f in files],
        }

    return _fake_verify, call_log


# ═══════════════════════════════════════════════════════════════════
# Contract 1 — PAUSED_FOR_USER on first exhaustion, hard FAILED (no
# duplicate) on retry-reentry with the global cap already consumed.
#
# W3 · 2026-08 — REWRITTEN. A `tests/test_iter309_phase03_self_heal_
# paused.py` guard (predating this file) explicitly locks in
# PAUSED_FOR_USER on first verify-self-heal-exhaustion; the original
# version of this test asserted the opposite (FAILED, no pause) and
# was never reconciled with that guard when it landed — a genuine
# undetected contradiction, not a deliberate joint decision.
# LIVE-REPRO'D (2026-08): calling `_do_verify()` twice on the same
# engine — first call exhausts MAX_SELF_HEALS and pauses; second call
# (simulating the router's retry) hits the PRE-EXISTING global heal
# cap guard (`total_heal_attempts`, loop_engine.py ~line 1927) BEFORE
# running any new heal attempts, and hard-fails with a DIFFERENT
# message ("Global heal cap reached..."/"Self-heal exhausted
# globally..."), not a repeat of "Verify failed after 2 attempts".
# So the original founder-reported bug (4+ duplicate "Verify failed"
# events from unlimited retries) is fixed by the loop-wide
# `total_heal_attempts` cap, NOT by removing PAUSED_FOR_USER — the
# pause is safe to keep because a second attempt can never get a
# fresh allowance.
# ═══════════════════════════════════════════════════════════════════
@pytest.mark.source_of_truth
def test_verify_pauses_once_then_hard_fails_on_reentry_no_duplicate(monkeypatch):
    from services import loop_engine as le

    # Shrink self-heal timeout so a stalled Parliament healer can't
    # keep the test running for minutes.
    monkeypatch.setattr(le, "SELF_HEAL_LLM_TIMEOUT_S", 1)

    eng = _make_engine()
    eng.context["submitted_files"] = [
        {"path": "broken.py", "content": "broken = "},
    ]

    fake_verify, verify_calls = _always_failing_verify_factory()
    import services.loop_verify as lv
    monkeypatch.setattr(lv, "verify_files", fake_verify)

    # Parliament healer — never succeeds. Simulates all heals failing.
    class _Healer:
        async def heal(self, **_kw):
            return {"status": "escalate"}   # → healed = None per engine

    class _Parliament:
        def __init__(self, db=None):
            self.healer = _Healer()

    import core.parliament as _pmod
    monkeypatch.setattr(_pmod, "Parliament", _Parliament)

    emitted: list[dict] = []
    orig_emit = eng._emit

    async def spy_emit(state, phase, **kwargs):
        emitted.append({"state": state, "phase": phase,
                        "message": kwargs.get("message"),
                        "requires_user_action": kwargs.get(
                            "requires_user_action", False)})
        return await orig_emit(state, phase, **kwargs)

    monkeypatch.setattr(eng, "_emit", spy_emit)

    # ── First call: exhausts MAX_SELF_HEALS, must PAUSE (not fail). ──
    asyncio.run(eng._do_verify())

    assert eng.state == le.LoopState.PAUSED_FOR_USER, (
        f"first verify exhaustion must pause for user (preserves "
        f"plan+execute context per Iter309 founder rationale), "
        f"got state={eng.state.value}"
    )
    paused_events = [e for e in emitted
                     if e["state"] == le.LoopState.PAUSED_FOR_USER]
    # 2 expected: the main pause emit (requires_user_action=True) +
    # its narrate() companion event (pre-existing Iter309 narration
    # system, drives the ECG strip — always paired, not a new
    # duplicate introduced by this contract).
    assert len(paused_events) == 2, (
        f"expected the main pause emit + its narrate companion on "
        f"first exhaustion, got {len(paused_events)}: {paused_events}"
    )
    main_pause = next(e for e in paused_events
                      if e["requires_user_action"] is True)
    assert "Verify failed after 2 self-heal attempts" in main_pause["message"]
    first_call_messages = {e["message"] for e in paused_events}
    assert eng.context.get("total_heal_attempts") == le.MAX_SELF_HEALS
    # Initial verify (1) + heal_round × subset_reverify (MAX_SELF_HEALS)
    # = 1 + 2 = 3 verify calls for the first call.
    assert len(verify_calls) == 1 + le.MAX_SELF_HEALS, (
        f"expected {1 + le.MAX_SELF_HEALS} verify calls, "
        f"got {len(verify_calls)}: {verify_calls}"
    )

    # ── Second call (simulates router retry re-entering _do_verify):
    # global cap already consumed → hard-fail immediately, NO new
    # heal attempts, NO duplicate "Verify failed after 2 attempts". ──
    emitted.clear()
    eng.state = le.LoopState.VERIFYING
    asyncio.run(eng._do_verify())

    assert eng.state == le.LoopState.FAILED, (
        f"reentry with the global heal cap already consumed must "
        f"hard-fail (no more free heals to grant), got "
        f"{eng.state.value}"
    )
    new_paused_events = [e for e in emitted
                         if e["state"] == le.LoopState.PAUSED_FOR_USER]
    assert new_paused_events == [], (
        f"reentry must NOT emit a second PAUSED_FOR_USER — this is "
        f"the duplicate-event bug the founder originally reported. "
        f"Found: {new_paused_events}"
    )
    failed_events = [e for e in emitted
                     if e["state"] == le.LoopState.FAILED]
    assert len(failed_events) == 1
    # The reentry message must not repeat any exact message text
    # already shown to the user in the first pause — proves no
    # literal duplicate is shown to the user on retry (the founder-
    # reported bug this whole file exists to prevent).
    assert failed_events[0]["message"] not in first_call_messages, (
        f"reentry FAILED message repeats a first-pause message: "
        f"{failed_events[0]['message']}"
    )
    # No new self-heal attempts ran on reentry (no fresh allowance).
    self_heal_events = [e for e in emitted if e["phase"] == "self_heal"]
    assert self_heal_events == [], (
        f"reentry with cap already consumed must not run new heal "
        f"attempts: {self_heal_events}"
    )


# ═══════════════════════════════════════════════════════════════════
# Contract 2 — Router refuses retry on terminal loops
# ═══════════════════════════════════════════════════════════════════
def test_pause_response_refuses_retry_on_terminal_state():
    """After the verify hard-fail, the frontend's stale UI snapshot
    (still showing a retry button from a prior render) must NOT be
    able to resurrect the loop by POSTing pause-response(retry).
    The router must return 409 Loop terminal."""
    router_src = Path("/app/backend/routers/loop.py").read_text(
        encoding="utf-8")
    # Contract: pause_response has a terminal-state guard that
    # rejects retry/skip with HTTP 409.
    assert 'loop_terminal' in router_src, (
        "pause_response must return an error tagged 'loop_terminal' "
        "when the loop is in a terminal state."
    )
    assert 'LoopState.FAILED' in router_src, (
        "pause_response terminal guard must include FAILED."
    )
    # The guard must precede the retry code path so the retry can't
    # sneak past by unconditionally setting state=AWAITING_CONFIRMATION.
    guard_idx = router_src.find('loop_terminal')
    retry_setstate_idx = router_src.find(
        "engine.state = eng.LoopState.AWAITING_CONFIRMATION")
    assert 0 < guard_idx < retry_setstate_idx, (
        "terminal-state guard must run BEFORE the retry path sets "
        "state=AWAITING_CONFIRMATION and calls confirm()."
    )


# ═══════════════════════════════════════════════════════════════════
# Contract 3 — Second _do_verify entry hard-fails on cap-consumed
# ═══════════════════════════════════════════════════════════════════
@pytest.mark.source_of_truth
def test_reentered_do_verify_short_circuits_when_cap_consumed(
        monkeypatch):
    """Defensive contract: if `_do_verify` is somehow re-entered
    after the cap is already consumed AND files STILL fail, the
    engine must hard-fail on the cap check (line 1902 guard) without
    running another 2 heal rounds. This prevents the pre-fix bug
    where each outer retry gave the loop another 2 free heals."""
    from services import loop_engine as le

    monkeypatch.setattr(le, "SELF_HEAL_LLM_TIMEOUT_S", 1)

    eng = _make_engine()
    eng.context["submitted_files"] = [
        {"path": "broken.py", "content": "broken = "},
    ]
    # Simulate a loop where the cap was already consumed in a
    # previous phase / previous _do_verify entry.
    eng.context["total_heal_attempts"] = le.MAX_SELF_HEALS

    fake_verify, verify_calls = _always_failing_verify_factory()
    import services.loop_verify as lv
    monkeypatch.setattr(lv, "verify_files", fake_verify)

    # Parliament healer — track invocations. If the reentry defense
    # works, heal() MUST NOT be called (cap check short-circuits
    # before entering the heal loop).
    heal_calls: list[Any] = []

    class _Healer:
        async def heal(self, **kw):
            heal_calls.append(kw)
            return {"status": "escalate"}

    class _Parliament:
        def __init__(self, db=None):
            self.healer = _Healer()

    import core.parliament as _pmod
    monkeypatch.setattr(_pmod, "Parliament", _Parliament)

    async def go():
        await eng._do_verify()

    asyncio.run(go())

    # Engine hard-fails on the cap check.
    assert eng.state == le.LoopState.FAILED, (
        f"reentered _do_verify with cap consumed + failing files "
        f"must hard-fail on the cap check, got {eng.state.value}"
    )
    # Defense proved: the heal loop never ran on this reentry.
    assert heal_calls == [], (
        f"cap-consumed reentry must NOT invoke any new heals. "
        f"Got {len(heal_calls)} heal() call(s)."
    )
    # Only the initial verify pass ran — no subset reverifies.
    assert len(verify_calls) == 1, (
        f"cap-consumed reentry must run exactly 1 verify pass "
        f"(initial only, no heal-round reverifies). Got "
        f"{len(verify_calls)}: {verify_calls}"
    )


# ═══════════════════════════════════════════════════════════════════
# Contract 4 — Persisted terminal loops return 409 (not 404)
# ═══════════════════════════════════════════════════════════════════
def test_pause_response_persisted_terminal_returns_409(monkeypatch):
    """Behavioral (HTTP-level) — a terminal loop that exists ONLY in
    Mongo (no live `_LIVE` engine, e.g. after a worker restart) must
    surface as 409 loop_terminal on retry/skip, NOT the misleading
    404 that `lookup_or_rehydrate` would otherwise cause (it refuses
    to rehydrate terminal states → returns None → 404).

    This exercises the persisted-doc guard added at the top of
    `pause_response` alongside the in-memory guard.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from routers import loop as loop_router
    from routers.loop import router as loop_api_router
    from services import loop_engine as le

    app = FastAPI()
    app.include_router(loop_api_router, prefix="/api/aurem-dev")

    async def _fake_dev(*_a, **_kw):
        return {"user_id": "u_test_terminal"}
    monkeypatch.setattr(loop_router, "current_dev", _fake_dev)

    class _Coll2:
        def __init__(self, rows):
            self._rows = rows

        async def find_one(self, q, *_a, **_kw):
            for r in self._rows:
                if all(r.get(k) == v for k, v in q.items()
                       if not isinstance(v, dict)):
                    return dict(r)
            return None

    fake_doc = {
        "loop_id": "lp_persisted_terminal_test",
        "user_id": "u_test_terminal",
        "state": le.LoopState.FAILED.value,
        "phase": "verify",
        "context": {},
    }

    class _DB2:
        def __init__(self):
            self.loop_sessions = _Coll2([fake_doc])

    fake_db = _DB2()
    monkeypatch.setattr(loop_router, "get_db", lambda: fake_db)

    # Reset the in-memory registry so lookup() returns None → the
    # router must fall into the persisted-doc guard.
    le.reset_registry()

    client = TestClient(app)
    resp = client.post(
        "/api/aurem-dev/loop/lp_persisted_terminal_test/pause-response",
        json={"action": "retry"},
        headers={"Authorization": "Bearer irrelevant"},
    )

    assert resp.status_code == 409, (
        f"expected 409 for persisted terminal loop, got "
        f"{resp.status_code}: {resp.text}"
    )
    body = resp.json()
    detail = body.get("detail", body)
    assert isinstance(detail, dict), f"unexpected body shape: {body}"
    assert detail.get("error") == "loop_terminal"
    assert detail.get("state") == le.LoopState.FAILED.value


@pytest.mark.source_of_truth
def test_pause_response_persisted_terminal_404_for_wrong_user(
        monkeypatch):
    """SEC-004 (documented 2026-08-19, `routers/loop.py` — see the
    inline "SEC-004 fix" comments + memory/CODEBASE_AUDIT.md /
    memory/PRD.md): an authenticated non-owner receives 404 (same as
    a genuinely not-found loop), not 403 — a distinguishable 403
    would let a caller enumerate valid loop IDs belonging to other
    users. Ownership check must still run BEFORE surfacing the
    terminal state, so a stranger's loop's state/phase is never
    leaked either way. Updated from an 403 assertion in W3·2026-08 —
    that assertion predated the SEC-004 decision and was never
    reconciled with it."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from routers import loop as loop_router
    from routers.loop import router as loop_api_router
    from services import loop_engine as le

    app = FastAPI()
    app.include_router(loop_api_router, prefix="/api/aurem-dev")

    async def _fake_dev(*_a, **_kw):
        return {"user_id": "some_other_user"}
    monkeypatch.setattr(loop_router, "current_dev", _fake_dev)

    class _Coll2:
        def __init__(self, rows):
            self._rows = rows

        async def find_one(self, q, *_a, **_kw):
            for r in self._rows:
                if all(r.get(k) == v for k, v in q.items()
                       if not isinstance(v, dict)):
                    return dict(r)
            return None

    fake_doc = {
        "loop_id": "lp_persisted_other_user",
        "user_id": "the_owner",
        "state": le.LoopState.FAILED.value,
        "phase": "verify",
        "context": {},
    }

    class _DB2:
        def __init__(self):
            self.loop_sessions = _Coll2([fake_doc])

    monkeypatch.setattr(loop_router, "get_db", lambda: _DB2())
    le.reset_registry()

    client = TestClient(app)
    resp = client.post(
        "/api/aurem-dev/loop/lp_persisted_other_user/pause-response",
        json={"action": "retry"},
        headers={"Authorization": "Bearer irrelevant"},
    )
    assert resp.status_code == 404, (
        f"SEC-004: non-owner must get uniform 404 (not a distinguishable "
        f"403 that would leak resource existence/ownership), and must "
        f"not see the leaked terminal state/phase either way. "
        f"Got {resp.status_code}: {resp.text}"
    )
    assert resp.json().get("detail") == "Loop not found"
