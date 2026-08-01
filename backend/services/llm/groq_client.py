"""services/llm/groq_client.py — Groq emergency-fallback transport.

Session D · D-2b (LLM Split Phase 4, 2026-02) — extracted from
`services/llm/__init__.py`. Owns:

  • `_GROQ_MODEL` — env-configurable model slug
    (default `llama-3.3-70b-versatile`)
  • `_GROQ_HOUSE_RULES_PATH` — filesystem path to
    `backend/prompts/groq_house_rules.md`
  • `_load_groq_house_rules()` — cached read of the identity prompt
  • `_groq_key()` — env-var accessor for `GROQ_API_KEY`
  • `_call_groq()` — the async caller. NEVER called speculatively —
    only reached after OpenRouter primary AND every free-tier
    candidate have failed. Raises on error (unlike OpenRouter
    helpers that return "") so callers get a LOUD failure when the
    whole chain is dead.

Per founder's spec (2026-02-27): "Groq sirf emergency net hai,
primary nahi banana." Enforcement lives at the call sites
(`_call_deepseek` in `__init__.py`, `call_openrouter_model` in
`openrouter_client.py`), which is why those files hold the routing
policy while THIS file owns only the transport.

Monkeypatch contract (Session D · D-2b, moved from
`services/llm/__init__.py`):
    Tests that need to simulate "house-rules file missing" must
    monkeypatch `services.llm.groq_client._GROQ_HOUSE_RULES_PATH`
    (the canonical location) — NOT `services.llm._GROQ_HOUSE_RULES_PATH`,
    which is now a re-export binding. Patching a re-export mutates
    only the parent module's namespace; `_load_groq_house_rules()`
    reads its OWN module global and would ignore the fake path.
"""
from __future__ import annotations

import logging
import os


logger = logging.getLogger(__name__)


# ─── Iter 212m-49 — Groq as TRUE last-resort fallback ─────────────────
# Vendor-independent safety net for when OpenRouter (paid AND free
# tier) is unreachable / quota-exhausted / globally rate-limited.
# Groq's own free tier has its own quota that's independent of
# OpenRouter — so a credit-stuffing attack on OpenRouter, an
# OpenRouter outage, or a "global free-tier 429 storm" still gets the
# user a response. Active only when GROQ_API_KEY is set.
_GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")


_GROQ_HOUSE_RULES_PATH = os.path.join(
    # Session C · Sub-step 1 caveat (retained after D-2b): `__file__`
    # is at `services/llm/groq_client.py`, so THREE `dirname` hops are
    # needed to reach `/app/backend/` (services/llm → services →
    # backend). If this file ever moves deeper into a nested package,
    # bump the count. Regression guard: `tests/test_llm_package_paths.py`
    # locks the resolved path.
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "prompts",
    "groq_house_rules.md",
)


def _load_groq_house_rules() -> str:
    """Read the Groq-only house rules from disk. Silent-skip on any
    error (file missing, permission, encoding) — Groq must still
    work even if the rules file is removed. Cached after the first
    successful read for the lifetime of the process."""
    cached = getattr(_load_groq_house_rules, "_cached", None)
    if cached is not None:
        return cached
    try:
        with open(_GROQ_HOUSE_RULES_PATH, "r", encoding="utf-8") as fh:
            text = fh.read().strip()
        setattr(_load_groq_house_rules, "_cached", text)
        return text
    except (FileNotFoundError, PermissionError, UnicodeDecodeError, OSError) as e:
        logger.debug("groq_house_rules.md not loaded: %r — defaults apply", e)
        setattr(_load_groq_house_rules, "_cached", "")
        return ""


def _groq_key() -> str:
    return os.environ.get("GROQ_API_KEY", "")


async def _call_groq(
    messages: list,
    system: str = "",
    max_tokens: int = 1500,
    temperature: float = 0.7,
) -> str:
    """Async call to Groq Cloud — only reached when OpenRouter primary
    AND every free-tier candidate have failed. Returns the completion
    string. Raises on any error so callers can decide whether to log
    or re-raise; this function never silently returns "" because Groq
    is the LAST link — we want a loud failure to surface that the
    whole chain is dead and the user should know to retry later.

    Note: the official `groq` Python SDK exposes an `AsyncGroq` client
    that mirrors OpenAI's `/v1/chat/completions` schema, so no payload
    surgery is needed."""
    key = _groq_key()
    if not key:
        raise RuntimeError("GROQ_API_KEY not set — emergency fallback unavailable")
    # Imported lazily so deploys without groq installed don't crash at
    # module import time.
    from groq import AsyncGroq
    client = AsyncGroq(api_key=key, timeout=float(os.getenv("GROQ_TIMEOUT_S", "30.0")))
    # Iter 212m-50 — Groq-only house rules. Prepend the markdown rules
    # to the caller-supplied system prompt so the fallback maintains
    # ORA's voice, never breaks character, and refuses destructive ops
    # without confirmation. Silent-skip if the rules file is missing.
    house_rules = _load_groq_house_rules()
    if house_rules:
        effective_system = (
            f"{house_rules}\n\n---\n\n{system}".strip()
            if system else house_rules
        )
    else:
        effective_system = system
    msgs = ([{"role": "system", "content": effective_system}]
            + messages) if effective_system else messages
    completion = await client.chat.completions.create(
        model=_GROQ_MODEL,
        messages=msgs,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    content = (completion.choices[0].message.content or "").strip()
    return content
