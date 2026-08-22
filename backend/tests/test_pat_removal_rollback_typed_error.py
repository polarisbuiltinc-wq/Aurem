"""Item 4 — synthetic Mongo rows + direct rollback_manager call MUST
return {'ok': False, 'reason': 'app_installation_missing'} for a
PAT-only project (no GitHub App installation).

This exercises the typed fail-closed path in
services/rollback_manager.py → services/pat_vault.get_repo_token.
Cleans up all injected rows afterwards.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

import pytest

sys.path.insert(0, "/app/backend")


def _load_env():
    from pathlib import Path
    for line in Path("/app/backend/.env").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k, v)


_load_env()


@pytest.mark.asyncio
async def test_rollback_pat_project_returns_app_installation_missing():
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    # Hydrate the App runtime config so pat_vault has enough context to
    # even attempt a token mint (though it should short-circuit at the
    # auth_method check before that).
    from services.github_app_config import set_runtime_github_app_config
    row = await db.admin_settings.find_one({"_id": "github_app_config"})
    if row:
        cfg = {k: v for k, v in row.items() if k != "_id"}
        set_runtime_github_app_config(cfg)

    now = time.time()
    proj = {
        "project_id": "p_ta_pat", "user_id": "u_ta",
        "auth_method": "pat", "github_owner": "TJSNDHU",
        "github_repo": "Aurem", "github_token": "v1:dead",
    }
    sess = {"loop_id": "l_ta_pat", "user_id": "u_ta",
            "project_id": "p_ta_pat", "status": "shipped"}
    outcome = {
        "loop_id": "l_ta_pat", "user_id": "u_ta",
        "project_id": "p_ta_pat",
        "commit_sha": "eeee00eeee00eeee00eeee00eeee00eeee00eeee",
        "reverted": False, "shipped_at": now,
    }

    # Clean any leftover rows first, then insert fresh.
    await db.cto_projects.delete_many({"project_id": "p_ta_pat"})
    await db.loop_sessions.delete_many({"loop_id": "l_ta_pat"})
    await db.loop_outcomes.delete_many({"loop_id": "l_ta_pat"})

    try:
        await db.cto_projects.insert_one(proj)
        await db.loop_sessions.insert_one(sess)
        await db.loop_outcomes.insert_one(outcome)

        from services.rollback_manager import execute_rollback
        result = await execute_rollback(
            db, target_sha="eeee00eeee00", triggered_by="ta",
        )

        assert result.get("ok") is False, result
        assert result.get("reason") == "app_installation_missing", (
            f"expected app_installation_missing, got: {result}"
        )
        hint = (result.get("hint") or "").lower()
        # Honest, App-centric guidance — never PAT-preflight language.
        assert "github app" in hint, f"hint missing GitHub-App reference: {hint}"
        assert "pat" not in hint.split(),  f"hint mentions PAT: {hint}"
        assert "preflight" not in hint, f"hint mentions preflight: {hint}"
    finally:
        # Cleanup synthetic rows unconditionally.
        await db.cto_projects.delete_many({"project_id": "p_ta_pat"})
        await db.loop_sessions.delete_many({"loop_id": "l_ta_pat"})
        await db.loop_outcomes.delete_many({"loop_id": "l_ta_pat"})
        client.close()
