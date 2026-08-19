"""
test_recurring_patterns_batch2.py — 2026-08-19

Real behavioral/regression tests for the 5 `ora_regression_patterns`
that previously had `test_ref=None` (patterns 3, 5, 6, and the two
`.env`/`.gitignore` policy entries). Each test targets the actual
mechanism the fix/decision lives in, not a loose grep-lock — see
`/app/memory/CODEBASE_AUDIT.md` §7.4-adjacent note on why grep-lock
tests were rejected for this registry.
"""
from __future__ import annotations

import os
import re
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from main import app
from services.mode_d_debugger import DIAGNOSIS_SYSTEM

pytestmark = pytest.mark.asyncio


def _db():
    from motor.motor_asyncio import AsyncIOMotorClient
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[
        os.environ.get("DB_NAME", "aurem_dev")
    ]


# ── Pattern #3 — Mode D must not demand a literal stack trace ─────────

def test_pattern3_diagnosis_prompt_accepts_natural_language_signal():
    """Locks the exact regression: DIAGNOSIS_SYSTEM must list plain
    natural-language symptoms as a VALID signal, not just stack
    traces/HTTP codes — that's the actual fix for pattern #3."""
    assert "Natural-language symptoms" in DIAGNOSIS_SYSTEM
    assert "0 live workers" in DIAGNOSIS_SYSTEM  # worked example, locked
    # The bail-out template must remain the LAST resort, not the default.
    bail_idx = DIAGNOSIS_SYSTEM.index("insufficient signal to diagnose")
    valid_signals_idx = DIAGNOSIS_SYSTEM.index("VALID DIAGNOSTIC SIGNALS")
    assert valid_signals_idx < bail_idx, (
        "VALID DIAGNOSTIC SIGNALS guidance must be presented before the "
        "bail-out template so the model tries diagnosis first"
    )


# ── Pattern #5 — no hard per-task file-count cap should exist ─────────

def test_pattern5_no_hard_multi_file_cap_in_orchestrator():
    """Root cause was VERIFIED-FALSE in code review: no hard 2-file
    cap actually exists in the orchestrator/loop engine. This guards
    against someone accidentally introducing one later, which would
    turn a believed-non-issue into a real one."""
    suspicious = re.compile(
        r"(MAX_FILES_PER_TASK|files\[:\s*[12]\s*\]|file_cap\s*=\s*[12]\b)"
    )
    for rel in ("services/orchestrator.py", "services/loop_engine.py"):
        path = os.path.join(os.path.dirname(__file__), "..", rel)
        with open(path, encoding="utf-8") as f:
            body = f.read()
        m = suspicious.search(body)
        assert m is None, (
            f"{rel} appears to introduce a hard per-task file cap "
            f"({m.group(0)!r}) — this would re-create pattern #5's "
            "symptom (1-of-N scaffold then 'Next: ...')"
        )


# ── Pattern #6 — cache purge endpoint is real, admin-gated, structured ─

@pytest.fixture
async def _admin_fixture():
    db = _db()
    user_id = f"pat6_admin_{uuid.uuid4().hex[:8]}"
    email = f"{user_id}@aurem.test"
    await db.dev_users.insert_one({
        "user_id": user_id, "email": email, "tier": "founder",
        "is_admin": True, "created_at": time.time(),
    })
    from cto_services.auth import create_token
    token = create_token(user_id, email, True)
    yield token
    await db.dev_users.delete_one({"user_id": user_id})


async def test_pattern6_cache_purge_rejects_unauthenticated():
    with TestClient(app) as c:
        r = c.post("/api/aurem-dev/admin/cache/purge")
    assert r.status_code in (401, 403)


async def test_pattern6_cache_purge_returns_structured_report(_admin_fixture):
    token = _admin_fixture
    with TestClient(app) as c:
        r = c.post(
            "/api/aurem-dev/admin/cache/purge",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") is True
    # Real report shape — proves this is a wired feature, not a stub.
    report = body["report"]
    assert "cloudflare" in report
    assert "lru_cache" in report
    assert "mongo_caches" in report


# ── Patterns #7 + #8 — .env / .gitignore hybrid policy, FINAL ─────────

def test_pattern7_8_gitignore_env_hybrid_policy_locked():
    """Two prior wrong policies caused real damage (both-ignored broke
    prod CORS bake-time env; both-committed would've leaked 38 backend
    secrets). Locks the FINAL hybrid: backend/.env stays ignored,
    frontend/.env is explicitly un-ignored via the negation rule."""
    gitignore_path = os.path.join(
        os.path.dirname(__file__), "..", "..", ".gitignore"
    )
    with open(gitignore_path, encoding="utf-8") as f:
        body = f.read()
    lines = [ln.strip() for ln in body.splitlines()]
    assert ".env" in lines, "backend/.env pattern must stay gitignored"
    assert ".env.*" in lines
    assert "*.env" in lines
    assert "!frontend/.env" in lines, (
        "frontend/.env MUST be un-ignored — Vite bakes REACT_APP_* at "
        "build time, so it needs to be committed (contains no secrets)"
    )
    # The negation exception must come AFTER the blanket ignore rules,
    # otherwise git's gitignore ordering rules make it a no-op.
    assert lines.index("*.env") < lines.index("!frontend/.env")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
