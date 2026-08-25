"""tests/resilience/test_global_exception_envelope.py - Resilience Layer
Phase 1 (2026-08-25).

Direct test of main.py's global exception handler: confirms the
response envelope now carries error_code + ref_id (Phase 1 addition)
without breaking the existing error_category/user_message contract
that frontend/src/hooks/useAsyncState.js already depends on.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest


def _fake_request(method="POST", path="/api/aurem-dev/chat/send"):
    return SimpleNamespace(method=method, url=SimpleNamespace(path=path))


class TestGlobalExceptionHandlerEnvelope:
    @pytest.mark.asyncio
    async def test_reproduces_the_production_incident_gracefully(self):
        """The actual bug that shipped: dict-shaped .get() on a str."""
        import main
        try:
            "backend/tests/test_admin_panel_features.py".get("path")
        except AttributeError as exc:
            resp = await main._global_exc_handler(_fake_request(), exc)
        body = json.loads(resp.body)
        assert resp.status_code == 500
        assert body["error_code"] == "SCHEMA_MISMATCH"
        assert body["ref_id"].startswith("ORA-")
        assert body["can_retry"] is False
        # No raw exception text anywhere in the user-facing response.
        rendered = json.dumps(body).lower()
        assert "attribute" not in rendered
        assert "'str' object" not in rendered

    @pytest.mark.asyncio
    async def test_generic_exception_gets_internal_unknown_code(self):
        import main
        resp = await main._global_exc_handler(_fake_request(), RuntimeError("boom"))
        body = json.loads(resp.body)
        assert body["error_code"] == "INTERNAL_UNKNOWN"
        assert "boom" not in json.dumps(body)

    @pytest.mark.asyncio
    async def test_existing_category_contract_still_present(self):
        """Regression guard: frontend's useAsyncState.js reads
        error_category + user_message (via `detail`) — must not
        disappear when error_code/ref_id are added."""
        import main
        resp = await main._global_exc_handler(_fake_request(), RuntimeError("boom"))
        body = json.loads(resp.body)
        assert "error_category" in body
        assert "detail" in body

    @pytest.mark.asyncio
    async def test_http_exception_passes_through_unchanged(self):
        import main
        from fastapi.exceptions import HTTPException
        resp = await main._global_exc_handler(
            _fake_request(), HTTPException(status_code=404, detail="not found"),
        )
        body = json.loads(resp.body)
        assert resp.status_code == 404
        assert body == {"detail": "not found"}

    @pytest.mark.asyncio
    async def test_cancelled_error_short_circuits_to_499(self):
        import main
        import asyncio as _aio
        resp = await main._global_exc_handler(_fake_request(), _aio.CancelledError())
        assert resp.status_code == 499

    @pytest.mark.asyncio
    async def test_ref_id_differs_across_two_failures(self):
        import main
        r1 = await main._global_exc_handler(_fake_request(), RuntimeError("a"))
        r2 = await main._global_exc_handler(_fake_request(), RuntimeError("b"))
        assert json.loads(r1.body)["ref_id"] != json.loads(r2.body)["ref_id"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
