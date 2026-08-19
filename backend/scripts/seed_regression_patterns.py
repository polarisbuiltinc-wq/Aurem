#!/usr/bin/env python3
"""
scripts/seed_regression_patterns.py — 2026-08-19

One-time (idempotent) migration of /app/memory/RECURRING_ISSUES.md's
known bug/policy patterns into the structured `ora_regression_patterns`
Mongo collection, so they're queryable + admin-visible instead of
living only in a markdown file an agent has to remember to open.

Run: python scripts/seed_regression_patterns.py
"""
from __future__ import annotations
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from motor.motor_asyncio import AsyncIOMotorClient
from services.ora_fix_learning import seed_regression_pattern, ensure_indexes


PATTERNS = [
    dict(
        pattern_id="pattern_1_empty_file_body_loop",
        title='"Empty file body" rejection loop',
        symptom='Task fails repeatedly: "AI returned suspect edits '
                '(refusing to push): ... — empty file body". ORA retries '
                'with the same prompt and produces the same empty body.',
        root_cause="Vanguard's pre-commit gate correctly rejects an empty "
                    "file body, but the rejection reason was never fed "
                    "back into the model's next-turn prompt — ORA just "
                    "retried the identical plan.",
        fix_locations=["backend/routers/cto_projects.py::retry_task",
                        "backend/routers/cto_projects.py::_run_task"],
        status="fixed",
        test_ref="tests/test_iter67_recurring_pattern_fixes.py::"
                 "test_retry_endpoint_carries_real_failure_into_new_task",
    ),
    dict(
        pattern_id="pattern_2_slow_api_timeout_message",
        title='"90s timeout" firing on barely-used budget',
        symptom='User sees "I cut myself off at 90s to avoid a runaway '
                'tool-loop" after only 1 tool call — misleading, since the '
                '90s was spent waiting on a slow model API, not looping.',
        root_cause="The 90s wall-clock budget doesn't distinguish "
                    "time-to-first-token (model API cold start / queue) "
                    "from genuine reasoning-loop time.",
        fix_locations=["backend/services/orchestrator.py::build_timeout_message",
                        "backend/routers/chat.py"],
        status="fixed",
        test_ref="tests/test_iter67_recurring_pattern_fixes.py::"
                 "test_timeout_message_genuine_loop_at_or_above_three_tool_calls",
    ),
    dict(
        pattern_id="pattern_3_mode_d_boilerplate",
        title="Mode D returns boilerplate for missing-signal cases",
        symptom='Mode D bails with "insufficient signal to diagnose" even '
                'when the user already described the problem in natural '
                "language (no literal stack trace attached).",
        root_cause="mode_d_debugger.py's signal threshold demanded a "
                    "literal HTTP status/stack trace before answering; "
                    "natural-language symptoms didn't count as signal.",
        fix_locations=["backend/services/mode_d_debugger.py::DIAGNOSIS_SYSTEM"],
        status="fixed",
        test_ref="tests/test_recurring_patterns_batch2.py::"
                 "test_pattern3_diagnosis_prompt_accepts_natural_language_signal",
    ),
    dict(
        pattern_id="pattern_4_wrong_mode_classification",
        title="Wrong-mode classification for repo-info queries",
        symptom='A repo-stats question ("kitni total files hain") routed '
                "to Mode D debug and returned a fabricated diagnosis "
                "quoting an unrelated URL from earlier in the conversation.",
        root_cause="The mode classifier matched on stray keywords "
                    "(\"abort\"/\"aborted\") instead of real intent, with "
                    "no confidence score or ambiguity handling.",
        fix_locations=["backend/services/mode_classifier.py::classify_intent_v2",
                        "backend/routers/chat.py"],
        status="fixed",
        test_ref="tests/test_iter67_mode_confidence.py",
    ),
    dict(
        pattern_id="pattern_5_multi_file_scaffold_1_of_n",
        title='Multi-pillar work always finishes 1-of-N then "Next:..."',
        symptom="Asking for N files in one task ships 1 file, says "
                '"Next: implement remaining", requiring the user to '
                "retype the request repeatedly.",
        root_cause="Believed to be a hard 2-file-per-task budget in the "
                    "orchestrator; VERIFIED in code review that no such "
                    "hard cap actually exists — the 1-of-N pattern comes "
                    "from the LLM's own self-limiting, not a code guard.",
        fix_locations=[],
        status="deferred",  # needs prompt engineering, not a code fix
        test_ref="tests/test_recurring_patterns_batch2.py::"
                 "test_pattern5_no_hard_multi_file_cap_in_orchestrator",
    ),
    dict(
        pattern_id="pattern_6_stale_browser_cache",
        title="Stale build / browser cache on production",
        symptom='"Last 2-3 deploys ka theme nahi dikh raha" — production '
                "bundle was actually current; the user's browser had "
                "cached the previous build.",
        root_cause="Client-side service-worker/disk cache held a stale "
                    "bundle after deploy; no easy self-diagnosis for the "
                    "user.",
        fix_locations=["frontend/src/pages/AdminOverview.jsx"],
        status="fixed",
        test_ref="tests/test_recurring_patterns_batch2.py::"
                 "test_pattern6_cache_purge_returns_structured_report",
    ),
    dict(
        pattern_id="policy_env_gitignore_dont_reblock",
        title="`.env` re-appearing in `.gitignore` is NOT a bug",
        symptom="Previous agents repeatedly removed `.env`/`.env.*` lines "
                "from `.gitignore`, believing they blocked deployment.",
        root_cause="Deploy failures were actually caused by missing "
                    "Emergent Deployment Dashboard env vars, not by the "
                    "gitignored .env file. Removing the gitignore lines "
                    "risked leaking secrets via `git add .`.",
        fix_locations=[".gitignore"],
        status="policy",
        test_ref="tests/test_recurring_patterns_batch2.py::"
                 "test_pattern7_8_gitignore_env_hybrid_policy_locked",
    ),
    dict(
        pattern_id="policy_env_gitignore_hybrid_final",
        title="`.gitignore` `.env` policy: hybrid, FINAL",
        symptom="Two prior wrong policies caused real damage: (a) both "
                "ignored → production CORS failures (frontend couldn't "
                "bake REACT_APP_BACKEND_URL at build time), (b) both "
                "committed → would have leaked 38 backend secrets "
                "including the vault master key.",
        root_cause="Vite inlines REACT_APP_* at compile time, so "
                    "frontend/.env MUST be committed (zero secrets in it); "
                    "backend/.env holds real secrets and MUST stay "
                    "gitignored, sourced from the deploy dashboard instead.",
        fix_locations=[".gitignore"],
        status="policy",
        test_ref="tests/test_recurring_patterns_batch2.py::"
                 "test_pattern7_8_gitignore_env_hybrid_policy_locked",
    ),
]


async def main() -> None:
    mongo_url = os.environ.get("MONGO_URL")
    if not mongo_url:
        print("ERROR: MONGO_URL not set.", file=sys.stderr)
        sys.exit(1)
    db = AsyncIOMotorClient(mongo_url)[os.environ.get("DB_NAME", "aurem_dev")]
    await ensure_indexes(db)
    for p in PATTERNS:
        await seed_regression_pattern(db, **p)
    print(f"Seeded {len(PATTERNS)} regression patterns into ora_regression_patterns.")


if __name__ == "__main__":
    asyncio.run(main())
