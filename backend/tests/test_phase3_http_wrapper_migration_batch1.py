"""
Phase 3 · Chunk D — HTTP wrapper migration pinning tests.

Guards that low-risk services now route their external calls through
`services.http` (ext_request / ext_client) rather than raw
`httpx.AsyncClient(...)`. Migration is source-file pinned — a future
refactor that "reverts" one of these back to a raw client should trip
this test intentionally.

Scope of this batch (2026-02-12):
  • services/topup_alerts.py       — Resend email delivery
  • services/mermaid_diagram.py    — OpenRouter LLM proxy
  • services/mock_reality_check.py — GitHub + OpenRouter shape probes
"""


def test_topup_alerts_uses_ext_request():
    src = open("/app/backend/services/topup_alerts.py").read()
    assert "from services.http import ext_request" in src
    assert 'ext_request(\n            "resend"' in src
    # Raw client for the Resend send must be gone.
    assert "httpx.AsyncClient(timeout=15.0)" not in src


def test_mermaid_diagram_uses_ext_request():
    src = open("/app/backend/services/mermaid_diagram.py").read()
    assert "from services.http import ext_request" in src
    assert 'ext_request(\n            "openrouter"' in src
    assert "httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S)" not in src


def test_mock_reality_check_uses_ext_client():
    src = open("/app/backend/services/mock_reality_check.py").read()
    assert "from services.http import ext_client" in src
    assert 'ext_client(\n            "github"' in src
    assert 'ext_client(\n            "openrouter"' in src
    # Raw AsyncClient with plain timeout kwarg must be gone here.
    assert "httpx.AsyncClient(timeout=timeout)" not in src


def test_integration_health_all_probes_use_ext_client():
    """All 8 probe callsites in integration_health.py were migrated
    away from raw httpx.AsyncClient(timeout=10) to ext_client() with
    per-dep names (github, tavily, firecrawl, resend, vercel,
    supabase, openrouter)."""
    src = open("/app/backend/services/integration_health.py").read()
    assert "from services.http import ext_client" in src
    # Every raw AsyncClient(timeout=10) must be gone.
    assert "httpx.AsyncClient(timeout=10)" not in src
    # At least these deps should appear as ext_client dep names.
    for dep in ("github", "tavily", "firecrawl", "resend",
                "vercel", "supabase", "openrouter"):
        assert f'ext_client(' in src and f'"{dep}"' in src, (
            f"expected ext_client({dep!r}, ...) call in "
            f"integration_health.py — did the migration miss a probe?"
        )
