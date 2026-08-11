"""
Phase 3 · Batch 8b — HTTP wrapper migration pinning test.

Scope (2026-02-12, post Batch 8a verified live):
  • routers/github_oauth.py::_gh_primary_email — 1 site (solo)

Why solo: this is the ONE migration where a silent failure has a
direct user-facing consequence — the GitHub OAuth SIGNUP flow.
Line 357 calls `_gh_primary_email` to recover a verified email
address when GitHub's public /user endpoint returned null (user
kept email private). If ExternalCallError propagates past the
existing try/except, the entire OAuth callback 500s and login
breaks for that class of users.

The contract that MUST survive migration:
  1. Function returns Optional[str] — never raises
  2. On ANY failure (network, timeout, non-2xx, ExternalCallError
     from wrapper, breaker-open, JSON parse fail) — returns None
     and logs a warning
  3. Graceful degrade: caller at line 357 checks the return value;
     None means "couldn't get a private email, proceed with what
     we have" — the signup flow does NOT depend on this succeeding
"""

import asyncio
import pytest


def test_gh_primary_email_migrated_to_ext_client():
    """Migration confirmation: uses ext_client('github') with 10s timeout."""
    src = open("/app/backend/routers/github_oauth.py").read()
    assert "from services.http import ext_client" in src
    assert 'ext_client("github", timeout=httpx.Timeout(10.0))' in src, (
        "_gh_primary_email must go through ext_client('github', 10s). "
        "Raw httpx.AsyncClient(timeout=10) migration incomplete."
    )
    # Raw client construction gone.
    assert "httpx.AsyncClient(timeout=10)" not in src


def test_gh_primary_email_still_swallows_all_exceptions():
    """The broad `except Exception` MUST survive the migration.
    Without it, ExternalCallError (which the wrapper raises on
    breaker-open + retry-exhaust) would propagate to the OAuth
    callback handler and 500 the entire login flow."""
    src = open("/app/backend/routers/github_oauth.py").read()
    # Locate the function block.
    func_start = src.find("async def _gh_primary_email")
    assert func_start > 0, "function _gh_primary_email missing"
    # Find the next `async def` OR module end to bound the function.
    next_def = src.find("\nasync def ", func_start + 1)
    if next_def < 0:
        next_def = src.find("\ndef ", func_start + 1)
    if next_def < 0:
        next_def = len(src)
    body = src[func_start:next_def]

    # The broad except must still be there.
    assert "except Exception" in body, (
        "The broad `except Exception` in _gh_primary_email is the "
        "load-bearing guard against ExternalCallError propagating "
        "to the OAuth callback. Restore it immediately if a future "
        "refactor tightened the exception filter."
    )
    # And the function must still return None on fall-through
    # (the caller at line 357 uses `if not gh_email: gh_email = ...`).
    assert "return None" in body


def test_gh_primary_email_docstring_records_the_batch_8b_contract():
    """The docstring must explicitly note the graceful-degrade
    contract so a future refactor reads it before rewriting."""
    src = open("/app/backend/routers/github_oauth.py").read()
    func_start = src.find("async def _gh_primary_email")
    next_def = src.find("\nasync def ", func_start + 1)
    body = src[func_start:next_def]
    # Must reference the contract in words a maintainer will read.
    assert "graceful-degrade" in body.lower() or \
           "returns None" in body or \
           "raise here would 500" in body, (
        "_gh_primary_email's docstring must record the contract "
        "(graceful degrade, returns None on failure) so a future "
        "'clean up' refactor doesn't remove the exception swallow."
    )


@pytest.mark.asyncio
async def test_gh_primary_email_returns_none_on_external_call_error(monkeypatch):
    """Runtime behavior — the real function must return None (not
    raise) when the wrapper raises ExternalCallError. Simulates a
    breaker-open condition."""
    from routers import github_oauth
    from services.http.client import ExternalCallError

    class _CtxRaisesOnGet:
        async def __aenter__(self):
            class _C:
                async def get(self_inner, *a, **kw):
                    raise ExternalCallError(
                        "github", "simulated breaker-open for test",
                        status=None, method="GET",
                        url="https://api.github.com/user/emails",
                    )
            return _C()

        async def __aexit__(self, *a):
            return False

    def _fake_ext_client(*args, **kwargs):
        return _CtxRaisesOnGet()

    monkeypatch.setattr(github_oauth, "ext_client", _fake_ext_client)
    result = await github_oauth._gh_primary_email("fake-token")
    assert result is None, (
        "_gh_primary_email must return None (not raise) when the "
        "HTTP wrapper raises ExternalCallError. If it raises, the "
        "GitHub OAuth callback 500s and login breaks."
    )


@pytest.mark.asyncio
async def test_gh_primary_email_returns_none_on_raise_for_status(monkeypatch):
    """Also simulate a 4xx from GitHub (bad token, missing scope) —
    raise_for_status fires HTTPStatusError which must ALSO be
    swallowed."""
    from routers import github_oauth
    import httpx

    class _FakeResp:
        status_code = 401
        def raise_for_status(self):
            raise httpx.HTTPStatusError(
                "401 unauthorized", request=None, response=None,
            )
        def json(self):
            return []

    class _CtxReturns401:
        async def __aenter__(self):
            class _C:
                async def get(self_inner, *a, **kw):
                    return _FakeResp()
            return _C()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(github_oauth, "ext_client",
                        lambda *a, **kw: _CtxReturns401())
    result = await github_oauth._gh_primary_email("bad-token")
    assert result is None


@pytest.mark.asyncio
async def test_gh_primary_email_happy_path_returns_verified_primary(monkeypatch):
    """Positive test — with a well-formed GitHub response, the
    function returns the primary+verified email."""
    from routers import github_oauth

    class _FakeResp:
        status_code = 200
        def raise_for_status(self):
            return None
        def json(self):
            return [
                {"email": "backup@x.io",  "primary": False, "verified": True},
                {"email": "main@x.io",    "primary": True,  "verified": True},
                {"email": "unverified@x", "primary": False, "verified": False},
            ]

    class _Ctx:
        async def __aenter__(self):
            class _C:
                async def get(self_inner, *a, **kw):
                    return _FakeResp()
            return _C()
        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(github_oauth, "ext_client", lambda *a, **kw: _Ctx())
    result = await github_oauth._gh_primary_email("good-token")
    assert result == "main@x.io"


def test_batch_8b_scope_is_ONLY_gh_primary_email():
    """Guard: this batch touches ONLY _gh_primary_email in
    github_oauth.py. Other files in Batch 8b's DEFERRED list
    (codebase_health.py, github_api_writer.py) must NOT have
    been migrated in the same push."""
    ch = open("/app/backend/routers/codebase_health.py").read()
    # codebase_health.py's deliberate 3-value timeout tuple must
    # STILL be present — its migration is a separate mini-batch.
    assert "httpx.Timeout(45.0, connect=6.0, read=15.0)" in ch, (
        "codebase_health.py's (45s, 6s, 15s) tuple was accidentally "
        "removed. That's a SEPARATE mini-batch. Revert."
    )
