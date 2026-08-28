# R4 — Billing / Cost Guard status report (2026-08-28, report-only, nothing built/changed)

Audited: `routers/chat.py`, `routers/diagram.py`, `routers/security_scan.py`,
`services/usage.py`, `services/subscription_tiers.py`, `services/llm_cost_breaker.py`,
`services/llm/_meta.py`, `services/ora_chat_v2/llm_client.py`, `routers/ora_chat.py`,
`backend/.env` (MOCK_LLM / key presence only, no secret values read).

## (a) PRE-CALL CHECK — YES, two independent layers, both run BEFORE the LLM call

1. **Per-user** — `assert_has_budget()` + `assert_has_task_budget()`
   (`services/usage.py`) called at the very top of `chat_send`/`chat_stream`
   in `routers/chat.py` (Iter 364 hard-stop fix, explicitly documented in
   the code as fixing an older "clamp AFTER the call" bug) and in
   `routers/diagram.py`. Raises `HTTPException(402)` before any provider
   call. `routers/security_scan.py`'s Vanguard auto-fix path uses a sibling
   mechanism, `assert_can_fix()` (task-quota, tier-gated, "Gate BEFORE any
   work").
2. **Global safety net** — `assert_within_cap()` (`services/llm_cost_breaker.py`,
   tagged G13) wired into the single shared `call_llm_with_meta()` choke
   point (`services/llm/_meta.py`) used by orchestrator / chat / loop_engine
   / Council / every mode. Hourly + daily + per-loop USD ceilings, checked
   before every call, org-wide (not per-user/per-plan).

## (b) FREE USER + REAL MODEL — Free user CAN call a real model today, but it IS metered (not a P0 hole)

`OPENROUTER_API_KEY` is present in `.env` — the customer chat path
(`routers/chat.py` → `services.llm`) is live with a real key today. A
Free-tier user CAN trigger a real LLM call, but is capped at **1,000
tokens** + **10 tasks/month** (`PLAN_LIMITS["free"]`, `MONTHLY_TASK_LIMITS["free"]`
in `services/usage.py`/`services/subscription_tiers.py`) before a hard
402 stop — this is working-as-designed metering, not an unmetered leak.
No free-burn hole found on the 3 customer-facing paths audited
(chat, diagram, vanguard-fix).
The one LLM client with **zero** budget check of any kind —
`services/ora_chat_v2/llm_client.py` (the ORA-Admin chat client) — is
unreachable by any customer: every route in `routers/ora_chat.py` is
gated by `require_admin` per its own docstring ("founder + admin flags
only"), and today `MOCK_LLM=true` + no `LLM_API_KEY` set for that path
anyway, so it's double-safe even for admins right now.

## (c) PER-PLAN USD CAP — NOT BUILT (literal gap); a differently-denominated substitute exists

Per-plan ceilings exist and ARE enforced pre-call, but they are
**token-count** (`PLAN_LIMITS`: free 1,000 / starter 10,000 / pro 50,000 /
team 100,000) and **task-count** (`MONTHLY_TASK_LIMITS`) denominated —
**not USD**. They are hardcoded Python constants in `services/usage.py` /
`services/subscription_tiers.py`, not a live admin-editable config (an
admin CAN grant a user extra `tokens_granted` at runtime, but cannot
retune the base per-plan ceiling itself without a code change + deploy).
The only USD-denominated cap that exists (`LLM_COST_CAP_HOURLY` /
`LLM_COST_CAP_DAILY`, `llm_cost_breaker.py`) is **global/org-wide across
every user and plan**, env-var-only — explicitly NOT per-plan.
**Conclusion: audit item #22, taken literally ("hard per-plan USD cap,
admin-editable"), is NOT BUILT.** A functionally-similar (same business
intent: stop a plan tier from costing more than it pays for) but
differently-denominated per-plan cap IS built and pre-call-enforced today.

## (d) LIMIT MESSAGE — human-readable in every mechanism, never a raw error/bare 429

- Token wallet exhausted → HTTP 402: *"Token limit reached (1,000/1,000).
  Upgrade your plan or wait for an admin grant to continue."*
- Monthly task cap hit → HTTP 402: *"You've used all 10 tasks on the Free
  plan this month. Upgrade to Pro for unlimited tasks."*
- Global USD breaker (per-loop) → HTTP 429: *"You've used up your tasks
  for this month. Your work is safe."*
- Global USD breaker (org hourly/daily) → HTTP 429: *"Hourly LLM spend
  cap reached — new requests temporarily blocked. Retry in ~1h."*

## GATE verdict for the founder

- (b) = **NO**, not a P0 — Free-tier LLM usage is genuinely metered today.
- (c) = **gap** — no literal per-plan USD cap exists; token/task-count
  caps substitute for it today and DO stop runaway spend per-plan, just
  not in dollars and not via a live admin-editable field.
- Per the standing rule ("if (b) is YES or (c) is not-built, the
  real-model round does NOT start"): (b) is NO so that trigger doesn't
  fire, but (c) is a literal not-built — flagging for founder decision
  on whether the existing token/task-count per-plan caps are an
  acceptable substitute, or whether a true USD-denominated,
  admin-editable per-plan cap must be built first.
