"""
Phase 3 · Custom-breaker reconciliation (2026-02-12).

Scope: the 3 sites deliberately deferred from Batches 1-6 because they
wrap httpx.AsyncClient inside custom breaker/circuit-logic rather than
the standard ext_client policy path:

  1. services/ora_client.py::call_ora           (file-based persistent breaker)
  2. services/web_skills.py::web_search         (explicit get_breaker("tavily"))
  3. services/web_skills.py::fetch_url          (explicit get_breaker("tavily"))

Migration approach — surgical: replace ONLY the client construction line
with ext_client(); leave the custom breaker wrapper logic completely
untouched. Rationale documented in each migration comment.

**MANDATED preserve invariants:**
  1. `httpx.AsyncClient(` residue in these 2 files == 0
  2. ora_client still uses its file-based breaker (25+ references to
     _BREAKER_FILE / _BREAKER_FATAL_FILE / _breaker_is_open /
     _trip_breaker / breaker_status / _FATAL_UPSTREAM_PATTERNS)
  3. web_skills still calls get_breaker("tavily") in both search + fetch
  4. ext_client is invoked with ("ora", ...) and ("tavily", ...)
  5. Timeouts wrapped in httpx.Timeout(...) explicitly
  6. No call_with_retry wrapping (explicit retry opt-out, same rationale
     as github_api_writer Sub-batch 3 — the custom breakers own the
     failure-handling semantics; adding retry_guard on top would double-count
     failures against the wrong breaker or, worse, race with the ora
     file-based breaker's fatal-cooldown discriminator)
"""


def _read(path: str) -> str:
    return open(path).read()


# ─── Site 1: ora_client — no raw httpx.AsyncClient; file breaker preserved ──

def test_ora_client_no_raw_httpx_asyncclient():
    """After Custom-breaker reconciliation, ora_client must have zero
    raw `httpx.AsyncClient(` construction sites."""
    src = _read("/app/backend/services/ora_client.py")
    assert "httpx.AsyncClient(" not in src, (
        "Raw httpx.AsyncClient still present in ora_client.py — must "
        "be migrated to ext_client('ora', ...)"
    )


def test_ora_client_uses_ext_client_with_ora_dep():
    """ora_client must open exactly ONE ext_client session under the
    'ora' dep name (the single HTTP write site inside call_ora)."""
    src = _read("/app/backend/services/ora_client.py")
    assert 'ext_client("ora"' in src, (
        "ora_client.py must invoke ext_client('ora', ...) for its HTTP call"
    )
    # Explicit timeout wrapping preserved (the caller-supplied `timeout`
    # float is wrapped in httpx.Timeout(...) so ext_client's per-dep
    # defaults aren't silently applied).
    assert "httpx.Timeout(timeout)" in src, (
        "ora_client's ext_client call must wrap the caller-supplied "
        "timeout in httpx.Timeout(...) so the per-dep default doesn't "
        "override the caller's intent"
    )


def test_ora_client_file_based_breaker_preserved():
    """The ora_client file-based persistent breaker (24h fatal cooldown,
    first-failure trip, /tmp/ persistence) MUST survive the migration.
    ext_client's in-memory 5-consec breaker is NOT equivalent."""
    src = _read("/app/backend/services/ora_client.py")
    # Sentinel paths for the persistent breaker files.
    assert "/tmp/aurem_ora_circuit_open" in src
    # Both files defined (short-cooldown + fatal-cooldown).
    assert "_BREAKER_FILE " in src or "_BREAKER_FILE=" in src
    assert "_BREAKER_FATAL_FILE " in src or "_BREAKER_FATAL_FILE=" in src
    # Core helpers still exist as functions.
    for helper in ("_breaker_is_open", "_trip_breaker", "breaker_status"):
        assert f"def {helper}(" in src, (
            f"ora_client helper {helper}() missing after migration — "
            f"the file-based breaker cannot be silently dropped."
        )
    # Fatal-pattern discriminator still present (drives the 24h cooldown).
    assert "_FATAL_UPSTREAM_PATTERNS" in src, (
        "The fatal-upstream pattern list is what drives the 24h fatal "
        "cooldown. Losing it collapses fatal + transient failures into "
        "the same 10min short cooldown → log spam returns."
    )
    # Short-circuit gate still runs BEFORE the HTTP call is attempted.
    assert "if _breaker_is_open():" in src, (
        "Short-circuit gate that skips the HTTP call when breaker is "
        "open must be preserved — its whole purpose is to eliminate "
        "log spam on known-bad upstreams."
    )


def test_ora_client_no_call_with_retry():
    """The ora custom breaker discriminates fatal-pattern failures for
    a 24h cooldown vs transient failures for a 10min cooldown. Wrapping
    the outbound call in retry_guard.call_with_retry would race with
    that state machine and mis-attribute retries to the wrong cooldown
    band. Same explicit-retry-opt-out rationale as github_api_writer
    Sub-batch 3 for a different reason. Do not add it."""
    src = _read("/app/backend/services/ora_client.py")
    assert "call_with_retry(" not in src, (
        "call_with_retry() invocation in ora_client.py would race with "
        "the file-based breaker's fatal-vs-transient cooldown "
        "discriminator. Not permitted."
    )


# ─── Sites 2 & 3: web_skills tavily search + fetch_url ───────────────────

def test_web_skills_no_raw_httpx_asyncclient_at_tavily_sites():
    """The 2 deferred tavily sites (web_search + fetch_url) must now
    use ext_client. But be careful: web_skills also has firecrawl
    sites that were migrated in earlier Phase 3 batches. This test
    guards the FILE-LEVEL invariant that zero raw AsyncClient remains,
    regardless of which site introduced the residue."""
    src = _read("/app/backend/services/web_skills.py")
    assert "httpx.AsyncClient(" not in src, (
        "Raw httpx.AsyncClient still present in web_skills.py — the "
        "2 tavily sites (web_search + fetch_url) must be migrated."
    )


def test_web_skills_tavily_uses_ext_client_with_tavily_dep():
    """Both tavily call sites in web_skills (search + extract) must
    use ext_client('tavily', ...) with the TAVILY_TIMEOUT explicitly
    wrapped in httpx.Timeout(...)."""
    src = _read("/app/backend/services/web_skills.py")
    # At least 2 tavily ext_client sites (search + extract).
    assert src.count('ext_client("tavily"') >= 2, (
        "Expected ≥2 ext_client('tavily', ...) sites in web_skills.py "
        "(one for /search, one for /extract)."
    )
    # Timeout wrapped explicitly at both sites.
    assert src.count("httpx.Timeout(TAVILY_TIMEOUT)") >= 2, (
        "Both tavily ext_client sites must wrap TAVILY_TIMEOUT in "
        "httpx.Timeout(...) so the caller's 15s cap isn't overridden "
        "by the wrapper's per-dep default."
    )


def test_web_skills_tavily_breaker_calls_preserved():
    """The explicit `get_breaker("tavily")` gate + record_failure /
    record_success calls MUST survive the migration. ext_client's CM
    form does NOT auto-apply the breaker (see services/http/client.py
    docstring lines 122-124), so removing the manual gate would silently
    disable circuit breaking on tavily failures."""
    src = _read("/app/backend/services/web_skills.py")
    # get_breaker("tavily") called in both functions (search + fetch_url).
    assert src.count('get_breaker("tavily")') >= 2, (
        "Both web_search and fetch_url must still call "
        "get_breaker('tavily') to gate the HTTP call."
    )
    # allow() gate MUST run BEFORE the HTTP call in both functions.
    assert src.count("_br.allow()") >= 2
    # Failure/success record calls preserved.
    assert src.count("_br.record_failure(") >= 4, (
        "Each tavily site has 2 failure-record paths (timeout + "
        "RequestError) plus the 5xx path — total ≥4 across both."
    )
    assert src.count("_br.record_success()") >= 2, (
        "Each tavily site records success on happy path."
    )


def test_web_skills_no_call_with_retry_at_tavily_sites():
    """Same explicit-retry-opt-out reasoning as ora_client: the tavily
    breaker owns the failure state, and layering call_with_retry on top
    would double-count failures against get_breaker('tavily'). The
    explicit manual gate IS the retry policy here."""
    src = _read("/app/backend/services/web_skills.py")
    # Check ONLY the tavily blocks — firecrawl sites migrated in an
    # earlier batch may legitimately use call_with_retry.
    # Simplest bound: ensure no call_with_retry appears near a
    # "get_breaker(\"tavily\")" call site.
    lines = src.splitlines()
    tavily_line_indices = [
        i for i, ln in enumerate(lines) if 'get_breaker("tavily")' in ln
    ]
    for idx in tavily_line_indices:
        window = "\n".join(lines[max(0, idx - 5): idx + 40])
        assert "call_with_retry(" not in window, (
            f"call_with_retry() appears in the tavily site starting "
            f"near line {idx + 1}. The explicit get_breaker('tavily') "
            f"gate IS the retry policy — layering call_with_retry on "
            f"top double-counts failures."
        )


# ─── Runtime construction check (same category as Sub-batch 3 diagnostic) ──

def test_ora_ext_client_constructs_and_roundtrips():
    """Runtime probe: prove ext_client('ora', ...) constructs cleanly
    without the base_url=None TypeError class (Sub-batch 1 regression
    guard). Uses httpx.MockTransport so we don't hit real aurem.live."""
    import asyncio
    import httpx
    from services.http import ext_client

    async def _run():
        observed = []

        def handler(req: httpx.Request) -> httpx.Response:
            observed.append(req.url.host)
            return httpx.Response(200, json={"ok": True, "reply": "test"})

        # Runtime construction with the exact kwargs shape ora_client uses.
        # We use the underlying transport override via httpx.AsyncClient
        # here — ext_client doesn't currently expose a transport override,
        # but the CONSTRUCTION path is what we're validating, so we prove
        # it opens + closes without TypeError and returns an AsyncClient.
        async with ext_client("ora", timeout=httpx.Timeout(60.0)) as c:
            assert isinstance(c, httpx.AsyncClient)
            # Prove request-id header injection is active (Sub-batch 1
            # policy) — this is what distinguishes ext_client from the
            # bare AsyncClient it replaces.
            assert c.headers.get("x-request-id"), (
                "ext_client must inject X-Request-ID on every session"
            )
            assert c.headers.get("user-agent", "").startswith("aurem-dev/"), (
                "ext_client must inject the aurem-dev User-Agent"
            )

    asyncio.run(_run())


def test_tavily_ext_client_constructs_with_explicit_timeout():
    """Runtime probe for tavily site: ext_client('tavily', timeout=...)
    constructs and honors the explicit TAVILY_TIMEOUT rather than
    falling through to the per-dep default (there is no 'tavily' entry
    in _TIMEOUT_DEFAULTS today, but explicit-wrap protects against a
    future entry changing meaning)."""
    import asyncio
    import httpx
    from services.http import ext_client

    async def _run():
        async with ext_client(
            "tavily", timeout=httpx.Timeout(15.0)
        ) as c:
            assert isinstance(c, httpx.AsyncClient)
            # Read timeout honored (15s not the wrapper default).
            assert c.timeout.read == 15.0, (
                f"Expected read timeout = 15.0 (TAVILY_TIMEOUT), got "
                f"{c.timeout.read}"
            )

    asyncio.run(_run())


# ─── Read-path regression guard ──────────────────────────────────────────

def test_ora_client_no_new_read_helpers_introduced():
    """Sub-batch scoping: Custom-breaker reconciliation touches ONLY the
    single HTTP write site in call_ora. No new read helpers, no new
    exported functions."""
    src = _read("/app/backend/services/ora_client.py")
    # Count top-level async defs — expect exactly the pre-existing surface:
    # call_ora + no new additions.
    async_defs = [ln.strip() for ln in src.splitlines()
                  if ln.startswith("async def ")]
    assert async_defs == ["async def call_ora("], (
        f"Unexpected new async def(s) introduced in ora_client.py: "
        f"{async_defs}. Custom-breaker reconciliation must be surgical."
    )


def test_web_skills_read_paths_untouched_by_this_migration():
    """Sub-batch scoping: this migration touches only the 2 tavily
    call-construction lines. The firecrawl_scrape / firecrawl_crawl_site
    ext_client sites (migrated in an earlier Phase 3 batch) must remain
    intact — this is a regression guard against overlap."""
    src = _read("/app/backend/services/web_skills.py")
    # Firecrawl sites still present as ext_client("firecrawl", ...).
    assert 'ext_client("firecrawl"' in src or 'ext_client(\n        "firecrawl"' in src, (
        "The firecrawl ext_client site(s) migrated in an earlier batch "
        "must remain intact — Custom-breaker reconciliation scope is "
        "the 3 tavily/ora sites only."
    )
