"""
2026-08-20 · Regression test — founder-reported production bug.

Sending a trivial/low-information message ("Testing Pro mode - what
is 2+2?") caused ORA to recall and effectively echo a completely
unrelated past answer (about fixing a Dashboard component's onRetry
bug) because the retriever's old `if s > 0` gate let ANY nonzero
TF-IDF score through — and generic filler words ("testing", "mode",
"what", "is") score nonzero against almost any past row.

This test reproduces the exact corpus shape from the incident and
asserts the weak/spurious matches are now filtered by `_MIN_SCORE`.
"""
from datetime import datetime, timezone
import pytest

import services.ora_council_retriever as r


class _AsyncCursor:
    def __init__(self, rows): self._rows = rows
    def sort(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def __aiter__(self): self._i = 0; return self
    async def __anext__(self):
        if self._i >= len(self._rows): raise StopAsyncIteration
        v = self._rows[self._i]; self._i += 1; return v


class _FakeColl:
    def __init__(self, rows): self.rows = rows
    def find(self, q=None, proj=None): return _AsyncCursor(self.rows)


class _FakeDB:
    def __init__(self, rows): self._rows = rows
    def __getitem__(self, name): return _FakeColl(self._rows)


def _row(msg, reply, mode="A", user="founder", project="p1"):
    return {
        "user_message": msg, "final_output": reply, "mode": mode,
        "user_id": user, "project_id": project,
        "pass_result": True, "lint_blocked": False,
        "timestamp": datetime.now(timezone.utc),
    }


@pytest.fixture(autouse=True)
def _reset(): r._reset_for_tests(); yield; r._reset_for_tests()


@pytest.mark.asyncio
async def test_trivial_query_does_not_recall_unrelated_dashboard_answer():
    rows = [
        _row(
            "Testing the retry mode for the Dashboard component onRetry function",
            "Root cause: The BodyStreamBuffer was aborted during a retry "
            "attempt in the Dashboard component.",
        ),
        _row("How do I test my Pro mode subscription flow",
             "Switch tier to pro in admin panel and verify features unlock."),
        _row("What is the status of my test suite",
             "Run pytest to check test mode results."),
        _row("How can I test if the server is running",
             "Curl the /health endpoint."),
        _row("Is my project mode set to production or preview",
             "Check ENVIRONMENT env var in .env."),
    ]
    db = _FakeDB(rows)
    out, n = await r.get_council_few_shot(
        db, "Testing Pro mode - what is 2+2?", mode="A", k=2,
    )
    assert out == ""
    assert n == 0
    assert "Dashboard" not in out
    assert "BodyStreamBuffer" not in out


@pytest.mark.asyncio
async def test_genuine_topical_match_still_recalled():
    # 2026-08-20 — scoped to the SAME user/project (no cross-user
    # fallback tier exists anymore); needs >= _MIN_BUCKET (20) rows.
    rows = [
        _row("How do I add a button to React?",
             "Use a <button> element with onClick handler."),
        _row("How to install Redis on docker?",
             "Run docker run -d -p 6379:6379 redis."),
        _row("React state update best practice",
             "Use useState hook with a function updater."),
        _row("Adding a button in HTML",
             "<button type='button'>Click</button>"),
        _row("React onClick handler example",
             "<button onClick={handler}>Click</button>"),
    ] + [_row(f"filler q{i}", f"filler a{i}") for i in range(15)]
    db = _FakeDB(rows)
    out, n = await r.get_council_few_shot(
        db, "add an onClick button in React", mode="A", k=2,
        user_id="founder", project_id="p1",
    )
    assert n > 0
    assert "[ORA COUNCIL — LEARNED EXAMPLES]" in out


@pytest.mark.asyncio
async def test_cross_user_fallback_removed_no_leakage():
    """2026-08-20 — founder-directed security fix. A user with zero
    history of their own must NEVER see another user's recalled
    examples, even when the global corpus easily clears the old
    cross-user thresholds and a query is a strong topical match."""
    rows = [
        _row("How do I add a button to React?",
             "Use a <button> element with onClick handler.",
             user="customer_a", project="proj_a"),
    ] * 3 + [_row(f"filler q{i}", f"filler a{i}",
                  user="customer_a", project="proj_a") for i in range(20)]
    db = _FakeDB(rows)
    # A brand-new user with NO history of their own asks a strongly
    # matching question — must get nothing, not customer_a's content.
    out, n = await r.get_council_few_shot(
        db, "How do I add a button to React?", mode="A", k=2,
        user_id="customer_b", project_id="proj_b",
    )
    assert out == ""
    assert n == 0
    assert "customer_a" not in out
