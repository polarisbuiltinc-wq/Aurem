"""
services/visibility/robots.py — robots.txt AI-crawler policy (spec §5/§6.2).

R5: read-modify-write. Managed section delimited by the
`# --- AI crawlers (managed by AUREM) ---` / `# --- end AUREM block ---`
comments; every other line in the file is preserved byte-for-byte.
R6: idempotent re-apply — a second call updates the block in place,
never duplicates it.
R8: training-bot allow/deny is the caller's choice (bot_policy), never
auto-decided here. Retrieval bots are ALWAYS allowed (spec §1/§2).
"""
from __future__ import annotations

_START = "# --- AI crawlers (managed by AUREM) ---"
_END = "# --- end AUREM block ---"

RETRIEVAL_BOTS = ["OAI-SearchBot", "Claude-Web", "PerplexityBot", "Bingbot"]
TRAINING_BOTS = ["GPTBot", "ClaudeBot", "Google-Extended", "CCBot", "DeepSeekBot"]


def render_managed_block(bot_policy: dict) -> str:
    lines = [_START]
    for bot in RETRIEVAL_BOTS:
        lines.append(f"User-agent: {bot}\nAllow: /")
    for bot in TRAINING_BOTS:
        decision = (bot_policy or {}).get(bot, "deny")  # D1 default: DENY
        verb = "Allow" if decision == "allow" else "Disallow"
        lines.append(f"User-agent: {bot}\n{verb}: /")
    lines.append(_END)
    return "\n".join(lines) + "\n"


def apply_managed_block(existing_content: str | None, bot_policy: dict) -> str:
    """R5/R6/R7 — merge the managed block into `existing_content` without
    touching any other line. `existing_content=None` (no robots.txt yet)
    produces a fresh file with just the managed block."""
    block = render_managed_block(bot_policy)
    if not existing_content:
        return block
    if _START in existing_content and _END in existing_content:
        pre, rest = existing_content.split(_START, 1)
        _, post = rest.split(_END, 1)
        return pre.rstrip("\n") + ("\n\n" if pre.strip() else "") + block + post.lstrip("\n")
    # No existing managed block — append ours, preserve everything else.
    sep = "\n\n" if existing_content and not existing_content.endswith("\n\n") else ""
    return existing_content.rstrip("\n") + "\n" + sep + block
