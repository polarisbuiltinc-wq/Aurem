import asyncio
import os

import pytest

from services import vanguard_verify_agent as vva


# ── _has_executable_python ─────────────────────────────────────
def test_executable_py_detected_via_def():
    blocks = {"app.py": "def foo():\n    return 1\n"}
    assert vva._has_executable_python(blocks) is True


def test_executable_py_detected_via_async_def():
    blocks = {"app.py": "async def foo():\n    pass\n"}
    assert vva._has_executable_python(blocks) is True


def test_executable_py_detected_via_class():
    blocks = {"models.py": "class Foo:\n    x = 1\n"}
    assert vva._has_executable_python(blocks) is True


def test_no_executable_py_in_constants_file():
    blocks = {"const.py": "FOO = 'bar'\nBAZ = 42\n"}
    assert vva._has_executable_python(blocks) is False


def test_no_executable_in_non_python_files():
    blocks = {"app.jsx": "function Foo() { return null; }",
              "README.md": "# AUREM"}
    assert vva._has_executable_python(blocks) is False


# ── verify_patch — happy-path & regex floor ──────────────────────
@pytest.mark.asyncio
async def test_clean_patch_passes(monkeypatch):
    """Clean Python — no secrets, no dangerous patterns. Mocks both the
    LLM agent (PASS) and the E2B sandbox (skipped)."""
    async def fake_llm(_blocks, _ctx):
        return {"pass": True, "findings": [], "summary": "clean", "model": "test"}
    async def fake_e2b(_blocks):
        return {"pass": True, "skipped": True, "reason": "no E2B in test"}
    monkeypatch.setattr(vva, "_llm_review", fake_llm)
    monkeypatch.setattr(vva, "_e2b_smoke",   fake_e2b)

    result = await vva.verify_patch(
        {"app.py": "def hello():\n    return 'hi'\n"},
        repo_ctx="o/r@main",
    )
    assert result["pass"] is True
    assert result["regex"]["blocked"] is False


@pytest.mark.asyncio
async def test_hardcoded_secret_BLOCKS_via_regex_floor(monkeypatch):
    """Even if the LLM agent says PASS (or is unavailable), regex
    Vanguard scanner MUST still block a hardcoded AWS key."""
    async def fake_llm(_b, _c):
        return {"pass": True, "findings": [], "summary": "missed it", "model": "test"}
    async def fake_e2b(_b):
        return {"pass": True, "skipped": True, "reason": "test"}
    monkeypatch.setattr(vva, "_llm_review", fake_llm)
    monkeypatch.setattr(vva, "_e2b_smoke",   fake_e2b)

    # TODO: set TEST_AWS_ACCESS_KEY env var with a fake AWS key for regex testing
    _test_key = os.environ.get('TEST_AWS_ACCESS_KEY') or ('AKIA' + '12345678901234XX')
    result = await vva.verify_patch(
        {"app.py": f"API_KEY = '{_test_key}'\n"},
        repo_ctx="o/r@main",
    )
    assert result["pass"] is False
    assert result["regex"]["blocked"] is True


@pytest.mark.asyncio
async def test_llm_agent_block_blocks_overall(monkeypatch):
    """Regex passes but the verify agent finds something → must block."""
    async def fake_llm(_b, _c):
        return {
            "pass": False,
            "findings": [{
                "file": "app.py", "line": 3, "severity": "HIGH",
                "rule": "missing_auth",
                "message": "/admin endpoint missing auth dep",
            }],
            "summary": "1 high finding",
            "model": "claude",
        }
    async def fake_e2b(_b):
        return {"pass": True, "skipped": True, "reason": "test"}
    monkeypatch.setattr(vva, "_llm_review", fake_llm)
    monkeypatch.setattr(vva, "_e2b_smoke",   fake_e2b)

    result = await vva.verify_patch(
        {"app.py": "def add(a, b):\n    return a + b\n"},
        repo_ctx="o/r@main",
    )
    assert result["pass"] is False
    assert any(f.get("rule") == "missing_auth" for f in result["findings"])


@pytest.mark.asyncio
async def test_e2b_block_blocks_overall(monkeypatch):
    """Regex + LLM pass, but the E2B smoke import fails (e.g.
    ImportError of a missing module) → must block."""
    async def fake_llm(_b, _c):
        return {"pass": True, "findings": [], "summary": "clean", "model": "claude"}
    async def fake_e2b(_b):
        return {"pass": False, "skipped": False,
                "stderr": "ModuleNotFoundError: nonexistent_lib",
                "stdout": ""}
    monkeypatch.setattr(vva, "_llm_review", fake_llm)
    monkeypatch.setattr(vva, "_e2b_smoke",   fake_e2b)

    result = await vva.verify_patch(
        {"app.py": "def foo():\n    import nonexistent_lib\n"},
        repo_ctx="o/r@main",
    )
    assert result["pass"] is False
    assert "BLOCK" in result["summary"] or "fail" in result["summary"].lower()


# ── verify_patch — graceful degradation ──────────────────────────
@pytest.mark.asyncio
async def test_llm_agent_unavailable_does_NOT_block(monkeypatch):
    """If Claude / Emergent LLM key is unreachable, the verify agent
    must NOT silently block clean patches — fall back to regex floor."""
    async def fake_llm(_b, _c):
        return {"pass": True, "findings": [],
                "summary": "skipped (network)", "model": ""}
    async def fake_e2b(_b):
        return {"pass": True, "skipped": True, "reason": "test"}
    monkeypatch.setattr(vva, "_llm_review", fake_llm)
    monkeypatch.setattr(vva, "_e2b_smoke",   fake_e2b)

    result = await vva.verify_patch(
        {"app.py": "def add(a,