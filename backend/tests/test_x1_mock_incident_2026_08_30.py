"""
test_x1_mock_incident_2026_08_30.py — Overnight Master Loop 2, X1.

Named tests (per the X1 spec):
  t_mock_isolation_preview_prod  — reinterpreted honestly (see docstring
                                    below): proves MOCK_LLM is read ONCE
                                    at process import and is immutable
                                    for the rest of that process's
                                    lifetime — a bare env mutation
                                    without a real restart can NEVER
                                    flip live behaviour mid-process.
                                    True Preview-vs-Production process
                                    isolation is a deployment-topology
                                    fact (this pod runs exactly one
                                    backend process for all traffic),
                                    not something a code change alone
                                    can create — flagged NEEDS-FOUNDER.
  t_mock_response_sets_serving_flag — call_llm_with_meta's mock branch
                                    marks its response `mock: True`.
  t_mock_gate_covers_council_and_loop_path — the ONE finding that
                                    explains "loops made real commits
                                    while chat replied with mock":
                                    services/llm/_meta.py had ZERO
                                    MOCK_LLM awareness before this fix.
  t_mock_detect_alert            — a mock resolution in
                                    call_llm_with_meta is logged as a
                                    durable, queryable
                                    mock_detected_in_live event.
  t_real_path_unchanged_when_mock_off — mock off -> real providers are
                                    still reachable (no over-guard).
"""
import importlib
import os

import pytest


def test_t_mock_isolation_preview_prod_read_once_at_import(monkeypatch):
    """MOCK_LLM is read exactly once at import time, not per-call. A
    bare os.environ mutation with NO re-import must not change what
    is_mock() returns for an already-imported module — this is the
    concrete, testable half of "isolation": a running process's mock
    state cannot drift mid-flight.

    Ends with MOCK_LLM back at conftest's test-suite baseline
    (`false`, reloaded) so this test never leaks mock-on state into
    any test file that runs after it in the same pytest session."""
    monkeypatch.setenv("MOCK_LLM", "true")
    from services.ora_chat_v2 import llm_client
    importlib.reload(llm_client)
    assert llm_client.is_mock() is True

    # Mutate the env WITHOUT reloading — simulates exactly what an
    # in-place .env edit does to an already-running process before a
    # restart actually happens.
    monkeypatch.setenv("MOCK_LLM", "false")
    assert llm_client.is_mock() is True, (
        "is_mock() drifted without a re-import/restart — MOCK_LLM is "
        "no longer read-once-at-boot."
    )

    # A genuine reload (== what a real process restart does) DOES
    # pick up the new value — proves this isn't just a broken/stuck
    # read, it's a deliberate one-time snapshot. This also happens to
    # restore the module to the test-suite's forced baseline (false),
    # so no state leaks into tests that run after this one.
    importlib.reload(llm_client)
    assert llm_client.is_mock() is False


@pytest.mark.asyncio
async def test_t_mock_response_sets_serving_flag(monkeypatch):
    from services.ora_chat_v2 import llm_client
    monkeypatch.setattr(llm_client, "_MOCK_LLM_AT_BOOT", True)
    from services.llm import _meta

    called = {"claude": False, "deepseek": False}

    async def _boom_claude(*a, **k):
        called["claude"] = True
        raise AssertionError("real Claude call must NEVER happen in mock mode")

    async def _boom_deepseek(*a, **k):
        called["deepseek"] = True
        raise AssertionError("real DeepSeek call must NEVER happen in mock mode")

    monkeypatch.setattr("services.llm._call_claude", _boom_claude, raising=False)
    monkeypatch.setattr("services.llm._call_deepseek", _boom_deepseek, raising=False)

    result = await _meta.call_llm_with_meta(
        system="sys", user="hello", mode="code",
    )
    assert result["mock"] is True
    assert result["ok"] is True
    assert result["provider"] == "mock"
    assert called["claude"] is False
    assert called["deepseek"] is False


def test_t_mock_gate_covers_council_and_loop_path():
    """Source-level lock: the gate must exist inside call_llm_with_meta
    itself (the ONE funnel every orchestrator/loop-plan/Council A/B/C
    call goes through), not bolted on somewhere downstream that a new
    caller could bypass."""
    from pathlib import Path
    src = Path("/app/backend/services/llm/_meta.py").read_text()
    assert "is_mock as _mock_llm_on" in src
    assert "_mock_llm_on()" in src
    idx_gate = src.index("_mock_llm_on()")
    idx_langfuse = src.index("from services.langfuse_tracing import trace_llm_call")
    assert idx_gate < idx_langfuse, (
        "mock gate must run BEFORE any tracing/cost-cap/provider logic"
    )


def test_t_ship_refuses_on_mock_source_level():
    """Companion belt-and-suspenders guard: even if a mock plan somehow
    produced file content, the real GitHub push must still refuse.
    Mirrors this codebase's own convention for hard-to-construct async
    state machines (see test_iter212m106_real_ship_and_sanitizer.py)."""
    from pathlib import Path
    src = Path("/app/backend/services/loop_engine.py").read_text()
    assert "SHIP REFUSED" in src
    assert "is_mock as _mock_llm_on" in src
    idx_guard = src.index("SHIP REFUSED")
    idx_commit = src.index("res = await commit_files(")
    idx_phase_ship = src.rindex('self.phase = "ship"', 0, idx_guard + 1)
    assert idx_phase_ship < idx_guard < idx_commit, (
        "ship-refuse guard must sit between phase=ship and the real "
        "commit_files() call"
    )


@pytest.mark.asyncio
async def test_t_mock_detect_alert_logged(monkeypatch):
    from services.ora_chat_v2 import llm_client
    monkeypatch.setattr(llm_client, "_MOCK_LLM_AT_BOOT", True)
    from services.llm import _meta

    logged = []

    async def _fake_log_trust_event(db, kind, *, user_id, **fields):
        logged.append((kind, user_id, fields))

    monkeypatch.setattr(
        "services.trust_surface_events.log_trust_event", _fake_log_trust_event)

    class _FakeDB:
        pass

    monkeypatch.setattr("cto_services.db.get_db", lambda: _FakeDB())

    await _meta.call_llm_with_meta(system="s", user="u", mode="write", user_id="u1")
    assert any(k == "mock_detected_in_live" for k, _, _ in logged), (
        "no mock_detected_in_live event was logged for a mock resolution"
    )


@pytest.mark.asyncio
async def test_t_real_path_unchanged_when_mock_off(monkeypatch):
    """No over-guard: with MOCK_LLM off, call_llm_with_meta must still
    reach the real routing logic (we don't assert an actual network
    call succeeds — just that the mock short-circuit does NOT fire)."""
    from services.ora_chat_v2 import llm_client
    monkeypatch.setattr(llm_client, "_MOCK_LLM_AT_BOOT", False)
    from services.llm import _meta

    async def _fake_deepseek(*a, **k):
        return "real content"

    monkeypatch.setattr("services.llm._call_deepseek", _fake_deepseek, raising=False)

    result = await _meta.call_llm_with_meta(system="s", user="u", mode="write")
    assert result.get("mock") is not True
    assert result["content"] == "real content"
