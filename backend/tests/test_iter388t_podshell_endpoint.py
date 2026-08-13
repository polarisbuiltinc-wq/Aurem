"""
Iter 388t — Bug 20 companion regression tests for /podshell endpoint.

Bug 20 root-cause fix (services/ora_context.py + services/local_tools.py
+ services/orchestrator.py) unlocked execute_bash for founder + Home
chat sessions.  But the UI has NO Home tab (deliberately removed per
past founder request in Iter 212m-20), so the fix was unreachable.

`/api/aurem-dev/dev-tools/podshell` gives the founder a first-class
alternative that:
  • bypasses the chat/LLM pipeline entirely
  • runs `validate_founder_pod_command` (chaining / traversal / secret
    denylist) before dispatch
  • dispatches through `execute_bash` with founder_pod_mode=True so
    the ora_boundary_violation refusal is lifted

Tests below verify the endpoint is registered, admin-gated, safety-
validated, and returns real stdout for the exact user Bug 20 command.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_podshell_returns_real_stdout_for_bug20_command(monkeypatch):
    """The user's Bug 20 exact command should now return real stdout
    via /podshell — no refusal template, no boundary violation."""
    from main import app

    async def fake_admin(authorization=None):
        return {
            "user_id":     "founder-uid",
            "email":       "founder@auremcto.com",
            "is_admin":    True,
            "tier":        "founder",
        }

    monkeypatch.setattr("routers.dev_tools.require_admin", fake_admin)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.post(
            "/api/aurem-dev/dev-tools/podshell",
            headers={"Authorization": "Bearer x"},
            json={"command": "ls /app/backend/routers/ | head -20"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True, f"podshell refused: {body!r}"
    # Real evidence — the routers directory always contains admin.py
    # and auth.py inside the first 20 alphabetical entries.
    assert "admin.py" in body["stdout"]
    assert "auth.py" in body["stdout"]
    # No refusal phrase, no boundary error.
    assert "I work with your repository only" not in body["stdout"]
    assert body.get("error_class") is None


@pytest.mark.asyncio
async def test_podshell_requires_admin(monkeypatch):
    """A non-founder token must NOT be able to use /podshell."""
    from main import app
    from fastapi import HTTPException

    async def fake_admin_denied(authorization=None):
        raise HTTPException(403, "Admin access required")

    monkeypatch.setattr("routers.dev_tools.require_admin", fake_admin_denied)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.post(
            "/api/aurem-dev/dev-tools/podshell",
            headers={"Authorization": "Bearer x"},
            json={"command": "ls /app/backend/routers/"},
        )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_podshell_blocks_chained_command(monkeypatch):
    """`;` and `&&` chaining must be refused with founder_pod_validation."""
    from main import app

    async def fake_admin(authorization=None):
        return {"user_id": "founder-uid", "email": "f@x.io", "is_admin": True}

    monkeypatch.setattr("routers.dev_tools.require_admin", fake_admin)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.post(
            "/api/aurem-dev/dev-tools/podshell",
            headers={"Authorization": "Bearer x"},
            json={"command": "ls /app ; cat /etc/passwd"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["error_class"] == "founder_pod_validation"
    assert "chaining" in (body["error"] or "").lower()


@pytest.mark.asyncio
async def test_podshell_blocks_secret_path(monkeypatch):
    """Even a founder cannot exfiltrate .env / /etc/shadow via /podshell."""
    from main import app

    async def fake_admin(authorization=None):
        return {"user_id": "founder-uid", "email": "f@x.io", "is_admin": True}

    monkeypatch.setattr("routers.dev_tools.require_admin", fake_admin)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        for cmd in ("cat /app/backend/.env", "cat /etc/shadow", "ls /root/.ssh/"):
            r = await ac.post(
                "/api/aurem-dev/dev-tools/podshell",
                headers={"Authorization": "Bearer x"},
                json={"command": cmd},
            )
            assert r.status_code == 200
            body = r.json()
            assert body["ok"] is False, f"{cmd!r} was not refused: {body!r}"
            assert body["error_class"] == "founder_pod_validation"
            assert "denylist" in (body["error"] or "").lower()


@pytest.mark.asyncio
async def test_podshell_blocks_path_traversal(monkeypatch):
    """`..` in any arg is refused."""
    from main import app

    async def fake_admin(authorization=None):
        return {"user_id": "founder-uid", "email": "f@x.io", "is_admin": True}

    monkeypatch.setattr("routers.dev_tools.require_admin", fake_admin)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.post(
            "/api/aurem-dev/dev-tools/podshell",
            headers={"Authorization": "Bearer x"},
            json={"command": "cat /app/../etc/passwd"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["error_class"] == "founder_pod_validation"
    assert "traversal" in (body["error"] or "").lower()


@pytest.mark.asyncio
async def test_podshell_blocks_outside_allowlist(monkeypatch):
    """Absolute paths outside /app, /tmp, /var, /etc, /usr must refuse."""
    from main import app

    async def fake_admin(authorization=None):
        return {"user_id": "founder-uid", "email": "f@x.io", "is_admin": True}

    monkeypatch.setattr("routers.dev_tools.require_admin", fake_admin)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.post(
            "/api/aurem-dev/dev-tools/podshell",
            headers={"Authorization": "Bearer x"},
            json={"command": "cat /opt/private/secret.txt"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["error_class"] == "founder_pod_validation"


@pytest.mark.asyncio
async def test_podshell_info_returns_whitelist(monkeypatch):
    """The /podshell/info helper surfaces the current allow/deny state
    so the founder can grep it without reading source code."""
    from main import app

    async def fake_admin(authorization=None):
        return {"user_id": "founder-uid", "email": "f@x.io", "is_admin": True}

    monkeypatch.setattr("routers.dev_tools.require_admin", fake_admin)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.get(
            "/api/aurem-dev/dev-tools/podshell/info",
            headers={"Authorization": "Bearer x"},
        )
    assert r.status_code == 200
    body = r.json()
    for path in ("/app", "/tmp", "/var", "/etc", "/usr"):
        assert path in body["allowed_paths"]
    assert "ls" in body["allowed_binaries"]
    assert "cat" in body["allowed_binaries"]
    assert ";" in body["chaining_operators_refused"]
    assert "&&" in body["chaining_operators_refused"]
    assert ".." in body["path_traversal_refused"]
    # Denylist mentions the .env files founders must NEVER surface.
    joined = "|".join(body["blocked_paths"])
    assert ".env" in joined
    assert "/etc/shadow" in joined


@pytest.mark.asyncio
async def test_podshell_empty_command_refused(monkeypatch):
    """Empty command bounces with 400 from Pydantic (min_length=1)."""
    from main import app

    async def fake_admin(authorization=None):
        return {"user_id": "founder-uid", "email": "f@x.io", "is_admin": True}

    monkeypatch.setattr("routers.dev_tools.require_admin", fake_admin)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.post(
            "/api/aurem-dev/dev-tools/podshell",
            headers={"Authorization": "Bearer x"},
            json={"command": ""},
        )
    # 422 from pydantic min_length validation.
    assert r.status_code in (400, 422)
