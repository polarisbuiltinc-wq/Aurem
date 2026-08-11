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
    """4 sites migrated (tavily summarize + firecrawl scrape/crawl x3);
    2 sites intentionally left behind (the manual-breaker tavily
    search/extract paths — see module docstring)."""
    src = open("/app/backend/services/web_skills.py").read()
    assert "from services.http import ext_client" in src
    # tavily summarize (no manual breaker) → migrated
    assert 'ext_client(\n            "tavily"' in src
    # firecrawl scrape + crawl (3 sites total)
    assert src.count('ext_client(\n            "firecrawl"') == 2
    assert src.count('ext_client(\n        "firecrawl"') == 1  # polling site at 8-space indent
    # Total ext_client references in web_skills should be >= 4
    assert src.count("ext_client(") >= 4
    # 2 manual-breaker sites still on raw AsyncClient — intentional.
    assert src.count("httpx.AsyncClient(timeout=TAVILY_TIMEOUT)") == 2


def test_ora_client_intentionally_deferred():
    """ora_client.py has its own 24h fatal-pattern breaker. Guard
    that we did NOT migrate it in this batch (a future supervised
    session must reconcile the two breakers before landing it)."""
    src = open("/app/backend/services/ora_client.py").read()
    assert "httpx.AsyncClient(timeout=timeout)" in src
    assert "_trip_breaker" in src
    assert "_breaker_is_open" in src
