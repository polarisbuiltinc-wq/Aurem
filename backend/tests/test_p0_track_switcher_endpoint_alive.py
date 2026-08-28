"""
tests/test_p0_track_switcher_endpoint_alive.py — Round-2 PR (P0-4).

Personal Track is HIDDEN from Settings (frontend gate), but the
backend /auth/set-track endpoint and /build* routes must stay fully
intact (future product surface, existing personal-track users still
depend on them). This guards against someone deleting the endpoint
outright while doing the frontend hide.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers.auth import router as auth_router


def _client():
    app = FastAPI()
    app.include_router(auth_router, prefix="/api/aurem-dev")
    return TestClient(app)


def test_set_track_endpoint_alive():
    """POST /auth/set-track must still resolve (not 404) — an
    unauthenticated call should fail on AUTH (401/403/422), never on
    routing."""
    client = _client()
    r = client.post(
        "/api/aurem-dev/auth/set-track",
        json={"track": "personal"},
    )
    assert r.status_code != 404, (
        "auth/set-track route must stay registered — Personal Track "
        "code/endpoint stays intact even though the Settings UI hides "
        "the switcher (P0-4)."
    )
    assert r.status_code in (401, 403, 422), (
        f"expected an auth/validation rejection, got {r.status_code}"
    )
