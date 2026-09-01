"""
tests/_stream_golden_harness.py — shared harness for the 2026-09-08
StreamState refactor's byte-identical golden test.

Not a test file itself (no test_ prefix) — imported by both the
one-time capture script and the permanent regression test so the
exact same mocks drive both the "before" and "after" runs.

CLOCK SOURCES (grep-confirmed on the pre-refactor stream.py): only
`time.monotonic()` (via local aliases `_t`/`_pg_time`, same module
object) and `time.time()` (DB-write timestamps only, never in the
SSE stream). No datetime.now()/loop.time() reads.

IMPORTANT — why we do NOT globally monkeypatch time.monotonic:
`asyncio.BaseEventLoop.time()` IS `time.monotonic()` (verified via
`inspect.getsource`) — patching the module-level function corrupts
asyncio's OWN internal scheduler (call_later/wait_for/sleep all key
off loop.time()), making the soft/hard-timeout races non-
deterministic in the opposite direction from what we want. Instead:
the 2 timeout scenarios use REAL tiny env-configured timeouts
(CHAT_SOFT_TIMEOUT_S / CHAT_HARD_TIMEOUT_S) to deterministically
FORCE the branch, and `normalize_events()` below masks the resulting
wall-clock-derived fields (`thinking_s`, `elapsed_s`, `t_started`)
before comparison. Every non-timing field (event type/order, mode,
content, provider, council data, verified_paths, ...) is still
compared byte-for-byte — that's what actually proves the refactor
didn't change behavior.
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient
from main import app
import routers.chat as chat_mod

_TIMING_FIELDS = {"thinking_s", "elapsed_s", "t_started", "elapsed"}


def normalize_events(events: list) -> list:
    """Zero out wall-clock-derived fields so the comparison is exact
    on everything that actually reflects behavior."""
    out = []
    for ev in events:
        ev = dict(ev)
        for k in list(ev.keys()):
            if k in _TIMING_FIELDS:
                ev[k] = 0
        out.append(ev)
    return out



def _test_user():
    return {
        "user_id": "golden-user-1", "email": "golden@example.com",
        "is_admin": False, "is_unlimited": True, "tier": "founder",
    }


async def _fake_current_dev(authorization=None):
    return _test_user()


class _FakeCollection:
    def __init__(self, find_one_result=None):
        self._find_one_result = find_one_result

    async def find_one(self, *a, **kw):
        return self._find_one_result

    async def update_one(self, *a, **kw):
        class _R:
            modified_count = 1
        return _R()


class _FakeDB:
    def __init__(self, pending_fix_task=None):
        self.chat_sessions = _FakeCollection(
            {"pending_fix_task": pending_fix_task} if pending_fix_task else {}
        )
        self.cto_projects = _FakeCollection(None)


def install_common_mocks(monkeypatch, *, pending_fix_task=None):
    """Mocks shared by every golden scenario — auth, budget, DB,
    council/context builders (all skipped via project_id='home'),
    background fire-and-forget tasks (noop'd for determinism), and
    the pre-LLM dedup (routers/chat_pre_llm.py) short-circuited per
    scenario via monkeypatch.setattr on resolve_pre_llm."""
    monkeypatch.setattr(chat_mod.stream, "current_dev", _fake_current_dev)
    monkeypatch.setattr(chat_mod.turn, "current_dev", _fake_current_dev)

    async def _noop(*a, **kw):
        return None
    monkeypatch.setattr("services.usage.assert_has_budget", _noop)
    monkeypatch.setattr("services.usage.assert_has_task_budget", _noop)
    monkeypatch.setattr(chat_mod.stream, "is_founder_email", lambda *_a, **_k: False)

    fake_db = _FakeDB(pending_fix_task=pending_fix_task)
    monkeypatch.setattr(chat_mod.stream, "get_db", lambda: fake_db)
    # 2026-09-08 StreamState refactor — worker.py imports its OWN
    # `get_db` binding (`from cto_services.db import get_db`), a
    # separate name from stream.py's. Patching only stream.py's
    # binding would silently let Mode D/E fast-path code hit the
    # real DB. See handoff note on moved-module patch targets.
    monkeypatch.setattr(chat_mod.worker, "get_db", lambda: fake_db)

    monkeypatch.setattr(
        chat_mod.stream, "_maybe_guard_shell_handoff_followup",
        lambda **kw: _none_coro(),
    )

    async def _persist_turn_noop(*a, **kw):
        return None
    monkeypatch.setattr(chat_mod.stream, "_persist_turn", _persist_turn_noop)

    async def _deduct_tokens_fixed(*a, **kw):
        return 4242
    monkeypatch.setattr(chat_mod.stream, "_deduct_tokens", _deduct_tokens_fixed)

    monkeypatch.setattr(
        "services.customer_cost_tracker.log_customer_chat_cost", _noop)
    monkeypatch.setattr("services.audit_log.record_turn", _noop)
    monkeypatch.setattr("services.ora_learning.maybe_log_ora_escalation", _noop)
    monkeypatch.setattr("services.ora_learning.extract_session_patterns", _noop)
    monkeypatch.setattr("services.ora_council_logger.log_conversational", _noop)
    monkeypatch.setattr(
        "services.project_brain.update_brain_from_conversation", _noop)

    class _FakeQM:
        def __init__(self, *a, **kw):
            pass

        async def score_async(self, *a, **kw):
            return None
    monkeypatch.setattr("core.quality_monitor.QualityMonitor", _FakeQM)

    class _FakeCitationGuard:
        async def enforce(self, *a, **kw):
            return {"retried": False}
    monkeypatch.setattr(
        "services.citation_guard.CitationGuard", _FakeCitationGuard)

    async def _apply_bv_noop(ora_panel, content, prompt, **kw):
        return content
    monkeypatch.setattr(
        "services.business_voice_filter.apply_business_owner_guards",
        _apply_bv_noop,
    )
    return fake_db


async def _none_coro():
    return None


def make_fake_chat_with_tools(reply_text: str, sleep_s: float = 0.0):
    async def _fake(**kwargs):
        if sleep_s:
            import asyncio
            await asyncio.sleep(sleep_s)
        return {
            "content": reply_text, "provider": "golden-provider",
            "tool_calls": [], "tool_invocations": [], "tool_calls_run": 0,
            "messages": [{"role": "user", "content": kwargs.get("prompt", "")}],
        }
    return _fake


def make_fake_resolve_pre_llm(*, tier="agentic", mode="agentic", result=None):
    from routers.chat_pre_llm import PreLLMOutcome

    async def _fake(**kwargs):
        return PreLLMOutcome(
            result=result,
            intent_result={"tier": tier, "confidence": 0.9, "method": "golden"},
            tier=tier, mode=mode,
        )
    return _fake


REQUEST_BODY_DEFAULTS = {
    "project_id": "home", "session_id": "golden-sess-1",
    "max_tool_iters": 2,
}


def post_stream(client: TestClient, prompt: str, **overrides) -> str:
    payload = dict(REQUEST_BODY_DEFAULTS, prompt=prompt, **overrides)
    r = client.post(
        "/api/aurem-dev/chat/stream",
        headers={"Authorization": "Bearer golden"},
        json=payload,
    )
    assert r.status_code == 200, r.text
    return r.text


def parse_events(body: str) -> list:
    return [json.loads(line[len("data: "):])
            for line in body.splitlines() if line.startswith("data: ")]


# ─── The 6 golden scenarios ────────────────────────────────────────
# Each covers a distinct code path through chat_stream/gen()/_worker().
# All use project_id="home" to skip bin_ctx/brain_ctx/GitHub entirely
# — those are pre-gen() context-build concerns, not part of the
# StreamState worker/watchdog/retries split under test here.

def run_plain_agentic(monkeypatch) -> str:
    """Orchestrator fallback path: no mode short-circuit, straight to
    chat_with_tools. Exercises worker.py's default dispatch branch."""
    install_common_mocks(monkeypatch)
    monkeypatch.setattr(chat_mod.stream, "classify_intent", lambda *a, **k: "A")
    monkeypatch.setattr(chat_mod.worker, "classify_intent", lambda *a, **k: "A")
    monkeypatch.setattr(
        "services.ora_council_retriever.get_council_few_shot",
        lambda *a, **k: _council_result(),
    )
    monkeypatch.setattr(chat_mod.stream, "get_repo_context",
                         lambda *a, **k: _empty_str_coro())
    monkeypatch.setattr(
        "routers.chat_pre_llm.resolve_pre_llm",
        make_fake_resolve_pre_llm(tier="agentic", mode="A"),
    )
    # 2026-09-08 — chat_with_tools now runs from worker.py's own
    # `_run_orchestrator` (its own bound import), not stream.py's.
    monkeypatch.setattr(chat_mod.worker, "chat_with_tools",
                         make_fake_chat_with_tools("Golden agentic reply."))
    monkeypatch.setattr(
        "services.response_confidence.response_seems_mismatched",
        lambda *a, **k: False,
    )
    client = TestClient(app)
    return post_stream(client, "explain how the retry queue works")


def run_mode_d_fast_path(monkeypatch) -> str:
    """is_fix_confirmation redirect — zero LLM calls, pure DB + string
    path. Exercises worker.py's Mode-D fast-path branch."""
    install_common_mocks(monkeypatch, pending_fix_task="fix the retry bug")
    client = TestClient(app)
    return post_stream(client, "yes fix it")


def run_mode_f_engage(monkeypatch) -> str:
    """Mode F (Engage/Market) dedicated dispatch function."""
    install_common_mocks(monkeypatch)
    monkeypatch.setattr(chat_mod.stream, "classify_intent", lambda *a, **k: "F")
    monkeypatch.setattr(
        "services.ora_council_retriever.get_council_few_shot",
        lambda *a, **k: _council_result(),
    )

    async def _fake_engage(**kwargs):
        return "Golden engage/positioning reply."
    monkeypatch.setattr("services.mode_f_engage.run_engage", _fake_engage)
    client = TestClient(app)
    return post_stream(client, "how should I position this to investors?")


def run_confidence_retry(monkeypatch) -> str:
    """First reply is a mismatched diagnosis; auto-retry without the
    council-recall block resolves silently. Exercises retries.py."""
    install_common_mocks(monkeypatch)
    monkeypatch.setattr(chat_mod.stream, "classify_intent", lambda *a, **k: "A")
    monkeypatch.setattr(
        "services.ora_council_retriever.get_council_few_shot",
        lambda *a, **k: _council_result(),
    )
    monkeypatch.setattr(chat_mod.stream, "get_repo_context",
                         lambda *a, **k: _empty_str_coro())
    monkeypatch.setattr(
        "routers.chat_pre_llm.resolve_pre_llm",
        make_fake_resolve_pre_llm(tier="query", mode="A"),
    )
    mismatched = (
        "Root cause: The API endpoint requires admin access.\n\n"
        "```aurem-handoff\n"
        '{"title": "Fix auth", "files": ["app.py"]}\n'
        "```"
    )
    # 2026-09-08 — first pass now runs through worker.py's own
    # `chat_with_tools` binding, not stream.py's.
    monkeypatch.setattr(chat_mod.worker, "chat_with_tools",
                         make_fake_chat_with_tools(mismatched))

    _calls = {"n": 0}

    def _mismatch_check(prompt, content, prior_fix_signal):
        _calls["n"] += 1
        return _calls["n"] == 1  # first call mismatched, retry is clean

    monkeypatch.setattr(
        "services.response_confidence.response_seems_mismatched", _mismatch_check)
    monkeypatch.setattr(
        "services.chat_helpers.chat_with_tools",
        make_fake_chat_with_tools("Golden retry-resolved reply."),
    )
    client = TestClient(app)
    return post_stream(client, "What is 5+5?")


def _timeout_scenario(monkeypatch, *, soft_s: str, hard_s: str, sleep_s: float) -> str:
    install_common_mocks(monkeypatch)
    monkeypatch.setenv("CHAT_SOFT_TIMEOUT_S", soft_s)
    monkeypatch.setenv("CHAT_HARD_TIMEOUT_S", hard_s)
    monkeypatch.setattr(chat_mod.stream, "classify_intent", lambda *a, **k: "A")
    monkeypatch.setattr(
        "services.ora_council_retriever.get_council_few_shot",
        lambda *a, **k: _council_result(),
    )
    monkeypatch.setattr(chat_mod.stream, "get_repo_context",
                         lambda *a, **k: _empty_str_coro())
    monkeypatch.setattr(
        "routers.chat_pre_llm.resolve_pre_llm",
        make_fake_resolve_pre_llm(tier="agentic", mode="A"),
    )
    # Real sleep, real (tiny, env-configured) timeout — see module
    # docstring for why we don't fake time.monotonic here.
    # 2026-09-08 — worker.py owns the live `chat_with_tools` binding.
    monkeypatch.setattr(
        chat_mod.worker, "chat_with_tools",
        make_fake_chat_with_tools("never seen", sleep_s=sleep_s),
    )
    client = TestClient(app)
    return post_stream(client, "do a deep multi-file audit please")


def run_soft_timeout(monkeypatch) -> str:
    """<=1 tool invocation + SOFT_TIMEOUT_S blown → rescued early with
    the proxy-safe soft-timeout message. Exercises watchdog.py.
    sleep_s=1.3 (> the ticker's fixed 0.6s cadence) so the first real
    tick reliably arrives — and gets evaluated as past-soft-deadline —
    before the worker's own result does; HARD_TIMEOUT_S=999 keeps the
    hard-deadline branch out of the race entirely."""
    return _timeout_scenario(monkeypatch, soft_s="0.05", hard_s="999", sleep_s=1.3)


def run_hard_timeout(monkeypatch) -> str:
    """Wall-clock HARD_TIMEOUT_S blown → timeout-guard message.
    Exercises watchdog.py's hard-deadline branch. sleep_s=0.6 is
    already >> HARD_TIMEOUT_S=0.05, and the hard branch fires on the
    very first `q.get()` timeout — no need to out-wait the ticker."""
    return _timeout_scenario(monkeypatch, soft_s="999", hard_s="0.05", sleep_s=0.6)


async def _empty_str_coro():
    return ""


async def _council_result():
    return "", 0


SCENARIOS = {
    "plain_agentic": run_plain_agentic,
    "mode_d_fast_path": run_mode_d_fast_path,
    "mode_f_engage": run_mode_f_engage,
    "confidence_retry": run_confidence_retry,
    "soft_timeout": run_soft_timeout,
    "hard_timeout": run_hard_timeout,
}
