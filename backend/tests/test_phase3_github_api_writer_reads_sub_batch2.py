"""
Phase 3 · Sub-batch 2 of github_api_writer migration — READ sites only.

Scope (2026-02-12, prep-only, HELD until reviewed):
  • fetch_file() — GET /contents/{path}?ref={ref}
  • _get_branch_head() — GET /git/ref + GET /git/commits/{sha}
  • _get_commit_details() — NEW helper wrapping the inline GET
    /commits/{sha} previously at line 221 of revert_commit

**Write sites (blob/tree/commit POSTs, ref-advance PATCHes) STAY
on raw httpx.AsyncClient for this sub-batch** — Sub-batch 3 will
migrate them with explicit retry opt-out.

Contract preserved by these tests (per founder direction):
  • Timeout: `httpx.Timeout(60.0)` passed EXPLICITLY at each ext_client
    site (not relying on the github dep default which is 20s read)
  • Limits: `httpx.Limits(max_connections=20, max_keepalive_connections=20)`
    passed EXPLICITLY at each ext_client site (not relying on the
    _LIMITS_DEFAULTS['github'] which happens to match — the writer
    must be robust to a future _LIMITS_DEFAULTS change)
  • Read helpers refactored to be SELF-CONTAINED (no client param) so
    they don't share pool state with the write-path client
  • Write blocks in commit_files + revert_commit still use raw
    httpx.AsyncClient(timeout=60.0, limits=_LIMITS) — no accidental
    Sub-batch 3 leakage

Per today's discovery (Sub-batch 1 hotfix): at least one runtime
test in this file MUST exercise the ext_client transport pool, not
just mock the wrapper. This catches the class of bugs that only
surface at actual httpx.AsyncClient construction time.
"""

import asyncio
import httpx
import pytest


def _read(path: str) -> str:
    return open(path).read()


# ─── Static invariants ──────────────────────────────────────────────

def test_writer_imports_ext_client():
    src = _read("/app/backend/services/github_api_writer.py")
    assert "from services.http import ext_client" in src, (
        "github_api_writer.py must import ext_client after Sub-batch 2."
    )


def test_fetch_file_uses_ext_client_with_explicit_timeout_and_limits():
    """fetch_file's ext_client site MUST pass timeout=60s and
    limits=20/20 EXPLICITLY — don't silently rely on the dep default."""
    src = _read("/app/backend/services/github_api_writer.py")
    # Locate fetch_file body.
    start = src.find("async def fetch_file(")
    assert start > 0
    end = src.find("\nasync def ", start + 1)
    body = src[start:end]
    # ext_client call must be in this function.
    assert 'ext_client(' in body and '"github"' in body, (
        "fetch_file must open its own ext_client('github', ...) for the GET."
    )
    # Timeout MUST be explicit 60s.
    assert "timeout=httpx.Timeout(60.0)" in body, (
        "fetch_file's ext_client MUST pass timeout=httpx.Timeout(60.0) "
        "explicitly. Relying on ext_client's github default (read=20s) "
        "would fail on slow contents fetches for large files."
    )
    # Limits MUST be explicit 20/20.
    assert "max_connections=20" in body and "max_keepalive_connections=20" in body, (
        "fetch_file's ext_client MUST pass limits=httpx.Limits(20/20) "
        "explicitly — not rely on _LIMITS_DEFAULTS['github'] happening "
        "to match. Future dep-defaults changes must not affect the "
        "writer's connection-pool shape."
    )


def test_get_branch_head_uses_ext_client_with_explicit_timeout_and_limits():
    """_get_branch_head's ext_client site MUST pass timeout=60s and
    limits=20/20 EXPLICITLY."""
    src = _read("/app/backend/services/github_api_writer.py")
    start = src.find("async def _get_branch_head(")
    assert start > 0
    end = src.find("\nasync def ", start + 1)
    body = src[start:end]
    assert 'ext_client(' in body and '"github"' in body
    assert "timeout=httpx.Timeout(60.0)" in body
    assert "max_connections=20" in body and "max_keepalive_connections=20" in body


def test_get_commit_details_helper_exists_and_uses_ext_client():
    """The inline GET commit at line ~221 of the OLD revert_commit MUST
    have been extracted into a named helper (`_get_commit_details` or
    similar). Sub-batch 2 introduces this helper so that the read is
    isolated + testable + uses ext_client, without touching the write
    path in revert_commit."""
    src = _read("/app/backend/services/github_api_writer.py")
    # Helper name must exist.
    assert "async def _get_commit_details(" in src, (
        "The inline `client.get(.../commits/{commit_sha})` in "
        "revert_commit at line ~221 must be extracted into a helper "
        "named `_get_commit_details(...)` so Sub-batch 2 owns it "
        "cleanly and Sub-batch 3 doesn't inherit it."
    )
    start = src.find("async def _get_commit_details(")
    end = src.find("\nasync def ", start + 1)
    body = src[start:end]
    assert 'ext_client(' in body and '"github"' in body
    assert "timeout=httpx.Timeout(60.0)" in body
    assert "max_connections=20" in body and "max_keepalive_connections=20" in body


def test_read_helpers_no_longer_take_client_param():
    """After Sub-batch 2, the three read helpers do NOT take a
    `client` param — they're self-contained. This is what lets
    commit_files/revert_commit keep their raw client scope for
    writes only, without accidentally routing reads through the
    same pool."""
    src = _read("/app/backend/services/github_api_writer.py")
    for name in ("fetch_file", "_get_branch_head", "_get_commit_details"):
        start = src.find(f"async def {name}(")
        assert start > 0, f"{name} missing"
        # Grab the signature line.
        sig_end = src.find(")", start)
        sig = src[start:sig_end + 1]
        assert "client: httpx.AsyncClient" not in sig, (
            f"{name}'s signature still takes `client: httpx.AsyncClient`. "
            f"Sub-batch 2 requires read helpers to be self-contained "
            f"(no client param) so their ext_client is isolated from "
            f"the write-path raw client."
        )


def test_write_sites_migrated_to_ext_client_post_sub_batch_3():
    """POST-SUB-BATCH-3: commit_files + revert_commit's write blocks
    MUST now use `ext_client("github", ...)` — the raw
    `httpx.AsyncClient(timeout=60.0, limits=_LIMITS)` pattern from
    Sub-batch 2 era is removed. This test was flipped from the
    transitional guard once Sub-batch 3 migrated the writes."""
    src = _read("/app/backend/services/github_api_writer.py")
    # Raw AsyncClient must be gone.
    assert "httpx.AsyncClient(" not in src, (
        "Raw httpx.AsyncClient still present in github_api_writer.py "
        "after Sub-batch 3. Writes must be on ext_client."
    )
    # At least 2 ext_client sites for writes (commit_files + revert_commit).
    # Plus 3 for Sub-batch 2 reads → ≥5 total.
    assert src.count("async with ext_client(") >= 5, (
        "Expected ≥5 ext_client sites post-Sub-batch-3 (3 read helpers "
        "+ commit_files + revert_commit)."
    )


def test_LIMITS_module_constant_removed_post_sub_batch_3():
    """POST-SUB-BATCH-3: the `_LIMITS = httpx.Limits(...)` module
    constant is removed — every ext_client site now inlines its own
    `httpx.Limits(max_connections=20, max_keepalive_connections=20)`
    so future changes to services.http.client._LIMITS_DEFAULTS cannot
    silently drift this writer's connection-pool shape. Flipped from
    the Sub-batch 2 preservation guard."""
    src = _read("/app/backend/services/github_api_writer.py")
    assert "_LIMITS = httpx.Limits(" not in src, (
        "The _LIMITS module constant re-appeared after Sub-batch 3. "
        "It was removed intentionally — per-site inline limits prevent "
        "drift with services.http.client defaults."
    )


# ─── Runtime tests — actually exercise the transport ────────────────

@pytest.mark.asyncio
async def test_get_branch_head_actually_hits_the_correct_endpoint_via_ext_client(monkeypatch):
    """Runtime: _get_branch_head opens a real ext_client, and the
    GET call it makes hits `/git/ref/heads/{branch}` then
    `/git/commits/{sha}`. Uses a MockTransport to observe the actual
    URLs being requested — no mocking of ext_client itself, so the
    wrapper's construction path IS exercised.

    This is the class of test that would have caught the base_url=None
    bug earlier (Sub-batch 1 discovery)."""
    from services import github_api_writer as gaw

    # Real observed URLs on the real transport
    observed_urls: list[str] = []

    def _mock_handler(request: httpx.Request):
        observed_urls.append(str(request.url))
        # Return canned responses in order:
        # 1st GET /git/ref/heads/main → ref object
        # 2nd GET /git/commits/{sha}  → commit object with tree
        if "/git/ref/heads/" in str(request.url):
            return httpx.Response(200, json={"object": {"sha": "abc123"}})
        if "/git/commits/" in str(request.url):
            return httpx.Response(200, json={"tree": {"sha": "tree_deadbeef"}})
        return httpx.Response(404)

    # Monkeypatch ext_client to yield a client bound to MockTransport,
    # while STILL going through the wrapper's construction path.
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_ext_client(dep, **kwargs):
        # Record the kwargs the caller passed so pinning-test can
        # assert timeout/limits were explicit.
        _fake_ext_client.last_call_kwargs = kwargs
        transport = httpx.MockTransport(_mock_handler)
        async with httpx.AsyncClient(transport=transport, **{
            k: v for k, v in kwargs.items() if k in ("headers", "timeout")
        }) as c:
            yield c

    monkeypatch.setattr(gaw, "ext_client", _fake_ext_client)

    result = await gaw._get_branch_head("test-owner", "test-repo", "main", "test-token")

    # Contract: returns {sha, tree_sha}
    assert result == {"sha": "abc123", "tree_sha": "tree_deadbeef"}

    # Two URLs observed: ref then commit
    assert len(observed_urls) == 2
    assert "/repos/test-owner/test-repo/git/ref/heads/main" in observed_urls[0]
    assert "/repos/test-owner/test-repo/git/commits/abc123" in observed_urls[1]

    # Caller passed timeout=60s and limits=20/20 EXPLICITLY
    kwargs = _fake_ext_client.last_call_kwargs
    to = kwargs.get("timeout")
    assert isinstance(to, httpx.Timeout)
    # httpx.Timeout(60.0) sets all fields to 60.
    assert to.read == 60.0
    lim = kwargs.get("limits")
    assert isinstance(lim, httpx.Limits)
    assert lim.max_connections == 20
    assert lim.max_keepalive_connections == 20


@pytest.mark.asyncio
async def test_ext_client_at_read_site_can_be_constructed_end_to_end():
    """Direct-invocation runtime test — same class as
    test_ext_client_applies_explicit_caller_limits — proves that the
    exact (dep, timeout, limits) tuple used by the read sites doesn't
    crash at construction. This is the check that WOULD have caught
    the Sub-batch 1 base_url=None bug had it been added earlier."""
    from services.http import ext_client

    async with ext_client(
        "github",
        timeout=httpx.Timeout(60.0),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=20),
    ) as c:
        # Access transport pool — this is what surfaces base_url=None-class bugs.
        pool = c._transport._pool
        assert pool._max_connections == 20
        assert pool._max_keepalive_connections == 20
        # Timeout is on the client itself.
        assert c.timeout.read == 60.0


# ─── Scope guards ───────────────────────────────────────────────────

def test_sub_batch_3_write_sites_untouched():
    """github_api_writer.py must still contain the raw client blocks
    with the exact write-site POSTs and PATCHes. These are Sub-batch 3."""
    src = _read("/app/backend/services/github_api_writer.py")
    # Blob POSTs still on raw client (client.post inside raw block).
    assert "await client.post(" in src, (
        "The blob/tree/commit POST sites MUST still exist as "
        "`client.post(...)` calls on the raw client. Sub-batch 3 owns them."
    )
    # Ref-advance PATCH still on raw client.
    assert "await client.patch(" in src, (
        "The ref-advance PATCH sites MUST still exist as "
        "`client.patch(...)` calls on the raw client. Sub-batch 3 owns them."
    )
