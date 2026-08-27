# ROADMAP — prioritized backlog (post 2026-08-27 ORA Chat v2 P1-P5 checkpoint)

See PRD.md for original requirements/architecture, CHANGELOG.md for what's shipped.

## P0 — in-flight / founder-blocking
- **ORA Chat v2 P6-P9** (next up, per founder's explicit sequential ordering):
  - P6: on-demand page inspector (screenshot capture + Qwen-VL vision, data-only injection, no continuous monitoring). Must confirm the *current* DashScope Qwen-VL model ID/pricing via `integration_expert` before wiring — do NOT assume Qwen3.8-27B has vision (founder corrected: text-only).
  - P7: scheduled morning brief reusing existing supervise/Resend infra, 1 email + 1 bell event/day/recipient.
  - P8: full UI redesign (stream/stop/think/advice chips already minimally present from P1-P5; still need: state/tool/inspect chips, copy-dev-prompt, recent-action audit list surfaced in UI beyond the raw `/actions/recent` endpoint).
  - P9: tune the founder-supplied system prompt with the founder directly (already embedded verbatim in `engine.py` from the prior scaffold — needs a founder review pass, not a rebuild).
  - P10: security/cost hardening tests (403/no-spend for non-admin — partially covered; daily-cap/kill-switch — partially covered; needs a dedicated pass once P6-P9 land).
- **Real DashScope key**: founder has not yet provided `LLM_API_KEY`. Once provided: flip `MOCK_LLM=false`, run one real-model smoke test, report model name/status only (never the key itself).
- **GitHub connect — production incident**: Preview-side recovery-link UI aid shipped and tested; the underlying production stuck-install for `RevootsBeauty/Revoots` has NOT been confirmed fixed by the founder. Ask for a fresh repro/timestamp if it recurs after the founder's manual revoke+reinstall wears off.

## P1 — known gaps / optional cleanup (non-blocking)
- `backend/.env` has a harmless-but-confusing duplicate `LLM_MODEL` key (line 14 legacy value now unused, superseded by `DEEPSEEK_COUNCIL_MODEL`; ORA v2's own `LLM_MODEL=qwen3.8-27b` is the live one). Left alone per the "never delete .env keys" rule — flagged as OPTIONAL clarity cleanup only if a future round touches that file anyway.
- `routers/ora_chat.py` is ~1122 lines (down from 1777 after removing legacy pipeline) — testing_agent suggested splitting into `ora_chat_v2_router.py` once P6-P9 land, to bring it back under ~700 lines. Not blocking.
- Promptfoo `qa/simulated-user/promptfooconfig.yaml` — the `1f deploy intent` case re-tag (from the P1-P7 "Run all tasks" round) was deprioritized in favor of live P6 proof and never circled back to. Still outstanding.

## P2 — future / nice-to-have
- Visibility Kit (TASK 2, founder-supplied spec) — explicitly gated behind its own "Phase A dogfood against AUREM" prerequisite. Not started, no code written. Do not begin until founder confirms Phase A is satisfied.
- `ora_chat_v2` catalog currently has a small, deliberately-scoped action set (create/park backlog item, toggle a single whitelisted flag, trigger digest). Expanding the catalog is a P2+ decision for the founder to make explicitly per-action (each new action needs its own risk-tier + idempotency + audit wiring).

## Known pre-existing baseline noise (not to be "fixed" opportunistically)
- `tests/test_ora_chat.py::TestSystemPromptLayering::test_default_house_rules_content_matches_spec` — stale assertion vs `services/ora_chat/safety.py::DEFAULT_HOUSE_RULES`, unrelated to any 2026-08-27 round, confirmed via git diff. Leave as-is unless a round specifically touches `safety.py`.
