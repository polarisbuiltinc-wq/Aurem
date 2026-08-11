"""
Iter 212m-31 — Persistent empty-state CTA banner.

Updated 2026-02-12 (Phase 4c · Chunk B+C):
  • Banner is now App-first. PAT walkthrough copy removed — the wizard
    is the single source of truth for connect UX.
  • Founder-spots ceiling is no longer hardcoded to 500; the banner
    reads `status.total` from /founder-offer/status so the promo
    ceiling can move without a frontend deploy.
"""


def test_banner_headline_and_cta_copy():
    src = open("/app/frontend/src/components/ConnectRepoBanner.jsx").read()
    assert "Connect a repo to unlock your free SEO fix" in src
    assert "Connect repo →" in src


def test_banner_counter_uses_dynamic_total_not_hardcoded():
    """Counter must interpolate `${total}` from /founder-offer/status
    — never a hardcoded ceiling like `of 500 founder spots`."""
    src = open("/app/frontend/src/components/ConnectRepoBanner.jsx").read()
    assert "${remaining} of ${total} founder spots remaining" in src
    assert "of 500 founder spots remaining" not in src


def test_banner_is_app_first_no_pat_walkthrough():
    """After Phase 4c the banner must not walk the user through PAT
    creation. The wizard handles the PAT fallback itself."""
    src = open("/app/frontend/src/components/ConnectRepoBanner.jsx").read()
    # No PAT settings deeplink, no fine-grained token guide in banner.
    assert "github.com/settings/tokens" not in src
    assert "Fine-grained tokens" not in src
    # App-first copy is present.
    assert "Aurem GitHub App" in src


def test_banner_polls_founder_offer_status_endpoint():
    src = open("/app/frontend/src/components/ConnectRepoBanner.jsx").read()
    assert '"/founder-offer/status"' in src
    assert "aurem_connect_banner_collapsed" in src


def test_dashboard_mounts_banner_only_when_zero_projects():
    src = open("/app/frontend/src/pages/Dashboard.jsx").read()
    assert "import ConnectRepoBanner from" in src
    assert "{projectCount === 0 && (" in src
    assert "<ConnectRepoBanner" in src
    assert "NewUserWizard" in src
    assert "openWizardFromBanner" in src


def test_banner_hides_when_offer_sold_out():
    src = open("/app/frontend/src/components/ConnectRepoBanner.jsx").read()
    assert "remaining ?? 0) <= 0) return null" in src
