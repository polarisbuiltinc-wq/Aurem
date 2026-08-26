"""
Iter 165 — Project Brain V2

Validates:
  - Module exports the V2 contract (BRAIN_VERSION, FULL_REFRESH_EVERY_N_TASKS,
    get_brain_v2, build_brain_v2, update_brain_after_task,
    format_brain_for_agent).
  - format_brain_for_agent stays compact (≤ ~300 token budget).
  - update_brain_after_task is non-destructive when DB is None.
  - Orchestrator imports brain helpers and injects on every turn with
    a 2s timeout (so a slow Mongo can never block chat).
  - cto_projects.py exposes /build-brain + /brain endpoints AND fires
    a fire-and-forget V2 update on every task completion (API + git
    workers both).
"""
from __future__ import annotations
import ast
import asyncio
import pathlib
import sys

import pytest

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


# ── Module surface ───────────────────────────────────────────────────

def test_brain_module_exports_v2_contract():
    from services import project_brain as pb
    for name in (
        "BRAIN_VERSION",
        "FULL_REFRESH_EVERY_N_TASKS",
        "get_brain_v2",
        "build_brain_v2",
        "update_brain_after_task",
        "format_brain_for_agent",
    ):
        assert hasattr(pb, name), f"project_brain.{name} missing"


def test_brain_version_is_2():
    from services.project_brain import BRAIN_VERSION
    assert BRAIN_VERSION == 2


def test_full_refresh_default_is_10():
    from services.project_brain import FULL_REFRESH_EVERY_N_TASKS
    assert FULL_REFRESH_EVERY_N_TASKS == 10


# ── format_brain_for_agent ───────────────────────────────────────────

def test_format_brain_empty_input_returns_empty_string():
    from services.project_brain import format_brain_for_agent
    assert format_brain_for_agent({}) == ""
    assert format_brain_for_agent(None) == ""  # type: ignore[arg-type]


def test_format_brain_is_compact_token_budget():
    """V2 brain context must stay ≤ ~300 tokens (~1200 chars) to keep
    the per-turn injection cheap."""
    from services.project_brain import format_brain_for_agent
    brain = {
        "version": 2,
        "task_count": 47,
        "structure": {
            "backend_root":  "backend/",
            "frontend_root": "frontend/src/",
            "components":    "frontend/src/components/",
            "pages":         "frontend/src/pages/",
            "hooks":         "frontend/src/hooks/",
            "routers":       "backend/routers/",
            "services":      "backend/services/",
            "tests":         "backend/tests/",
        },
        "stack": {
            "frontend":  "React + Vite",
            "backend":   "FastAPI",
            "db":        "MongoDB",
            "auth":      "JWT",
            "languages": ["Python", "JavaScript"],
        },
        "entry_points": {
            "backend_main":  "backend/main.py",
            "frontend_main": "frontend/src/App.jsx",
            "env_file":      "backend/.env",
        },
        "hot_paths": [
            "backend/routers/chat.py",
            "frontend/src/components/ChatPanel.jsx",
            "backend/services/orchestrator.py",
        ],
        "recent_changes": [
            {"file": "backend/routers/chat.py", "task": "t_xxx", "ts": 1.0},
        ],
        "sensitive_paths": ["backend/.env"],
    }
    out = format_brain_for_agent(brain)
    assert "[PROJECT BRAIN V2]" in out
    assert "backend/routers/chat.py" in out
    assert "Tasks done: 47" in out
    assert len(out) <= 1200, f"brain formatter exceeded budget: {len(out)} chars"


# ── update_brain_after_task non-destructive ──────────────────────────

def test_update_brain_after_task_handles_missing_db_gracefully():
    """Must never raise when db is None — task path can't be broken
    by an offline Mongo."""
    from services.project_brain import update_brain_after_task
    out = asyncio.run(update_brain_after_task(
        db=None, project_id="p_x", user_id="u_x",
        changed_files=["a.py"], task_id="t_x",
    ))
    assert out == {}


# ── Orchestrator wiring ──────────────────────────────────────────────

def test_orchestrator_imports_brain_v2_helpers():
    src = (BACKEND_DIR / "services" / "orchestrator.py").read_text()
    assert "get_brain_v2" in src
    assert "format_brain_for_agent" in src


def test_orchestrator_brain_injection_has_2s_timeout():
    """Brain inject MUST be bounded so a stalled Mongo can't block chat."""
    src = (BACKEND_DIR / "services" / "orchestrator.py").read_text()
    # Must use asyncio.wait_for around the brain fetch
    inject = src[src.find("Brain V2 inject"):src.find("Brain V2 inject") + 1000]
    assert "asyncio.wait_for" in inject
    assert "timeout=2.0" in inject


# ── cto_projects wiring ──────────────────────────────────────────────

def test_cto_projects_has_build_brain_endpoint():
    src = (BACKEND_DIR / "routers" / "cto_projects.py").read_text()
    assert '"/projects/{project_id}/build-brain"' in src
    assert "build_project_brain" in src


def test_cto_projects_has_get_brain_endpoint():
    src = (BACKEND_DIR / "routers" / "cto_projects.py").read_text()
    assert '"/projects/{project_id}/brain"' in src
    assert "get_project_brain" in src


def test_cto_projects_auto_triggers_brain_build_on_connect():
    """`POST /projects/add` must fire a build_brain_v2 background task
    so the first chat turn already has structural map injected."""
    src = (BACKEND_DIR / "routers" / "cto_projects.py").read_text()
    add_section = src[src.find("async def add_project"):]
    add_section = add_section[:add_section.find("@router.")]
    assert "build_brain_v2" in add_section
    assert "create_task" in add_section


def test_cto_projects_updates_brain_v2_on_api_task_completion():
    """The API-path worker must fire `update_brain_after_task` once
    the commit is verified."""
    src = (BACKEND_DIR / "routers" / "cto_projects.py").read_text()
    # Both task workers (API + git) must call it — count must be ≥ 2
    assert src.count("update_brain_after_task") >= 2, (
        "Brain V2 auto-update missing from one of the task worker paths"
    )


# ── 2026-08 security-triage regression: undefined `user_id` bug ─────
# Both `_run_task_via_api` and `_run_task_with_git` used to pass a
# bare `user_id` name to `update_brain_after_task(...)` that was never
# defined in either function's scope — a NameError on every single
# task completion, silently swallowed by the surrounding
# `except Exception as _bv2e: logger.warning(...)`. Brain V2 has been
# a silent no-op via these two paths since Iter 165. Fixed to
# `proj.get("user_id")`, matching every other call site in this same
# file (there are 6+ others already using that exact expression).
def _brain_update_call_nodes(func_src: str) -> list[ast.Call]:
    """Return every `update_brain_after_task(...)` Call node inside a
    function's source slice."""
    tree = ast.parse(func_src)
    out = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "update_brain_after_task"):
            out.append(node)
        elif (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "update_brain_after_task"):
            out.append(node)
    return out


def _extract_function_source(full_src: str, def_line: str) -> str:
    start = full_src.find(def_line)
    assert start != -1, f"could not locate {def_line!r}"
    # Slice to the next top-level `async def _run_task_` or EOF —
    # generous enough to include the whole worker function body.
    rest = full_src[start:]
    next_def = rest.find("\nasync def _run_task_", 1)
    return rest if next_def == -1 else rest[:next_def]


@pytest.mark.parametrize("def_line", [
    "async def _run_task_via_api(",
    "async def _run_task_with_git(",
])
def test_brain_v2_update_uses_correct_user_id_source(def_line):
    """AST-precision regression test: the `user_id=` keyword passed to
    `update_brain_after_task` inside each worker MUST resolve through
    `proj.get("user_id")` — never a bare/undefined name. This is the
    exact class of bug (wrong source, not just "no exception") that a
    plain `pyflakes`/string-presence check would miss once the name
    coincidentally exists in some other scope."""
    src = (BACKEND_DIR / "routers" / "cto_projects.py").read_text()
    func_src = _extract_function_source(src, def_line)
    calls = _brain_update_call_nodes(func_src)
    assert calls, f"no update_brain_after_task(...) call found in {def_line}"
    for call in calls:
        user_id_kw = next((kw for kw in call.keywords if kw.arg == "user_id"), None)
        assert user_id_kw is not None, "update_brain_after_task call is missing user_id="
        val = user_id_kw.value
        is_proj_get_user_id = (
            isinstance(val, ast.Call)
            and isinstance(val.func, ast.Attribute)
            and val.func.attr == "get"
            and isinstance(val.func.value, ast.Name)
            and val.func.value.id == "proj"
            and any(isinstance(a, ast.Constant) and a.value == "user_id" for a in val.args)
        )
        assert is_proj_get_user_id, (
            f"user_id= in {def_line} must be proj.get(\"user_id\"), "
            f"got: {ast.dump(val)}"
        )


@pytest.mark.asyncio
async def test_brain_v2_update_actually_proceeds_with_the_real_project_user_id():
    """Behavioural proof (not just 'no exception'): calling
    `update_brain_after_task` with the SAME value the fixed call sites
    now use — `proj.get("user_id")` from a realistic project doc —
    must proceed past the early-return guard and actually attempt a
    brain fetch. Before the fix, the call site never even reached this
    function (NameError, swallowed) so `get_brain_v2` was NEVER
    invoked. Asserting a real (mocked) `get_brain_v2` call is the
    proof the feature fires, not merely that nothing crashes."""
    from unittest.mock import AsyncMock, patch
    from services import project_brain as pb

    proj = {"project_id": "p1", "user_id": "u1",
            "github_owner": "acme", "github_repo": "widgets", "branch": "main"}

    with patch.object(pb, "get_brain_v2", AsyncMock(return_value={"version": 2})) as mocked_get:
        result = await pb.update_brain_after_task(
            db=object(),  # non-None sentinel — only truthiness matters at the guard
            project_id=proj.get("project_id", ""),
            user_id=proj.get("user_id"),   # <- exactly what the fixed call sites pass
            changed_files=["main.py"],
            task_id="t1",
        )
    mocked_get.assert_awaited_once()
    assert result != {}, (
        "update_brain_after_task no-opped even with a real user_id — "
        "the guard clause is broken independent of the call-site fix"
    )


# ── Compile sanity ───────────────────────────────────────────────────

@pytest.mark.parametrize("relpath", [
    "services/project_brain.py",
    "services/orchestrator.py",
    "routers/cto_projects.py",
])
def test_files_parse_clean(relpath: str):
    import ast
    ast.parse((BACKEND_DIR / relpath).read_text())
