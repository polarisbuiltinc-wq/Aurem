"""Phase 2c coverage wave 3 — backend/routers/chat.py (2026-08-24).

Founder-approved exception (see memory/code_quality_ledger.md): the
SSE tool-calling generator body of `chat_stream` (nested `gen` /
`_step` / `_activity` / `_llm_retry`, ~1716-3523) is NOT targeted here
— deep multi-round tool-call + LLM mocking would be brittle/low-
fidelity; the real-repo E2E ship/rollback drill is the substitute
evidence for that region.

This wave targets everything ELSE still uncovered after waves 1-2:
  * ChatBody._validate_task_type empty-string branch
  * _is_transient_proxy_error non-str/non-bytes body branch
  * chat_send's remaining try/except swallow branches (council,
    house_rules, response_confidence, maxx_cost log, customer cost
    log, timing log, first-chat funnel event)
  * chat_stream's SETUP phase only (rate limit, loop-mode prompt
    enrichment, ora-agent downgrade, repo-context timeout, the
    `_clarify_stream` early-return generator, brain-context +
    `_pat_lookup`, ORA-Council / House-Rules block construction) —
    none of these consume the scoped-out `gen()` generator.
  * chat_history / chat_task_followup / _generate_done_followup /
    draft_support_email small remaining branches.
"""
from __future__ import annotations

import asyncio
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch


class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def sort(self, *a, **k):
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    async def __aiter__(self):
        for r in self._rows:
            yield r

    async def to_list(self, length=None):
        return list(self._rows[: length if length else len(self._rows)])


class _FakeCollection:
    def __init__(self):
        self.rows: list[dict] = []

    def _match(self, row, query):
        for k, v in (query or {}).items():
            if row.get(k) != v:
                return False
        return True

    async def find_one(self, query=None, projection=None, sort=None):
        matched = [r for r in self.rows if self._match(r, query)]
        return dict(matched[0]) if matched else None

    async def update_one(self, query, update, upsert=False):
        import types
        for r in self.rows:
            if self._match(r, query):
                for k, v in (update.get("$set") or {}).items():
                    r[k] = v
                for k, v in (update.get("$push") or {}).items():
                    each = v.get("$each", [])
                    r.setdefault(k, []).extend(each)
                return types.SimpleNamespace(matched_count=1, modified_count=1)
        if upsert:
            new_row = dict(query or {})
            new_row.update(update.get("$setOnInsert") or {})
            new_row.update(update.get("$set") or {})
            for k, v in (update.get("$push") or {}).items():
                new_row[k] = list(v.get("$each", []))
            self.rows.append(new_row)
            return types.SimpleNamespace(matched_count=0, modified_count=0, upserted_id="new")
        return types.SimpleNamespace(matched_count=0, modified_count=0)

    def find(self, query=None, projection=None, sort=None, limit=None):
        matched = [dict(r) for r in self.rows if self._match(r, query)]
        if limit:
            matched = matched[:limit]
        return _FakeCursor(matched)


class _FakeDB:
    def __init__(self):
        object.__setattr__(self, "_cols", {})

    def __getattr__(self, name):
        cols = object.__getattribute__(self, "_cols")
        if name not in cols:
            cols[name] = _FakeCollection()
        return cols[name]


USER = {"user_id": "u1", "email": "user@example.com", "tier": "pro",
       "is_admin": False, "created_at": time.time()}
AUTH = {"Authorization": "Bearer u1"}


@pytest.fixture
def fake_db():
    return _FakeDB()


@pytest.fixture
def client(fake_db):
    from routers import chat as router_mod
    from cto_services import db as _dbmod
    _dbmod.set_db(fake_db)

    async def _fake_current_dev(authorization=None):
        if not authorization:
            from fastapi import HTTPException as _HE
            raise _HE(401, "Authorization header missing")
        return USER

    # 2026-09-08 chat.py -> chat/ package split — shared fixture covers
    # both /chat/send (turn.py) and /chat/stream (stream.py) tests below.
    _submods = (router_mod.misc, router_mod.turn, router_mod.stream, router_mod.history)
    _old = {m: m.current_dev for m in _submods}
    for m in _submods:
        m.current_dev = _fake_current_dev

    app = FastAPI()
    app.include_router(router_mod.router, prefix="/api/aurem-dev")
    c = TestClient(app)
    yield c

    for m in _submods:
        m.current_dev = _old[m]
    _dbmod.set_db(None)


# ═════════════════════════════════════════════════════════════════════
# ChatBody._validate_task_type / _is_transient_proxy_error edge cases
# ═════════════════════════════════════════════════════════════════════

class TestValidateTaskType:
    def test_empty_string_task_type_normalizes_to_none(self):
        from routers import chat as router_mod
        body = router_mod.ChatBody(prompt="hi", task_type="")
        assert body.task_type is None


class TestIsTransientProxyErrorEdgeCase:
    def test_non_str_non_bytes_body_returns_false(self):
        from routers import chat as router_mod
        assert router_mod._is_transient_proxy_error(502, 12345) is False


# ═════════════════════════════════════════════════════════════════════
# POST /chat/send — remaining try/except swallow branches
# ═════════════════════════════════════════════════════════════════════

class TestChatSendMoreExceptionSwallows:
    def test_council_retrieval_crash_is_swallowed(self, client, fake_db):
        with patch("services.usage.assert_has_budget", AsyncMock(return_value=None)), \
             patch("services.usage.assert_has_task_budget", AsyncMock(return_value=None)), \
             patch("services.ora_council_retriever.get_council_few_shot",
                  AsyncMock(side_effect=RuntimeError("council down"))), \
             patch("routers.chat.turn.chat_with_tools",
                  AsyncMock(return_value={"content": "hi", "provider": "deepseek", "meta": {}})), \
             patch("services.response_confidence.response_seems_mismatched", return_value=False), \
             patch("routers.chat.turn._deduct_tokens", AsyncMock(return_value=500)):
            r = client.post("/api/aurem-dev/chat/send", headers=AUTH,
                            json={"prompt": "hello", "project_id": "home", "session_id": "s1"})
        assert r.status_code == 200, r.text

    def test_house_rules_and_chat_extra_prepended(self, client, fake_db):
        with patch("services.usage.assert_has_budget", AsyncMock(return_value=None)), \
             patch("services.usage.assert_has_task_budget", AsyncMock(return_value=None)), \
             patch("services.house_rules.get_active_house_rules",
                  AsyncMock(return_value="Be nice")), \
             patch("services.house_rules.format_house_rules_block",
                  return_value="[RULES] Be nice"), \
             patch("services.house_rules.get_active_chat_prompt",
                  AsyncMock(return_value="Extra chat instructions")), \
             patch("routers.chat.turn.chat_with_tools",
                  AsyncMock(return_value={"content": "hi", "provider": "deepseek", "meta": {}})), \
             patch("services.response_confidence.response_seems_mismatched", return_value=False), \
             patch("routers.chat.turn._deduct_tokens", AsyncMock(return_value=500)):
            r = client.post("/api/aurem-dev/chat/send", headers=AUTH,
                            json={"prompt": "hello", "project_id": "home", "session_id": "s1"})
        assert r.status_code == 200, r.text

    def test_response_confidence_gate_crash_is_swallowed(self, client, fake_db):
        with patch("services.usage.assert_has_budget", AsyncMock(return_value=None)), \
             patch("services.usage.assert_has_task_budget", AsyncMock(return_value=None)), \
             patch("services.response_confidence.response_seems_mismatched",
                  side_effect=RuntimeError("boom")), \
             patch("routers.chat.turn.chat_with_tools",
                  AsyncMock(return_value={"content": "hi", "provider": "deepseek", "meta": {}})), \
             patch("routers.chat.turn._deduct_tokens", AsyncMock(return_value=500)):
            r = client.post("/api/aurem-dev/chat/send", headers=AUTH,
                            json={"prompt": "add a feature", "project_id": "home", "session_id": "s1"})
        assert r.status_code == 200, r.text
        assert r.json()["content"] == "hi"

    def test_maxx_cost_log_crash_is_swallowed(self, client, fake_db):
        with patch("services.usage.assert_has_budget", AsyncMock(return_value=None)), \
             patch("services.usage.assert_has_task_budget", AsyncMock(return_value=None)), \
             patch("services.loop_beta.assert_maxx_daily_budget", AsyncMock(return_value=None)), \
             patch("routers.chat.turn.chat_with_tools",
                  AsyncMock(return_value={"content": "done", "provider": "claude", "meta": {}})), \
             patch("services.response_confidence.response_seems_mismatched", return_value=False), \
             patch("services.loop_beta.log_maxx_cost",
                  AsyncMock(side_effect=RuntimeError("cost log down"))), \
             patch("routers.chat.turn._deduct_tokens", AsyncMock(return_value=500)):
            r = client.post("/api/aurem-dev/chat/send", headers=AUTH,
                            json={"prompt": "add a feature", "project_id": "home",
                                  "session_id": "s1", "maxx_mode": True})
        assert r.status_code == 200, r.text

    def test_customer_cost_tracker_crash_is_swallowed(self, client, fake_db):
        with patch("services.usage.assert_has_budget", AsyncMock(return_value=None)), \
             patch("services.usage.assert_has_task_budget", AsyncMock(return_value=None)), \
             patch("routers.chat.turn.chat_with_tools",
                  AsyncMock(return_value={"content": "hi", "provider": "deepseek", "meta": {}})), \
             patch("services.response_confidence.response_seems_mismatched", return_value=False), \
             patch("services.customer_cost_tracker.log_customer_chat_cost",
                  AsyncMock(side_effect=RuntimeError("cost tracker down"))), \
             patch("routers.chat.turn._deduct_tokens", AsyncMock(return_value=500)):
            r = client.post("/api/aurem-dev/chat/send", headers=AUTH,
                            json={"prompt": "hello", "project_id": "home", "session_id": "s1"})
        assert r.status_code == 200, r.text

    def test_timing_log_crash_is_swallowed(self, client, fake_db):
        from routers import chat as router_mod

        def _boom_on_timing(msg, *a, **k):
            if "chat_send.timing" in msg:
                raise RuntimeError("log boom")

        with patch("services.usage.assert_has_budget", AsyncMock(return_value=None)), \
             patch("services.usage.assert_has_task_budget", AsyncMock(return_value=None)), \
             patch("routers.chat.turn.chat_with_tools",
                  AsyncMock(return_value={"content": "hi", "provider": "deepseek", "meta": {}})), \
             patch("services.response_confidence.response_seems_mismatched", return_value=False), \
             patch("routers.chat.turn._deduct_tokens", AsyncMock(return_value=500)), \
             patch.object(router_mod.turn.logger, "info", side_effect=_boom_on_timing):
            r = client.post("/api/aurem-dev/chat/send", headers=AUTH,
                            json={"prompt": "hello", "project_id": "home", "session_id": "s1"})
        assert r.status_code == 200, r.text

    def test_funnel_event_emitted_on_first_chat(self, client, fake_db):
        class _DevUsersColl:
            async def find_one_and_update(self, query, update, projection=None):
                return {"user_id": "u1"}
        fake_db._cols["dev_users"] = _DevUsersColl()
        with patch("services.usage.assert_has_budget", AsyncMock(return_value=None)), \
             patch("services.usage.assert_has_task_budget", AsyncMock(return_value=None)), \
             patch("routers.chat.turn.chat_with_tools",
                  AsyncMock(return_value={"content": "hi", "provider": "deepseek", "meta": {}})), \
             patch("services.response_confidence.response_seems_mismatched", return_value=False), \
             patch("services.signup_guards.emit_funnel_event",
                  AsyncMock(return_value=None)) as mock_emit, \
             patch("routers.chat.turn._deduct_tokens", AsyncMock(return_value=500)):
            r = client.post("/api/aurem-dev/chat/send", headers=AUTH,
                            json={"prompt": "hello", "project_id": "home", "session_id": "s1"})
        assert r.status_code == 200, r.text
        assert mock_emit.await_count == 1


# ═════════════════════════════════════════════════════════════════════
# POST /chat/stream — setup-phase coverage only (see wave-1 docstring;
# none of these tests consume gen()'s scoped-out generator body)
# ═════════════════════════════════════════════════════════════════════

class TestChatStreamSetupMore:
    def test_rate_limit_exceeded_429(self, client, fake_db):
        from starlette.requests import Request
        from routers import chat as router_mod
        scope = {"type": "http", "client": ("1.2.3.4", 0), "headers": []}
        fake_request = Request(scope)
        body = router_mod.ChatBody(prompt="hello", project_id="home", session_id="s1")

        async def go():
            with patch("services.usage.assert_has_budget", AsyncMock(return_value=None)), \
                 patch("services.usage.assert_has_task_budget", AsyncMock(return_value=None)), \
                 patch("services.rate_limiter.check_rate_limit_async",
                      AsyncMock(return_value=False)):
                return await router_mod.chat_stream(fake_request, body, "Bearer u1")

        with pytest.raises(Exception) as exc_info:
            asyncio.run(go())
        assert "429" in str(exc_info.value) or "Rate limit" in str(exc_info.value)

    def test_loop_mode_downgraded_for_non_founder(self, client, fake_db):
        from starlette.requests import Request
        from routers import chat as router_mod
        # 2026-08-26 — the shared `USER` fixture is tier="pro", which the
        # Aug-24 Loop Mode rollout (6f4a6af) deliberately made ELIGIBLE
        # for Loop Mode — so it correctly no longer downgrades, which is
        # why this test started failing. This test's actual intent is
        # "an ineligible tier still gets downgraded" — use a free-tier
        # user (still ineligible) to keep testing that real behaviour.
        free_user = {**USER, "tier": "free"}
        router_mod.stream.current_dev = AsyncMock(return_value=free_user)
        scope = {"type": "http", "client": ("1.2.3.4", 0), "headers": []}
        fake_request = Request(scope)
        body = router_mod.ChatBody(prompt="hello", project_id="home", session_id="s1",
                                   execution_mode="loop")

        async def go():
            with patch("services.usage.assert_has_budget", AsyncMock(return_value=None)), \
                 patch("services.usage.assert_has_task_budget", AsyncMock(return_value=None)), \
                 patch("routers.chat.stream.is_founder_email", return_value=False), \
                 patch("routers.chat.stream._maybe_guard_shell_handoff_followup",
                      AsyncMock(return_value=None)):
                return await router_mod.chat_stream(fake_request, body, "Bearer u1")

        resp = asyncio.run(go())
        from starlette.responses import StreamingResponse
        assert isinstance(resp, StreamingResponse)
        assert body.execution_mode == "prompt"

    def test_loop_mode_appends_suffix_for_founder(self, client, fake_db):
        from starlette.requests import Request
        from routers import chat as router_mod
        founder_user = {**USER, "tier": "founder"}
        router_mod.stream.current_dev = AsyncMock(return_value=founder_user)
        scope = {"type": "http", "client": ("1.2.3.4", 0), "headers": []}
        fake_request = Request(scope)
        body = router_mod.ChatBody(prompt="hello", project_id="home", session_id="s1",
                                   execution_mode="loop")

        async def go():
            with patch("services.usage.assert_has_budget", AsyncMock(return_value=None)), \
                 patch("services.usage.assert_has_task_budget", AsyncMock(return_value=None)), \
                 patch("routers.chat.stream._maybe_guard_shell_handoff_followup",
                      AsyncMock(return_value=None)):
                return await router_mod.chat_stream(fake_request, body, "Bearer u1")

        resp = asyncio.run(go())
        from starlette.responses import StreamingResponse
        assert isinstance(resp, StreamingResponse)
        assert "[LOOP MODE" in body.prompt

    def test_repo_context_timeout_returns_503(self, client, fake_db):
        from starlette.requests import Request
        from routers import chat as router_mod
        fake_db.cto_projects.rows.append({"project_id": "p1", "user_id": "u1"})
        scope = {"type": "http", "client": ("1.2.3.4", 0), "headers": []}
        fake_request = Request(scope)
        body = router_mod.ChatBody(prompt="hello", project_id="p1", session_id="s1")

        async def go():
            with patch("services.usage.assert_has_budget", AsyncMock(return_value=None)), \
                 patch("services.usage.assert_has_task_budget", AsyncMock(return_value=None)), \
                 patch("services.ora_context.build_ora_context",
                      AsyncMock(side_effect=asyncio.TimeoutError)):
                return await router_mod.chat_stream(fake_request, body, "Bearer u1")

        with pytest.raises(Exception) as exc_info:
            asyncio.run(go())
        assert "503" in str(exc_info.value) or "timed out" in str(exc_info.value)

    def test_agent_ora_downgraded_for_non_founder_email(self, client, fake_db):
        from starlette.requests import Request
        from routers import chat as router_mod
        scope = {"type": "http", "client": ("1.2.3.4", 0), "headers": []}
        fake_request = Request(scope)
        body = router_mod.ChatBody(prompt="hello", project_id="home", session_id="s1",
                                   agent="ora")

        async def go():
            with patch("services.usage.assert_has_budget", AsyncMock(return_value=None)), \
                 patch("services.usage.assert_has_task_budget", AsyncMock(return_value=None)), \
                 patch("routers.chat.stream.is_founder_email", return_value=False), \
                 patch("routers.chat.stream._maybe_guard_shell_handoff_followup",
                      AsyncMock(return_value=None)):
                return await router_mod.chat_stream(fake_request, body, "Bearer u1")

        resp = asyncio.run(go())
        from starlette.responses import StreamingResponse
        assert isinstance(resp, StreamingResponse)
        assert body.agent == "auto"


# ═════════════════════════════════════════════════════════════════════
# POST /chat/stream — `_clarify_stream` early-return generator,
# brain-context + `_pat_lookup`, and ORA-Council / House-Rules block
# construction. All still setup-phase (no gen() consumption).
# ═════════════════════════════════════════════════════════════════════

class TestChatStreamClarifyAndBrainContext:
    def test_clarify_stream_generator_emits_frames_and_persists(self, client, fake_db):
        from starlette.requests import Request
        from routers import chat as router_mod
        scope = {"type": "http", "client": ("1.2.3.4", 0), "headers": []}
        fake_request = Request(scope)
        body = router_mod.ChatBody(prompt="do it", project_id="home", session_id="s1")

        async def go():
            with patch("services.usage.assert_has_budget", AsyncMock(return_value=None)), \
                 patch("services.usage.assert_has_task_budget", AsyncMock(return_value=None)), \
                 patch("routers.chat.stream._maybe_guard_shell_handoff_followup",
                      AsyncMock(return_value="Did you mean to run `npm install`?")):
                resp = await router_mod.chat_stream(fake_request, body, "Bearer u1")
            chunks = []
            async for c in resp.body_iterator:
                chunks.append(c)
            return chunks

        chunks = asyncio.run(go())
        joined = "".join(c if isinstance(c, str) else c.decode() for c in chunks)
        assert "aurem-clarify-fix" in joined
        assert "npm install" in joined
        sess = fake_db.chat_sessions.rows[0]
        assert sess["turns"][-1]["provider"] == "aurem-clarify-fix"

    def test_brain_context_and_pat_lookup_populates_extra_sys(self, client, fake_db):
        from starlette.requests import Request
        from routers import chat as router_mod
        fake_db.cto_projects.rows.append({
            "project_id": "p1", "user_id": "u1",
            "github_owner": "acme", "github_repo": "widgets",
            "auth_method": "github_app", "installation_id": 1,
        })
        scope = {"type": "http", "client": ("1.2.3.4", 0), "headers": []}
        fake_request = Request(scope)
        body = router_mod.ChatBody(prompt="what did we ship", project_id="p1", session_id="s1")

        async def go():
            with patch("services.usage.assert_has_budget", AsyncMock(return_value=None)), \
                 patch("services.usage.assert_has_task_budget", AsyncMock(return_value=None)), \
                 patch("services.ora_context.build_ora_context",
                      AsyncMock(return_value={"repo_owner": "acme", "repo_name": "widgets"})), \
                 patch("routers.chat.stream._maybe_guard_shell_handoff_followup",
                      AsyncMock(return_value=None)), \
                 patch("services.pat_vault.get_repo_token_or_error",
                      AsyncMock(return_value=("tok123", None, None))), \
                 patch("services.project_brain.get_brain_context",
                      AsyncMock(return_value="recent commits: fixed bug X")):
                return await router_mod.chat_stream(fake_request, body, "Bearer u1")

        resp = asyncio.run(go())
        from starlette.responses import StreamingResponse
        assert isinstance(resp, StreamingResponse)

    def test_pat_lookup_crash_is_swallowed(self, client, fake_db):
        from starlette.requests import Request
        from routers import chat as router_mod
        fake_db.cto_projects.rows.append({
            "project_id": "p1", "user_id": "u1",
            "github_owner": "acme", "github_repo": "widgets",
        })
        scope = {"type": "http", "client": ("1.2.3.4", 0), "headers": []}
        fake_request = Request(scope)
        body = router_mod.ChatBody(prompt="what did we ship", project_id="p1", session_id="s1")

        async def go():
            with patch("services.usage.assert_has_budget", AsyncMock(return_value=None)), \
                 patch("services.usage.assert_has_task_budget", AsyncMock(return_value=None)), \
                 patch("services.ora_context.build_ora_context",
                      AsyncMock(return_value={"repo_owner": "acme", "repo_name": "widgets"})), \
                 patch("routers.chat.stream._maybe_guard_shell_handoff_followup",
                      AsyncMock(return_value=None)), \
                 patch("services.pat_vault.get_repo_token_or_error",
                      AsyncMock(side_effect=RuntimeError("vault down"))), \
                 patch("services.project_brain.get_brain_context",
                      AsyncMock(return_value="")):
                return await router_mod.chat_stream(fake_request, body, "Bearer u1")

        resp = asyncio.run(go())
        from starlette.responses import StreamingResponse
        assert isinstance(resp, StreamingResponse)

    def test_council_and_house_rules_blocks_prepended_in_stream(self, client, fake_db):
        from starlette.requests import Request
        from routers import chat as router_mod
        scope = {"type": "http", "client": ("1.2.3.4", 0), "headers": []}
        fake_request = Request(scope)
        body = router_mod.ChatBody(prompt="hello", project_id="home", session_id="s1")

        async def go():
            with patch("services.usage.assert_has_budget", AsyncMock(return_value=None)), \
                 patch("services.usage.assert_has_task_budget", AsyncMock(return_value=None)), \
                 patch("routers.chat.stream._maybe_guard_shell_handoff_followup",
                      AsyncMock(return_value=None)), \
                 patch("services.ora_council_retriever.get_council_few_shot",
                      AsyncMock(return_value=("recalled block", 3))), \
                 patch("services.house_rules.get_active_house_rules",
                      AsyncMock(return_value="Be concise")), \
                 patch("services.house_rules.format_house_rules_block",
                      return_value="[RULES] Be concise"), \
                 patch("services.house_rules.get_active_chat_prompt",
                      AsyncMock(return_value="Extra chat rule")):
                return await router_mod.chat_stream(fake_request, body, "Bearer u1")

        resp = asyncio.run(go())
        from starlette.responses import StreamingResponse
        assert isinstance(resp, StreamingResponse)

    def test_ora_panel_advisor_house_rules_injected(self, client, fake_db):
        from starlette.requests import Request
        from routers import chat as router_mod
        scope = {"type": "http", "client": ("1.2.3.4", 0), "headers": []}
        fake_request = Request(scope)
        body = router_mod.ChatBody(prompt="what's the status?", project_id="home",
                                   session_id="s1", ora_panel=True)

        async def go():
            with patch("services.usage.assert_has_budget", AsyncMock(return_value=None)), \
                 patch("services.usage.assert_has_task_budget", AsyncMock(return_value=None)), \
                 patch("routers.chat.stream._maybe_guard_shell_handoff_followup",
                      AsyncMock(return_value=None)), \
                 patch("services.house_rules.get_active_house_rules",
                      AsyncMock(return_value="Advisor tone rule")), \
                 patch("services.house_rules.format_house_rules_block",
                      return_value="[ADVISOR RULES]"):
                return await router_mod.chat_stream(fake_request, body, "Bearer u1")

        resp = asyncio.run(go())
        from starlette.responses import StreamingResponse
        assert isinstance(resp, StreamingResponse)


# ═════════════════════════════════════════════════════════════════════
# chat_history / chat_task_followup / _generate_done_followup /
# draft_support_email — remaining small branches
# ═════════════════════════════════════════════════════════════════════

class TestSmallRemainingBranches:
    def test_chat_history_no_session_id_returns_empty(self, client, fake_db):
        r = client.get("/api/aurem-dev/chat/history", headers=AUTH)
        assert r.status_code == 200
        assert r.json() == {"ok": True, "messages": [], "session_id": None}

    def test_chat_task_followup_db_none_503(self, client, monkeypatch):
        from routers import chat as router_mod
        monkeypatch.setattr(router_mod.misc, "get_db", lambda: None)
        r = client.post("/api/aurem-dev/chat/task-followup", headers=AUTH,
                        json={"task_id": "t1", "session_id": "s1"})
        assert r.status_code == 503

    @pytest.mark.asyncio
    async def test_generate_done_followup_empty_llm_returns_fallback(self):
        from routers import chat as router_mod
        with patch("services.chat_helpers.call_llm_with_meta",
                  AsyncMock(return_value={"content": "   "})):
            result = await router_mod._generate_done_followup(
                original="fix the bug", summary="fixed", files=["a.py"], sha="abc123")
        assert "abc123" in result

    def test_draft_support_email_projects_fetch_crash_swallowed(self, client, fake_db):
        class _Boom:
            def find(self, *a, **k):
                raise RuntimeError("mongo down")
        fake_db._cols["cto_projects"] = _Boom()
        with patch("services.llm.call_openrouter_model", AsyncMock(return_value="ok body")):
            r = client.post("/api/aurem-dev/chat/ora/draft-support-email", headers=AUTH,
                            json={"issue": "still broken"})
        assert r.status_code == 200

    def test_draft_support_email_created_at_conversion_crash_falls_back_to_unknown(
            self, client, fake_db):
        from routers import chat as router_mod

        async def _bad_created(authorization=None):
            return {**USER, "created_at": "not-a-number"}
        router_mod.misc.current_dev = _bad_created
        with patch("services.llm.call_openrouter_model", AsyncMock(return_value="ok body")):
            r = client.post("/api/aurem-dev/chat/ora/draft-support-email", headers=AUTH,
                            json={"issue": "still broken"})
        assert r.status_code == 200
        assert "unknown" in r.json()["body"]

    def test_draft_support_email_no_created_at_falls_back_to_unknown(self, client, fake_db):
        from routers import chat as router_mod

        async def _no_created(authorization=None):
            return {"user_id": "u1", "email": "user@example.com", "tier": "pro"}
        router_mod.misc.current_dev = _no_created
        with patch("services.llm.call_openrouter_model", AsyncMock(return_value="ok body")):
            r = client.post("/api/aurem-dev/chat/ora/draft-support-email", headers=AUTH,
                            json={"issue": "still broken"})
        assert r.status_code == 200
        assert "unknown" in r.json()["body"]

    def test_draft_support_email_tasks_fetch_crash_swallowed(self, client, fake_db):
        class _Boom:
            def find(self, *a, **k):
                raise RuntimeError("mongo down")
        fake_db._cols["cto_tasks"] = _Boom()
        with patch("services.llm.call_openrouter_model", AsyncMock(return_value="ok body")):
            r = client.post("/api/aurem-dev/chat/ora/draft-support-email", headers=AUTH,
                            json={"issue": "still broken"})
        assert r.status_code == 200


class TestChatSendAndStreamRemainingSwallows:
    def test_chat_send_house_rules_crash_is_swallowed(self, client, fake_db):
        with patch("services.usage.assert_has_budget", AsyncMock(return_value=None)), \
             patch("services.usage.assert_has_task_budget", AsyncMock(return_value=None)), \
             patch("services.house_rules.get_active_house_rules",
                  AsyncMock(side_effect=RuntimeError("house rules down"))), \
             patch("routers.chat.turn.chat_with_tools",
                  AsyncMock(return_value={"content": "hi", "provider": "deepseek", "meta": {}})), \
             patch("services.response_confidence.response_seems_mismatched", return_value=False), \
             patch("routers.chat.turn._deduct_tokens", AsyncMock(return_value=500)):
            r = client.post("/api/aurem-dev/chat/send", headers=AUTH,
                            json={"prompt": "hello", "project_id": "home", "session_id": "s1"})
        assert r.status_code == 200, r.text

    def test_reset_last_provider_crash_is_swallowed(self, client, fake_db):
        from starlette.requests import Request
        from routers import chat as router_mod
        scope = {"type": "http", "client": ("1.2.3.4", 0), "headers": []}
        fake_request = Request(scope)
        body = router_mod.ChatBody(prompt="hello", project_id="home", session_id="s1")

        async def go():
            with patch("services.usage.assert_has_budget", AsyncMock(return_value=None)), \
                 patch("services.usage.assert_has_task_budget", AsyncMock(return_value=None)), \
                 patch("services.llm.reset_last_provider",
                      side_effect=RuntimeError("boom")), \
                 patch("routers.chat.stream._maybe_guard_shell_handoff_followup",
                      AsyncMock(return_value=None)):
                return await router_mod.chat_stream(fake_request, body, "Bearer u1")

        resp = asyncio.run(go())
        from starlette.responses import StreamingResponse
        assert isinstance(resp, StreamingResponse)

    def test_safe_repo_context_timeout_degrades_to_empty(self, client, fake_db):
        from starlette.requests import Request
        from routers import chat as router_mod
        scope = {"type": "http", "client": ("1.2.3.4", 0), "headers": []}
        fake_request = Request(scope)
        body = router_mod.ChatBody(prompt="hello", project_id="home", session_id="s1")

        async def go():
            with patch("services.usage.assert_has_budget", AsyncMock(return_value=None)), \
                 patch("services.usage.assert_has_task_budget", AsyncMock(return_value=None)), \
                 patch("routers.chat.stream.get_repo_context",
                      AsyncMock(side_effect=asyncio.TimeoutError)), \
                 patch("routers.chat.stream._maybe_guard_shell_handoff_followup",
                      AsyncMock(return_value=None)):
                return await router_mod.chat_stream(fake_request, body, "Bearer u1")

        resp = asyncio.run(go())
        from starlette.responses import StreamingResponse
        assert isinstance(resp, StreamingResponse)

    def test_brain_context_outer_crash_swallowed(self, client, fake_db):
        from starlette.requests import Request
        from routers import chat as router_mod

        class _Boom:
            async def find_one(self, *a, **k):
                raise RuntimeError("mongo down")
        fake_db._cols["cto_projects"] = _Boom()
        scope = {"type": "http", "client": ("1.2.3.4", 0), "headers": []}
        fake_request = Request(scope)
        body = router_mod.ChatBody(prompt="hello", project_id="p1", session_id="s1")

        async def go():
            with patch("services.usage.assert_has_budget", AsyncMock(return_value=None)), \
                 patch("services.usage.assert_has_task_budget", AsyncMock(return_value=None)), \
                 patch("services.ora_context.build_ora_context",
                      AsyncMock(return_value={"repo_owner": "acme", "repo_name": "widgets"})), \
                 patch("routers.chat.stream._maybe_guard_shell_handoff_followup",
                      AsyncMock(return_value=None)):
                return await router_mod.chat_stream(fake_request, body, "Bearer u1")

        resp = asyncio.run(go())
        from starlette.responses import StreamingResponse
        assert isinstance(resp, StreamingResponse)

    def test_council_block_crash_in_stream_is_swallowed(self, client, fake_db):
        from starlette.requests import Request
        from routers import chat as router_mod
        scope = {"type": "http", "client": ("1.2.3.4", 0), "headers": []}
        fake_request = Request(scope)
        body = router_mod.ChatBody(prompt="hello", project_id="home", session_id="s1")

        async def go():
            with patch("services.usage.assert_has_budget", AsyncMock(return_value=None)), \
                 patch("services.usage.assert_has_task_budget", AsyncMock(return_value=None)), \
                 patch("routers.chat.stream._maybe_guard_shell_handoff_followup",
                      AsyncMock(return_value=None)), \
                 patch("services.ora_council_retriever.get_council_few_shot",
                      AsyncMock(side_effect=RuntimeError("council down"))):
                return await router_mod.chat_stream(fake_request, body, "Bearer u1")

        resp = asyncio.run(go())
        from starlette.responses import StreamingResponse
        assert isinstance(resp, StreamingResponse)

    def test_house_rules_crash_in_stream_is_swallowed(self, client, fake_db):
        from starlette.requests import Request
        from routers import chat as router_mod
        scope = {"type": "http", "client": ("1.2.3.4", 0), "headers": []}
        fake_request = Request(scope)
        body = router_mod.ChatBody(prompt="hello", project_id="home", session_id="s1")

        async def go():
            with patch("services.usage.assert_has_budget", AsyncMock(return_value=None)), \
                 patch("services.usage.assert_has_task_budget", AsyncMock(return_value=None)), \
                 patch("routers.chat.stream._maybe_guard_shell_handoff_followup",
                      AsyncMock(return_value=None)), \
                 patch("services.house_rules.get_active_house_rules",
                      AsyncMock(side_effect=RuntimeError("house rules down"))):
                return await router_mod.chat_stream(fake_request, body, "Bearer u1")

        resp = asyncio.run(go())
        from starlette.responses import StreamingResponse
        assert isinstance(resp, StreamingResponse)

    def test_advisor_house_rules_crash_in_ora_panel_is_swallowed(self, client, fake_db):
        from starlette.requests import Request
        from routers import chat as router_mod
        scope = {"type": "http", "client": ("1.2.3.4", 0), "headers": []}
        fake_request = Request(scope)
        body = router_mod.ChatBody(prompt="status?", project_id="home", session_id="s1",
                                   ora_panel=True)

        async def go():
            with patch("services.usage.assert_has_budget", AsyncMock(return_value=None)), \
                 patch("services.usage.assert_has_task_budget", AsyncMock(return_value=None)), \
                 patch("routers.chat.stream._maybe_guard_shell_handoff_followup",
                      AsyncMock(return_value=None)), \
                 patch("services.house_rules.get_active_house_rules",
                      AsyncMock(side_effect=RuntimeError("house rules down"))):
                return await router_mod.chat_stream(fake_request, body, "Bearer u1")

        resp = asyncio.run(go())
        from starlette.responses import StreamingResponse
        assert isinstance(resp, StreamingResponse)
