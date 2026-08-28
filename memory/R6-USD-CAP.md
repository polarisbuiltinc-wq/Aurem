# R6 — Per-Plan USD Cap for ORA v2 / Qwen (2026-08-28)

Closes audit item #22 for the DashScope/Qwen LLM client
(`services/ora_chat_v2/llm_client.py`). Layers a real dollar ceiling
ON TOP of the existing token/task caps (`services/usage.py`, untouched,
still gates the OTHER chat path) and the existing global hourly/daily
breaker (`services/llm_cost_breaker.py`, untouched, still gates the
orchestrator/loop path).

## What was built

- **`services/llm_rate_table.py`** — admin-editable model → $/1M-token
  rates. Seeded with REAL Alibaba Cloud Model Studio (DashScope)
  international rates, cited 2026-08-28 (web-search verified against
  the exact model names in `.env`):
    - `qwen3.8-27b` (active chat model): $0.425/1M in, $2.55/1M out.
    - `qwen3.7-plus` (active vision model): $0.40/1M in, $1.60/1M out.
  `_default` fallback priced at the chat-model rate so an unrecognised
  model is never silently under-counted.
- **`services/llm_usd_cap.py`** — `DEFAULT_PER_PLAN_CAPS_USD` (free
  $0.50/mo, starter $3/mo, pro $15/mo, team $50/mo, founder unlimited)
  + `DEFAULT_GLOBAL_KILL_SWITCH_USD` ($200/mo org-wide backstop).
  `assert_within_usd_cap()` checks both BEFORE any provider call;
  `record_usd_spend()` logs REAL cost after a successful call;
  `backfill_current_month_from_usage_log()` — idempotent, dry-run-first
  migration from the existing `ora_chat_usage` log.
- **Enforcement wired into the single choke point**
  (`services/ora_chat_v2/llm_client.py::stream_chat()`): right after
  model resolution, BEFORE the `AsyncOpenAI` client is even
  constructed — zero tokens spent past the cap. On block, yields
  `{"type": "error", "error": "monthly_limit_reached", "detail":
  "Monthly limit reached — upgrade to continue."}` (never a raw
  429/stack). `services/ora_chat_v2/engine.py` now threads `user_id`
  (the admin's `admin_id`) through both call sites.
- **Admin API** (`routers/admin_llm_usd_cap.py`, registered in
  `main.py`): `GET/POST /admin/llm/rate-table`, `GET/POST
  /admin/llm/usd-caps`, `GET /admin/llm/usd-caps/spend/{user_id}`,
  `POST /admin/llm/usd-caps/backfill` (dry_run default true).

## Live-verified (real, not mocked)

- Backfill dry-run against real `ora_chat_usage` data: 1,211 rows this
  month, $0.0132 real computed spend for `test_admin_001`.
- Backfill real run + re-run: identical totals both times (idempotent,
  confirmed via curl, not just the test suite).
- `test_admin_001` (founder/admin) is correctly exempt (`tier ==
  "founder"` → unlimited) — matches every other cap in this codebase;
  today's only caller (the admin ORA copilot) is unaffected, exactly
  as intended. This ALSO means: today, with `MOCK_LLM=true`, nothing
  about this build enables or changes real spend — it only INSTALLS
  the gate so the moment this client is ever opened to non-founder
  traffic (or MOCK is flipped for a founder-only smoke test), the cap
  is already live and enforced pre-call.

## Tests (5 named, all passing)

`tests/test_r6_llm_usd_cap.py`:
  - `test_usd_rate_table_used` — exact $ math for known token counts.
  - `test_usd_cap_blocks_over` — over cap → exact human message, zero
    tokens spent after (ledger row count unchanged).
  - `test_usd_free_user_precall_block` — through the REAL
    `stream_chat()` choke point with the provider client mocked to
    `raise AssertionError` if ever constructed — proves the block
    happens strictly before any network call.
  - `test_usd_backfill_idempotent` — same source row, backfilled
    twice, ledger has exactly 1 row (upsert, not duplicate).
  - `test_usd_secondary_caps_kept` — `services.usage.assert_has_budget`
    still raises HTTP 402 for an exhausted user — confirms R6 didn't
    touch/disable the existing token/task cap.

## E2E note (honest gap, standing-rule-compliant)

The founder's spec asked for a live-UI E2E ("exhaust a sub-cap,
message appears, admin edits the ceiling up, next call proceeds").
**Not done as a live-UI browser flow** — doing so would require
flipping `MOCK_LLM` off for a real call, which the standing rules for
this round explicitly forbid ("No MOCK_LLM activation changes, no
real LLM key wiring, no real-model smoke/spend"). `test_usd_free_user_precall_block`
is the compliant equivalent: it exercises the REAL `stream_chat()`
function end-to-end with only the network boundary (the OpenAI SDK
client) mocked, and proves the block fires before that boundary. A
true live-UI E2E is queued for the R8 real-model round (gated on the
founder's key + GO per the standing sequencing).

## Full regression check

- Targeted backend subset (ora_chat/usage/llm/webhook/usd/github_app,
  489 tests): 12 failed, 477 passed. **11 of the 12 are pre-existing
  in `test-baseline.txt`.** The 1 not in baseline
  (`test_iter2026_08_28_ora_chat_v2_e2e.py::TestActionFlow::test_approve_reversible_action`)
  was investigated and is **not caused by this round** — it asserts
  `/ora-chat/actions/recent` returns separate proposed/approved/executed
  rows, but that endpoint deliberately calls `audit.recent_proposals()`
  (one merged row per proposal, by its own docstring — "so the founder
  sees one line per decision"), a pre-existing design choice in code I
  never touched this round (`services/ora_chat_v2/audit.py`,
  `routers/ora_chat.py` lines 406-412, last touched in the prior
  overnight round). Flagging for founder awareness, not fixed
  (out of scope — no "while-you're-here" fixes).
- Full frontend suite: 541/541 passing (94 files), zero regressions.
