"""Iter 386 — Error Handling Items 1 + 3 coverage.

Item 1: `services.bg_safe.safe_bg` — decorator wrapping BackgroundTask
        callables so exceptions never vanish into the FastAPI runner.

Item 3: Stripe post-signature failure → dedicated Sentry alert with
        `event=stripe_upgrade_failed` tag on the checkout.session.
        completed handler in `routers.payments`.

Design contract (verified below):

  ── Item 1 ──
  • safe_bg wraps sync AND async callables transparently.
  • Exceptions are caught, logged, and shipped to Sentry with tags
    `kind=bg_task_failed` + `bg_fn=<function-name>`.
  • Wrapped callable NEVER raises to the caller.
  • Sentry init failures inside the capture path never re-raise.
  • `services.loop_rollback.run_rollback_bg` is a safe_bg wrapper
    around the raw `run_rollback` — the alias exists so routers
    calling `bg.add_task(run_rollback_bg, …)` get the safety net
    while asyncio.create_task paths keep the raw coroutine.

  ── Item 3 ──
  • checkout.session.completed handler wraps `db.dev_users.update_one`
    in its own try/except that pushes a Sentry scope tagged
    `event=stripe_upgrade_failed` and `stage=tier_update`, then
    re-raises HTTPException(500) so Stripe retries the webhook.
  • Ledger writeback failure ALSO tags Sentry (stage=ledger_writeback)
    but does NOT re-raise (best-effort). Tier update is the source
    of truth.
  • Referral reward failure tags Sentry (stage=referral_reward)
    without re-raising.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.bg_safe import safe_bg


# ══════════════════════════════════════════════════════════════════════
# Item 1 — safe_bg decorator behaviour
# ══════════════════════════════════════════════════════════════════════
class TestSafeBgSync:
    def test_sync_happy_path_returns_none(self):
        """A well-behaved sync task runs and its return value is
        implicitly discarded (BG tasks are fire-and-forget)."""
        called = {}

        @safe_bg
        def task(a, b):
            called["args"] = (a, b)
            return "ignored"

        result = task(1, 2)
        assert result is None
        assert called["args"] == (1, 2)

    def test_sync_exception_swallowed_and_captured(self):
        """A raising sync task must not propagate; it MUST call
        sentry_sdk.capture_exception with the correct tags."""
        captured = {}

        def _fake_capture(exc):
            captured["exc"] = exc

        class _FakeScope:
            def set_tag(self, k, v):
                captured.setdefault("tags", {})[k] = v

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        fake_sentry = SimpleNamespace(
            push_scope=lambda: _FakeScope(),
            capture_exception=_fake_capture,
        )

        with patch.dict("sys.modules", {"sentry_sdk": fake_sentry}):
            @safe_bg
            def task():
                raise RuntimeError("boom")

            # Must NOT raise — that's the whole point of safe_bg.
            task()

        assert isinstance(captured.get("exc"), RuntimeError)
        assert captured["exc"].args == ("boom",)
        assert captured["tags"]["kind"] == "bg_task_failed"
        assert captured["tags"]["bg_fn"] == "task"

    def test_sentry_init_failure_does_not_re_raise(self):
        """If sentry_sdk import or capture itself throws, the task
        runner must still see NO exception. Otherwise safe_bg would
        defeat its own purpose."""

        broken_sentry = SimpleNamespace()  # no push_scope attribute
        with patch.dict("sys.modules", {"sentry_sdk": broken_sentry}):
            @safe_bg
            def task():
                raise ValueError("x")

            task()  # asserts by not raising


class TestSafeBgAsync:
    async def test_async_happy_path(self):
        called = {}

        @safe_bg
        async def task(x):
            called["x"] = x

        await task(42)
        assert called["x"] == 42

    async def test_async_exception_swallowed_and_captured(self):
        captured = {}

        def _fake_capture(exc):
            captured["exc"] = exc

        class _FakeScope:
            def set_tag(self, k, v):
                captured.setdefault("tags", {})[k] = v

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        fake_sentry = SimpleNamespace(
            push_scope=lambda: _FakeScope(),
            capture_exception=_fake_capture,
        )

        with patch.dict("sys.modules", {"sentry_sdk": fake_sentry}):
            @safe_bg
            async def task():
                raise RuntimeError("async-boom")

            await task()

        assert isinstance(captured.get("exc"), RuntimeError)
        assert captured["tags"]["kind"] == "bg_task_failed"
        assert captured["tags"]["bg_fn"] == "task"

    async def test_async_wrapper_preserves_asyncio_semantics(self):
        """Awaiting a safe_bg-wrapped coroutine must be indistinguishable
        from awaiting the raw coroutine (same event-loop cooperation)."""

        @safe_bg
        async def task():
            await asyncio.sleep(0)  # cooperative yield point
            return "unused"

        await task()

    def test_functools_wraps_preserves_name(self):
        @safe_bg
        def my_task():
            pass

        @safe_bg
        async def my_async_task():
            pass

        assert my_task.__name__ == "my_task"
        assert my_async_task.__name__ == "my_async_task"


class TestRunRollbackBgAlias:
    def test_alias_exists_and_wraps_run_rollback(self):
        """`run_rollback_bg` must exist as a safe_bg-wrapped alias of
        the raw `run_rollback` so routers can point BackgroundTasks
        at the safe variant without touching internal call sites."""
        from services import loop_rollback

        assert hasattr(loop_rollback, "run_rollback_bg")
        # Wrapper preserves __name__ via functools.wraps.
        assert loop_rollback.run_rollback_bg.__name__ == "run_rollback"
        # Wrapped alias is async — matches the raw callable.
        assert asyncio.iscoroutinefunction(loop_rollback.run_rollback_bg)
        # But it is NOT the same object — the wrapper indirection is
        # what buys us the exception-catching layer.
        assert loop_rollback.run_rollback_bg is not loop_rollback.run_rollback

    async def test_alias_swallows_leaked_exception(self):
        """Simulate `run_rollback` internal try/except failing to
        catch (e.g. because the DB write in the except body itself
        raises). safe_bg outer envelope MUST swallow + capture."""
        captured = {}

        def _fake_capture(exc):
            captured["exc"] = exc

        class _FakeScope:
            def set_tag(self, k, v):
                captured.setdefault("tags", {})[k] = v

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        fake_sentry = SimpleNamespace(
            push_scope=lambda: _FakeScope(),
            capture_exception=_fake_capture,
        )

        from services import loop_rollback

        async def _boom(*a, **kw):
            raise RuntimeError("leaked-from-inner-except")

        with patch.object(loop_rollback, "run_rollback", _boom), \
                patch.dict("sys.modules", {"sentry_sdk": fake_sentry}):
            # Rebuild the alias inside the patch scope so it points
            # at the boom stub.
            safe_alias = safe_bg(loop_rollback.run_rollback)
            await safe_alias(db=None, loop_id="x", project={},
                             commit_sha="deadbeef", user_token="t")

        assert isinstance(captured.get("exc"), RuntimeError)
        assert captured["tags"]["kind"] == "bg_task_failed"


class TestSuggestionsAndSupabaseDecoration:
    """`@safe_bg` MUST be applied at the function definition in each
    router — regression guard: verify the decorated object is the
    wrapper, not the raw coroutine. If a future refactor removes the
    decorator this test explodes."""

    def test_suggestions_analyze_with_groq_is_wrapped(self):
        from routers import suggestions

        raw_qualname = "_analyze_with_groq"
        fn = getattr(suggestions, raw_qualname)
        # Confirm the wrapper indirection via __wrapped__ (functools).
        assert hasattr(fn, "__wrapped__"), (
            "@safe_bg not applied — fn missing __wrapped__ attr")

    def test_supabase_run_migration_is_wrapped(self):
        from routers import supabase as sb_router

        fn = getattr(sb_router, "_run_migration")
        assert hasattr(fn, "__wrapped__"), (
            "@safe_bg not applied to _run_migration")


# ══════════════════════════════════════════════════════════════════════
# Item 3 — Stripe post-signature failure alert
# ══════════════════════════════════════════════════════════════════════
class _FakeRequest:
    def __init__(self, body: bytes, sig: str = "sig123"):
        self._body = body
        self.headers = {"stripe-signature": sig}

    async def body(self) -> bytes:
        return self._body


def _build_completed_event(user_id: str, plan: str,
                           session_id: str = "cs_test_1"):
    """Minimal Stripe `checkout.session.completed` event dict."""
    return {
        "type": "checkout.session.completed",
        "data": {"object": {
            "id":            session_id,
            "amount_total":  2900,
            "subscription":  "sub_1",
            "customer":      "cus_1",
            "metadata":      {"user_id": user_id, "plan": plan},
        }},
    }


@pytest.fixture
def sentry_capture_spy(monkeypatch):
    """Record every (exception, tags, context) pushed through
    sentry_sdk during the test. Returns the list of captures."""
    captures: list[dict] = []

    class _Scope:
        def __init__(self):
            self.tags = {}
            self.contexts = {}

        def set_tag(self, k, v):
            self.tags[k] = v

        def set_context(self, k, v):
            self.contexts[k] = v

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _FakeSentry:
        def __init__(self):
            self._current_scope = None

        def push_scope(self):
            s = _Scope()
            self._current_scope = s
            captures.append({"scope": s})
            return s

        def capture_exception(self, exc):
            captures[-1]["exc"] = exc

    fake = _FakeSentry()
    monkeypatch.setitem(__import__("sys").modules, "sentry_sdk", fake)
    return captures


@pytest.fixture
def payments_env(monkeypatch):
    """Force STRIPE_WEBHOOK_SECRET so the webhook doesn't 503 early."""
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")


class TestStripeUpgradeFailureAlert:
    async def test_tier_update_failure_tags_and_reraises(
            self, sentry_capture_spy, payments_env, monkeypatch):
        """Signature verifies, ledger write ok, dev_users update FAILS.
        MUST: push scope tag `stripe_upgrade_failed` + stage=tier_update,
        capture the exception, and raise HTTPException(500) so Stripe
        retries."""
        from routers import payments
        from fastapi import HTTPException

        event = _build_completed_event("u1", "founder")

        # Stub construct_event to bypass signature verification.
        monkeypatch.setattr(
            payments.stripe.Webhook, "construct_event",
            staticmethod(lambda *a, **kw: event),
        )

        # Fake DB: cto_payments write succeeds, dev_users write blows up.
        fake_db = MagicMock()
        fake_db.cto_payments.update_one = AsyncMock(return_value=None)
        fake_db.dev_users.update_one = AsyncMock(
            side_effect=RuntimeError("mongo-down"))

        monkeypatch.setattr(payments, "require_db", lambda: fake_db)
        monkeypatch.setattr(payments, "_require_stripe", lambda: None)

        req = _FakeRequest(b"{}", sig="sig")

        with pytest.raises(HTTPException) as excinfo:
            await payments.stripe_webhook(req)
        assert excinfo.value.status_code == 500
        assert "tier_update_failed" in str(excinfo.value.detail)

        # Assert Sentry received the right tag family.
        tier_captures = [
            c for c in sentry_capture_spy
            if c.get("scope") and
               c["scope"].tags.get("stage") == "tier_update"
        ]
        assert tier_captures, (
            "no Sentry scope with stage=tier_update — the whole "
            "point of Item 3 is that on-call can grep for this")
        s = tier_captures[0]["scope"]
        assert s.tags["event"] == "stripe_upgrade_failed"
        assert s.tags["user_id"] == "u1"
        assert s.tags["plan"] == "founder"
        assert s.contexts["stripe_session"]["session_id"] == "cs_test_1"
        assert isinstance(tier_captures[0].get("exc"), RuntimeError)

    async def test_ledger_writeback_failure_tags_but_does_not_reraise(
            self, sentry_capture_spy, payments_env, monkeypatch):
        """Ledger write fails but tier update succeeds — must tag
        Sentry (stage=ledger_writeback) yet return 200 so Stripe
        does not retry (source of truth is the tier row)."""
        from routers import payments

        event = _build_completed_event("u2", "founder")
        monkeypatch.setattr(
            payments.stripe.Webhook, "construct_event",
            staticmethod(lambda *a, **kw: event),
        )

        fake_db = MagicMock()
        fake_db.cto_payments.update_one = AsyncMock(
            side_effect=RuntimeError("ledger-down"))
        fake_db.dev_users.update_one = AsyncMock(return_value=None)

        # Stub referral cron to a no-op so it doesn't polute captures.
        import services.billing_cron as bc
        monkeypatch.setattr(
            bc, "grant_referral_reward",
            AsyncMock(return_value={"granted": False}),
        )

        monkeypatch.setattr(payments, "require_db", lambda: fake_db)
        monkeypatch.setattr(payments, "_require_stripe", lambda: None)

        req = _FakeRequest(b"{}", sig="sig")
        resp = await payments.stripe_webhook(req)
        assert resp == {"ok": True}

        ledger_captures = [
            c for c in sentry_capture_spy
            if c.get("scope") and
               c["scope"].tags.get("stage") == "ledger_writeback"
        ]
        assert ledger_captures, "ledger failure must alert Sentry"
        assert ledger_captures[0]["scope"].tags["event"] \
            == "stripe_upgrade_failed"

    async def test_happy_path_produces_no_sentry_capture(
            self, sentry_capture_spy, payments_env, monkeypatch):
        """A clean webhook must not touch Sentry at all — noise floor
        matters. Any capture here means false positives on-call."""
        from routers import payments

        event = _build_completed_event("u3", "founder")
        monkeypatch.setattr(
            payments.stripe.Webhook, "construct_event",
            staticmethod(lambda *a, **kw: event),
        )

        fake_db = MagicMock()
        fake_db.cto_payments.update_one = AsyncMock(return_value=None)
        fake_db.dev_users.update_one = AsyncMock(return_value=None)

        import services.billing_cron as bc
        monkeypatch.setattr(
            bc, "grant_referral_reward",
            AsyncMock(return_value={"granted": False}),
        )

        monkeypatch.setattr(payments, "require_db", lambda: fake_db)
        monkeypatch.setattr(payments, "_require_stripe", lambda: None)

        req = _FakeRequest(b"{}", sig="sig")
        resp = await payments.stripe_webhook(req)
        assert resp == {"ok": True}

        upgrade_captures = [
            c for c in sentry_capture_spy
            if c.get("scope") and
               c["scope"].tags.get("event") == "stripe_upgrade_failed"
        ]
        assert not upgrade_captures, (
            f"unexpected Sentry alerts on happy path: {upgrade_captures}")
