"""Session 4 · Priority-1 · ora_learning.py silent-catch surgery.

The 2 sites patched here were the *ironic* culprit of Session 4's
live P0 investigation — `maybe_log_ora_escalation()` silently
returned during the entire 24h ORA outage with zero log entries,
and no one knew for 6h that learning samples had stopped.

This test proves:
  1. Both sites now log (rate-limit @ debug, catch-all @ warning).
  2. Behaviour preserved — rate-limit still fails-open, catch-all
     still returns None (never re-raises).
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from unittest.mock import patch

import pytest


BACKEND = Path(__file__).resolve().parents[1]


# ═════════════════════════════════════════════════════════════════
# 1) Source-text guard — both patched sites use the grep prefix
# ═════════════════════════════════════════════════════════════════
def test_ora_learning_silent_catch_sites_now_log():
    src = (BACKEND / "services" / "ora_learning.py").read_text()
    # Must have a debug line for the rate-limit swallow
    assert "logger.debug(" in src and "rate-limit lookup failed" in src, \
        "rate-limit try/except must log at debug"
    # Must have a WARNING line for the top-level invariant catch-all
    assert "logger.warning(" in src and "shadow-logging invariant" in src, \
        "top-level invariant catch-all must log at warning"
    # Both messages must use the grep prefix
    assert src.count('"[silent-catch] ora_learning.py:') == 2, \
        "expected exactly 2 [silent-catch] prefix strings"


def test_no_bare_except_pass_left_in_maybe_log_ora_escalation():
    """AST-precise regression guard. The two sites we patched were
    the ONLY silent swallows in maybe_log_ora_escalation. Anyone
    reintroducing a bare `except: pass` there will fail this test.
    """
    import ast
    src = (BACKEND / "services" / "ora_learning.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "maybe_log_ora_escalation"):
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Try):
                continue
            for h in sub.handlers:
                if len(h.body) != 1:
                    continue
                s = h.body[0]
                if isinstance(s, ast.Pass):
                    pytest.fail(
                        f"maybe_log_ora_escalation has a bare "
                        f"`except: pass` at line {s.lineno}"
                    )
                if (isinstance(s, ast.Return) and s.value is None):
                    # A bare `return` in the top-level catch-all is
                    # legit (part of the invariant), but there must
                    # be a `logger.warning` above it. Check the two
                    # source lines directly above.
                    src_lines = src.splitlines()
                    look = src_lines[max(0, s.lineno - 4): s.lineno - 1]
                    assert any("logger.warning" in ln or "logger.debug" in ln
                               for ln in look), (
                        f"bare return in maybe_log_ora_escalation at line "
                        f"{s.lineno} is not preceded by a log line"
                    )


# ═════════════════════════════════════════════════════════════════
# 2) Behavioural — rate-limit swallow logs but does NOT abort
# ═════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_rate_limit_failure_logs_and_fails_open(caplog):
    """If `count_documents` raises, we must:
       • log at DEBUG with the [silent-catch] prefix, AND
       • NOT return early — proceed with the ORA call (fail-open).
    """
    import services.ora_learning as mod

    # Build a minimal db double where count_documents raises but
    # insert_one records the eventual write.
    class _Coll:
        def __init__(self):
            self.inserted = []
        async def count_documents(self, *a, **kw):
            raise RuntimeError("simulated mongo hiccup")
        async def insert_one(self, doc):
            self.inserted.append(dict(doc))
            return type("R", (), {"inserted_id": "id"})()
    class _DB:
        def __init__(self):
            self.ora_learning_logs = _Coll()

    db = _DB()

    # Force is_ora_available() → True and low-confidence detection
    # so the code path enters the rate-limit try/except.
    async def _fake_call_ora(**kwargs):
        return {"reply": "ORA's answer here"}

    with patch.object(mod, "is_ora_available", return_value=True), \
         patch.object(mod, "_detect_low_confidence",
                      return_value="detected"), \
         patch.object(mod, "call_ora", side_effect=_fake_call_ora):
        caplog.set_level(logging.DEBUG, logger=mod.logger.name)
        await mod.maybe_log_ora_escalation(
            db=db,
            user_id="u1",
            session_id="s1",
            project_id="p1",
            provider="test",
            prompt="i don't know",
            aurem_response="i cannot answer",
        )

    # 1) Rate-limit debug log fired
    assert any(
        "[silent-catch] ora_learning.py" in r.getMessage()
        and "rate-limit" in r.getMessage()
        for r in caplog.records
    ), f"expected rate-limit debug log; got: {[r.getMessage() for r in caplog.records]}"

    # 2) Behaviour preserved — the insert STILL happened (fail-open)
    assert len(db.ora_learning_logs.inserted) == 1
    saved = db.ora_learning_logs.inserted[0]
    assert saved["reason"] == "detected"
    assert saved["ora_response"] == "ORA's answer here"


# ═════════════════════════════════════════════════════════════════
# 3) Behavioural — top-level invariant catch-all logs at WARNING
# ═════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_invariant_catch_all_logs_warning_and_swallows(caplog):
    """When something in the inner body raises unexpectedly
    (e.g. `is_ora_available()` itself throws), the outer invariant
    must:
       • log at WARNING with the [silent-catch] prefix
       • return None (never re-raise into the request path)
    """
    import services.ora_learning as mod

    def _boom():
        raise RuntimeError("simulated invariant break")

    with patch.object(mod, "is_ora_available", side_effect=_boom):
        # Ensure the module logger propagates to caplog's root handler
        mod.logger.propagate = True
        with caplog.at_level(logging.WARNING, logger=mod.logger.name):
            result = await mod.maybe_log_ora_escalation(
                db=object(),   # not None → skip the early db-guard return
                user_id="u1",
                session_id="s1",
                project_id="p1",
                provider="test",
                prompt="anything",
                aurem_response="anything",
            )

    # 1) Function did not re-raise — returned None as invariant demands
    assert result is None

    # 2) WARNING log fired with the [silent-catch] prefix
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "[silent-catch] ora_learning.py" in r.getMessage()
        and "shadow-logging invariant" in r.getMessage()
        for r in warnings
    ), f"expected invariant WARNING log; got: {[(r.levelname, r.getMessage()) for r in caplog.records]}"
