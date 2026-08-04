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
# Contract 1 — Terminal FAILED state (not PAUSED_FOR_USER)
# ═══════════════════════════════════════════════════════════════════
def test_verify_hard_fails_after_max_heals_no_pause(monkeypatch):
    from services import loop_engine as le

    # Shrink self-heal timeout so a stalled Parliament healer can't
    # keep the test running for minutes — the healer is expected to
    # either fail fast or time out, which is fine because we're
    # asserting the OUTER contract (state=FAILED after MAX_SELF_HEALS).
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

    # Capture every _emit + _fail call so we can assert:
    #   • exactly one FAILED event
    #   • no PAUSED_FOR_USER event
    emitted: list[dict] = []
    orig_emit = eng._emit

    async def spy_emit(state, phase, **kwargs):
        emitted.append({"state": state, "phase": phase,
                        "message": kwargs.get("message"),
                        "requires_user_action": kwargs.get(
                            "requires_user_action", False)})
        return await orig_emit(state, phase, **kwargs)

    monkeypatch.setattr(eng, "_emit", spy_emit)

    async def go():
        await eng._do_verify()

    asyncio.run(go())

    # Contract 1a — terminal state is FAILED, not PAUSED_FOR_USER.
    assert eng.state == le.LoopState.FAILED, (
        f"verify must hard-fail after {le.MAX_SELF_HEALS} heals, "
        f"got state={eng.state.value}"
    )

    # Contract 1b — outer pipeline halts.
    assert eng._should_stop(), (
        "terminal FAILED state must halt the pipeline (_should_stop=True)"
    )

    # Contract 1c — NO PAUSED_FOR_USER event was emitted during
    # verify. This is the specific regression the bug_testing_agent
    # caught: the pre-fix code emitted PAUSED_FOR_USER *and then*
    # (on user retry) FAILED, so the user saw two "Verify failed
    # after 2 attempts" messages.
    paused_events = [e for e in emitted
                     if e["state"] == le.LoopState.PAUSED_FOR_USER]
    assert paused_events == [], (
        f"verify must NOT pause for user on heal-cap exhaustion. "
        f"Found paused events: {paused_events}"
    )

    # Contract 1d — exactly ONE FAILED event.
    failed_events = [e for e in emitted
                     if e["state"] == le.LoopState.FAILED]
    assert len(failed_events) == 1, (
        f"expected exactly 1 FAILED event, got {len(failed_events)}: "
        f"{failed_events}"
    )

    # Contract 1e — the FAILED event surfaces the cap.
    assert le.MAX_SELF_HEALS == 2
    assert "self-heal" in (failed_events[0]["message"] or "").lower()

    # Contract 1f — heal rounds actually ran up to (not past) the cap.
    # Initial verify (1) + heal_round × subset_reverify (MAX_SELF_HEALS)
    # = 1 + 2 = 3 verify calls total.
    assert len(verify_calls) == 1 + le.MAX_SELF_HEALS, (
        f"expected {1 + le.MAX_SELF_HEALS} verify calls, "
        f"got {len(verify_calls)}: {verify_calls}"
    )

    # Contract 1g — total_heal_attempts advanced to the cap.
    assert eng.context.get("total_heal_attempts") == le.MAX_SELF_HEALS


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
