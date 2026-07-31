"""
Session 6 · Item 1 — VS Code Marketplace live-status endpoint contract.

Real-user QA discovered the /admin panel was hardcoding
"VS Code extension" as `status="live"` even though the founder
hadn't published to the Marketplace yet (real 404 on the itemName).
This is the same anti-pattern as the earlier Supabase / Vercel
silent-no-op fix — hardcoded fake-green.

The fix installs a real Marketplace probe endpoint at
`/api/aurem-dev/admin/qa/vscode-marketplace-status`. This test
locks the contract of that endpoint against real network:

  • Hitting a KNOWN-unpublished itemName (`auremcto.aurem-cto`)
    MUST return `published=false, reason="not_published"` — proving
    the badge on the admin panel will render amber, not fake-green.
  • Payload shape MUST include `url`, `checked_at`, `cache_ttl_s`,
    `http_code`, `detail` for the frontend to render a helpful note.
  • Cache MUST kick in on the second call within TTL — no double
    hit on marketplace.visualstudio.com per admin page load.

ZERO MOCKS. Real HTTP request against Marketplace.
"""
from __future__ import annotations

import os
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

BACKEND = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


@pytest.fixture(scope="module")
def admin_client():
    """Real TestClient with a real founder JWT. Uses the test account
    from test_credentials.md — no mocks."""
    from main import app
    from cto_services.auth import create_token
    client = TestClient(app)
    # Mint a JWT directly via create_token so the test doesn't depend
    # on the login endpoint being live. is_admin=True satisfies both
    # `require_admin_dep` at router scope and `_require_admin` at
    # per-route scope.
    token = create_token(
        user_id="test-founder",
        email="test@aurem.dev",
        is_admin=True,
    )
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


def test_endpoint_returns_expected_payload_shape(admin_client):
    r = admin_client.get(
        "/api/aurem-dev/admin/qa/vscode-marketplace-status")
    assert r.status_code == 200, r.text
    body = r.json()
    # Contract lock — the frontend uses each of these fields.
    for key in ("item_name", "url", "checked_at", "cache_ttl_s",
                "published", "http_code", "reason", "detail"):
        assert key in body, f"missing key {key} in payload: {body}"
    assert body["item_name"] == "auremcto.aurem-cto"
    assert body["url"].startswith("https://marketplace.visualstudio.com/items?")
    assert isinstance(body["cache_ttl_s"], int) and body["cache_ttl_s"] > 0


def test_currently_unpublished_returns_not_published(admin_client):
    """The itemName `auremcto.aurem-cto` is REAL and CURRENTLY UNPUBLISHED
    (founder hasn't done the Azure DevOps PAT step). Marketplace returns
    404 for it. The probe MUST surface that faithfully as
    `published=false, reason="not_published"` — NOT fake-green."""
    r = admin_client.get(
        "/api/aurem-dev/admin/qa/vscode-marketplace-status")
    assert r.status_code == 200
    body = r.json()
    assert body["published"] is False, (
        f"expected published=False for unpublished itemName, got {body!r}"
    )
    # Either genuine 404 or Marketplace returned the "not found" HTML.
    assert body["reason"] in ("not_published", "check_failed"), body
    # A helpful detail message so the founder knows what step is missing.
    assert isinstance(body["detail"], str) and body["detail"], body


def test_response_is_cached(admin_client):
    """5-min in-memory cache prevents the admin dashboard from hammering
    marketplace.visualstudio.com on every page load. Two back-to-back
    calls must return the SAME `checked_at` epoch — proving the cache
    served the second request."""
    r1 = admin_client.get(
        "/api/aurem-dev/admin/qa/vscode-marketplace-status")
    r2 = admin_client.get(
        "/api/aurem-dev/admin/qa/vscode-marketplace-status")
    b1, b2 = r1.json(), r2.json()
    assert b1["checked_at"] == b2["checked_at"], (
        f"cache didn't serve second call: {b1['checked_at']} vs {b2['checked_at']}"
    )


def test_admin_auth_required():
    """Regression guard — this is an admin endpoint, MUST 401 without a
    valid founder JWT. The router-level `Depends(require_admin_dep)`
    on admin_qa.py handles this; belt-and-braces test."""
    from main import app
    unauth = TestClient(app)
    r = unauth.get(
        "/api/aurem-dev/admin/qa/vscode-marketplace-status")
    assert r.status_code in (401, 403), (
        f"expected 401/403 without auth, got {r.status_code}: {r.text}"
    )


# ═══ FeatureRow-level contract — the JSX must not hardcode "live" ═══
def test_admin_overview_jsx_no_longer_hardcodes_live_for_vscode():
    """Source-level lock: the JSX for the VS Code row MUST NOT contain
    the old `status="live"` hardcode. If it does, the fake-green badge
    is back. The fix routes status through the API response."""
    jsx_path = pathlib.Path("/app/frontend/src/pages/AdminOverview.jsx")
    src = jsx_path.read_text()
    # The old row was a single hardcoded line with status="live".
    old_pattern = (
        '<FeatureRow name="VS Code extension"       status="live"    '
        'note="aurem-cto-0.1.0.vsix shipped (Iter 72)" />'
    )
    assert old_pattern not in src, (
        "Hardcoded live-status VS Code FeatureRow is back — fake-green regression"
    )
    # And the replacement must route through the marketplace state.
    assert "vscodeMarketplace?.published === true" in src
    assert "not-published" in src
