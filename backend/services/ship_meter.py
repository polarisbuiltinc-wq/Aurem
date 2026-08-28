"""services/ship_meter.py — Overnight T1 (Ladder Item 1 · METER)

Deterministic, zero-LLM, zero-extra-GitHub-call diff metrics attached
to every ship/task-completion record. Computed purely from diff rows
the writer already has in-process:
  - services.task_diff.build_files_changed()  → legacy cto_tasks engine
    (_run_task_via_api / _run_task_with_git), rows shaped
    {"name": str, "lines_added": int, "lines_removed": int, ...}
  - services.loop_ship_diff.compute_files_diff() → LoopEngine ship path,
    rows shaped {"path": str, "additions": int, "deletions": int, ...}

No network call, no LLM call — pure arithmetic over an already-computed
diff. Both row shapes are normalized here so callers don't need to know
which engine produced them.
"""
from __future__ import annotations

_DEP_MANIFESTS = {
    "requirements.txt", "package.json", "yarn.lock", "package-lock.json",
    "pyproject.toml", "pipfile", "pipfile.lock", "go.mod", "go.sum",
    "cargo.toml", "cargo.lock",
}


def _is_dep_manifest(path: str) -> bool:
    name = (path or "").rsplit("/", 1)[-1].lower()
    return name in _DEP_MANIFESTS


def compute_meter_fields(rows: list[dict]) -> dict:
    """rows: build_files_changed()-shaped or compute_files_diff()-shaped
    per-file diff rows. Returns the 4 deterministic meter fields:
        lines_added, lines_removed, files_touched, new_dependencies_added
    """
    rows = rows or []
    lines_added = 0
    lines_removed = 0
    new_dependencies_added = 0
    for row in rows:
        path = row.get("name") or row.get("path") or ""
        added = row.get("lines_added", row.get("additions", 0)) or 0
        removed = row.get("lines_removed", row.get("deletions", 0)) or 0
        lines_added += int(added)
        lines_removed += int(removed)
        if _is_dep_manifest(path):
            new_dependencies_added += int(added)
    return {
        "lines_added": lines_added,
        "lines_removed": lines_removed,
        "files_touched": len(rows),
        "new_dependencies_added": new_dependencies_added,
    }
