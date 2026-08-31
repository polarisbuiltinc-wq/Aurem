"""
tests/test_deploy_readiness_offload_2026_09_02.py

Audit sweep (#B) follow-up fix: `get_deploy_readiness()`'s
`_workspace_state()` (up to 3 sequential `git` subprocess calls) is
now offloaded via `asyncio.to_thread`, same class/fix as the
write_repo_file syntax gate.
"""
from __future__ import annotations

import inspect

from services.deploy_readiness import get_deploy_readiness


def test_get_deploy_readiness_offloads_workspace_state_to_a_thread():
    src = inspect.getsource(get_deploy_readiness)
    assert "await asyncio.to_thread(_workspace_state)" in src
