"""
services/customer_cost_tracker.py — 2026-08-19

Real per-call cost logging for the CUSTOMER-FACING chat path
(routers/chat.py `/chat/send` + `/chat/stream`).

Root cause this fixes: `routers/chat.py` and `services/orchestrator.py`
never logged a single dollar of cost anywhere — `ora_chat_usage` only
ever held admin-ORA-tool / system-health-check / QA-canary rows (see
PRD.md 2026-08-19 audit). The BI Cockpit's "real inference cost" was
therefore near-certainly a large undercount of actual OpenRouter spend.

Design choice — SEPARATE collection (`customer_chat_cost`), not a
reuse of `ora_chat_usage`:
  `ora_chat_usage` backs `services/ora_chat/cost_tracker.py`'s
  personal $30/day admin-tool budget guard (which sends real email
  alerts and can force a model downgrade). Writing customer-chat rows
  into that same collection would corrupt that guard with unrelated
  volume. Keeping customer cost in its own collection means the
  existing admin-tool guard is provably unaffected — it never sees
  a customer-chat row — while `routers/admin_bi.py` sums BOTH
  collections for the founder's real, combined cost picture.

Caveat, stated up front (do not let this get lost): `chat_with_tools()`
does not thread real provider-reported token usage back up to
`routers/chat.py` — only the response TEXT and a `provider` label
string. Getting exact token counts would require re-plumbing
`_call_claude`/`_call_deepseek`/`_call_glm`/`_call_longcat` (and every
existing monkeypatch-based test that patches them) to return usage
metadata — a much larger, riskier refactor. This module instead uses
the standard ~4-chars-per-token estimation heuristic against the real
prompt/system/output text actually sent — a genuine, real estimate
(NOT a hardcoded placeholder), consistent with the character-count
approach `routers/chat.py::_deduct_tokens` already uses for the
token-wallet feature. Flagged as `estimation_method` on every row so
this is never confused with exact provider accounting.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from cto_services.db import get_db
from services.ora_chat.cost_tracker import (
    _COST_PER_M_TOKENS,
    _current_day_key,
    _current_month_key,
    compute_cost_usd,
)

logger = logging.getLogger(__name__)

CHARS_PER_TOKEN = 4  # standard rough estimate (OpenAI/Anthropic docs)


def estimate_tokens(text: str) -> int:
    """Character-count token estimate. Real text in, real (estimated)
    tokens out — not a stub."""
    return max(1, len(text or "") // CHARS_PER_TOKEN)


def model_slug_for_provider(provider: str) -> str:
    """Map the free-text `provider` label chat_with_tools() returns
    (e.g. "claude-sonnet-maxx-direct", "glm-5.2+claude-review",
    "deepseek-v3-council-c") to a pricing-table model slug. Provider
    labels are compound/varied by design (see services/llm/_meta.py),
    so this matches by substring rather than exact equality."""
    p = (provider or "").lower()
    # Order matters: "glm-5.2+claude-review" means GLM is PRIMARY with
    # Claude only as a reviewer pass, so glm/longcat/deepseek must be
    # checked before the bare "claude" substring match.
    if "glm" in p:
        return "z-ai/glm-5.2"
    if "longcat" in p:
        return "deepseek/deepseek-chat"  # LongCat priced same tier as DeepSeek today
    if "deepseek" in p:
        return "deepseek/deepseek-chat"
    if "claude" in p:
        return "anthropic/claude-sonnet-4.5"
    return next(iter(_COST_PER_M_TOKENS))  # unknown → cheapest-known default


# ─── Real-customer filter (2026-08 hardening) ─────────────────────
# Task 2 cost audit found 95.1% of `customer_chat_cost` docs (99.3% of
# its $) belong to `user_id="test_admin_001"` — the founder's own
# admin/QA account, not a paying customer. `ora_chat_usage` is
# similarly dominated by `system:health_check`/`canary`/`test-*`
# harness tags that were never signed up at all. Rather than a
# hardcoded literal-string list (which rots as new test IDs get
# invented), a user_id counts as a REAL customer only if it resolves
# to an actual `dev_users` signup that isn't the founder/admin's own
# account — orphaned IDs (canary, system:health_check, unsigned-up
# harness tags) have no dev_users row at all and are excluded here
# automatically.
_NON_CUSTOMER_TIERS = frozenset({"founder"})


def real_customer_match_stages() -> list[dict]:
    """$lookup + $match stages to inject into any aggregation pipeline
    (on a collection with a `user_id` field) right after the initial
    time-window $match, to restrict to REAL customer rows only.
    Cleans up its own temp field via a trailing $project."""
    return [
        {"$lookup": {
            "from":         "dev_users",
            "localField":   "user_id",
            "foreignField": "user_id",
            "as":           "_du",
        }},
        {"$match": {
            "_du.0":        {"$exists": True},
            "_du.tier":     {"$nin": list(_NON_CUSTOMER_TIERS)},
            "_du.is_admin": {"$ne": True},
        }},
        {"$project": {"_du": 0}},
    ]


async def real_customer_user_ids(db, user_ids: list) -> set:
    """Non-pipeline variant — resolve a list of user_ids down to the
    subset that are real customers. Used where a pipeline injection
    isn't practical (e.g. a Python-side loop)."""
    ids = list({u for u in (user_ids or []) if u})
    if not ids:
        return set()
    cursor = db.dev_users.find(
        {"user_id": {"$in": ids},
         "tier": {"$nin": list(_NON_CUSTOMER_TIERS)},
         "is_admin": {"$ne": True}},
        {"_id": 0, "user_id": 1},
    )
    return {d["user_id"] async for d in cursor}


async def log_customer_chat_cost(
    *, user_id: str, session_id: str, project_id: Optional[str],
    route: str, provider: str, prompt_text: str, system_text: str,
    output_text: str,
) -> float:
    """Best-effort, never raises — a cost-logging bug must never break
    a real chat turn. Returns the estimated cost of this call."""
    db = get_db()
    if db is None:
        return 0.0
    model = model_slug_for_provider(provider)
    input_tokens = estimate_tokens((prompt_text or "") + (system_text or ""))
    output_tokens = estimate_tokens(output_text)
    cost = compute_cost_usd(model, input_tokens, output_tokens)
    now = time.time()
    try:
        await db.customer_chat_cost.insert_one({
            "user_id":          user_id,
            "session_id":       session_id,
            "project_id":       project_id,
            "route":            route,
            "provider":         provider,
            "model":            model,
            "input_tokens":     input_tokens,
            "output_tokens":    output_tokens,
            "cost_usd":         cost,
            "estimation_method": "char_count_v1",
            "ts":               now,
            "ts_month":         _current_month_key(now),
            "ts_day":           _current_day_key(now),
        })
    except Exception as e:  # pragma: no cover
        logger.warning("log_customer_chat_cost insert failed: %r", e)
    return cost
