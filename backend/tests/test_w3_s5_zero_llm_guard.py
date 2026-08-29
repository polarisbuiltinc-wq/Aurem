"""
tests/test_w3_s5_zero_llm_guard.py — Overnight loop W3-S5 (2026-08-29).

L16 — the entire S0-S5 Trust Surfaces preview/code/deploy journey
must construct ZERO LLM providers. Two layers of proof:
  1. Static source scan of every new/touched S-flow module for any
     LLM-provider import (fails loudly on drift, not just at runtime).
  2. A live spy-provider guard: monkeypatch the two LLM call
     entrypoints to raise if constructed, then actually invoke the
     deterministic classifiers/detectors the S-flow endpoints use.
"""
import ast
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent

S_FLOW_MODULES = [
    "services/preview_capture.py",
    "services/trust_surface_events.py",
]

LLM_MARKERS = {
    "call_llm", "call_llm_with_meta", "chat_with_tools", "orchestrator",
    "openrouter", "anthropic", "deepseek", "groq_client", "ora_chat_v2",
}


def _imported_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module)
            names.update(a.name for a in node.names)
    return names


@pytest.mark.parametrize("rel_path", S_FLOW_MODULES)
def test_s_flow_module_imports_no_llm_provider(rel_path):
    path = BACKEND / rel_path
    names = _imported_names(path)
    lowered = {n.lower() for n in names}
    hit = lowered & LLM_MARKERS
    assert not hit, f"{rel_path} imports LLM-provider machinery: {hit}"


def test_s_flow_classifiers_are_pure_zero_llm(monkeypatch):
    """Spy-provider guard: even if the classifiers accidentally
    reached for an LLM call, this would explode — they never do."""
    def _boom(*a, **k):
        raise AssertionError("S-flow classifier reached a real/would-be LLM call")

    monkeypatch.setattr("services.llm.call_llm", _boom, raising=False)
    monkeypatch.setattr("services.orchestrator.call_llm_with_meta", _boom, raising=False)

    from services.preview_capture import (
        classify_user_repo_change, classify_changed_file,
        summarise_change_classification, detect_live_url_from_config,
    )
    assert classify_user_repo_change(["frontend/src/pages/Home.jsx"]) == ["/"]
    assert classify_changed_file("backend/routers/chat.py") == "server"
    assert summarise_change_classification(["a.py"])["n_files"] == 1
    assert detect_live_url_from_config("package.json", '{"homepage": "https://x.com"}') == "https://x.com"


def test_deploy_router_new_endpoints_import_no_llm():
    path = BACKEND / "routers/deploy.py"
    names = {n.lower() for n in _imported_names(path)}
    assert not (names & LLM_MARKERS), f"routers/deploy.py imports LLM machinery: {names & LLM_MARKERS}"
