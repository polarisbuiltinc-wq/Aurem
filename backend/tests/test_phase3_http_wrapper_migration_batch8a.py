"""
Phase 3 · Batch 8a — HTTP wrapper migration pinning tests.

Scope (2026-02-12, post Iter 314 verified live):
  • routers/admin_qa.py         — 3 sites (2 GitHub Actions + 1 VSCode Marketplace)
  • routers/admin_bin.py        — 2 sites (1 GitHub HEAD + 1 OpenRouter credits)
  • routers/admin_projects_brain.py — 1 site (internal service probe)
  • routers/admin_ops_config.py — 1 site (Cloudflare purge)
  • routers/admin_users.py      — 1 site (Resend email send)
  • routers/upload.py           — 1 site (OpenRouter vision — 45s timeout MUST preserve)
  • routers/fix_pipeline.py     — 1 site (GitHub commit verification)

Excluded from this batch (intentional):
  • routers/github_oauth.py     → Batch 8b (auth-adjacent, solo)
  • routers/codebase_health.py  → mini-batch (custom timeout tuple needs preserve test)

Total: 10 sites across 7 files.
"""


def _read(path: str) -> str:
    return open(path).read()


# ─── admin_qa.py (3 sites) ──────────────────────────────────────────

def test_admin_qa_module_imports_ext_client():
    src = _read("/app/backend/routers/admin_qa.py")
    assert "from services.http import ext_client" in src


def test_admin_qa_github_actions_two_sites_migrated():
    """The two GitHub Actions API sites (workflow_runs + jobs) use
    ext_client('github') with 6s timeout preserved."""
    src = _read("/app/backend/routers/admin_qa.py")
    # Both must use ext_client with github dep + 6s timeout.
    assert src.count('ext_client("github", timeout=httpx.Timeout(6.0))') >= 2, (
        "Both GitHub Actions probes must use ext_client('github', "
        "timeout=6s) — migration incomplete."
    )
    # Raw client gone from those sites.
    assert "httpx.AsyncClient(timeout=6.0) as client" not in src


def test_admin_qa_vscode_marketplace_site_uses_dedicated_dep():
    """The VSCode Marketplace check uses a distinct dep name so a
    Marketplace outage doesn't trip the GitHub breaker."""
    src = _read("/app/backend/routers/admin_qa.py")
    assert 'ext_client(\n            "vscode_marketplace"' in src or \
           'ext_client("vscode_marketplace"' in src, (
        "VSCode Marketplace check must use its own dep name — "
        "sharing the 'github' breaker scope with actual GitHub "
        "traffic would let one outage trip the other."
    )
    # Old raw client with 8s + follow_redirects gone.
    assert "httpx.AsyncClient(\n            timeout=8.0" not in src


# ─── admin_bin.py (2 sites) ────────────────────────────────────────

def test_admin_bin_module_imports_ext_client():
    src = _read("/app/backend/routers/admin_bin.py")
    assert "from services.http import ext_client" in src


def test_admin_bin_github_head_probe_migrated():
    """The BIN tracker's HEAD probe of user repos uses github dep
    with a TIGHT 4s timeout preserved — this is called inside a
    parallel loop over projects, so a hung upstream would fan out."""
    src = _read("/app/backend/routers/admin_bin.py")
    assert 'ext_client("github", timeout=httpx.Timeout(4.0))' in src, (
        "BIN tracker HEAD probe must keep the 4s timeout — the "
        "wrapper's default github read=20s would let a hung upstream "
        "block admin/bin/{bin_id}/projects for 100+ seconds when "
        "probing 25 projects in a row."
    )
    assert "httpx.AsyncClient(timeout=4.0) as c" not in src


def test_admin_bin_openrouter_balance_migrated():
    """OpenRouter credit balance check uses openrouter dep + 6s."""
    src = _read("/app/backend/routers/admin_bin.py")
    assert 'ext_client("openrouter", timeout=httpx.Timeout(6.0))' in src
    assert "httpx.AsyncClient(timeout=6.0) as c" not in src


# ─── admin_projects_brain.py (1 site) ──────────────────────────────

def test_admin_projects_brain_probe_migrated():
    """Architecture-page service probes use a distinct dep name
    (internal_probe) so a GitHub outage tripping the github breaker
    doesn't also fast-fail the internal service probes."""
    src = _read("/app/backend/routers/admin_projects_brain.py")
    assert "from services.http import ext_client" in src
    assert 'ext_client("internal_probe", timeout=httpx.Timeout(4.0))' in src, (
        "Architecture probes must use 'internal_probe' dep, not "
        "share the 'github' breaker."
    )
    assert "httpx.AsyncClient(timeout=4.0) as c" not in src


# ─── admin_ops_config.py (1 site) ──────────────────────────────────

def test_admin_ops_config_cloudflare_purge_migrated():
    """Cloudflare API uses its own dep name — separate SLA from GitHub."""
    src = _read("/app/backend/routers/admin_ops_config.py")
    assert "from services.http import ext_client" in src
    assert 'ext_client("cloudflare", timeout=httpx.Timeout(10.0))' in src
    assert "httpx.AsyncClient(timeout=10.0) as client" not in src


# ─── admin_users.py (1 site) ───────────────────────────────────────

def test_admin_users_resend_email_migrated():
    """The email-offer send path uses resend dep + 15s timeout."""
    src = _read("/app/backend/routers/admin_users.py")
    assert "from services.http import ext_client" in src
    assert 'ext_client("resend", timeout=httpx.Timeout(15.0))' in src
    assert "httpx.AsyncClient(timeout=15) as client" not in src


# ─── upload.py (1 site) ────────────────────────────────────────────

def test_upload_vision_45s_timeout_preserved():
    """CRITICAL guard: the vision LLM call MUST keep the 45s timeout.
    Vision models over OpenRouter can take 20-40s for image
    description + OCR. The wrapper's default openrouter dep timeout
    is read=60s — safe. But we still explicitly pass 45s to match
    the original behavior AND cap the wall-clock upload wait."""
    src = _read("/app/backend/routers/upload.py")
    assert "from services.http import ext_client" in src
    assert 'ext_client("openrouter", timeout=httpx.Timeout(45.0))' in src, (
        "Vision upload path must keep the explicit 45s timeout — "
        "if a future refactor drops it, uploads could hang up to "
        "the wrapper default (60s) OR silently succeed on partial "
        "responses on flaky vision models."
    )
    assert "httpx.AsyncClient(timeout=45.0) as c" not in src


# ─── fix_pipeline.py (1 site) ──────────────────────────────────────

def test_fix_pipeline_commit_verify_migrated():
    """The GitHub commit-exists verification (post-fix ground-truth
    check) uses github dep + 10s timeout preserved."""
    src = _read("/app/backend/routers/fix_pipeline.py")
    # Local import is fine; assert both imports and site.
    assert "from services.http import ext_client" in src
    assert 'ext_client("github", timeout=httpx.Timeout(10.0))' in src
    assert "httpx.AsyncClient(timeout=10.0) as cx" not in src


# ─── Batch-level guards ────────────────────────────────────────────

def test_batch_8a_total_sites_migrated():
    """Sum across all 7 files must show at least 10 ext_client
    call sites migrated in this batch. Guards against a partial
    revert where one file gets rolled back without the tests catching."""
    total = 0
    for path in (
        "/app/backend/routers/admin_qa.py",
        "/app/backend/routers/admin_bin.py",
        "/app/backend/routers/admin_projects_brain.py",
        "/app/backend/routers/admin_ops_config.py",
        "/app/backend/routers/admin_users.py",
        "/app/backend/routers/upload.py",
        "/app/backend/routers/fix_pipeline.py",
    ):
        total += _read(path).count("ext_client(")
    assert total >= 10, (
        f"Expected ≥10 ext_client sites across Batch 8a files, "
        f"found {total}. Migration reverted?"
    )


def test_batch_8a_excludes_stay_raw():
    """Batch 8a excludes github_oauth.py (Batch 8b) and
    codebase_health.py (mini-batch). Guard that they are NOT
    accidentally migrated in this batch."""
    oauth = _read("/app/backend/routers/github_oauth.py")
    ch = _read("/app/backend/routers/codebase_health.py")

    # github_oauth may or may not have ext_client already (Wave 7B did
    # a different site). What matters is _gh_primary_email specifically
    # — Batch 8b will migrate that. For now assert file loads.
    assert "def _gh_primary_email" in oauth, (
        "github_oauth.py::_gh_primary_email still exists and is "
        "queued for Batch 8b."
    )

    # codebase_health.py MUST still have the 3-value httpx.Timeout
    # tuple — its mini-batch will migrate this + add a preserve test.
    assert "httpx.Timeout(45.0, connect=6.0, read=15.0)" in ch, (
        "codebase_health.py's deliberate 3-value timeout tuple must "
        "stay until its own mini-batch adds the preserve-test. "
        "Do NOT migrate it in Batch 8a."
    )
