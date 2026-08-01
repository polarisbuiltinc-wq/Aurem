"""Iter 360 — Guard 17 (Retry & cascade protection) regression locks.

Charter locks: mocked failing dep → breaker OPENS, STOPS hammering,
HALF-OPENS on schedule; jittered exponential backoff; transition log;
migrated callers (loop_safety github, repo_heal, llm shim, tavily);
founder-gated QA endpoint.
"""
import asyncio
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.retry_guard import (
    BreakerOpenError,
    CircuitBreaker,
    call_with_retry,
    get_breaker,
    recent_transitions,
    snapshot_all,
)


class TestBreakerStateMachine:
    def test_opens_after_threshold_consecutive_fails(self):
        br = CircuitBreaker("t1", fail_threshold=3, cooldown_s=60)
        for _ in range(3):
            assert br.allow()
            br.record_failure("boom")
        assert br.state == "open"
        assert br.allow() is False
        assert br.trip_count == 1

    def test_success_resets_consecutive_counter(self):
        br = CircuitBreaker("t2", fail_threshold=3)
        br.record_failure("a")
        br.record_failure("b")
        br.record_success()
        br.record_failure("c")
        br.record_failure("d")
        assert br.state == "closed"

    def test_half_open_probe_after_cooldown_then_closes(self):
        br = CircuitBreaker("t3", fail_threshold=1, cooldown_s=0.05)
        br.record_failure("dead")
        assert br.state == "open" and br.allow() is False
        time.sleep(0.06)
        assert br.allow() is True          # cooldown elapsed → probe
        assert br.state == "half_open"
        br.record_success()
        assert br.state == "closed"

    def test_half_open_probe_failure_reopens(self):
        br = CircuitBreaker("t4", fail_threshold=1, cooldown_s=0.05)
        br.record_failure("dead")
        time.sleep(0.06)
        assert br.allow() is True
        br.record_failure("still dead")
        assert br.state == "open"
        assert br.allow() is False          # fresh cooldown

    def test_half_open_allows_only_one_probe_in_flight(self):
        br = CircuitBreaker("t5", fail_threshold=1, cooldown_s=0.05)
        br.record_failure("dead")
        time.sleep(0.06)
        assert br.allow() is True
        assert br.allow() is False          # second concurrent probe blocked

    def test_transition_log_records_full_cycle(self):
        br = CircuitBreaker("t6-log", fail_threshold=1, cooldown_s=0.05)
        br.record_failure("x")
        time.sleep(0.06)
        br.allow()
        br.record_success()
        evts = [e for e in recent_transitions(200) if e["dep"] == "t6-log"]
        assert [(e["from"], e["to"]) for e in evts] == [
            ("closed", "open"), ("open", "half_open"), ("half_open", "closed")]


class TestCallWithRetry:
    @pytest.mark.asyncio
    async def test_failing_dep_opens_breaker_and_stops_hammering(self, monkeypatch):
        calls = {"n": 0}

        async def dead():
            calls["n"] += 1
            raise ConnectionError("dep is down")

        async def no_sleep(_):
            pass
        monkeypatch.setattr(asyncio, "sleep", no_sleep)

        br = get_breaker("mockdep-hammer")
        br.fail_threshold = 4
        br.cooldown_s = 60
        for _ in range(2):
            with pytest.raises(ConnectionError):
                await call_with_retry("mockdep-hammer", dead, max_retries=2)
        assert br.state == "open"
        n_before = calls["n"]
        with pytest.raises(BreakerOpenError):    # fast-fail, fn NOT invoked
            await call_with_retry("mockdep-hammer", dead)
        assert calls["n"] == n_before

    @pytest.mark.asyncio
    async def test_backoff_is_exponential_with_jitter(self, monkeypatch):
        delays = []

        async def capture(d):
            delays.append(d)
        monkeypatch.setattr(asyncio, "sleep", capture)

        async def flaky():
            raise TimeoutError("t")

        with pytest.raises(TimeoutError):
            await call_with_retry("mockdep-backoff", flaky,
                                  max_retries=3, base_delay=1.0, max_delay=100)
        assert len(delays) == 3
        # full-jitter window: base*2^attempt * [0.5, 1.5)
        for i, d in enumerate(delays):
            assert 0.5 * (2 ** i) <= d < 1.5 * (2 ** i)
        assert delays[1] > delays[0] * 0.5   # growing envelope

    @pytest.mark.asyncio
    async def test_success_passes_result_through(self):
        async def ok():
            return {"answer": 42}
        assert (await call_with_retry("mockdep-ok", ok))["answer"] == 42
        assert get_breaker("mockdep-ok").state == "closed"


class TestMigratedCallers:
    @pytest.mark.asyncio
    async def test_github_request_fast_fails_when_breaker_open(self):
        from services.loop_safety import github_request_with_retry
        br = get_breaker("github")
        old = (br.state, br.opened_at)
        br.state, br.opened_at = "open", time.monotonic()
        try:
            with pytest.raises(BreakerOpenError):
                await github_request_with_retry(
                    "GET", "https://api.github.com/rate_limit",
                    headers={}, max_retries=0)
        finally:
            br.state, br.opened_at = old
            br.consecutive_fails = 0

    @pytest.mark.asyncio
    async def test_repo_heal_returns_breaker_open_error_tuple(self):
        from services.repo_heal import _try_with_retries
        br = get_breaker("github")
        old = (br.state, br.opened_at)
        br.state, br.opened_at = "open", time.monotonic()
        try:
            ok, resp, err = await _try_with_retries(lambda: None)
            assert ok is False and resp is None and "OPEN" in err
        finally:
            br.state, br.opened_at = old
            br.consecutive_fails = 0

    @pytest.mark.asyncio
    async def test_tavily_web_search_degrades_gracefully_when_open(self, monkeypatch):
        monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
        from services.web_skills import web_search
        br = get_breaker("tavily")
        old = (br.state, br.opened_at)
        br.state, br.opened_at = "open", time.monotonic()
        try:
            out = await web_search({}, {"query": "hello"})
            assert out["ok"] is False
            assert "circuit open" in out["error"]
        finally:
            br.state, br.opened_at = old
            br.consecutive_fails = 0

    def test_llm_breaker_shim_reads_central_registry(self):
        from services.llm_circuit_breaker import get_breaker_state
        assert get_breaker_state() == get_breaker("openrouter").state

    def test_llm_py_wired_to_central_breaker(self):
        # Session D · D-2d — the OpenRouter chain (which owns the
        # breaker integration) moved with `_call_deepseek` into
        # `services/llm/openrouter_providers.py`.
        src = open(os.path.join(os.path.dirname(__file__), "..",
                                "services", "llm", "openrouter_providers.py")).read()
        assert "retry_guard" in src and "_or_br.record_success()" in src \
            and "_or_br.record_failure" in src

    def test_no_orphan_retry_loops_left_in_migrated_files(self):
        base = os.path.join(os.path.dirname(__file__), "..")
        heal = open(os.path.join(base, "services", "repo_heal.py")).read()
        assert "call_with_retry" in heal
        ls = open(os.path.join(base, "services", "loop_safety.py")).read()
        assert "get_breaker(\"github\")" in ls


class TestQARow:
    def test_snapshot_contains_known_deps(self):
        snap = snapshot_all()
        for dep in ("openrouter", "github", "stripe", "tavily", "firecrawl"):
            assert dep in snap
            assert snap[dep]["state"] in ("closed", "open", "half_open")
            assert "trip_count" in snap[dep]

    def test_endpoint_registered_and_admin_gated(self):
        from routers.admin_qa import router
        paths = [r.path for r in router.routes]
        assert "/admin/qa/guard17-breakers" in paths
        assert any(d.dependency.__name__ == "require_admin_dep"
                   for d in router.dependencies)