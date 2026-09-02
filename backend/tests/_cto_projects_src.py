"""
tests/_cto_projects_src.py — shared test helper.

`routers/cto_projects.py` was split into a responsibility-based
package on 2026-09-08 (see routers/cto_projects/__init__.py's
docstring). Many pre-existing tests do a raw source-text search
(`open(...).read()` / `Path(...).read_text()`) against the old
single-file path to regression-guard a specific code pattern still
existing somewhere in the CTO router. This helper reproduces the
same "one big source blob" search surface those tests expect, so
none of them need to know (or care) which submodule a given pattern
now lives in.
"""
from pathlib import Path

_CTO_PKG_DIR = Path(__file__).resolve().parent.parent / "routers" / "cto_projects"

# Same order as the original monolithic file, for anyone relying on
# relative ordering between two search patterns.
_SUBMODULE_ORDER = (
    "__init__.py", "management.py", "brain.py", "graph.py", "preview.py",
    "what_changed.py", "tasks.py", "rollback.py", "worker_api.py", "worker_git.py",
)


def cto_projects_src() -> str:
    return "\n".join(
        (_CTO_PKG_DIR / name).read_text() for name in _SUBMODULE_ORDER
    )
