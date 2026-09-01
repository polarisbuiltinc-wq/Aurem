"""
routers/chat/stream_state.py — shared per-request state for the
chat_stream() SSE pipeline (StreamState refactor, 2026-09-08).

Replaces the ~20 variables that used to live as closures shared
between chat_stream()/gen()/_ticker()/_worker() with one explicit
object threaded through context_build.py / worker.py / watchdog.py /
retries.py / sse_stream.py. Mechanical de-closuring — no behavior
change; every field here is read/written at the exact same logical
point the old closure variable was.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional

from fastapi import Request

from .turn import ChatBody


@dataclass
class StreamState:
    # ── Request-scoped, set once by context_build.py ──
    request: Request
    body: ChatBody
    authorization: Optional[str]
    user: dict
    user_id: str
    jwt_token: str
    pid_stream: str

    # ── Context built before gen() (immutable once set) ──
    bin_ctx: Any = None
    repo_ctx: str = ""
    brain_ctx: str = ""
    extra_sys: str = ""
    council_recalled: int = 0
    council_block: str = ""
    recall_mode: Optional[str] = None
    plain_english_active: bool = False
    is_founder: bool = False

    # ── gen()-scoped runtime primitives. `default_factory` guarantees
    # a NEW asyncio.Queue/Event per StreamState instance — sharing
    # either across concurrent streams would interleave/corrupt
    # unrelated chats. Never pass shared instances in here. ──
    q: asyncio.Queue = field(default_factory=asyncio.Queue)
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    activity: dict = field(
        default_factory=lambda: {"label": "thinking…", "invocations": []})
    t_start: float = 0.0
    hard_timeout_s: float = 180.0
    soft_timeout_s: float = 48.0

    # ── worker.py output, read by sse_stream.py's cost-logging call
    # after the worker completes ──
    sys_for_advisor: str = ""

    # ── watchdog.py output, read by sse_stream.py after the queue-
    # consumption loop ends ──
    result: Any = None
    collected_steps: list = field(default_factory=list)
    timed_out: bool = False

    # ── retries.py output, read by sse_stream.py after the
    # confidence-mismatch gate runs ──
    low_confidence: bool = False
    ship_suppressed: bool = False
    bail_reason: Optional[str] = None
    prior_fix_signal: Any = None

    def __post_init__(self):
        # Cheap per-instance guard — see class docstring.
        assert isinstance(self.q, asyncio.Queue), "q must be per-instance"
        assert isinstance(self.stop_event, asyncio.Event), (
            "stop_event must be per-instance")
