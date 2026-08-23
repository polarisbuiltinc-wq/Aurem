"""
Iter 205 regression — encrypted-PAT decryption in tool/skill resolvers.

Bug history:
  `cto_projects.github_token` is stored as Fernet ciphertext (`v1:…`).
  Tool functions in `services/local_tools.py` and `services/dev_skills.py`
  used to call `proj.get("github_token")` directly and pass that raw
  ciphertext to GitHub's API, which returned `401 Bad credentials` for
  every Mode-D/E scan and every tool invocation by the orchestrator.

Fix: both files' `_resolve_project()` now decrypt in place and fall back
to the user's GitHub OAuth `access_token` when no per-project PAT is
stored (OAuth-only flow).

Iter 212m-225 (boundary refactor) moved the PAT helpers from
`routers/cto_projects.py` into `services/pat_vault.py` so tools no
longer reach across the router/service boundary.

2026-06 PAT-removal update: `decrypt_pat`/`get_user_gh_token` (and any
OAuth fallback) no longer exist — App-only auth means
`pat_vault.get_repo_token_or_error(proj)` is the only resolver, and it
either mints a fresh GitHub App installation token or returns a typed
error code, never a decrypted PAT. These tests were rewritten to patch
that actual current call site instead of asserting the removed
decrypt/OAuth-fallback contract.

These tests cover the contract:
  - `_resolve_project` attaches the App-installation token
    `get_repo_token_or_error` returns
  - when the App isn't connected, `github_token` is None (logged, not
    raised) — no PAT/OAuth fallback exists any more
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# Make `backend/` importable when pytest runs from repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def fake_db():
    """Mock get_db() so _resolve_project hits the fake collection."""
    db = types.SimpleNamespace()
    db.cto_projects = types.SimpleNamespace(find_one=AsyncMock())
    return db


@pytest.mark.asyncio
async def test_local_tools_resolve_uses_app_token(fake_db):
    """App-only (2026-06+): _resolve_project attaches whatever
    get_repo_token_or_error mints — no PAT decryption exists any more."""
    from services import local_tools

    fake_db.cto_projects.find_one.return_value = {
        "project_id":      "p1",
        "user_id":         "u1",
        "auth_method":     "github_app",
        "installation_id": 42,
        "github_owner":    "tejisandhu",
        "github_repo":     "auremcto",
    }

    with patch.object(local_tools, "get_db", return_value=fake_db), \
         patch("services.pat_vault.get_repo_token_or_error",
               new=AsyncMock(return_value=("ghs_APP_TOKEN", None, None))):
        proj = await local_tools._resolve_project("u1", "p1")

    assert proj is not None
    assert proj["github_token"] == "ghs_APP_TOKEN"


@pytest.mark.asyncio
async def test_local_tools_resolve_none_token_when_app_not_connected(fake_db):
    """Project not connected via the GitHub App — github_token is None
    (logged, not raised). No PAT/OAuth fallback exists any more."""
    from services import local_tools

    fake_db.cto_projects.find_one.return_value = {
        "project_id":   "p1",
        "user_id":      "u1",
        "github_owner": "x",
        "github_repo":  "y",
    }

    with patch.object(local_tools, "get_db", return_value=fake_db), \
         patch("services.pat_vault.get_repo_token_or_error",
               new=AsyncMock(return_value=(
                   None, "app_installation_missing", "not connected"))):
        proj = await local_tools._resolve_project("u1", "p1")

    assert proj is not None
    assert proj["github_token"] is None


@pytest.mark.asyncio
async def test_dev_skills_resolve_uses_app_token(fake_db):
    from services import dev_skills

    fake_db.cto_projects.find_one.return_value = {
        "project_id":      "p1",
        "user_id":         "u1",
        "auth_method":     "github_app",
        "installation_id": 7,
        "github_owner":    "x",
        "github_repo":     "y",
    }

    # Iter 212m-139 — dev_skills._resolve_project delegates to
    # local_tools._resolve_project (single-source-of-truth). Patch
    # local_tools.get_db too since that's where the DB lookup happens.
    from services import local_tools as _lt
    with patch.object(dev_skills, "get_db", return_value=fake_db), \
         patch.object(_lt, "get_db", return_value=fake_db), \
         patch("services.pat_vault.get_repo_token_or_error",
               new=AsyncMock(return_value=("ghs_DEV_TOKEN", None, None))):
        proj = await dev_skills._resolve_project("u1", "p1")

    assert proj is not None
    assert proj["github_token"] == "ghs_DEV_TOKEN"


@pytest.mark.asyncio
async def test_resolve_returns_none_for_home_or_missing_ids(fake_db):
    from services import local_tools, dev_skills

    with patch.object(local_tools, "get_db", return_value=fake_db), \
         patch.object(dev_skills,  "get_db", return_value=fake_db):
        assert await local_tools._resolve_project("",   "p1")   is None
        assert await local_tools._resolve_project("u1", "")     is None
        assert await local_tools._resolve_project("u1", "home") is None
        assert await dev_skills._resolve_project("u1", "home")  is None
