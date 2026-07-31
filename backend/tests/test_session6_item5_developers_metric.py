"""
Session 6 · Item 5 regression contract — "Developers" metric wiring.

Real-user QA found the /admin "Users & Ships" tile rendered
"— Developers" (dash) while sibling tiles ("Tasks shipped", "Active
repos") showed real numbers. Root cause: frontend read
`stats?.developers` but the /usage/public/stats endpoint returns
`real_developers` + `users` (no `developers` field). Undefined →
"—" render.

This test locks two invariants:

  1. Backend endpoint `/api/aurem-dev/usage/public/stats` MUST
     continue to expose `real_developers` (test-account-filtered) +
     `users` (unfiltered) fields. If a future refactor renames these
     back to `developers`, the FE fix would silently break again.

  2. Frontend source MUST NOT reintroduce the broken
     `stats?.developers` read. Source-level lock catches accidental
     regressions.

ZERO MOCKS. Real HTTP request against the running backend.
"""
from __future__ import annotations

import os
import pathlib
import sys

import httpx
import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def _api_base() -> str:
    """Preview backend URL — same one the frontend hits. TestClient
    doesn't fire the startup lifespan so `get_db()` returns None and
    the stats endpoint reports `available:false` there. This test
    calls the LIVE preview backend to exercise the real DB path."""
    root = pathlib.Path("/app/frontend/.env").read_text()
    for line in root.splitlines():
        if line.startswith("REACT_APP_BACKEND_URL="):
            return line.split("=", 1)[1].strip().strip('"')
    raise RuntimeError("REACT_APP_BACKEND_URL missing from /app/frontend/.env")


def test_public_stats_exposes_real_developers_field():
    r = httpx.get(f"{_api_base()}/api/aurem-dev/usage/public/stats", timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("available") is True, body
    # The two fields the frontend reads (with real_developers preferred).
    assert "real_developers" in body, (
        f"missing `real_developers` field — frontend `Developers` "
        f"tile will render '—'. body={body}"
    )
    assert "users" in body, "missing `users` fallback field"
    # Both must be non-negative integers.
    assert isinstance(body["real_developers"], int) and body["real_developers"] >= 0, body
    assert isinstance(body["users"],           int) and body["users"]           >= 0, body


def test_admin_overview_jsx_does_not_read_broken_stats_developers_field():
    """Source-level guard: the `stats?.developers` read (no such
    field on the payload) must NEVER come back. The fix reads
    `stats?.real_developers ?? stats?.users`."""
    jsx = pathlib.Path("/app/frontend/src/pages/AdminOverview.jsx").read_text()
    # The specific broken pattern from the pre-fix code.
    broken = 'value={stats?.developers   ?? "—"}'
    assert broken not in jsx, (
        "AdminOverview.jsx has the pre-fix broken `stats?.developers` "
        "read back — Developers tile will show '—' again."
    )
    # The correct pattern must appear.
    correct = "stats?.real_developers ?? stats?.users"
    assert correct in jsx, (
        "AdminOverview.jsx no longer reads `real_developers` — "
        "the Session 6 · Item 5 fix has been reverted."
    )
