"""
tests/_chat_pkg_src.py — shared helper for source-level regression
tests written against the old monolithic `routers/chat.py` (4184
lines), which was split into a package (`routers/chat/{misc,turn,
stream,history}.py` + `__init__.py`) on 2026-09-08.

`chat_package_source()` returns the concatenation of every submodule
in the package, standing in for the old single-file `open(CHAT_PY).
read()` pattern. Any test doing a plain substring / regex presence-
or-absence check against the whole former god-file keeps working
unchanged against this concatenation — nothing was deleted, only
relocated. Tests that need ONE specific submodule (e.g. AST-parsing
`chat_stream` itself) should use `CHAT_STREAM_PY` / `CHAT_TURN_PY`
directly instead.
"""
from __future__ import annotations

import os

_CHAT_DIR = os.path.join(os.path.dirname(__file__), "..", "routers", "chat")

CHAT_INIT_PY    = os.path.join(_CHAT_DIR, "__init__.py")
CHAT_MISC_PY    = os.path.join(_CHAT_DIR, "misc.py")
CHAT_TURN_PY    = os.path.join(_CHAT_DIR, "turn.py")
CHAT_STREAM_PY  = os.path.join(_CHAT_DIR, "stream.py")
CHAT_HISTORY_PY = os.path.join(_CHAT_DIR, "history.py")
CHAT_HANDOFF_GUARD_PY = os.path.join(_CHAT_DIR, "handoff_guard.py")
# 2026-09-08 StreamState refactor — stream.py's ~1,100-line _worker()
# god-function was further split into these 4 modules (mode dispatch,
# queue-consumption/timeout race, confidence-mismatch retry, shared
# state). Same "nothing deleted, only relocated" contract as above.
CHAT_STREAM_STATE_PY = os.path.join(_CHAT_DIR, "stream_state.py")
CHAT_WATCHDOG_PY = os.path.join(_CHAT_DIR, "watchdog.py")
CHAT_RETRIES_PY  = os.path.join(_CHAT_DIR, "retries.py")
CHAT_WORKER_PY   = os.path.join(_CHAT_DIR, "worker.py")

_SUBMODULE_PATHS = [
    CHAT_INIT_PY, CHAT_MISC_PY, CHAT_TURN_PY, CHAT_STREAM_PY, CHAT_HISTORY_PY,
    CHAT_HANDOFF_GUARD_PY,
    CHAT_STREAM_STATE_PY, CHAT_WATCHDOG_PY, CHAT_RETRIES_PY, CHAT_WORKER_PY,
]


def chat_package_source() -> str:
    parts = []
    for path in _SUBMODULE_PATHS:
        with open(path, encoding="utf-8") as fh:
            parts.append(fh.read())
    return "\n\n".join(parts)
