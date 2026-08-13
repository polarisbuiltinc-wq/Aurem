"""
routers/dev_tools.py — Iter 388t · Bug 20 root-cause fix companion.

`/podshell` slash-command endpoint.  Founder-only, deterministic
alternative to sending the LLM a "run bash on the pod" prompt.  The
LLM used to see the ORA_BOUNDARY_NO_REPO_RULE template and refuse
with a canned phrase (Bug 20).  The founder-pod-mode fix in
services/ora_context.py + services/local_tools.py lifted the refusal
for Home chat, but Home chat is not reachable from the current UI
(the Home tab was deliberately removed per past founder request).

This router gives the founder a first-class way to hit execute_bash
without going through the chat/LLM pipeline at all:

    POST /api/aurem-dev/dev-tools/podshell
    Body: {"command": "ls /app/backend/routers/ | head -20"}

Requires an admin JWT.  Runs the same validate_founder_pod_command
safety layer that Home chat would (whitelist commands, no chaining,
no traversal, no secret paths).  Returns the raw stdout/stderr so the
frontend can render it inline.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from cto_services.auth import require_admin
from services.local_tools import execute_bash
from services.ora_context import (
    validate_founder_pod_command,
    FOUNDER_POD_ALLOWED_PATHS,
    FOUNDER_POD_BLOCKED_PATHS,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dev-tools", tags=["DevTools"])


class PodShellRequest(BaseModel):
    command: str = Field(..., min_length=1, max_length=1000)


class PodShellResponse(BaseModel):
    ok: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: Optional[int] = None
    error: Optional[str] = None
    error_class: Optional[str] = None
    command: str = ""


@router.post("/podshell", response_model=PodShellResponse)
async def podshell(
    body: PodShellRequest,
    authorization: Optional[str] = Header(None),
) -> PodShellResponse:
    """Run a whitelisted read-only shell command on the pod.

    Founder-only (require_admin gate).  Two-layer safety:
      1. `validate_founder_pod_command` runs before execute_bash so
         chaining / traversal / secret paths bounce with a specific
         reason string.
      2. `execute_bash` (services/local_tools.py) still runs its own
         binary allowlist + 15s wall-clock + 8 KB stdout cap.

    The founder_pod_mode ctx flag is set to True here (same escape
    hatch used by the Home chat path in orchestrator.py) so
    execute_bash's ora_boundary_violation check is lifted for
    documented pod paths (/app, /tmp, /var, /etc, /usr).
    """
    user = await require_admin(authorization)
    cmd = (body.command or "").strip()
    if not cmd:
        raise HTTPException(400, "command is required")

    # Layer 1: founder-pod safety validator.  Returns a specific reason
    # if the command trips a rule so the founder can adjust and retry.
    ok, reason = validate_founder_pod_command(cmd)
    if not ok:
        return PodShellResponse(
            ok=False,
            error=f"podshell refused: {reason}",
            error_class="founder_pod_validation",
            command=cmd[:200],
        )

    # Layer 2: dispatch through execute_bash with founder_pod_mode=True.
    # We build the same shape of ctx the orchestrator would build for a
    # Home-chat founder session so the escape hatch path fires.
    ctx = {
        "user_id":         user.get("user_id"),
        "project_id":      None,
        "is_founder":      True,   # require_admin already enforced this
        "bin_ctx":         None,   # no project attached for /podshell
        "founder_pod_mode": True,
    }
    result = await execute_bash(ctx, {"command": cmd})

    return PodShellResponse(
        ok=bool(result.get("ok")),
        stdout=result.get("stdout", "") or "",
        stderr=result.get("stderr", "") or "",
        exit_code=result.get("exit_code"),
        error=result.get("error"),
        error_class=result.get("error_class"),
        command=cmd[:200],
    )


@router.get("/podshell/info")
async def podshell_info(
    authorization: Optional[str] = Header(None),
) -> dict:
    """Return the current whitelist/denylist state so the founder can
    check what /podshell will accept.  Admin-gated for parity with the
    /podshell endpoint itself."""
    await require_admin(authorization)
    from services.local_tools import _BASH_ALLOWED
    return {
        "allowed_binaries":  sorted(_BASH_ALLOWED),
        "allowed_paths":     list(FOUNDER_POD_ALLOWED_PATHS),
        "blocked_paths":     list(FOUNDER_POD_BLOCKED_PATHS),
        "chaining_operators_refused": [";", "&&", "||"],
        "path_traversal_refused":     [".."],
        "usage": (
            "POST /api/aurem-dev/dev-tools/podshell "
            'with body {"command": "..."}'
        ),
    }
