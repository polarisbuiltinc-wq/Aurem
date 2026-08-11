"""
Phase 3 · Chunk D · Batch 2 — HTTP wrapper migration pinning tests.

Second wave of low-risk service files moved onto `services.http`
(ext_request / ext_client). Same safe pattern as Batch 1: pure
external POST/GET call, no state machine, no chained pooled
requests. Migration adds retry_guard breaker + uniform
ExternalCallError + X-Request-ID injection.

Scope of this batch (2026-02-12):
  • services/advisor_vision.py  — OpenRouter vision proxy
  • services/financials.py      — Frankfurter USD→CAD FX
  • services/daily_digest.py    — Resend email delivery
  • services/url_fetcher.py     — arbitrary user URL fetch
"""


def test_advisor_vision_uses_ext_request():
    src = open("/app/backend/services/advisor_vision.py").read()
    assert "from services.http import ext_request" in src
    assert 'ext_request(\n            "openrouter"' in src
    assert "httpx.AsyncClient(timeout=_TIMEOUT_S)" not in src


def test_financials_frankfurter_uses_ext_client():
    src = open("/app/backend/services/financials.py").read()
    assert "from services.http import ext_client" in src
    assert 'ext_client(\n            "frankfurter"' in src
    assert "httpx.AsyncClient(timeout=8)" not in src


def test_daily_digest_uses_ext_request():
    src = open("/app/backend/services/daily_digest.py").read()
    assert "from services.http import ext_request" in src
    assert 'ext_request(\n            "resend"' in src
    assert "httpx.AsyncClient(timeout=15.0)" not in src


def test_url_fetcher_uses_ext_client():
    src = open("/app/backend/services/url_fetcher.py").read()
    assert "from services.http import ext_client" in src
    assert 'ext_client(\n            "user_url"' in src
    # Raw AsyncClient with the multi-kwarg form must be gone here.
    assert "httpx.AsyncClient(\n            timeout=TIMEOUT_SECONDS" not in src
