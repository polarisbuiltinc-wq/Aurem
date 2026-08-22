"""Iter 212m-12 — Friendly task-failure translator.

Hybrid pipeline:

  1. **Static catalog** — fast regex match against ~20 common
     failure shapes (PAT issues, GitHub merge conflicts, OpenRouter
     rate-limits, network/DNS, vault, etc.). Returns plain English
     + a concrete "what to do" step list. Zero LLM cost.

  2. **LLM fallback** — when no static match wins, ship the raw
     error string to Claude Haiku via the Emergent LLM key with a
     tight 200-token budget. Forces a stable JSON shape so the
     frontend can render the same `(plain, steps, suggestion)`
     contract regardless of how the failure originated.

Failure-mode philosophy: NEVER throw. If the LLM rewrite breaks
we fall back to a tiny generic message — it's better to render
*something* friendly than to wedge the worker on a translator
crash.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────
# Static catalog — ordered: most-specific patterns first.
# Each entry maps a regex match to a {plain, steps, suggestion} dict.
# ──────────────────────────────────────────────────────────────────

_RULES: List[Dict[str, Any]] = [
    # ── GitHub App auth issues (2026-06 · English-only + App-era copy) ─
    {
        "pattern": re.compile(r"app_installation_missing|no\s+pat\b|pat\s+missing|missing\s+pat", re.I),
        "plain":   "This project isn't connected through the AUREM GitHub App, so ORA couldn't write to your repo.",
        "steps":   [
            "Open the Projects page",
            "Click Connect / Edit on this project",
            "Install the AUREM GitHub App on the repo (the only supported auth method)",
            "Come back to chat and retry the task",
        ],
        "suggestion": "PAT support was removed — the GitHub App is more secure and never expires like a token.",
    },
    {
        "pattern": re.compile(r"app_installation_revoked|invalid[_\s-]?token|401|bad\s+credentials", re.I),
        "plain":   "GitHub rejected the App installation for this repo — it may have been suspended or uninstalled.",
        "steps":   [
            "Open GitHub → Settings → Applications → Installed GitHub Apps",
            "Check that the AUREM app is still installed and covers this repo",
            "Re-install it if missing, then retry the task",
        ],
        "suggestion": "Org repos sometimes need an org admin to approve the installation.",
    },
    {
        "pattern": re.compile(r"missing[_\s-]?scope|insufficient\s+scope|forbidden|403", re.I),
        "plain":   "The GitHub App is connected but doesn't have write permission on this repo.",
        "steps":   [
            "Open GitHub → Settings → Installed GitHub Apps → AUREM → Configure",
            "Make sure this repo is included in the installation's repository access",
            "Retry the task",
        ],
        "suggestion": "Permission changes apply instantly — no reconnect needed after saving.",
    },

    # ── Repo / branch problems ───────────────────────────────────
    {
        "pattern": re.compile(r"repo[_\s-]?not[_\s-]?found|404.*repo|repository.*not\s+found", re.I),
        "plain":   "GitHub can't find this repository — it may have been renamed or deleted.",
        "steps":   [
            "Check on GitHub that the repo still exists under the same name",
            "If renamed: Projects → Edit → update the repo name",
            "If deleted: remove this project and create a new one",
        ],
        "suggestion": "Check the owner/org name too — a changed handle needs syncing as well.",
    },
    {
        "pattern": re.compile(r"branch[_\s-]?not[_\s-]?found|branch.*not\s+exist", re.I),
        "plain":   "The branch ORA was about to push to no longer exists in the repo.",
        "steps":   [
            "Open the repo's branches list on GitHub",
            "Confirm the target branch (e.g. 'main' or 'develop') exists",
            "Set the correct branch in the Project Edit dialog and retry",
        ],
        "suggestion": "Keeping 'main' as the default branch is safest — some repos still use 'master'.",
    },
    {
        "pattern": re.compile(r"merge\s+conflict|conflict.*on\s+push|409|fast[_\s-]?forward", re.I),
        "plain":   "A new commit landed on your branch after ORA took its working copy, so the push was rejected.",
        "steps":   [
            "Pull the repo locally and check what the new commit is",
            "Either merge manually, or retry this task (ORA will fetch fresh)",
            "Hit Retry — ORA will re-plan from the latest HEAD",
        ],
        "suggestion": "If you and ORA work on the same branch in parallel, smaller tasks hit fewer conflicts.",
    },

    # ── Rate-limits / LLM gateway ────────────────────────────────
    {
        "pattern": re.compile(r"429|rate[_\s-]?limit|too\s+many\s+requests", re.I),
        "plain":   "The AI provider throttled the request under load — it clears on its own shortly.",
        "steps":   [
            "Wait 2-3 minutes",
            "Hit Retry — the backend reconnects automatically",
        ],
        "suggestion": "If you hit 429s often as a heavy user, the Pro/Team plans get a priority queue that bypasses the public throttle.",
    },
    {
        "pattern": re.compile(r"openrouter.*5\d\d|llm.*5\d\d|upstream.*5\d\d|bad\s+gateway|503", re.I),
        "plain":   "The AI provider's own servers are hiccuping — nothing to fix on our side.",
        "steps":   [
            "Wait 1-2 minutes",
            "Retry — the provider usually recovers within 30-60 seconds",
            "If it persists past 5 minutes, check status.openrouter.ai",
        ],
        "suggestion": "If persistent, try switching Pro → Swift in the chat's Mode selector — sometimes only one specific model is down.",
    },

    # ── Network / DNS ────────────────────────────────────────────
    {
        "pattern": re.compile(r"timeout|timed\s+out|deadline\s+exceeded|connection.*reset", re.I),
        "plain":   "The network was slow and a request got cut off midway. This is temporary.",
        "steps":   [
            "Briefly check your internet connection",
            "Retry — the same network blip rarely happens twice",
        ],
        "suggestion": "If every task times out, the repo may be very large — small focused tasks (1-2 files) are more reliable.",
    },
    {
        "pattern": re.compile(r"dns|getaddrinfo|name\s+resolution|unreachable", re.I),
        "plain":   "The backend couldn't reach GitHub or the AI provider's servers.",
        "steps":   [
            "Check your internet connection",
            "If a VPN is on, try without it (some VPNs block github.com)",
            "Hit Retry",
        ],
        "suggestion": "Office/campus networks sometimes block outbound HTTPS — if persistent, test once on a mobile hotspot.",
    },

    # ── Vault / encryption ───────────────────────────────────────
    {
        "pattern": re.compile(r"vault_unavailable|master[_\s-]?key|fernet|decrypt.*fail", re.I),
        "plain":   "The server's encryption key isn't configured — an admin needs to set it.",
        "steps":   [
            "This is an admin-side issue — nothing you can do from here",
            "Send one line to support (auremcto.com/support): 'Vault unavailable error on task XYZ'",
            "The admin sets the AUREM_CTO_MASTER_KEY env var and restarts the service",
        ],
        "suggestion": "Self-hosting? Set `AUREM_CTO_MASTER_KEY` in `backend/.env` to a Fernet key (Python: `Fernet.generate_key()`).",
    },

    # ── Token / wallet exhausted ─────────────────────────────────
    {
        "pattern": re.compile(r"token.*exhaust|wallet.*empty|tokens?.*remaining.*0|out\s+of\s+tokens", re.I),
        "plain":   "This month's task quota is used up.",
        "steps":   [
            "Open the Pricing page → hit Upgrade",
            "Pro ($19/mo) → 300 tasks, Team ($49/mo) → 400 tasks plus Maxx mode",
            "The new quota unlocks the moment the subscription activates",
        ],
        "suggestion": "Founder accounts (FOUNDER_EMAILS env var) have unlimited quota — talk to the admin if that fits your case.",
    },

    # ── Common test/lint ─────────────────────────────────────────
    {
        "pattern": re.compile(r"lint.*fail|eslint.*error|ruff.*error|syntax.*error", re.I),
        "plain":   "ORA generated the code but it failed lint, so the commit was blocked for safety.",
        "steps":   [
            "Hit Retry — ORA fixes lint errors itself on the second pass",
            "If the same error repeats: type 'fix the lint errors and try again' in chat",
        ],
        "suggestion": "Maxx mode (Team plan) auto-runs a 2-pass review on lint failures — Pro/Swift needs a manual retry.",
    },
]


# Generic fallback when nothing matches and LLM is also unreachable.
_GENERIC: Dict[str, Any] = {
    "plain":      "The task couldn't complete — the exact reason is in the backend logs.",
    "steps":      [
        "Hit Retry — many failures are one-time blips",
        "If the same error repeats: ask in chat 'why did the last task fail'",
        "If it persists, send the error message to auremcto.com/support",
    ],
    "suggestion": "The first retry succeeds more than 60% of the time.",
}


def _static_match(raw_error: str) -> Dict[str, Any] | None:
    """Return the first static rule whose regex matches the raw error."""
    if not raw_error:
        return None
    for rule in _RULES:
        if rule["pattern"].search(raw_error):
            return {
                "plain":      rule["plain"],
                "steps":      list(rule["steps"]),
                "suggestion": rule["suggestion"],
                "source":     "static_table",
            }
    return None


async def _llm_rewrite(raw_error: str) -> Dict[str, Any] | None:
    """LLM fallback. Wraps the raw error into a {plain, steps,
    suggestion} contract. Returns None on any failure — caller
    falls back to the generic template."""
    key = os.environ.get("EMERGENT_LLM_KEY") or os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return None
    try:
        # Import lazily so the translator module stays cheap to import
        # on cold-start; the dependency is only touched on real failures.
        from services.llm import call_llm_with_meta
        sys_prompt = (
            "You translate raw backend error messages into a plain-English "
            "explanation for a non-technical founder. "
            "Output STRICT JSON only, no prose around it. Schema:\n"
            '{"plain": "<1-2 sentences why this failed, in plain English>",\n'
            ' "steps": ["step 1", "step 2", "step 3"],\n'
            ' "suggestion": "<1 helpful tip in plain English>"}\n'
            "Constraints: total under 200 tokens, no markdown, no code "
            "fences, no extra keys. Steps must be concrete actions the "
            "user can do RIGHT NOW (not 'contact your admin' unless the "
            "error is clearly server-side)."
        )
        user_prompt = f"Raw error from backend worker:\n\n{raw_error[:1200]}"
        result = await call_llm_with_meta(
            sys_prompt, user_prompt,
            max_tokens=300, mode="chat",
        )
        text = (result.get("content") or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        text = text.replace("True", "true").replace("False", "false").replace("None", "null")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if not m:
                return None
            data = json.loads(m.group(0))
        plain = (data.get("plain") or "").strip()
        steps = data.get("steps") or []
        sugg  = (data.get("suggestion") or "").strip()
        if not plain or not isinstance(steps, list):
            return None
        return {
            "plain":      plain[:300],
            "steps":      [str(s)[:200] for s in steps[:6]],
            "suggestion": sugg[:240],
            "source":     "llm_rewrite",
        }
    except Exception as e:                # noqa: BLE001
        logger.warning("error_translator LLM rewrite failed: %r", e)
        return None


async def translate(raw_error: str | None) -> Dict[str, Any]:
    """Public entry point. Always returns a complete contract dict
    of `{plain, steps, suggestion, source, technical}` — never raises."""
    technical = (raw_error or "").strip()[:500]
    if not technical:
        return {**_GENERIC, "source": "empty", "technical": ""}

    hit = _static_match(technical)
    if hit:
        return {**hit, "technical": technical}

    llm = await _llm_rewrite(technical)
    if llm:
        return {**llm, "technical": technical}

    return {**_GENERIC, "source": "generic", "technical": technical}


__all__ = ["translate"]
