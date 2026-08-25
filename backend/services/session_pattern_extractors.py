"""
services/session_pattern_extractors.py — file-path & stack-signal
mining helpers used by the Session Learning System.

Extracted from services/ora_learning.py (2026-08-27, mechanical split
— no behaviour change) to keep that module under the platform's
file-size guard. Re-exported from `services.ora_learning` so existing
import sites (`from services.ora_learning import _extract_file_paths`)
keep working unchanged.
"""
from __future__ import annotations

import re

# Regex matches `foo/bar/baz.ext` or `baz.ext` — tight enough to skip
# URLs, dotted python identifiers, and prose mentions of file types.
_FILE_PATH_RX = re.compile(
    r"(?<![A-Za-z0-9/])"               # boundary before
    r"([A-Za-z0-9_.\-/]+\."            # path body + dot
    r"(?:py|js|jsx|ts|tsx|md|json|yml|yaml|toml|css|html|sh|sql))"
    r"(?![A-Za-z0-9])"                 # boundary after
)

# Stack-signal keywords (case-insensitive substring match).
_STACK_SIGNALS: tuple[str, ...] = (
    "fastapi", "flask", "django", "express", "next.js", "nextjs",
    "react", "vue", "svelte", "angular", "tailwind",
    "mongo", "mongodb", "postgres", "postgresql", "mysql", "redis",
    "sqlite", "supabase", "firebase",
    "celery", "rabbitmq", "kafka", "websocket", "sse",
    "docker", "kubernetes", "k8s", "terraform",
    "openai", "anthropic", "gemini", "openrouter", "claude", "deepseek",
    "stripe", "razorpay", "paypal",
    "jwt", "oauth", "github oauth", "pat",
    "typescript", "python", "javascript",
    "pytest", "jest", "playwright",
    "vite", "webpack", "yarn", "npm",
)


def _extract_file_paths(text: str) -> list[str]:
    """Pull plausible file paths from prose. Dedupes, caps at 50."""
    if not text:
        return []
    found = _FILE_PATH_RX.findall(text)
    seen: set[str] = set()
    out: list[str] = []
    for p in found:
        if p in seen or len(p) > 200:
            continue
        seen.add(p)
        out.append(p)
        if len(out) >= 50:
            break
    return out


def _extract_stack_signals(text: str) -> list[str]:
    """Return de-duped lower-cased stack signals seen in `text`."""
    if not text:
        return []
    low = text.lower()
    return [s for s in _STACK_SIGNALS if s in low]
