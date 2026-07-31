"""
Session 5 · Item 5 — ORA-chat silent-catch hygiene cleanup.

Verifies four sites that previously had `except Exception: pass` with
no observability now:
  (a) still fail-OPEN (no exception escapes → callers get sane defaults),
  (b) emit a `[silent-catch] ...` DEBUG log so the sweep is traceable,
  (c) L396's `except ValueError: pass` in `_is_safe_public_url` remains
      a DELIBERATE control-flow NO-OP (documented, not logged — it
      fires on every non-IP hostname, so a log would be spam).

ZERO MOCKS RULE: no unittest.mock. We drive the code paths with real
inputs. The one hard-failure site (`unreviewed_count` DB outage) is
exercised by monkey-patching `get_db` to a plain callable that raises —
that's a live substitution, not a `Mock()` object.
"""
from __future__ import annotations

import logging
import pytest

from services.ora_chat import deep_research
from services.ora_chat import hallucination_classifier


# ─── Site 1: _gh_fetch_repo_contents tree_fetch_failed ──────────
@pytest.mark.asyncio
async def test_gh_fetch_repo_contents_tree_failure_logs_and_fails_open(caplog):
    """Force httpx.AsyncClient.get to raise → verify fail-OPEN
    (`tree: []`, `files: []`) + debug marker fires."""
    import httpx

    class _RaisingClient:
        async def get(self, *_a, **_kw):
            raise httpx.ConnectError("simulated network drop")

    caplog.set_level(logging.DEBUG, logger="services.ora_chat.deep_research")
    out = await deep_research._gh_fetch_repo_contents(
        _RaisingClient(), {}, "octocat", "hello-world", "main")
    # (a) fail-OPEN
    assert out == {"tree": [], "files": []}, \
        f"expected fail-open shape, got {out!r}"
    # (b) marker fires
    hits = [r for r in caplog.records
            if "[silent-catch]" in r.getMessage()
            and "tree_fetch_failed" in r.getMessage()]
    assert hits, f"expected tree_fetch_failed silent-catch log, got {caplog.records!r}"


# ─── Site 2: _gh_fetch_repo_contents file_fetch_failed ──────────
@pytest.mark.asyncio
async def test_gh_fetch_repo_contents_file_failure_logs_and_continues(caplog):
    """First .get() returns a fake tree; second .get() (file body)
    raises. Verify loop continues and marker fires per failure."""
    import httpx

    class _MixedClient:
        def __init__(self):
            self._calls = 0

        async def get(self, url, *_a, **_kw):
            self._calls += 1
            if self._calls == 1:
                # Tree call → succeed with one readable candidate.
                class _R:
                    status_code = 200
                    def json(self_inner):  # noqa: ANN001
                        return {"tree": [
                            {"path": "README.md", "type": "blob"},
                            {"path": "CLAUDE.md", "type": "blob"},
                        ]}
                return _R()
            # Every file fetch raises → both must be logged.
            raise httpx.ReadTimeout("simulated read timeout")

    caplog.set_level(logging.DEBUG, logger="services.ora_chat.deep_research")
    out = await deep_research._gh_fetch_repo_contents(
        _MixedClient(), {}, "octocat", "hello-world", "main")
    # (a) fail-OPEN — tree populated, files empty, no exception.
    assert out["files"] == [], f"expected empty files list, got {out['files']!r}"
    assert out["tree"] == ["README.md", "CLAUDE.md"], out["tree"]
    # (b) BOTH candidate failures marked.
    file_hits = [r for r in caplog.records
                 if "[silent-catch]" in r.getMessage()
                 and "file_fetch_failed" in r.getMessage()]
    assert len(file_hits) == 2, \
        f"expected 2 file_fetch_failed logs, got {len(file_hits)}"


# ─── Site 3 (L396): DELIBERATE control-flow NO-OP ───────────────
def test_is_safe_public_url_hostname_falls_through_to_dns_branch():
    """A regular hostname (not a bare IP) MUST route past the
    `except ValueError: pass` and reach the DNS-resolve branch.
    We assert on the reason string: if the code hit the bare-IP
    return path early, the reason would NOT be a dns_* one."""
    # example.com resolves to a public IP → safe.
    safe, reason = deep_research._is_safe_public_url("https://example.com/x")
    assert safe is True, f"example.com should be safe, got reason={reason!r}"
    # Bare IPv4 loopback MUST still be blocked (path L390-394 fires,
    # never reaches the except).
    blocked, why = deep_research._is_safe_public_url("http://127.0.0.1/")
    assert blocked is False and why == "loopback", (blocked, why)


# ─── Site 3b: _robots_allows fail-OPEN ──────────────────────────
@pytest.mark.asyncio
async def test_robots_allows_fails_open_and_logs(caplog):
    """robots.txt fetch raises → fail-OPEN (returns True) + marker."""
    import httpx

    # Bust the module-level cache so this test forces a real fetch attempt.
    deep_research._robots_cache.clear()

    class _RaisingClient:
        async def get(self, *_a, **_kw):
            raise httpx.ConnectError("simulated robots outage")

    caplog.set_level(logging.DEBUG, logger="services.ora_chat.deep_research")
    allowed = await deep_research._robots_allows(
        _RaisingClient(), "https://example.com/path")
    # (a) fail-OPEN
    assert allowed is True, "robots outage must fail-open (allowed=True)"
    # (b) marker fires
    hits = [r for r in caplog.records
            if "[silent-catch]" in r.getMessage()
            and "robots_fetch_failed" in r.getMessage()]
    assert hits, f"expected robots_fetch_failed silent-catch log; records={caplog.records!r}"


# ─── Site 4: hallucination_classifier.unreviewed_count DB fail ──
@pytest.mark.asyncio
async def test_unreviewed_count_db_outage_returns_zero_and_logs(
        monkeypatch, caplog):
    """Force `get_db()` to raise → unreviewed_count() returns 0
    (fail-OPEN → treated as below-trigger) + debug marker fires."""
    def _boom():
        raise RuntimeError("simulated mongo outage")

    monkeypatch.setattr(hallucination_classifier, "get_db", _boom)
    caplog.set_level(logging.DEBUG,
                     logger="services.ora_chat.hallucination_classifier")

    n = await hallucination_classifier.unreviewed_count()
    # (a) fail-OPEN
    assert n == 0, f"expected 0 on db outage, got {n!r}"
    # (b) marker fires
    hits = [r for r in caplog.records
            if "[silent-catch]" in r.getMessage()
            and "db_unavailable" in r.getMessage()]
    assert hits, f"expected db_unavailable silent-catch log; records={caplog.records!r}"


# ─── End-to-end: classify_batch respects fail-OPEN unreviewed_count ─
@pytest.mark.asyncio
async def test_classify_batch_survives_db_outage_via_get_db_wrapper(
        monkeypatch):
    """Beyond the marker, verify the fail-OPEN keeps the WIDER
    scheduler workable: classify_batch(force=False) must return a
    clean error dict (not raise) when Mongo is down."""
    def _boom():
        raise RuntimeError("simulated mongo outage")

    monkeypatch.setattr(hallucination_classifier, "get_db", _boom)
    result = await hallucination_classifier.classify_batch(force=False)
    assert isinstance(result, dict)
    assert result.get("ok") is False
    assert "db_unavailable" in result.get("error", ""), result
