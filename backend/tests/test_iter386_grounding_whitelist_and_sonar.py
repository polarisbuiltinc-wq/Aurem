"""Iter 386 · Session 2.7 · Fixes B + D — grounding whitelist +
Sonar timeout wiring coverage."""
from __future__ import annotations
import sys
sys.path.insert(0, "/app/backend")

import os
from unittest.mock import AsyncMock, patch

import pytest


# ══════════════════════════════════════════════════════════════════════
# Fix B — `/image` no longer flagged as an unknown slash-command
# ══════════════════════════════════════════════════════════════════════
class TestSlashCommandWhitelist:
    def test_image_is_recognised(self):
        """The 2026-02-08 prod incident: ORA suggested `/image ...`
        and every response was suffixed with "⚠️ Unverified citations:
        /image — ye paths repo mein exist nahi karte." because
        `/image` isn't in KNOWN_COMMANDS (it's client-side intercepted).
        This test locks that fix."""
        from services.ora_chat.grounding_check import extract_unknown_commands
        reply = ("For a logo I'd suggest using `/image logo for AUREM "
                 "fintech, minimal, monochrome`. Tap the button below.")
        assert extract_unknown_commands(reply) == []

    def test_image_gen_alias_also_whitelisted(self):
        """The intercept regex matches both `/image` and `/image-gen`."""
        from services.ora_chat.grounding_check import extract_unknown_commands
        assert extract_unknown_commands(
            "run `/image tiny concept` right now") == []

    def test_backend_slash_commands_still_recognised(self):
        """Regression: don't break the original set."""
        from services.ora_chat.grounding_check import extract_unknown_commands
        reply = ("Try `/read backend/main.py` or `/find loop_engine` to "
                 "inspect. See `/help` for the full list.")
        assert extract_unknown_commands(reply) == []

    def test_genuine_unknown_command_still_flagged(self):
        """The whitelist is precise — a NEW/invented command like
        `/deploy-production` MUST still trip the warning."""
        from services.ora_chat.grounding_check import extract_unknown_commands
        reply = ("Run `/deploy-production` to ship this change to prod.")
        r = extract_unknown_commands(reply)
        assert "/deploy-production" in r

    def test_multiple_valid_commands_no_warning(self):
        from services.ora_chat.grounding_check import extract_unknown_commands
        reply = ("Options: `/image logo idea`, `/read safety.py`, "
                 "`/loop-stats abc123`, `/help`.")
        assert extract_unknown_commands(reply) == []


# ══════════════════════════════════════════════════════════════════════
# Fix D — Sonar timeout bump + retry + slow-call telemetry
# ══════════════════════════════════════════════════════════════════════
class TestSonarTimeoutConfig:
    def test_default_timeout_is_20_seconds(self):
        """The default was 12s pre-Session-2.7 and produced 4 ×
        "Sources didn't respond in time" in one prod session. 20s
        gives Sonar comfortable headroom on p99 latency."""
        # Reload the module to pick up env changes.
        os.environ.pop("ORA_HTTP_TIMEOUT", None)
        import importlib
        import services.ora_chat.deep_research as dr
        importlib.reload(dr)
        assert dr._HTTP_TIMEOUT == 20.0

    def test_timeout_env_override_wins(self):
        os.environ["ORA_HTTP_TIMEOUT"] = "45.5"
        import importlib
        import services.ora_chat.deep_research as dr
        importlib.reload(dr)
        assert dr._HTTP_TIMEOUT == 45.5
        del os.environ["ORA_HTTP_TIMEOUT"]

    def test_slow_threshold_default(self):
        os.environ.pop("ORA_SONAR_SLOW_THRESHOLD_S", None)
        import importlib
        import services.ora_chat.deep_research as dr
        importlib.reload(dr)
        assert dr._SONAR_SLOW_THRESHOLD_S == 5.0


class TestSonarRetry:
    async def test_timeout_triggers_one_retry(self, monkeypatch):
        """First `one_shot` call times out → we retry once → success.
        Zero user-visible failure."""
        import services.ora_chat.deep_research as dr

        call_count = {"n": 0}

        async def _one_shot_stub(*a, **kw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # Simulate a slow response — asyncio.wait_for in the
                # wrapper will raise TimeoutError before this returns.
                import asyncio
                await asyncio.sleep(999)
            return ("real result", {"input_tokens": 10,
                                     "output_tokens": 20}, None)

        monkeypatch.setattr(dr, "one_shot", _one_shot_stub)
        # Fake `resolve("research")` so we don't need the full config.
        monkeypatch.setattr(dr, "resolve", lambda _: {
            "model": "test", "temperature": 0, "top_p": 1,
            "presence_penalty": 0, "max_tokens": 100,
        })
        # Fake cost tracker.
        monkeypatch.setattr(dr.cost_tracker, "compute_cost_usd",
                             lambda *a, **kw: 0.0)
        # Short timeout for the test.
        monkeypatch.setattr(dr, "_HTTP_TIMEOUT", 0.05)

        res = await dr._fetch_sonar("test query")
        # Retry fired.
        assert call_count["n"] == 2
        # Second attempt returned the real result — retry saved
        # a user-visible failure.
        assert res["ok"] is True
        assert res["text"] == "real result"

    async def test_slow_call_emits_sentry_warning(self, monkeypatch):
        """A Sonar call that succeeds but takes longer than the slow
        threshold MUST push a Sentry breadcrumb tagged
        `event=sonar_upstream_degraded`, `kind=slow_call` — so on-call
        sees upstream degradation before users see outages."""
        import services.ora_chat.deep_research as dr
        import time as _t

        captured = {}

        class _FakeScope:
            def set_tag(self, k, v): captured.setdefault("tags", {})[k] = v
            def set_context(self, k, v): captured.setdefault("ctx", {})[k] = v
            def __enter__(self): return self
            def __exit__(self, *a): return False

        class _FakeSentry:
            def push_scope(self): return _FakeScope()
            def capture_message(self, msg, level=None):
                captured["message"] = msg
                captured["level"] = level

        monkeypatch.setitem(sys.modules, "sentry_sdk", _FakeSentry())

        # Deliberately slow one_shot — over threshold.
        monkeypatch.setattr(dr, "_SONAR_SLOW_THRESHOLD_S", 0.05)
        monkeypatch.setattr(dr, "_HTTP_TIMEOUT", 5.0)

        async def _slow_one_shot(*a, **kw):
            import asyncio
            await asyncio.sleep(0.1)
            return ("ok", {"input_tokens": 1, "output_tokens": 1}, None)

        monkeypatch.setattr(dr, "one_shot", _slow_one_shot)
        monkeypatch.setattr(dr, "resolve", lambda _: {
            "model": "test", "temperature": 0, "top_p": 1,
            "presence_penalty": 0, "max_tokens": 100,
        })
        monkeypatch.setattr(dr.cost_tracker, "compute_cost_usd",
                             lambda *a, **kw: 0.0)

        res = await dr._fetch_sonar("some query")
        assert res["ok"] is True
        assert captured.get("tags", {}).get("event") \
            == "sonar_upstream_degraded"
        assert captured["tags"]["kind"] == "slow_call"
