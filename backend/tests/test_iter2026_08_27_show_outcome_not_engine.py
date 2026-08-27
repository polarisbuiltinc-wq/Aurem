"""Regression — "Show the Outcome, Never the Engine" P0b + P0c (2026-08-27).

P0b: a missing-argument TypeError classifies as INTERNAL_CALL_ERROR (AUREM's
own bug), never SCHEMA_MISMATCH (implies bad user data) — and the rendered
message for an INTERNAL error never tells the user to fix their profile or
account. `_fail_ship()` now routes a real exception through this translation
layer instead of leaking `f"GitHub push failed: {e}"` raw to the user.

P0c: `_narrate()` strips known internal-engine codenames/jargon before a
live-feed line reaches the user (defense-in-depth on top of fixing the
`_do_scan()` "Vanguard" call site directly).
"""
from __future__ import annotations

import asyncio

import pytest


# ── P0b — whose fault ────────────────────────────────────────────────────
class TestWhoseFault:
    def test_missing_arg_typeerror_is_internal_not_schema(self):
        from core.errors import classify_exception, ErrorCode

        def needs_two(a, b):
            pass

        try:
            needs_two(1)
        except TypeError as e:
            assert classify_exception(e) == ErrorCode.INTERNAL_CALL_ERROR
            assert classify_exception(e) != ErrorCode.SCHEMA_MISMATCH
        else:
            pytest.fail("expected TypeError")

    def test_other_typeerrors_still_schema_mismatch(self):
        """Only the missing-argument SHAPE reclassifies — a generic
        TypeError (e.g. bad operand types) stays SCHEMA_MISMATCH."""
        from core.errors import classify_exception, ErrorCode

        try:
            1 + "x"
        except TypeError as e:
            assert classify_exception(e) == ErrorCode.SCHEMA_MISMATCH
        else:
            pytest.fail("expected TypeError")

    def test_never_blame_user_for_internal_error(self):
        from core.errors import build_error_envelope, ErrorCode

        def needs_two(a, b):
            pass

        try:
            needs_two(1)
        except TypeError as e:
            envelope = build_error_envelope(e)
        assert envelope["error_code"] == ErrorCode.INTERNAL_CALL_ERROR.value
        rendered = (envelope["title"] + " " + envelope["what_happened"]
                    + " " + " ".join(envelope["what_to_try"])).lower()
        for banned in ("fix your profile", "update your profile",
                       "check your profile", "fix your account",
                       "update your account", "check your account"):
            assert banned not in rendered
        # positive proof: the message says it's on AUREM, not the user
        assert "this one's on us" in envelope["title"].lower() \
            or "aurem" in envelope["what_happened"].lower()
        assert "ref_id" in envelope and envelope["ref_id"].startswith("ORA-")

    def test_internal_call_error_has_i18n_catalog_entry(self):
        from core.errors import translate_error, ErrorCode
        entry = translate_error(ErrorCode.INTERNAL_CALL_ERROR)
        assert entry.get("title")
        assert entry.get("what_happened")
        assert entry.get("what_to_try")


# ── P0b — _fail_ship no longer leaks the raw exception ─────────────────
class _Coll:
    def __init__(self):
        self.rows: list[dict] = []

    async def insert_one(self, d):
        self.rows.append(dict(d))

    async def update_one(self, q, u, upsert=False):
        pass

    async def find_one(self, q, *_a, **_kw):
        return None

    async def find_one_and_update(self, q, u, *_a, **_kw):
        return {"loop_id": "lp_test_p0b"}

    async def delete_one(self, q):
        pass


class _DB:
    def __init__(self):
        self.loop_sessions = _Coll()
        self.loop_backups = _Coll()
        self.loop_plans = _Coll()
        self.loop_lock = _Coll()
        self.loop_failures = _Coll()
        self.cto_projects = _Coll()


class TestFailShipNoRawLeak:
    def test_confirm_ship_typeerror_produces_friendly_message_with_ref_id(self):
        from unittest.mock import AsyncMock, patch
        from services import loop_engine as le

        eng = le.LoopEngine(db=_DB(), loop_id="lp_test_p0b", user_id="u1",
                            project_id="p1", user_message="ship it")
        eng.state = le.LoopState.PAUSED_FOR_USER
        eng.phase = "ship"
        eng.context["ship_pending"] = {
            "owner": "acme", "repo": "widgets", "branch": "main",
            "token": "tok",
            "files": {"app.py": "print('hi')\n"},
            "commit_message": "feat: test",
        }

        async def _raise_missing_arg(**kw):
            def commit_files(owner, repo, branch, token, files,
                              commit_message, author_name, author_email,
                              progress=None):
                pass
            commit_files(owner="acme", repo="widgets")  # deliberately missing args

        captured_narration = []
        orig_narrate = eng._narrate

        async def _spy_narrate(step, tone, text, **kw):
            captured_narration.append(text)
            return await orig_narrate(step, tone, text, **kw)
        eng._narrate = _spy_narrate

        with patch("services.pat_vault.get_repo_token_or_error",
                  AsyncMock(return_value=(None, "no_refresh", None))), \
             patch("services.github_api_writer.commit_files", _raise_missing_arg):
            asyncio.run(eng.confirm_ship(approved=True))

        assert eng.state == le.LoopState.FAILED
        rendered = eng.context["commit"]["error"]
        assert "TypeError" not in rendered
        assert "missing" not in rendered.lower()
        assert "positional argument" not in rendered
        assert eng.context["commit"].get("ref_id", "").startswith("ORA-")
        # the live-feed narration line must also be clean, not raw exception text
        joined = " ".join(captured_narration)
        assert "TypeError" not in joined
        assert "positional argument" not in joined

    def test_static_reason_call_sites_unaffected(self):
        """The 5 call sites that already pass a clean static string
        (no `exc=`) must behave byte-identically — no ref_id/error_code
        injected when there's no real exception behind the failure."""
        from services import loop_engine as le

        eng = le.LoopEngine(db=_DB(), loop_id="lp_test_p0b2", user_id="u1",
                            project_id="p1", user_message="ship it")
        asyncio.run(eng._fail_ship("Submitted files were empty — nothing valid to commit."))
        assert eng.context["commit"]["error"] == (
            "Submitted files were empty — nothing valid to commit."
        )
        assert eng.context["commit"]["ref_id"] is None


# ── P0c — live-feed narration strips known engine codenames ────────────
class TestNarrationLeakStrip:
    def test_vanguard_is_deliberately_not_stripped_public_feature_name(self):
        """2026-08-27 correction — "Vanguard" is the product's own
        public, marketed feature name (landing page: "Vanguard
        Security", "Vanguard 2.0"; the chat UI itself says "Vanguard
        active"). It is NOT an internal codename and must NOT be
        stripped — a prior pass in this same session mistakenly
        stripped it; this test guards against that regressing."""
        from services.loop_engine import _strip_engine_leak_tokens
        out = _strip_engine_leak_tokens("Running Vanguard security scan")
        assert out == "Running Vanguard security scan"

    def test_do_scan_call_site_still_says_vanguard(self):
        import os
        backend = os.path.dirname(os.path.dirname(__file__))
        src = open(os.path.join(backend, "services", "loop_engine.py")).read()
        idx = src.find("async def _do_scan(self)")
        assert idx > -1
        region = src[idx: idx + 700]
        assert "Vanguard" in region

    def test_e2b_and_disabled_by_admin_stripped(self):
        from services.loop_engine import _strip_engine_leak_tokens
        out = _strip_engine_leak_tokens("e2b disabled by admin")
        assert "e2b" not in out.lower()
        assert "disabled by admin" not in out.lower()

    def test_narrate_applies_the_strip_for_real(self):
        from unittest.mock import AsyncMock, patch
        from services import loop_engine as le

        eng = le.LoopEngine(db=_DB(), loop_id="lp_test_p0c", user_id="u1",
                            project_id="p1", user_message="task")
        captured = {}

        async def _spy_emit(state, phase, **kw):
            captured["data"] = kw.get("data") or {}
        eng._emit = _spy_emit

        asyncio.run(eng._narrate(step="scan", tone="pending",
                                 text="Running Vanguard security scan"))
        assert "Vanguard" in captured["data"]["narration_text"]
        assert "e2b" not in captured["data"]["narration_text"]
