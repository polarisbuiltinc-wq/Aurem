"""
Phase 3 · Chunk D · Batch 4 — HTTP wrapper migration pinning tests.

Fourth wave of `httpx.AsyncClient` migrations onto `services/http`.
All 4 files in this batch touch the SAME dep (`github`) so the
retry_guard breaker for GitHub gets consolidated coverage across
the graph, repo-context, local-tools, and repo-heal call paths.

Scope of this batch (2026-02-12):
  • services/graph_builder.py — 2 sites (tree fetch + parallel
    file reads)
  • services/repo_context.py  — 3 sites (tree + file + subtree
    walk with pooled session)
  • services/local_tools.py   — 7 sites (contents walk, tree
    fetch, HEAD sha ref, tarball stream, tree fallback, code
    search, commit fetch)
  • services/repo_heal.py     — 1 site (pooled session used with
    the pre-existing retry_guard.call_with_retry wrapper — this
    is exactly the "multiple related calls in same pooled
    session" pattern ext_client is designed for)

Total: 13 sites migrated. All to the "github" dep so the breaker
is shared across every GitHub outbound call site in these paths.
"""


def test_graph_builder_all_sites_migrated():
    src = open("/app/backend/services/graph_builder.py").read()
    assert "from services.http import ext_client" in src
    assert src.count("ext_client(") >= 2
    assert "httpx.AsyncClient(timeout=15.0)" not in src
    assert "httpx.AsyncClient(timeout=10.0)" not in src


def test_repo_context_all_sites_migrated():
    src = open("/app/backend/services/repo_context.py").read()
    assert "from services.http import ext_client" in src
    assert src.count("ext_client(") >= 3
    # Raw AsyncClient with follow_redirects kwarg fully gone.
    assert "httpx.AsyncClient(timeout=20.0, follow_redirects=True)" not in src
    assert "httpx.AsyncClient(timeout=15.0, follow_redirects=True)" not in src


def test_local_tools_all_sites_migrated():
    src = open("/app/backend/services/local_tools.py").read()
    # 7 ext_client blocks (one per migrated site).
    assert src.count("from services.http import ext_client") >= 7
    assert src.count("ext_client(") >= 7
    # All 7 raw AsyncClient forms should be gone.
    for pattern in (
        "httpx.AsyncClient(timeout=15.0)",
        "httpx.AsyncClient(timeout=20.0)",
        "httpx.AsyncClient(timeout=10.0)",
        "httpx.AsyncClient(\n            timeout=_SNAPSHOT_DL_TIMEOUT_S",
    ):
        assert pattern not in src, f"expected {pattern!r} to be gone from local_tools.py"


def test_repo_heal_pooled_session_migrated():
    """repo_heal keeps httpx.AsyncClient as a TYPE ANNOTATION on
    _gh_get's `client` param — that stays. Only the actual client
    creation moves to ext_client."""
    src = open("/app/backend/services/repo_heal.py").read()
    assert "from services.http import ext_client" in src
    assert 'ext_client(\n            "github"' in src
    # No more raw AsyncClient CONSTRUCTION.
    assert "httpx.AsyncClient(timeout=_GH_TIMEOUT_S)" not in src
    # But the type annotation is still allowed.
    assert "client: httpx.AsyncClient" in src
