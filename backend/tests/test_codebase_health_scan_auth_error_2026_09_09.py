"""
tests/test_codebase_health_scan_auth_error_2026_09_09.py

Founder-reported bug: "codebase health nahi kaam kar raha, score show
nahi ho raha" (Codebase Health scan not working, score never shows).

Reproduced directly (preview, curl): `POST /codebase-health/scan` for a
project whose GitHub App linkage was missing/stale crashed with an
UNHANDLED `GithubAppAuthError` from `get_repo_token(proj)` — no
try/except around that call — surfacing to the client as a raw
"client disconnected or upstream error" instead of a clean message,
and the scan never got far enough to compute/persist anything (so
`/codebase-health/last` stays `score=None` forever — the page is
permanently stuck on "unscanned").

Fix: wrap `get_repo_token(proj)` in `routers/codebase_health.py::scan`
with the same try/except pattern already used for `_build_text_cache`
right below it, mapping `GithubAppAuthError.code` to a clean
HTTPException (400 for user-actionable missing/revoked install, 502
for transient GitHub-side hiccups).
"""
import pytest
from unittest.mock import AsyncMock, patch


def _extract_scan_fn_src():
    import inspect
    from routers import codebase_health
    return inspect.getsource(codebase_health.scan)


def test_t_scan_wraps_get_repo_token_in_try_except():
    src = _extract_scan_fn_src()
    assert "GithubAppAuthError" in src
    idx_get_token = src.index("await get_repo_token(proj)")
    idx_try = src.rindex("try:", 0, idx_get_token)
    idx_except = src.index("except GithubAppAuthError", idx_get_token)
    assert idx_try < idx_get_token < idx_except, (
        "get_repo_token(proj) must be inside a try/except GithubAppAuthError block"
    )


@pytest.mark.asyncio
async def test_t_scan_returns_clean_400_on_missing_install():
    from routers import codebase_health
    from services.pat_vault import GithubAppAuthError
    from fastapi import HTTPException

    fake_db = AsyncMock()
    fake_db.cto_projects.find_one = AsyncMock(return_value={
        "github_owner": "acme", "github_repo": "widgets", "user_id": "u1",
    })

    async def _raise_missing(proj):
        raise GithubAppAuthError(
            "app_installation_missing",
            "This project isn't connected through the AUREM GitHub App.",
        )

    with patch("routers.codebase_health.current_dev",
               new=AsyncMock(return_value={"user_id": "u1", "is_admin": True})), \
         patch("routers.codebase_health.get_db", return_value=fake_db), \
         patch("services.pat_vault.get_repo_token", side_effect=_raise_missing):
        with pytest.raises(HTTPException) as exc_info:
            await codebase_health.scan(
                {"project_id": "p1", "categories": ["code_quality"]},
                authorization="Bearer fake",
            )
        assert exc_info.value.status_code == 400
        assert "AUREM GitHub App" in str(exc_info.value.detail)
