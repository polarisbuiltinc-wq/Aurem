"""Module-boundary lint — `services/llm/` package encapsulation guard.

RATIONALE (Session C · Sub-step 2 hardening, 2026-02):
    The LLM package's internal submodules (`_state`, `_routing`,
    `_probes`, and future Session-D `openrouter_client`) are prefixed
    with a single underscore to signal INTERNAL-ONLY. External callers
    must use the public `services.llm` surface (re-exports).

    Direct imports of internal submodules from outside the package
    silently create tight coupling that will make Session D+E
    refactors painful. This test catches such leaks at CI time.

WHITELIST — the only files allowed to import `services.llm._*`:
  1. Any file INSIDE the `services/llm/` package (its own submodules)
  2. The 3 backward-compat shims at `services/_llm_state.py`,
     `services/_llm_routing.py`, `services/_llm_probes.py` — these
     exist BY DESIGN to bridge legacy call sites during migration
  3. Test files under `backend/tests/` — regression tests may need
     internal visibility to lock invariants

Everything else is a violation.

Zero-mocks rule: this test scans the real filesystem via `ast`.
"""
from __future__ import annotations

import ast
import pathlib

BACKEND = pathlib.Path(__file__).resolve().parent.parent

# Files legitimately allowed to reach into `services.llm._*`.
_ALLOWED_FILES = {
    BACKEND / "services" / "_llm_state.py",
    BACKEND / "services" / "_llm_routing.py",
    BACKEND / "services" / "_llm_probes.py",
}


def _is_internal_llm_import(module: str | None) -> bool:
    """True if `module` refers to a private submodule under
    `services.llm._*` (or a sub-submodule thereof)."""
    if not module:
        return False
    parts = module.split(".")
    # `services.llm._state`, `services.llm._routing`, `services.llm._probes`
    # OR future `services.llm._foo._bar` etc.
    if len(parts) >= 3 and parts[0] == "services" and parts[1] == "llm":
        return parts[2].startswith("_")
    return False


def _scan(path: pathlib.Path) -> list[tuple[int, str]]:
    """Return list of (line_number, offending_import_text) tuples."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return []
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if _is_internal_llm_import(node.module):
                hits.append((node.lineno, f"from {node.module} import ..."))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if _is_internal_llm_import(alias.name):
                    hits.append((node.lineno, f"import {alias.name}"))
    return hits


def test_no_external_imports_of_llm_internals():
    """Zero external callers may `import services.llm._<internal>`."""
    violations: list[str] = []
    for py in BACKEND.rglob("*.py"):
        rel = py.relative_to(BACKEND)
        # Skip: the package itself.
        if rel.parts[:2] == ("services", "llm"):
            continue
        # Skip: test files (may exercise internals directly).
        if rel.parts and rel.parts[0] == "tests":
            continue
        # Skip: bytecode dirs, .venv, node_modules — rglob covers them
        # but they shouldn't contain .py we own. Belt-and-suspenders:
        if any(seg in {"__pycache__", ".venv", "node_modules"} for seg in rel.parts):
            continue
        # Skip: explicit whitelist (the shims).
        if py in _ALLOWED_FILES:
            continue
        hits = _scan(py)
        for lineno, text in hits:
            violations.append(f"{rel}:{lineno} {text}")

    assert not violations, (
        "Module-boundary lint FAILED — the following files import "
        "`services.llm._<internal>` directly instead of using the "
        "public `services.llm` surface. Either (a) use the public "
        "import, or (b) add the file to `_ALLOWED_FILES` in "
        "`test_llm_module_boundary.py` with a written justification:\n"
        + "\n".join(f"  • {v}" for v in violations)
    )


def test_shim_files_still_present_and_re_exporting():
    """The 3 backward-compat shims must still exist and re-export from
    the moved package. If a future refactor deletes them, EVERY legacy
    test that imports `services._llm_*` breaks silently at collection
    time. This test locks the migration contract."""
    shims = {
        BACKEND / "services" / "_llm_state.py":   "services.llm._state",
        BACKEND / "services" / "_llm_routing.py": "services.llm._routing",
        BACKEND / "services" / "_llm_probes.py":  "services.llm._probes",
    }
    for shim, expected_source in shims.items():
        assert shim.exists(), f"backward-compat shim missing: {shim}"
        text = shim.read_text(encoding="utf-8")
        assert expected_source in text, (
            f"{shim.name} no longer re-exports from {expected_source} — "
            "did the migration back out without updating the shim?"
        )


def test_llm_public_surface_reexports_probes_and_routing():
    """The public `services.llm` module must re-export the symbols
    external callers rely on, so nobody has an EXCUSE to reach into
    internals. Regression guard for accidental unexport."""
    import sys
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    import services.llm as pkg

    # Symbols that were promoted from the internals into the public API.
    for name in (
        "probe_longcat_availability",
        "periodic_longcat_reprobe",
        "_deepseek_model",
        "council_a_primary_model",
        "council_b_primary_model",
        "cap_for",
        "temperature_for",
        "get_last_provider",
        "reset_last_provider",
        "MAX_TOKENS",
        "TEMPERATURE",
    ):
        assert hasattr(pkg, name), (
            f"services.llm missing expected re-export {name!r} — "
            "external callers will be forced back into the internals."
        )
