"""
Phase 3 · Chunk D · Batch 7A — HTTP wrapper migration pinning tests.

Wave A of Batch 7: 4 non-auth files. Wave B (github_oauth +
github_app) ships after this wave is verified on prod, so a
post-deploy regression is debuggable by file rather than by
"one of six".

Scope of wave 7A (2026-02-12):
  • services/codebase_indexer.py  — 1 site (pooled tree walk)
    + 1 preserved type annotation on _gh_get
  • services/personal_track_smoke.py — 2 smoke probes
  • services/github_org_client.py — 5 GitHub org sites
  • services/project_brain.py     — 3 sites, with the deliberate
    4-second timeout on the chat-brain enrichment path preserved.

CRITICAL guard: project_brain.py line 115's `read=4.0` timeout
override MUST be present. The wrapper's default for "github" dep
is `read=20.0` — 5x too long. If a future refactor drops the
override, chat responses will slow ~15s on brain-cache-miss.
This test PINS the override so the founder gets an early signal
instead of a bug report.
"""


def test_codebase_indexer_migrated_annotation_preserved():
    """Type annotation `client: httpx.AsyncClient` on _gh_get
    stays — ext_client yields the same base type."""
    src = open("/app/backend/services/codebase_indexer.py").read()
    assert "from services.http import ext_client" in src
    assert 'ext_client(\n        "github"' in src or \
           'ext_client(\n            "github"' in src
    # Real client construction gone.
    assert "httpx.AsyncClient(timeout=30)" not in src
    # Type annotation preserved.
    assert "client: httpx.AsyncClient" in src


def test_personal_track_smoke_both_probes_migrated():
    src = open("/app/backend/services/personal_track_smoke.py").read()
    assert "from services.http import ext_client" in src
    assert src.count("ext_client(") >= 2
    assert "httpx.AsyncClient(timeout=20.0)" not in src


def test_github_org_client_all_five_sites_migrated():
    src = open("/app/backend/services/github_org_client.py").read()
    assert "from services.http import ext_client" in src
    assert src.count("ext_client(") >= 5
    # All 5 raw AsyncClient(timeout=_TIMEOUT) gone.
    assert "httpx.AsyncClient(timeout=_TIMEOUT)" not in src


def test_project_brain_tight_4s_timeout_preserved():
    """The critical guard: project_brain.py::_recent_commits_context
    MUST keep the 4-second read timeout. The wrapper's default
    for `github` dep is 20s — 5x too long for the chat-brain
    enrichment path, which is called in the hot path of chat
    responses. This override must survive the migration."""
    src = open("/app/backend/services/project_brain.py").read()
    assert "from services.http import ext_client" in src
    # There must be an ext_client call with read=4.0 SOMEWHERE.
    assert "read=4.0" in src, (
        "Migration dropped the deliberate 4-second timeout on "
        "the chat-brain enrichment path — chat responses will "
        "slow ~15s on cache-miss. Restore `read=4.0` in "
        "_recent_commits_context."
    )
    # Old raw AsyncClient with timeout=4 must be gone.
    assert "httpx.AsyncClient(timeout=4)" not in src


def test_project_brain_other_two_sites_use_8s():
    """The other two sites (_gh_list_files, _gh_read_small) keep
    their 8-second budget."""
    src = open("/app/backend/services/project_brain.py").read()
    assert "read=8.0" in src
    assert "httpx.AsyncClient(timeout=8.0)" not in src


def test_batch_7b_files_migrated_after_wave_a_verified():
    """This test flipped from "must be raw" to "must be migrated"
    once Wave 7A was verified on prod (built_at 18:44:51 on 2026-02-12).
    Wave 7B is now landing github_oauth + github_app together — see
    tests/test_phase3_http_wrapper_migration_batch7b.py for the
    detailed guards."""
    oauth_src = open("/app/backend/services/github_oauth.py").read()
    app_src = open("/app/backend/services/github_app.py").read()
    # No raw client construction in either module anymore.
    assert "httpx.AsyncClient(timeout=15)" not in oauth_src
    assert "httpx.AsyncClient(timeout=15.0)" not in app_src
