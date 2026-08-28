"""Overnight T1 (Ladder Item 1 · METER) — guardrail tests.

t_meter_fields_present : deterministic fixture diff → all 4 fields present
                          + correct.
t_meter_zero_llm       : ship_meter.py never imports/touches any LLM
                          client — grep-guard, zero-network by construction.
"""
import ast
from pathlib import Path

from services.ship_meter import compute_meter_fields


def test_meter_fields_present_task_diff_shape():
    """build_files_changed()-shaped rows (legacy cto_tasks engine)."""
    rows = [
        {"name": "backend/foo.py", "lines_added": 5, "lines_removed": 2},
        {"name": "requirements.txt", "lines_added": 1, "lines_removed": 0},
    ]
    m = compute_meter_fields(rows)
    assert m == {
        "lines_added": 6,
        "lines_removed": 2,
        "files_touched": 2,
        "new_dependencies_added": 1,
    }


def test_meter_fields_present_loop_ship_diff_shape():
    """compute_files_diff()-shaped rows (LoopEngine ship path)."""
    rows = [
        {"path": "frontend/src/App.jsx", "additions": 10, "deletions": 3, "is_new": False},
        {"path": "package.json", "additions": 2, "deletions": 0, "is_new": False},
        {"path": "new_file.py", "additions": 20, "deletions": 0, "is_new": True},
    ]
    m = compute_meter_fields(rows)
    assert m["lines_added"] == 32
    assert m["lines_removed"] == 3
    assert m["files_touched"] == 3
    assert m["new_dependencies_added"] == 2  # package.json additions


def test_meter_fields_present_empty_diff_is_all_zero():
    m = compute_meter_fields([])
    assert m == {
        "lines_added": 0,
        "lines_removed": 0,
        "files_touched": 0,
        "new_dependencies_added": 0,
    }


def test_meter_zero_llm_no_llm_import_anywhere_in_module():
    """Grep-guard: ship_meter.py must never import an LLM client, call
    an LLM, or perform network I/O. Static AST scan of the actual
    module source — not a runtime mock check, so it can't be
    accidentally satisfied by a test double."""
    src_path = Path(__file__).resolve().parents[1] / "services" / "ship_meter.py"
    tree = ast.parse(src_path.read_text())
    imported_names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names += [n.name for n in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported_names.append(node.module or "")
    real_imports = [n for n in imported_names if n != "__future__"]
    assert real_imports == [], (
        f"ship_meter.py must have ZERO real imports (pure arithmetic "
        f"only, __future__ annotations excepted), found: {real_imports}"
    )
    # Function-call-level guard: no bare-name call anywhere in the
    # module could invoke an LLM/network client without an import,
    # so the import-emptiness assertion above is already sufficient
    # and doesn't false-positive on the word "LLM" inside comments.
