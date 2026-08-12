"""
Phase 3 · Chunk D · Batch 5 — HTTP wrapper migration pinning tests.

Fifth wave, per the founder-approved Option A: ship what's proven
low-risk (supabase_provisioner + dev_skills), defer
github_api_writer.py to a supervised session because it uses a
custom `httpx.Limits` connection pool + 60s timeout that
`ext_client()` doesn't yet support (needs a wrapper API upgrade).

Scope of this batch (2026-02-12):
  • services/supabase_provisioner.py — 5 sites (Supabase mgmt
    API: create, get, run_sql, delete, transfer)
  • services/dev_skills.py           — 7 sites (5 GitHub, 1 npm,
    1 PyPI). Two new dep names introduced: "npm" and "pypi" —
    both fall back to `_default` timeout in the wrapper, which
    is fine for these registry lookups.

Intentionally deferred (surveyed in
`/app/memory/BATCH_5_SURVEY_2026-02-12.md`):
  • services/github_api_writer.py — 2 sites. Uses a deliberately
    pinned `httpx.Limits(max_connections=20, max_keepalive=20)`
    pool tuned to the parallel `asyncio.gather` fan-out over
    blob uploads, and a 60s timeout for big commits.
    `ext_client()` doesn't accept `limits=` yet — needs
    wrapper API upgrade + supervised deploy.
  • services/github_deploy_service.py — 4 sites, needs a
    quick per-site read before it can be stamped safe.
"""


def test_supabase_provisioner_all_sites_migrated():
    src = open("/app/backend/services/supabase_provisioner.py").read()
    assert "from services.http import ext_client" in src
    assert src.count('ext_client(\n        "supabase"') >= 5
    # Raw AsyncClient with the _TIMEOUT constant must be gone.
    assert "httpx.AsyncClient(timeout=_TIMEOUT)" not in src


def test_dev_skills_all_sites_migrated():
    src = open("/app/backend/services/dev_skills.py").read()
    assert "from services.http import ext_client" in src
    # 5 github sites, 1 npm site, 1 pypi site → 7 ext_client calls.
    assert src.count("ext_client(") >= 7
    assert 'ext_client(\n            "github"' in src or \
           'ext_client(\n        "github"' in src
    assert 'ext_client(\n                "npm"' in src or \
           'ext_client(\n            "npm"' in src
    assert 'ext_client(\n                "pypi"' in src or \
           'ext_client(\n            "pypi"' in src
    # No more raw AsyncClient in the module.
    assert "httpx.AsyncClient(timeout=15.0)" not in src
    assert "httpx.AsyncClient(timeout=20.0)" not in src
    assert "httpx.AsyncClient(timeout=10.0)" not in src


def test_github_api_writer_intentionally_deferred():
    """POST-SUB-BATCH-3 (2026-02-12): the deferral that this test
    originally guarded is now COMPLETE. Sub-batch 1 upgraded ext_client
    with a `limits=` kwarg; Sub-batch 2 migrated the 3 read helpers;
    Sub-batch 3 migrated commit_files + revert_commit writes. This
    guard is flipped to lock in the post-migration invariant:

      1. No raw httpx.AsyncClient(timeout=60.0, limits=_LIMITS) sites
      2. _LIMITS module constant removed (each ext_client site now
         inlines explicit `httpx.Limits(max_connections=20, ...)`)

    If a future refactor regresses either, this test breaks the build.
    """
    src = open("/app/backend/services/github_api_writer.py").read()
    # No more raw AsyncClient with the Sub-batch 2 sentinel signature.
    assert src.count("httpx.AsyncClient(timeout=60.0, limits=_LIMITS)") == 0
    # And the module-level constant is gone (per-site limits now).
    assert "_LIMITS = httpx.Limits(max_connections=20, max_keepalive_connections=20)" not in src
