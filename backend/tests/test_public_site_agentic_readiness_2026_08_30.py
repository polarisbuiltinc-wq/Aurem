"""
tests/test_public_site_agentic_readiness_2026_08_30.py

AUREM public-site "agentic readiness" fixes (is-agentic.com scan, 61/100).
Scope note: that scanner measures "are you a machine-API PLATFORM"
(OAuth machine-auth, dev portal, CLI) — AUREM is a product SaaS, not a
machine-API platform, so only the findings real for AUREM's own public
site (also our AI-citation moat) are fixed. See llms.txt's own
"When to use ORA" section for the explicit scope statement.

Named checks:
  t_llms_when_to_use_present   — P1 #9: specific use-case guidance, not marketing copy
  t_llms_no_cli_claim          — #14: no unpublished-CLI claim anywhere in llms.txt/llms-full.txt
  t_llms_dev_resources_listed  — P1 #12: /api/docs, /api/redoc, /api/openapi.json listed
  t_api_docs_title_has_product_names — P1 #12: Swagger UI title carries AUREM + ORA
  t_api_versioning_policy_page_exists — P2 #13: deprecation policy documented
  t_vary_header_present        — P2 #4
  t_rate_limit_headers_present — P2 #10
  t_prerender_covers_trust_pages — P0 #1 (build-output check, skipped if no build yet)
  t_dockerfile_real_404_allowlist — P0 #2 (nginx config shape check)
"""
from __future__ import annotations
import os
import re
from pathlib import Path

import pytest

os.environ.setdefault("JWT_SECRET", os.environ.get("JWT_SECRET", "test-secret"))

from fastapi.testclient import TestClient  # noqa: E402
from main import app  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
LLMS = FRONTEND / "public" / "llms.txt"
LLMS_FULL = FRONTEND / "public" / "llms-full.txt"
DOCKERFILE = FRONTEND / "Dockerfile"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_t_llms_when_to_use_present():
    src = LLMS.read_text()
    assert "## When to use ORA" in src
    assert "Do not use ORA for" in src
    # specific use cases, not marketing copy
    assert "GitHub repository" in src
    assert "Non-GitHub source control" in src


CLI_CLAIM = re.compile(
    r"(published|available)\s+(as\s+)?(a\s+)?(the\s+)?(aurem|ora)\s+cli|"
    r"npm install (-g )?(aurem|ora)|pip install (aurem|ora)|npx (aurem|ora)",
    re.I,
)


def test_t_llms_no_cli_claim():
    for p in (LLMS, LLMS_FULL):
        src = p.read_text()
        m = CLI_CLAIM.search(src)
        assert not m, f"{p.name} still claims a published AUREM/ORA CLI: {m.group(0)!r}"
    # explicit honesty statement present in the trimmed file
    assert "Not a published CLI" in LLMS.read_text()


def test_t_llms_dev_resources_listed():
    src = LLMS.read_text()
    assert "/api/docs" in src
    assert "/api/redoc" in src
    assert "/api/openapi.json" in src
    assert "## Developer resources" in src


def test_t_api_docs_title_has_product_names(client):
    r = client.get("/api/openapi.json")
    assert r.status_code == 200
    title = r.json()["info"]["title"]
    assert "AUREM" in title
    assert "ORA" in title


def test_t_api_versioning_policy_page_exists():
    p = FRONTEND / "public" / "policies" / "api-versioning.md"
    assert p.exists()
    src = p.read_text()
    assert len(src) > 300
    assert "Deprecation" in src or "Sunset" in src
    policy_page_src = (FRONTEND / "src" / "pages" / "PolicyPage.jsx").read_text()
    assert '"api-versioning"' in policy_page_src
    app_src = (FRONTEND / "src" / "App.jsx").read_text()
    # 2026-08-30 · testing-agent finding: a route starting with "/api"
    # collides with the "/api* -> backend" ingress rule and would 404
    # as JSON before React ever renders it. Must live OUTSIDE /api/*.
    assert 'path="/policies/api-versioning"' in app_src
    assert 'path="/api-versioning"' not in app_src


def test_t_no_frontend_route_starts_with_api(client):
    """Static ingress-safety guard: NO <Route path> may start with
    "/api" — the K8s ingress hard-routes any /api* path to the FastAPI
    backend, so such a route would 404 as JSON before React renders."""
    app_src = (FRONTEND / "src" / "App.jsx").read_text()
    for m in re.finditer(r'<Route\s+path="([^"]+)"', app_src):
        path = m.group(1)
        assert not path.startswith("/api"), f"route {path!r} would be shadowed by the /api ingress rule"


def test_t_vary_header_present(client):
    r = client.get("/api/aurem-dev/usage/public/stats")
    assert r.status_code == 200
    vary = r.headers.get("vary", "")
    assert "Accept" in vary


def test_t_rate_limit_headers_present(client):
    r = client.get("/api/aurem-dev/usage/public/stats")
    assert r.status_code == 200
    assert "x-ratelimit-limit" in r.headers
    assert "x-ratelimit-remaining" in r.headers
    assert "x-ratelimit-reset" in r.headers


def test_t_prerender_covers_trust_pages():
    """Only enforced when dist/ exists (a build has run this session)."""
    dist = FRONTEND / "dist"
    if not (dist / "index.html").exists():
        pytest.skip("no dist/ build present")
    home = (dist / "index.html").read_text()
    assert len(home) > 500
    assert "<h1>" in home
    for rel, needle in (
        ("about/index.html", "Polaris Built Inc"),
        ("contact/index.html", "security@auremcto.com"),
        ("privacy/index.html", "Application No. 2492318"),
    ):
        p = dist / rel
        assert p.exists(), f"missing snapshot {rel}"
        html = p.read_text()
        assert len(html) > 500
        assert "<h1>" in html
        assert needle in html


def test_t_dockerfile_real_404_allowlist():
    src = DOCKERFILE.read_text()
    # the final catch-all must be a real 404, not a blanket SPA fallback
    assert "try_files $uri $uri/ =404;" in src
    assert "error_page 404 /404.html;" in src
    # the allowlist regex must still let known app routes fall through
    assert "dashboard" in src and "admin" in src and "about" in src and "contact" in src
