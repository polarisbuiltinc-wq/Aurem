"""
Iter 212m-156 — Mobile dashboard drawer.

PROD QA (iter 212m-154 chat E2E) caught that the desktop sidebar
reveal logic (mouse-hover + left-edge `mousemove`) does NOTHING on
touch devices, so mobile users had NO WAY to switch repos, open
tools, or access settings/logout from the dashboard.

This iter adds a touch-friendly drawer:
  • `mobileSidebarOpen` state + `isMobile` matchMedia (<=900 px)
  • Hamburger button (top-left, fixed, z=1500) shown only on mobile
  • Backdrop overlay (z=1400) closes the drawer on tap
  • Sidebar slides in via translateX with a 240 ms cubic-bezier curve
  • Auto-close on repo select, tool click, settings, logout

Tests below pin the contract so the mobile drawer can't silently
regress.

Iter 356 UPDATE — the hamburger/drawer pattern was replaced by the
unified RailShell (rail visible on every viewport, flyout panels for
sections). Tests that pinned the old drawer now pin the NEW contract;
leftover drawer state in Dashboard.jsx awaits Phase 4 cleanup.
"""
import re
from pathlib import Path

_FRONTEND = Path(__file__).resolve().parent.parent.parent / "frontend" / "src"


def test_dashboard_has_mobile_matchmedia_breakpoint():
    """A 900-px breakpoint is required so the drawer logic kicks in
    at the same point the rest of the chrome's mobile rules do."""
    src = (_FRONTEND / "pages" / "Dashboard.jsx").read_text()
    assert 'matchMedia("(max-width: 900px)")' in src
    assert "setIsMobile" in src
    assert "setMobileSidebarOpen" in src


def test_dashboard_has_hamburger_button_with_testid():
    """Iter 356 — the hamburger/drawer was REPLACED by the unified
    RailShell (56px rail always visible on every viewport). The old
    hamburger must NOT come back."""
    src = (_FRONTEND / "pages" / "Dashboard.jsx").read_text()
    assert 'data-testid="mobile-sidebar-toggle"' not in src
    assert "<RailShell" in src


def test_dashboard_has_backdrop_with_testid_and_tap_to_close():
    """Iter 356 — backdrop overlay replaced by the RailShell flyout
    (closes on outside click / Escape / route change)."""
    src = (_FRONTEND / "pages" / "Dashboard.jsx").read_text()
    assert 'data-testid="mobile-sidebar-backdrop"' not in src
    rail = (_FRONTEND / "components" / "nav" / "RailShell.jsx").read_text()
    assert 'data-testid="rail-flyout"' in rail
    assert "Escape" in rail


def test_dashboard_sidebar_wrap_has_mobile_branch():
    """Iter 356 — no mobile branch needed: the rail renders identically
    on all viewports via RailShell railOnly."""
    src = (_FRONTEND / "pages" / "Dashboard.jsx").read_text()
    assert "<RailShell" in src
    assert "railOnly" in src


def test_sidebar_real_supports_after_action_callback():
    """Every nav action inside SidebarReal must call onAfterAction so
    Dashboard can close the mobile drawer on tool click / settings /
    logout / token recharge."""
    src = (_FRONTEND / "pages" / "Dashboard.jsx").read_text()
    assert "onAfterAction" in src
    # The new `_go` helper threads it through navigate().
    assert "onAfterAction?.()" in src


def test_repo_select_closes_mobile_drawer():
    """Iter 356 — repo switching now lives in the RailShell chat
    flyout; selecting a repo must close the flyout."""
    src = (_FRONTEND / "components" / "nav" / "RailShell.jsx").read_text()
    m = re.search(r"const selectRepo = useCallback\(.*?\n  \}, ", src, re.S)
    assert m, "selectRepo not found in RailShell"
    assert "setOpen(null)" in m.group(0)


def test_sidebar_collapsed_is_disabled_on_mobile():
    """The hover-driven `sidebarCollapsed` icon-rail mode should NOT
    fire on mobile — touch users see either the full drawer or
    nothing at all."""
    src = (_FRONTEND / "pages" / "Dashboard.jsx").read_text()
    assert "const sidebarCollapsed = !isMobile" in src


def test_sidebar_full_hide_uses_mobile_state_on_phones():
    src = (_FRONTEND / "pages" / "Dashboard.jsx").read_text()
    assert "const sidebarFullyHidden = isMobile" in src
    assert "!mobileSidebarOpen" in src
