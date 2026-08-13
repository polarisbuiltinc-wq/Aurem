"""
Iter 388s — Bug 18 root fix regression.

Bug 18 (P0): my earlier Bug 12 patch only changed the LOOP TEMPLATE
wording ("send same prompt again" → "rephrase with specific slice").
Founder QA proved the root cause was untouched: query-tier still
exhausted iters without producing text, and the fallback still asked
the founder to rephrase instead of delivering SOMETHING useful.

Root fix: `invocations` now carries the actual tool `result` payload
(trimmed to a shallow whitelist), and `_synthesise_max_iters_summary`
inlines the substantive content — file bodies, web-search summaries,
fetched URL bodies — in the fallback so the founder gets a real,
usable answer even when max_iters trips.

These tests exercise the shape contract of the fallback under three
realistic invocation transcripts.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_orchestrator_stores_result_in_invocation_entry():
    """The `entry` dict populated per tool call must reserve a
    `result` slot (populated after the tool returns).  Without this,
    the synthesiser has no material to inline."""
    src = Path("/app/backend/services/orchestrator.py").read_text()
    # Marker + slot.
    assert 'Iter 388s — Bug 18 root fix' in src
    # `entry["result"] = ...` after the tool returns.
    assert 'entry["result"] = {' in src
    # The whitelisted result fields must all be surfaced.
    for k in ("content", "text", "results", "summary", "answer",
              "files", "sources", "matches"):
        assert f'"{k}":' in src


def test_synth_inlines_file_body_from_read_repo_file_result():
    from services.orchestrator import _synthesise_max_iters_summary
    msg = _synthesise_max_iters_summary(
        "Read backend/routers/health.py",
        [{
            "tool": "read_repo_file",
            "args": {"path": "backend/routers/health.py"},
            "result": {
                "ok": True,
                "content": "@router.get('/health')\ndef health():\n    return {'ok': True}\n",
            },
        }],
    )
    # File body must be inlined.
    assert "backend/routers/health.py" in msg
    assert "@router.get('/health')" in msg
    # No more "rephrase your question" fallback when we have content.
    assert "Rephrase" not in msg
    # Never the banned Bug 12 phrase.
    assert "send the same prompt again" not in msg.lower()


def test_synth_inlines_web_search_summary_and_hits():
    from services.orchestrator import _synthesise_max_iters_summary
    msg = _synthesise_max_iters_summary(
        "Search the web for 'FastAPI SSE 2026 best practices'",
        [{
            "tool": "web_search",
            "args": {"query": "FastAPI SSE 2026 best practices"},
            "result": {
                "ok": True,
                "summary": "FastAPI supports SSE via StreamingResponse with text/event-stream.",
                "results": [
                    {"title": "FastAPI SSE tutorial",
                     "url": "https://x.com/1",
                     "snippet": "Use StreamingResponse with a generator."},
                    {"title": "Backpressure patterns",
                     "url": "https://x.com/2",
                     "snippet": "yield frames one at a time; flush explicitly."},
                ],
            },
        }],
    )
    assert "FastAPI supports SSE" in msg
    assert "FastAPI SSE tutorial" in msg
    assert "Backpressure patterns" in msg
    # Never "rephrase" when we have search results.
    assert "Rephrase" not in msg


def test_synth_inlines_fetch_url_body():
    from services.orchestrator import _synthesise_max_iters_summary
    msg = _synthesise_max_iters_summary(
        "Fetch fastapi.tiangolo.com",
        [{
            "tool": "fetch_url",
            "args": {"url": "https://fastapi.tiangolo.com/tutorial/"},
            "result": {
                "ok": True,
                "text": "FastAPI is a modern, fast web framework for Python 3.9+ ...",
            },
        }],
    )
    assert "https://fastapi.tiangolo.com/tutorial/" in msg
    assert "FastAPI is a modern" in msg
    assert "Rephrase" not in msg


def test_synth_still_falls_back_gracefully_when_result_empty():
    """Guard: when tools ran but every result was empty, we still
    return an actionable message (no infinite-loop invitation)."""
    from services.orchestrator import _synthesise_max_iters_summary
    msg = _synthesise_max_iters_summary(
        "read x",
        [{
            "tool": "read_repo_file",
            "args": {"path": "x.py"},
            "result": {"ok": False, "content": None, "error": "not_found"},
        }],
    )
    # Path was named — must appear.
    assert "x.py" in msg
    # And still not the banned loop phrase.
    assert "send the same prompt again" not in msg.lower()


def test_synth_zero_invocations_still_works():
    """Guard: legacy call sites that pre-date the result field must
    still work (Bug 12 test file uses this shape)."""
    from services.orchestrator import _synthesise_max_iters_summary
    msg = _synthesise_max_iters_summary("blah", [])
    assert "rephrase" in msg.lower() or "specific" in msg.lower()
