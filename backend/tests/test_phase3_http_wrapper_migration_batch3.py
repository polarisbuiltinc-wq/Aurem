"""
Phase 3 · Chunk D · Batch 3 — HTTP wrapper migration pinning tests.

Third wave of low-risk service migrations. Same safe pattern as
Batches 1 & 2: pure external HTTP calls onto `services/http`
(ext_request / ext_client) with retry_guard breaker + uniform
ExternalCallError + X-Request-ID injection.

Scope of this batch (2026-02-12):
  • services/mode_d_debugger.py — GitHub PAT fetch (single drop-in)
  • services/tools_bridge.py    — aurem.live upstream tools (2 sites)
  • services/web_skills.py      — Tavily summarize + Firecrawl
    scrape/crawl (4 sites; 2 tavily-search sites intentionally
    skipped because they mix manual breaker gating that would
    double-track with the wrapper's breaker)

Intentionally deferred to supervised session (documented for the
next agent so they don't re-attempt these unsupervised):
  • services/ora_client.py   — custom 24h fatal-pattern breaker
    (_trip_breaker / _breaker_is_open with fatal-pattern list).
    Naive wrapper drop-in would cause two breakers to compete.
  • services/web_skills.py::web_search (line ~105) and
    services/web_skills.py::fetch_url (line ~180) — call
    services.retry_guard.get_breaker("tavily") manually and
    already gate/record via _br.allow() / .record_failure(). The
    wrapper uses the SAME breaker; migrating naively would double-
    record failures. Refactor to remove the manual gates AND
    switch to ext_request needs a supervised review.
"""


def test_mode_d_debugger_uses_ext_client():
    src = open("/app/backend/services/mode_d_debugger.py").read()
    assert "from services.http import ext_client" in src
    assert 'ext_client(\n            "github"' in src
    assert "httpx.AsyncClient(timeout=15.0)" not in src


def test_tools_bridge_uses_ext_client_both_sites():
    src = open("/app/backend/services/tools_bridge.py").read()
    assert "from services.http import ext_client" in src
    # Both list_tools + invoke_tool routed to the same dep name.
    assert src.count('ext_client(\n            "aurem_upstream"') == 2
    assert "httpx.AsyncClient(timeout=10.0)" not in src
    assert "httpx.AsyncClient(timeout=60.0)" not in src


def test_web_skills_partial_migration():
    """POST-CUSTOM-BREAKER-RECONCILIATION (2026-02-12): the 2 tavily
    manual-breaker sites (web_search + fetch_url) that this batch
    originally left behind are now migrated. Flipped from partial-
    migration guard to full-migration guard:

      - Zero raw httpx.AsyncClient(timeout=TAVILY_TIMEOUT) sites
      - Tavily now has 3 ext_client sites (summarize from Batch 3 +
        search + extract from Custom-breaker reconciliation)
      - Total ext_client refs in web_skills grew by 2
      - Firecrawl sites (3) still intact (untouched by reconciliation)
    """
    src = open("/app/backend/services/web_skills.py").read()
    assert "from services.http import ext_client" in src
    # No raw AsyncClient left in web_skills anywhere.
    assert "httpx.AsyncClient(" not in src, (
        "Custom-breaker reconciliation should have removed the last "
        "raw AsyncClient sites in web_skills.py"
    )
    # 3 tavily ext_client sites now (summarize + search + extract).
    # Note: indent may vary depending on which function opens the CM.
    tavily_sites = src.count('ext_client("tavily"') + src.count('ext_client(\n            "tavily"')
    assert tavily_sites >= 3, (
        f"Expected ≥3 tavily ext_client sites after reconciliation, "
        f"found {tavily_sites}"
    )
    # Firecrawl sites still intact.
    assert src.count('ext_client(\n            "firecrawl"') == 2
    assert src.count('ext_client(\n        "firecrawl"') == 1
    # Total ext_client references in web_skills should now be >= 6
    # (was ≥4 pre-reconciliation, +2 from tavily search+extract).
    assert src.count("ext_client(") >= 6


def test_ora_client_intentionally_deferred():
    """POST-CUSTOM-BREAKER-RECONCILIATION (2026-02-12): the deferral
    this test originally guarded is complete. Flipped from
    'still-raw-AsyncClient' guard to 'now-on-ext_client' guard.

    Critically, the file-based persistent breaker MUST still exist —
    ext_client's in-memory retry_guard breaker is NOT equivalent
    (see test_phase3_custom_breaker_reconciliation.py for the diff):
      - Persistent across worker restarts (file-based, /tmp/)
      - First-failure trip (not 5-consecutive)
      - 24h fatal-pattern cooldown extension
    """
    src = open("/app/backend/services/ora_client.py").read()
    # No raw AsyncClient — migrated.
    assert "httpx.AsyncClient(timeout=timeout)" not in src
    assert "httpx.AsyncClient(" not in src
    # Now on ext_client with the ora dep name.
    assert 'ext_client("ora"' in src
    # File-based breaker MUST still exist (invariant flip vs original).
    assert "_trip_breaker" in src
    assert "_breaker_is_open" in src
    # Fatal-cooldown mechanism preserved (24h silencer).
    assert "_BREAKER_FATAL_FILE" in src

