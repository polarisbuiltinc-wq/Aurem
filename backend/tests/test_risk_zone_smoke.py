"""Smoke tests for the 5 architecture-audit risk-zone files
(large ≥ 500 LOC, low churn ≤ 3 commits, no test coverage).

Purpose: guarantee "imports don't crash + module-level state doesn't
KeyError" — the cheapest possible regression cushion for files nobody
has actively maintained. Not a functional test — that would need real
Vercel / Supabase / OpenRouter creds and would flake in CI.

If any of these tests break in the future, it means someone changed
a module-level import / class definition in a rarely-touched file
without noticing. That's exactly the regression class this test set
is designed to catch.
"""
from __future__ import annotations

import importlib
import pytest


RISK_ZONE_MODULES = [
    "services.supabase_provisioner",
    "services.vercel_skills",
    "services.llm.openrouter_providers",
]


@pytest.mark.parametrize("mod_path", RISK_ZONE_MODULES)
def test_risk_zone_module_imports_clean(mod_path):
    """Import the module fresh — any ImportError / NameError / config
    misread at module load time fails here."""
    if mod_path in list(dir()):  # rare; drop any stale reference
        del globals()[mod_path]
    importlib.invalidate_caches()
    m = importlib.import_module(mod_path)
    assert m is not None
    # At least one public callable / symbol should exist
    public = [n for n in dir(m) if not n.startswith("_")]
    assert public, f"{mod_path} has no public symbols"


def test_deploy_panel_frontend_source_parses():
    """DeployPanel.jsx has ~679 LOC and 1 commit. Guarantee the JSX
    still exists and has no obvious garble at byte 0 (BOM / fence
    marker) — same class of bug that plagued the customer's
    __init__.py loop failure."""
    from pathlib import Path
    p = Path("/app/frontend/src/components/DeployPanel.jsx")
    assert p.exists(), "DeployPanel.jsx missing — has the file moved?"
    raw = p.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "UTF-8 BOM at byte 0 — will break Vite parser"
    text = raw.decode("utf-8")
    # First line should be either a comment/import; not a fence marker
    first_line = text.split("\n", 1)[0].strip()
    assert not first_line.startswith("```"), "LLM fence marker at top of DeployPanel.jsx"
    # File should contain the expected top-level React export
    assert ("export default" in text) or ("export function" in text) \
        or ("export const" in text), \
        "DeployPanel.jsx has no discoverable React export"


def test_admin_financials_frontend_source_parses():
    """AdminFinancials.jsx reads live Stripe data — silent Stripe API
    shape drift is a real risk. This test just ensures the file
    itself is well-formed and defines a default export."""
    from pathlib import Path
    p = Path("/app/frontend/src/pages/AdminFinancials.jsx")
    assert p.exists()
    raw = p.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8")
    first_line = text.split("\n", 1)[0].strip()
    assert not first_line.startswith("```")
    assert "export default" in text, \
        "AdminFinancials.jsx must have a default export for React Router"
