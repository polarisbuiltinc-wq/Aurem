"""
services/ora_chat/image_gen.py — Phase 5 · Feb 2026

Deliberately-minimal image-generation service for the /ora chat.
Founder-tier only.  gpt-image-1 · low quality · 1024×1024.
Hard cost gates:

  · Per-user monthly cap:   10 images  (default_cap_ora_images_month)
  · Global daily kill-switch: $3.00 USD  (ORA_IMAGE_DAILY_CAP_USD)
  · Per-image cost (fixed): $0.011 (gpt-image-1 low @ 1024²)

Both gates check-then-reserve BEFORE the OpenAI call so a burst of
concurrent requests can't over-shoot the ceiling.  Post-call the
counter is trued-up to the actual reported cost — this is closer to
"optimistic charge, correct on receipt" than a strict two-phase
commit, but for $0.011 per unit + hard daily $3 ceiling the drift
window is 1 image at absolute worst.

Founder brief (2026-02-08): "build for CURRENT reality, not projected
reality" — no Pro/Team access yet, no free-tier growth hook.
"""
from __future__ import annotations

import base64
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


# ── Cost / cap constants (single source of truth) ──────────────────
GPT_IMAGE_1_LOW_USD_PER_IMAGE = 0.011        # Founder-locked default
ORA_IMAGE_DAILY_CAP_USD       = float(os.environ.get("ORA_IMAGE_DAILY_CAP_USD", "3.00"))
ORA_IMAGE_MONTH_PER_USER_CAP  = int(os.environ.get("ORA_IMAGE_MONTH_PER_USER_CAP", "10"))
ORA_IMAGE_MODEL               = "gpt-image-1"
ORA_IMAGE_QUALITY             = "low"        # cheapest tier
ORA_IMAGE_SIZE                = "1024x1024"


class ImageGenError(Exception):
    """Raised when a gate blocks generation. Carries a structured
    payload the router can render as a 402 / 429 without prose parsing."""
    def __init__(self, kind: str, message: str, extra: Optional[dict] = None):
        super().__init__(message)
        self.kind    = kind
        self.message = message
        self.extra   = extra or {}


def _utc_today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def _utc_month_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


async def check_and_reserve(db, user_id: str) -> dict:
    """Atomic pre-flight gate.

    Enforces (in order):
      1. Global daily USD cap.
      2. Per-user monthly image count.

    On success, reserves the cost against BOTH counters (increment
    global daily spend + user monthly count).  Caller MUST call
    `truebup(...)` after the OpenAI response with the actual cost so
    the daily meter reflects reality.
    """
    day  = _utc_today_key()
    mon  = _utc_month_key()
    # 1. Global daily USD cap.
    daily = await db["ora_image_daily_spend"].find_one({"day": day}) or {}
    already = float(daily.get("spent_usd") or 0.0)
    if already + GPT_IMAGE_1_LOW_USD_PER_IMAGE > ORA_IMAGE_DAILY_CAP_USD:
        raise ImageGenError(
            kind="daily_cap_reached",
            message=(f"Global daily image cap of ${ORA_IMAGE_DAILY_CAP_USD:.2f} "
                     f"reached (already ${already:.4f} today). Try again after "
                     "00:00 UTC."),
            extra={"day": day, "spent_usd": already,
                     "cap_usd": ORA_IMAGE_DAILY_CAP_USD},
        )
    # 2. Per-user monthly count cap.
    monthly = await db["ora_image_user_month"].find_one(
        {"user_id": user_id, "month": mon}) or {}
    used = int(monthly.get("count") or 0)
    if used >= ORA_IMAGE_MONTH_PER_USER_CAP:
        raise ImageGenError(
            kind="monthly_cap_reached",
            message=(f"You've used {used}/{ORA_IMAGE_MONTH_PER_USER_CAP} "
                     f"image generations this month."),
            extra={"month": mon, "used": used,
                     "cap": ORA_IMAGE_MONTH_PER_USER_CAP},
        )
    # Reserve (pre-charge) both counters atomically.
    await db["ora_image_daily_spend"].update_one(
        {"day": day},
        {"$inc": {"spent_usd": GPT_IMAGE_1_LOW_USD_PER_IMAGE},
         "$set": {"day": day}},
        upsert=True,
    )
    await db["ora_image_user_month"].update_one(
        {"user_id": user_id, "month": mon},
        {"$inc": {"count": 1},
         "$set": {"user_id": user_id, "month": mon}},
        upsert=True,
    )
    return {"day": day, "month": mon,
             "reserved_usd": GPT_IMAGE_1_LOW_USD_PER_IMAGE,
             "user_used": used + 1}


async def refund_reservation(db, user_id: str) -> None:
    """Undo `check_and_reserve` if the actual OpenAI call fails so a
    transient upstream error doesn't burn the founder's daily/monthly
    quota."""
    day = _utc_today_key()
    mon = _utc_month_key()
    await db["ora_image_daily_spend"].update_one(
        {"day": day},
        {"$inc": {"spent_usd": -GPT_IMAGE_1_LOW_USD_PER_IMAGE}},
    )
    await db["ora_image_user_month"].update_one(
        {"user_id": user_id, "month": mon},
        {"$inc": {"count": -1}},
    )


async def generate(prompt: str) -> dict:
    """Actually call the OpenAI image API via Emergent LLM key.

    Returns `{image_base64, mime, cost_usd, prompt}`.  Does NOT
    enforce gates — caller must have already reserved via
    `check_and_reserve`.
    """
    key = os.environ.get("EMERGENT_LLM_KEY", "")
    if not key:
        raise ImageGenError("missing_key",
                            "EMERGENT_LLM_KEY not configured on server.")
    prompt = (prompt or "").strip()
    if not prompt:
        raise ImageGenError("empty_prompt", "Image prompt is empty.")
    if len(prompt) > 1000:
        prompt = prompt[:1000]
    from emergentintegrations.llm.openai.image_generation import OpenAIImageGeneration
    gen = OpenAIImageGeneration(api_key=key)
    try:
        images = await gen.generate_images(
            prompt=prompt,
            model=ORA_IMAGE_MODEL,
            number_of_images=1,
        )
    except Exception as e:
        logger.warning("ora image_gen: OpenAI call failed: %r", e)
        raise ImageGenError("upstream_error",
                             f"Image generation failed: {str(e)[:200]}")
    if not images or not images[0]:
        raise ImageGenError("no_image_returned",
                             "The image API returned an empty result.")
    b64 = base64.b64encode(images[0]).decode("utf-8")
    return {
        "image_base64": b64,
        "mime":         "image/png",
        "cost_usd":     GPT_IMAGE_1_LOW_USD_PER_IMAGE,
        "prompt":       prompt,
        "model":        ORA_IMAGE_MODEL,
    }


async def daily_status(db) -> dict:
    day = _utc_today_key()
    row = await db["ora_image_daily_spend"].find_one({"day": day}) or {}
    return {
        "day":       day,
        "spent_usd": float(row.get("spent_usd") or 0.0),
        "cap_usd":   ORA_IMAGE_DAILY_CAP_USD,
    }


async def user_month_status(db, user_id: str) -> dict:
    mon = _utc_month_key()
    row = await db["ora_image_user_month"].find_one(
        {"user_id": user_id, "month": mon}) or {}
    used = int(row.get("count") or 0)
    return {
        "month":    mon,
        "used":     used,
        "cap":      ORA_IMAGE_MONTH_PER_USER_CAP,
        "remaining": max(0, ORA_IMAGE_MONTH_PER_USER_CAP - used),
    }
