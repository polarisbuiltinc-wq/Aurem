"""
Phase 3 · Chunk D · Batch 7B — HTTP wrapper migration pinning tests.

Wave B of Batch 7: the 2 AUTH-CRITICAL files (github_oauth.py +
github_app.py). Shipped separately from Wave 7A so a
post-deploy regression is debuggable by file rather than by
"one of six".

Guards specifically requested by founder in the Wave 7A → 7B
sequencing plan:
  1. OAuth `exchange()` still hits `github.com` (the web host)
     — NOT `api.github.com`. Different endpoint, same dep
     name, intentionally routed through the shared "github"
     breaker so an outage fast-fails both paths together.
  2. `_INSTALL_TOKEN_CACHE` stays intact — it's a per-installation
     TTL dict, orthogonal to httpx, must not have been touched
     by the migration.
  3. All 9 sites now go through `ext_client("github", ...)`.
"""


def test_github_oauth_all_three_sites_migrated():
    src = open("/app/backend/services/github_oauth.py").read()
    assert "from services.http import ext_client" in src
    # 3 ext_client blocks (exchange, gh_user, gh_repos).
    assert src.count("ext_client(") >= 3
    assert "httpx.AsyncClient(timeout=15)" not in src


def test_github_oauth_exchange_still_hits_github_com_web_host():
    """The OAuth code exchange endpoint is on github.com (web host),
    NOT api.github.com. Migration must preserve this URL — if a
    future agent "helpfully" normalises it, OAuth login breaks."""
    src = open("/app/backend/services/github_oauth.py").read()
    assert '"https://github.com/login/oauth/access_token"' in src
    # Sanity — must NOT have accidentally been rewritten.
    assert '"https://api.github.com/login/oauth/access_token"' not in src


def test_github_app_all_six_sites_migrated():
    src = open("/app/backend/services/github_app.py").read()
    assert "from services.http import ext_client" in src
    # 6 ext_client blocks (get_installation_token, list_installations,
    # list_installations_for_user, list_installation_repos,
    # get_repo_via_installation, revoke_installation).
    assert src.count("ext_client(") >= 6
    assert "httpx.AsyncClient(timeout=15.0)" not in src


def test_github_app_install_token_cache_still_intact():
    """The per-installation TTL token cache is orthogonal to httpx
    and MUST survive the migration untouched. It's the reason a
    live app doesn't mint a fresh token on every request."""
    src = open("/app/backend/services/github_app.py").read()
    # Cache module-global still present.
    assert "_INSTALL_TOKEN_CACHE" in src
    assert "_INSTALL_TOKEN_CACHE[installation_id] = " in src
    # Eviction on 404 (installation deleted / suspended) still present.
    assert "_INSTALL_TOKEN_CACHE.pop(installation_id, None)" in src
    # Prune helper still present.
    assert "def _prune_install_token_cache" in src


def test_github_app_pagination_via_link_header_intact():
    """list_installations + list_installation_repos paginate via the
    GitHub `Link` header. `ext_client` yields a real httpx.AsyncClient
    so response headers work normally — pin the pattern so a naive
    refactor doesn't break pagination."""
    src = open("/app/backend/services/github_app.py").read()
    assert 'r.headers.get("link", "")' in src
    assert "url = _next_link(link)" in src
