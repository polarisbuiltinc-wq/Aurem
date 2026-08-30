"""
services/visibility/robots.py — robots.txt AI-crawler policy (spec §5/§6.2).

R5: read-modify-write. Managed section delimited by the
`# --- AI crawlers (managed by AUREM) ---` / `# --- end AUREM block ---`
comments; every other line in the file is preserved byte-for-byte.
R6: idempotent re-apply — a second call updates the block in place,
never duplicates it.
R8: training-bot allow/deny is the caller's choice (bot_policy), never
auto-decided here. Retrieval bots are ALWAYS allowed (spec §1/§2).

2026-08-30 KIT GAP-PATCH — bot tokens re-verified against each
vendor's current crawler docs (real bug fix: "Claude-Web" is a
deprecated/dead token per Anthropic's own docs, so the retrieval bot
was never actually allowed):
  - GPTBot / OAI-SearchBot — https://developers.openai.com/api/docs/bots
  - ClaudeBot / Claude-SearchBot — https://support.claude.com/en/articles/8896518
    ("Claude-Web" explicitly listed there as deprecated/no-longer-effective)
  - PerplexityBot — https://docs.perplexity.ai/docs/resources/perplexity-crawlers
  - Google-Extended — https://developers.google.com/crawling/docs/crawlers-fetchers/google-common-crawlers
  - CCBot — https://commoncrawl.org/faq
  verified 2026-08-30. Re-check these URLs before ever changing this
  list again — vendors add/rename tokens without notice.

  DeepSeekBot (previously in TRAINING_BOTS) — NOT kept. No
  DeepSeek-operator-published docs page exists (checked 2026-08-30);
  only third-party crawler-directory listings, which don't meet the
  same "vendor's own current docs" bar as the 5 tokens above. Removed
  per the "no verified source, removed" call.

  User-fetch bots (ChatGPT-User, Claude-User, Perplexity-User) are
  intentionally NOT added — they don't reliably honor robots.txt and
  aren't the citation lever this block targets.
"""
from __future__ import annotations

_START = "# --- AI crawlers (managed by AUREM) ---"
_END = "# --- end AUREM block ---"

RETRIEVAL_BOTS = ["OAI-SearchBot", "Claude-SearchBot", "PerplexityBot"]
TRAINING_BOTS = ["GPTBot", "ClaudeBot", "Google-Extended", "CCBot"]


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
