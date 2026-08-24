"""
services/slo_metrics.py — 2026-08-26, Blueprint Phase 5.3 gap
("decide your SLOs before you need them for an incident review").

Declares explicit SLO targets for AUREM's two most customer-critical
paths (chat response, ship completion) and computes ACTUAL rolling
compliance against them — purely as aggregation queries over
collections that already exist and are already populated by real
traffic, per Rule 12 (no new tracking infra):

  - chat_response : p95/p50 wall-clock of `/api/chat/send` requests,
    read from `health_endpoint_latency` (the same collection
    `services/health_score.py::score_performance()` already samples
    via `main.py`'s `_health_latency_sampler_mw`). `/chat/send` and
    `/chat/stream` both resolve through the exact same
    `chat_with_tools()` call underneath (the stream endpoint just
    chunks the same finished string over SSE afterward) — so
    `/chat/send`'s sampled latency is a real, honest proxy for "how
    long a chat turn actually takes," even though `/stream` itself is
    excluded from the sampler (streaming connections have a different
    duration shape and were excluded when the sampler was built).
  - ship_completion : p95/p50 of `completed_at - created_at` over
    `cto_tasks` documents with `status == "done"` (a real, successful
    ship) — both fields are written today by every task worker
    (`routers/cto_projects.py::_run_task`/`_run_task_via_api`), no new
    write path needed.

Targets are declared constants (not invented per-request) — the
"good" boundary for chat reuses the existing internal
`CHAT_SOFT_TIMEOUT_S` constant (`routers/chat.py`) as the "bad"
boundary, since that's the point the product's OWN code already
treats a chat turn as "slow." Ship's "bad" boundary reuses the
existing "5 min wall-clock" SSE-stream-close constant
(`routers/cto_projects.py`).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

# Chat response — reuses CHAT_SOFT_TIMEOUT_S (routers/chat.py, default
# 48s) as the "bad" boundary; "good" is a deliberately tighter target
# so the SLO means something before the product's own slow-path kicks in.
CHAT_SLO_GOOD_MS = 15_000
CHAT_SLO_BAD_MS = 48_000

# Ship completion — "bad" reuses the existing 5-min (300s) SSE stream
# wall-clock close already hardcoded in cto_projects.py; "good" is a
# realistic fast-path target (plan+execute+verify+commit with no retries).
SHIP_SLO_GOOD_S = 90
SHIP_SLO_BAD_S = 300


def _percentile(sorted_vals: list, pct: float):
    if not sorted_vals:
        return None
    idx = max(0, min(len(sorted_vals) - 1, int(len(sorted_vals) * pct) - 1))
    return sorted_vals[idx]


async def compute_slo(db, *, period_days: int = 7) -> dict:
    if db is None:
        return {"error": "no_db"}
    since_dt = datetime.now(timezone.utc) - timedelta(days=period_days)

    # ── chat_response SLO ──────────────────────────────────────────
    chat_docs = await db.health_endpoint_latency.find(
        {"ts": {"$gte": since_dt}, "path": "/api/chat/send"},
        {"_id": 0, "elapsed_ms": 1},
    ).to_list(20000)
    chat_elapsed = sorted(d["elapsed_ms"] for d in chat_docs if isinstance(d.get("elapsed_ms"), (int, float)))
    chat_p50 = _percentile(chat_elapsed, 0.50)
    chat_p95 = _percentile(chat_elapsed, 0.95)
    chat_met = (chat_p95 is not None) and (chat_p95 <= CHAT_SLO_GOOD_MS)

    # ── ship_completion SLO ─────────────────────────────────────────
    since_epoch = since_dt.timestamp()
    ship_docs = await db.cto_tasks.find(
        {"status": "done", "completed_at": {"$gte": since_epoch}, "created_at": {"$ne": None}},
        {"_id": 0, "created_at": 1, "completed_at": 1},
    ).to_list(20000)
    ship_durations_s = sorted(
        (d["completed_at"] - d["created_at"])
        for d in ship_docs
        if isinstance(d.get("created_at"), (int, float)) and isinstance(d.get("completed_at"), (int, float))
        and d["completed_at"] >= d["created_at"]
    )
    ship_p50 = _percentile(ship_durations_s, 0.50)
    ship_p95 = _percentile(ship_durations_s, 0.95)
    ship_met = (ship_p95 is not None) and (ship_p95 <= SHIP_SLO_GOOD_S)

    return {
        "period_days": period_days,
        "slos": {
            "chat_response": {
                "label": "Chat response (p95, /chat/send full-turn)",
                "target_good_ms": CHAT_SLO_GOOD_MS,
                "target_bad_ms": CHAT_SLO_BAD_MS,
                "p50_ms": chat_p50,
                "p95_ms": chat_p95,
                "sample_size": len(chat_elapsed),
                "met": chat_met if chat_elapsed else None,
            },
            "ship_completion": {
                "label": "Ship completion (p95, task created→done)",
                "target_good_s": SHIP_SLO_GOOD_S,
                "target_bad_s": SHIP_SLO_BAD_S,
                "p50_s": round(ship_p50, 1) if ship_p50 is not None else None,
                "p95_s": round(ship_p95, 1) if ship_p95 is not None else None,
                "sample_size": len(ship_durations_s),
                "met": ship_met if ship_durations_s else None,
            },
        },
    }
