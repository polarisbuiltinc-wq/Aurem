"""
Phase 3 · Sub-batch 1 of github_api_writer migration —
`ext_client(limits=)` API upgrade pinning tests.

Scope (2026-02-12, prep-only, HELD until reviewed):
  • Extend `services.http.ext_client` with `limits` parameter
  • Add `_LIMITS_DEFAULTS` per-dep dict (github → 20/20 to match
    github_api_writer.py's current writer behavior)
  • Guarantee: explicit `limits=httpx.Limits(...)` passed by a
    caller reaches the underlying httpx.AsyncClient

These tests pin the wrapper API before Sub-batch 2 (read-site
migration) or Sub-batch 3 (write-site migration) touch the writer.
If the wrapper API changes in a way that breaks these invariants,
tests will fail before the writer migration can ship.
"""

import httpx
import pytest


def _read(path: str) -> str:
    return open(path).read()


# ─── Static / signature invariants ──────────────────────────────────

def test_ext_client_signature_accepts_limits_kwarg():
    """The wrapper API must expose `limits` as a keyword parameter."""
    import inspect
    from services.http.client import ext_client
    # ext_client is @asynccontextmanager-wrapped; the underlying
    # function is on ext_client.__wrapped__ (contextmanager sets it).
    fn = getattr(ext_client, "__wrapped__", None) or ext_client
    sig = inspect.signature(fn)
    assert "limits" in sig.parameters, (
        "ext_client must accept a `limits` keyword parameter. "
        "This is Sub-batch 1 of the github_api_writer migration."
    )
    param = sig.parameters["limits"]
    # Must be optional (default None) so existing 65 migrated sites
    # keep working without changes.
    assert param.default is None, (
        "`limits` default must be None to preserve backward "
        "compatibility with existing sites that don't pass it."
    )


def test_limits_defaults_dict_defines_github_at_20_20():
    """The per-dep limits default for `github` MUST match the current
    github_api_writer.py behavior (20/20). If Sub-batch 2/3 lands
    without passing explicit limits, this default is what gets used —
    it must match the pre-migration behavior exactly."""
    from services.http.client import _LIMITS_DEFAULTS
    gh = _LIMITS_DEFAULTS.get("github")
    assert gh is not None, (
        "`github` MUST have an explicit entry in _LIMITS_DEFAULTS. "
        "Without it, wrapper migration would silently fall through "
        "to httpx's default (100/20) — 5× the writer's current cap "
        "of 20 — risking GitHub secondary rate-limit trips on "
        "large multi-file commits."
    )
    assert gh.max_connections == 20, (
        f"github default max_connections must be 20 (got {gh.max_connections})"
    )
    assert gh.max_keepalive_connections == 20, (
        f"github default max_keepalive_connections must be 20 "
        f"(got {gh.max_keepalive_connections})"
    )


def test_limits_defaults_has_rationale_comment():
    """Preserve the 'why 20/20' comment block so a future refactor
    doesn't 'clean up' the tight cap."""
    src = _read("/app/backend/services/http/client.py")
    assert "GitHub's secondary rate-limiter" in src or \
           "github_api_writer" in src, (
        "The rationale for github's 20/20 limit must survive in "
        "an inline comment on _LIMITS_DEFAULTS. If future maintainer "
        "sees a raw dict entry with no context, they might 'optimize' "
        "it back up to httpx defaults and reintroduce the rate-limit "
        "class of bugs."
    )


# ─── Runtime: caller-passed limits are applied ──────────────────────

@pytest.mark.asyncio
async def test_ext_client_applies_explicit_caller_limits():
    """When a caller passes `limits=httpx.Limits(...)`, that exact
    object must reach the underlying httpx.AsyncClient — not the
    per-dep default and not httpx's own default."""
    from services.http.client import ext_client

    custom_limits = httpx.Limits(max_connections=7, max_keepalive_connections=3)
    async with ext_client("github", limits=custom_limits) as c:
        # httpx exposes limits on the transport pool for AsyncHTTPTransport.
        # Check via the internal _transport._pool (best available signal).
        transport = c._transport
        pool = getattr(transport, "_pool", None)
        assert pool is not None, "expected httpx pool on transport"
        # httpcore Pool stores max_connections directly.
        max_conns = getattr(pool, "_max_connections", None)
        max_ka = getattr(pool, "_max_keepalive_connections", None)
        assert max_conns == 7, (
            f"caller's explicit max_connections=7 not applied "
            f"(pool reports {max_conns}). limits= parameter is broken."
        )
        assert max_ka == 3, (
            f"caller's explicit max_keepalive_connections=3 not "
            f"applied (pool reports {max_ka})."
        )


@pytest.mark.asyncio
async def test_ext_client_applies_per_dep_default_when_no_explicit_limits():
    """When no `limits=` is passed, the per-dep default from
    _LIMITS_DEFAULTS must be applied. For 'github' that's 20/20."""
    from services.http.client import ext_client

    async with ext_client("github") as c:
        pool = c._transport._pool
        assert pool._max_connections == 20
        assert pool._max_keepalive_connections == 20


@pytest.mark.asyncio
async def test_ext_client_falls_through_to_httpx_default_for_unlisted_dep():
    """For deps NOT in _LIMITS_DEFAULTS (everyone except github),
    NO limits kwarg is forwarded to httpx.AsyncClient — meaning httpx
    uses its own default (100/20). This preserves the 65 already-
    migrated sites' behavior."""
    from services.http.client import ext_client

    # 'openrouter' has no entry in _LIMITS_DEFAULTS → httpx default.
    async with ext_client("openrouter") as c:
        pool = c._transport._pool
        # httpx default: max_connections=100
        assert pool._max_connections == 100, (
            f"unlisted deps should fall through to httpx default "
            f"(100), got {pool._max_connections} — the Sub-batch 1 "
            f"upgrade silently changed behavior for the 65 already-"
            f"migrated sites."
        )


# ─── Regression guard: existing sites unaffected ───────────────────

def test_existing_migrated_sites_do_not_pass_limits_kwarg():
    """None of the 65 already-migrated sites use `limits=` — they
    rely on wrapper defaults. This test just confirms no test
    infrastructure is broken by the new signature."""
    import glob
    hits = 0
    for py in glob.glob("/app/backend/**/*.py", recursive=True):
        if "/tests/" in py or "/services/http/" in py:
            continue
        src = open(py).read()
        if "ext_client(" in src and "limits=" in src:
            hits += 1
    # Zero pre-existing sites use `limits=`. Sub-batches 2/3 will
    # add it. This test acts as a witness: at the moment Sub-batch 1
    # lands, no non-test file passes limits=.
    assert hits == 0, (
        f"Found {hits} non-test files using `limits=` with ext_client. "
        "Sub-batch 1 landed alone — Sub-batch 2 (read) and Sub-batch "
        "3 (write) migrations should not have shipped alongside it."
    )
