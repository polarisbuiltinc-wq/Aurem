"""
services/ora_chat/tool_output_wrapper.py — Iter 388g

Convert slash-command / tool-call outputs into the structured
`edited_files` and `command_exec` SSE payload shapes consumed by
the ORA Diff View (EditedFileBubble + CommandExecutionBar).

Two envelope helpers today:
  • wrap_edited_files(files)   → {"type": "edited_files", "files": [...]}
  • wrap_command_exec(command, exit_code, ran_at=None)
                                → {"type": "command_exec", ...}

The frame shapes match what the frontend `Bubble` component looks
for on the assistant message (`m.edited_files`, `m.command_exec`).

Track-gate lives in the router (routers/ora_chat.py) — it reads
`dev_users.track` and drops both payloads for personal-track users
before yielding to the SSE stream.
"""
from __future__ import annotations

import time
from typing import Any


def wrap_edited_files(files: list[dict]) -> dict[str, Any]:
    """Envelope for a list of {path, hunks} dicts.

    Each `hunk` is what `services.task_diff.build_unified_diff_hunks`
    returns — line-level structured diff with per-line old_n/new_n.
    """
    return {
        "type":  "edited_files",
        "files": [
            {
                "path":  str(f.get("path") or "")[:512],
                "hunks": f.get("hunks") or [],
            }
            for f in (files or [])
            if f.get("path")
        ],
    }


def wrap_command_exec(
    command:   str,
    exit_code: int,
    *,
    ran_at:    float | None = None,
) -> dict[str, Any]:
    """Envelope for a single command-execution event.

    Minimal v1 (per founder scope): just command + exit_code + ran_at.
    stdout/stderr expansion is intentionally NOT included — that
    lives in a future v2 when we build the expandable panel.
    """
    return {
        "type":       "command_exec",
        "command":    str(command or "")[:2048],
        "exit_code":  int(exit_code),
        "ran_at":     ran_at if ran_at is not None else time.time(),
    }
