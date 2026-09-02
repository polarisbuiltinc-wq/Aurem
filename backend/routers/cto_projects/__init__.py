"""
routers/cto_projects/__init__.py — AUREM multi-project system.
Connect existing client GitHub repos, run AI tasks (git pull → fix → push).
Mounted under /api/aurem-dev/cto/* to avoid clashing with /projects/* (new-project flow).

2026-09-08 — Split from the former ~4,400-line monolithic
`routers/cto_projects.py` into responsibility-based submodules
(management, brain, graph, preview, what_changed, tasks, rollback,
worker_api, worker_git). This file now holds ONLY the shared router/
constants/imports every submodule needs, plus compat re-exports so
every existing `from routers.cto_projects import X` and
`patch("routers.cto_projects.X", ...)` call site keeps resolving
unchanged. See each submodule's docstring for the `_pkg.<name>`
dynamic-dispatch convention used to keep package-level test patches
working even after the code that USES those names moved to a
submodule with its own separate globals.
"""
# arch: allow-http — GitHub repo API for project setup (iter 212m-225)
from __future__ import annotations
import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from cto_services.auth import current_dev
from cto_services.db import get_db, require_db
from core.errors import PushFailedError
from services.llm import call_llm
from services.usage import assert_has_budget, assert_has_task_budget, get_usage, is_founder_email
from services.github_api_writer import (
    commit_files as gh_api_commit,
    revert_commit as gh_api_revert,
    fetch_file as gh_api_fetch_file,
)
# 2026-08-26 — safe mechanical extraction (zero logic change): pure/
# standalone helpers moved to services/cto_projects_helpers.py to
# shrink this file. Re-exported here so every existing bare-name call
# site inside the worker/rollback functions below, every
# `from routers.cto_projects import X`, and every
# `patch("routers.cto_projects.X", ...)` in the test suite keep
# working unchanged. See PRD.md 2026-08-26 entry — the worker/rollback
# execution pipelines themselves (_run_task_via_api, _run_task_with_git,
# _run_rollback*) are a separate, deliberately-untouched future item.
from services.cto_projects_helpers import (
    _task_queues, _emit, _parse_repo, _run_project_indexing,
    _BROWSE_SKIP_DIRS, _BROWSE_SKIP_EXTS, _BROWSE_MAX_FILE_BYTES,
    _browse_keep_path, _classify_phase, _log, _set_status, _sh,
    _load_design_system, _TRUNCATION_PATTERNS,
    _retry, _hallucination_reasons,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/cto", tags=["AUREM Projects"])

# Detect whether `git` binary is available — production containers don't
# have it (Iter 21). When missing we route to the pure-HTTP GitHub API path.
_GIT_AVAILABLE = shutil.which("git") is not None
if not _GIT_AVAILABLE:
    logger.warning(
        "`git` binary not found on this server. CTO tasks will use the "
        "GitHub REST API path (no clone, no push subprocess)."
    )

# 2026-08-27 — Checkpoint/resume Phase 2 (founder-approved scoped fix).
# The expensive/risky part of a task retry is the LLM codegen call(s),
# NOT file-level write granularity (codegen produces all file edits in
# one shot; the GitHub write is already one atomic commit — see PRD.md
# 2026-08-27 Phase 1 investigation for the full reasoning on why a
# generic step-log was rejected in favor of this narrower fix).
# `pending_edits` is saved to `cto_tasks` the moment generation succeeds
# (before the commit). A retry within this TTL reuses the saved edits
# and skips straight to Vanguard-verify + commit — Vanguard still runs
# fresh every time (cheap relative to codegen, and keeps the security
# gate meaningful even on a resumed task).
#
# 15 minutes: long enough to cover the realistic "crash right after
# generation, immediate retry" case this was built for (worker restart,
# transient GitHub 5xx on commit, operator or automated retry within a
# couple minutes), short enough that we don't silently ship a diff
# generated against repo/context state that's gone stale. The commit
# step always writes against the CURRENT branch tip (no cached base
# SHA), so a stale reuse fails loudly or applies cleanly — it can't
# silently corrupt — but repo-drift risk (someone else pushed to the
# branch in the meantime) still grows with age, so we keep the window
# short rather than "as long as possible."
PENDING_EDITS_TTL_S = 15 * 60

WORKSPACE = Path(os.getenv("WORKSPACE_PATH", "/tmp/aurem-dev-projects"))
WORKSPACE.mkdir(parents=True, exist_ok=True)

# 2026-08-25 — Priority 2 (ambiguity-gate). Now a shared, single-
# source-of-truth module (`services/ambiguity_gate.py`) so this logic
# can never drift from the Loop Mode equivalent (`routers/loop.py::
# start_loop`) — formalized 2026-08-26 once Loop Mode's own gap (zero
# ambiguity protection despite being live for Pro/Team) was closed.
from services.ambiguity_gate import is_ambiguous_task as _is_ambiguous_task


# ── Submodules — each attaches its routes onto the shared `router`
# above. Import order matters only for worker_api-before-worker_git
# (worker_git imports `_frontend_subset`/`_AI_SYS` from worker_api at
# load time); every other cross-module call goes through `_pkg.<name>`
# dynamic dispatch (see each submodule's docstring) so it doesn't
# matter which order THOSE load in.
from . import management        # noqa: E402
from . import brain              # noqa: E402
from . import graph              # noqa: E402
from . import preview            # noqa: E402
from . import what_changed       # noqa: E402
from . import worker_api         # noqa: E402
from . import worker_git         # noqa: E402
from . import rollback           # noqa: E402
from . import tasks              # noqa: E402

# ── Compat re-exports — every name that used to live directly on this
# module. Keeps `from routers.cto_projects import X` and
# `patch("routers.cto_projects.X", ...)` resolving exactly as before.
from .management import (          # noqa: E402,F401
    AddProject, VerifyPatBody, UpdateProject, get_repo_token,
    check_project_pat, add_project, project_indexing_status, verify_pat,
    list_projects, remove_project, test_project_pat, get_project_tree,
    get_project_file, detect_live_url, update_project,
)
from .brain import (               # noqa: E402,F401
    build_project_brain, get_project_brain, warm_start_project,
    _run_warm_agents, warm_start_status,
)
from .graph import (               # noqa: E402,F401
    build_project_graph, get_project_graph, build_project_mermaid,
    get_graph_tour, search_graph, graph_impact,
)
from .preview import (             # noqa: E402,F401
    PreviewSessionBody, log_preview_session, get_pending_change,
    capture_preview_route, get_preview_receipt,
)
from .what_changed import get_what_changed   # noqa: E402,F401
from .tasks import (               # noqa: E402,F401
    TaskBody, _enqueue_cto_task, submit_task, get_task, get_task_scan,
    retry_task, project_tasks, task_stream,
)
from .rollback import (            # noqa: E402,F401
    RollbackBody, rollback_task, _rollback_log, _run_rollback,
    _run_rollback_via_api, _run_rollback_with_git,
)
from .worker_api import (          # noqa: E402,F401
    _run_task, _persist_push_failed, _run_task_via_api, _frontend_subset,
    _looks_truncated, _AI_SYS,
)
from .worker_git import _run_task_with_git   # noqa: E402,F401
