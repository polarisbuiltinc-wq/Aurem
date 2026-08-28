"""
services/llm_rate_table.py — R6 (2026-08-28).

Admin-editable model -> $/1M-token rate table for the ORA v2
(DashScope/Qwen) LLM client (services/ora_chat_v2/llm_client.py).
Seeded with REAL rates pulled from Alibaba Cloud Model Studio's
pricing page, international endpoint, cited 2026-08-28:
  - qwen3.8-27b  (LLM_MODEL, the active chat model):
      $0.425 / 1M input tokens, $2.55 / 1M output tokens.
  - qwen3.7-plus (LLM_VISION_MODEL, the active vision model):
      $0.40 / 1M input tokens, $1.60 / 1M output tokens
      (0-256K context tier — Model Studio's international list price).
These are starting points, not contractual — an admin can edit any
entry live via the Models & LLM settings screen
(POST /admin/llm/rate-table); DB values always win over these
defaults.
"""
from __future__ import annotations

import time
from typing import Optional

DEFAULT_RATES: dict[str, dict[str, float]] = {
    "qwen3.8-27b":  {"input_per_m": 0.425, "output_per_m": 2.55},
    "qwen3.7-plus": {"input_per_m": 0.40,  "output_per_m": 1.60},
    # Conservative fallback for any model not listed above — priced at
    # the (more expensive) chat-model rate so an unrecognised model
    # never silently under-counts real spend.
    "_default":     {"input_per_m": 0.425, "output_per_m": 2.55},
}

_DOC_ID = "llm_rate_table"


async def get_rate_table(db) -> dict[str, dict[str, float]]:
    """DEFAULT_RATES with any admin-saved overrides merged on top."""
    rates = {k: dict(v) for k, v in DEFAULT_RATES.items()}
    if db is None:
        return rates
    try:
        doc = await db.admin_settings.find_one({"_id": _DOC_ID})
        for model, r in ((doc or {}).get("rates") or {}).items():
            rates[model] = {
                "input_per_m":  float(r.get("input_per_m", 0) or 0),
                "output_per_m": float(r.get("output_per_m", 0) or 0),
            }
    except Exception:                                          # noqa: BLE001
        pass
    return rates


async def set_rate_table(db, rates: dict, updated_by: Optional[str] = None) -> None:
    """Admin write. Persists the FULL rate dict (existing DB-override
    pattern used elsewhere in this codebase, e.g.
    services/github_app_config.py) — merged on top of DEFAULT_RATES
    by get_rate_table(), never replacing it wholesale."""
    clean = {
        model: {
            "input_per_m":  float(r.get("input_per_m", 0) or 0),
            "output_per_m": float(r.get("output_per_m", 0) or 0),
        }
        for model, r in (rates or {}).items()
    }
    await db.admin_settings.update_one(
        {"_id": _DOC_ID},
        {"$set": {"rates": clean, "updated_at": time.time(), "updated_by": updated_by}},
        upsert=True,
    )


def rate_for(rates: dict, model: str) -> dict:
    return rates.get(model) or rates.get("_default") or {"input_per_m": 0.0, "output_per_m": 0.0}


def cost_usd(rates: dict, model: str, input_tokens: int, output_tokens: int) -> float:
    r = rate_for(rates, model)
    return (input_tokens / 1_000_000.0) * r["input_per_m"] + (output_tokens / 1_000_000.0) * r["output_per_m"]
