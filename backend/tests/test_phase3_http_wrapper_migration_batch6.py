"""
Phase 3 · Chunk D · Batch 6 — HTTP wrapper migration pinning tests.

Sixth wave: fold in `github_deploy_service.py` (surveyed after
Batch 5 shipped) plus three more single-site files. Same safe
pattern — no custom breakers, no manual retries beyond what
the wrapper is compatible with.

Scope of this batch (2026-02-12):
  • services/github_deploy_service.py — 4 sites (token verify,
    branch/blob/commit pooled session, PR fetch, workflow
    install check) → `ext_client("github", ...)`
  • services/billing_cron.py          — 1 site (referral reward
    email via Resend) → `ext_request("resend", ...)`
  • services/finding_fix_applier.py   — 1 site (GitHub file
    fetch inside a manual `for attempt in range(3)` retry loop).
    NOTE: `ext_client` is a context-manager only (no auto-retry,
    no failure recording), so the manual retry loop stays intact
    and we just get the breaker gate + X-Request-ID header
    as an additive benefit.
  • services/github_issues_context.py — 1 site (list issues)
    → `ext_client("github", ...)`

Also skipped intentionally (not migrated in this batch):
  • services/architecture_health.py — the "1 site" grep hit was
    a docstring warning, not an actual httpx call. Nothing to
    migrate here.
"""


def test_github_deploy_service_all_sites_migrated():
    src = open("/app/backend/services/github_deploy_service.py").read()
    assert "from services.http import ext_client" in src
    # 4 ext_client blocks (one per migrated site).
    assert src.count("ext_client(") >= 4
    assert "httpx.AsyncClient(timeout=10.0)" not in src
    assert "httpx.AsyncClient(timeout=15.0)" not in src
    assert "httpx.AsyncClient(timeout=30.0)" not in src


def test_billing_cron_referral_email_migrated():
    src = open("/app/backend/services/billing_cron.py").read()
    assert "from services.http import ext_request" in src
    assert 'ext_request(\n                    "resend"' in src
    assert "httpx.AsyncClient(timeout=8)" not in src


def test_finding_fix_applier_migrated_but_keeps_manual_retry():
    """Migration must swap the raw client for ext_client
    but MUST preserve the `for attempt in range(3)` manual retry
    loop — the wrapper's context-manager form doesn't auto-retry,
    so removing the loop would change behavior."""
    src = open("/app/backend/services/finding_fix_applier.py").read()
    assert "from services.http import ext_client" in src
    assert 'ext_client(\n            "github"' in src
    # Manual retry loop must still be present.
    assert "for attempt in range(3):" in src
    assert "httpx.AsyncClient(timeout=15.0)" not in src


def test_github_issues_context_migrated():
    src = open("/app/backend/services/github_issues_context.py").read()
    assert "from services.http import ext_client" in src
    assert 'ext_client(\n        "github"' in src
    assert "httpx.AsyncClient(timeout=10)" not in src
