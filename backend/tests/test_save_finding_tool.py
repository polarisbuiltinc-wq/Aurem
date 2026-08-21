"""
tests/test_save_finding_tool.py — 2026-08-22

Regression coverage for `save_finding` (services/local_tools.py):
persists ONE conversational-audit finding into `cto_open_findings`,
the same store the automated scanner pipelines write to. Closes the
gap where ORA's chat security/bug audits (## CRITICAL / ## HIGH
markdown reports) vanished into scroll-back with nothing tracked.
"""
import time
import pytest


@pytest.mark.asyncio
async def test_save_finding_persists_critical_finding():
    from cto_services.db import get_db, set_db
    if get_db() is None:
        import os
        from motor.motor_asyncio import AsyncIOMotorClient
        set_db(AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ.get("DB_NAME", "aurem_dev")])

    from services.bin_context import BINContext
    from services.local_tools import invoke_local_tool

    project_id = f"pytest_save_finding_{int(time.time())}"
    bc = BINContext(bin_id="pytest_user", pid=project_id, repo_owner="octocat",
                     repo_name="Hello-World", branch="main", pat="fake", is_founder=False)
    ctx = {"user_id": "pytest_user", "bin_ctx": bc}

    r = await invoke_local_tool("save_finding", {
        "title": "Hardcoded JWT secret fallback",
        "severity": "critical",
        "file": "auth.py",
        "line": 42,
        "description": "JWT secret falls back to a hardcoded default if env var missing.",
        "fix_hint": "Require JWT_SECRET env var, fail fast if unset.",
    }, ctx)

    assert r["ok"] is True
    assert r["saved"] is True
    assert r["severity"] == "critical"

    db = get_db()
    doc = await db.cto_open_findings.find_one({"project_id": project_id, "user_id": "pytest_user"})
    assert doc is not None
    assert doc["severity"] == "critical"
    assert doc["title"] == "Hardcoded JWT secret fallback"
    assert doc["file"] == "auth.py"
    assert doc["status"] == "open"
    assert doc["scanner"] == "ora_chat_audit"
    await db.cto_open_findings.delete_many({"project_id": project_id})


@pytest.mark.asyncio
async def test_save_finding_medium_severity_not_written_to_backlog():
    """persist_findings_to_backlog only writes critical/high — medium/
    low are filtered out by design (they don't surface on the
    reminder strip). The tool must report saved=False, not error."""
    from cto_services.db import get_db, set_db
    if get_db() is None:
        import os
        from motor.motor_asyncio import AsyncIOMotorClient
        set_db(AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ.get("DB_NAME", "aurem_dev")])

    from services.bin_context import BINContext
    from services.local_tools import invoke_local_tool

    project_id = f"pytest_save_finding_medium_{int(time.time())}"
    bc = BINContext(bin_id="pytest_user", pid=project_id, repo_owner="octocat",
                     repo_name="Hello-World", branch="main", pat="fake", is_founder=False)
    ctx = {"user_id": "pytest_user", "bin_ctx": bc}

    r = await invoke_local_tool("save_finding", {
        "title": "Missing docstring on helper function",
        "severity": "medium",
        "file": "utils.py",
    }, ctx)
    assert r["ok"] is True
    assert r["saved"] is False


@pytest.mark.asyncio
async def test_save_finding_requires_valid_severity():
    from services.bin_context import BINContext
    from services.local_tools import invoke_local_tool

    bc = BINContext(bin_id="pytest_user", pid="pytest_proj", repo_owner="octocat",
                     repo_name="Hello-World", branch="main", pat="fake", is_founder=False)
    ctx = {"user_id": "pytest_user", "bin_ctx": bc}

    r = await invoke_local_tool("save_finding", {"title": "Something", "severity": "urgent"}, ctx)
    assert r["ok"] is False


def test_save_finding_is_registered_as_a_tool():
    from services.local_tools import TOOL_SPECS, LOCAL_TOOLS
    names = [t["name"] for t in TOOL_SPECS]
    assert "save_finding" in names
    assert "save_finding" in LOCAL_TOOLS


def test_persona_instructs_ora_to_call_save_finding():
    from services.orchestrator import AUREM_CTO_PERSONA
    assert "save_finding" in AUREM_CTO_PERSONA
