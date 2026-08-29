# NEW P0 (2026-08-28) — ship-approve false-success + no-button close-out

## Founder-verified original bugs
1. Approve button did not render after a fix diagnosis (only copy/like/dislike).
2. "yes please ship it" pointed at a button that wasn't there.
3. Bare "approve" -> "Approved! Let me know what you need" with NO commit landing (false success).

## Root causes found
- Task 1 (no button): NOT a frontend rendering bug — `MessageBubble.new_p0_button_renders.test.jsx`
  proves the button renders correctly from any valid injected `aurem-handoff` fence, regardless of
  `m.provider` value. The real defect was upstream: the mismatch-gate in
  `response_confidence.py::response_seems_mismatched` swapped out a legitimately re-emitted fence
  for the generic FALLBACK_MESSAGE on a bare confirmation reply, because it had no memory that the
  prior turn already carried a fix signal. Fixed via `prior_turn_had_fix_signal` (already landed
  before this session's continuation) + verified still correct now.
- Task 2 (false success, the #1 fix): the intent-gateway CASUAL branch (`casual_direct_reply`) is a
  free-form LLM call with zero guard. A bare "approve"/"yes"/"ship it" with nothing pending reached
  it and the model (mock or real) could improvise "Approved!" with zero real action behind it.

## Fix (backend/services/response_confidence.py, backend/core/intent_gateway.py, backend/routers/chat.py)
- `is_confirmation_reply()` widened to also catch "yes please ship it"-style phrasing (was only
  bare single-phrase before).
- `contains_false_success_claim()` / `apply_no_false_success_guard()` — new deterministic guard.
  Applied at 2 chokepoints in BOTH `chat_send` and `chat_stream`: (a) short-circuits the casual LLM
  call entirely for a bare confirmation with no prior fix signal (deterministic honest reply, zero
  LLM spend); (b) final defense-in-depth scan on the assembled content right before it reaches the
  user, on every path (including agentic).
- `intent_gateway.py::_classify_heuristic` already routed pending-fix + bare-ack to TIER_AGENTIC
  (real re-processing pipeline) instead of TIER_CASUAL — re-verified still correct.

## Fix (frontend/src/components/MessageBubble.jsx, ChatPanel.jsx) — Task 1d, honest fallback
- The "please retype the fix" text-only fallback replaced with a REAL, working
  `ship-cta-fallback-retry-{idx}` button wired to `ChatPanel.jsx`'s new `retryLastFix()` (uses
  `send()`'s `promptOverride`, not just prefilling the input box) — one click actually resubmits
  the request.

## Proof
- Backend unit: `tests/test_new_p0_2026_08_28_false_success_guard.py` — 10/10 pass (deterministic
  guard logic, incl. the testing_agent-found present-tense "On it—shipping now!" gap, now fixed).
- Frontend unit: `MessageBubble.new_p0_button_renders.test.jsx` — 4/4 pass (button renders from a
  valid fence regardless of model; real retry button in the fallback).
- Live curl E2E against running Preview, BOTH `/chat/send` and `/chat/stream`:
  - fresh-session "approve" -> honest NO_PENDING_FIX_MESSAGE, provider `intent-gateway-no-pending-fix`.
  - fresh-session "yes please ship it" (founder's exact quote) -> same honest message.
- `testing_agent` (`/app/test_reports/iteration_p0_ship_approve_fix_verify_2026_01_29.json`):
  confirmed all of the above live in a real browser session; 0 critical issues; 1 minor finding
  (present-tense promise gap) — fixed same session, re-verified live via curl above.
- Live ship+rollback drill against `polarisbuiltinc-wq/ora-grounding` (via the SAME
  `POST /cto/tasks/submit` + `POST /cto/tasks/{id}/rollback` endpoints the real Approve button
  calls): real commit `cf64ac7` landed, real revert `689217d` landed, repo restored byte-identical,
  zero orphan branches. See `/app/e2e-proof/P0-prod-repro/summary.json`.
- Full backend regression (6595 tests) + full frontend suite (567 tests) reconciled against
  `test-baseline.txt`: every failure not already documented was individually confirmed pre-existing
  via `git stash` A/B (several isolating this session's exact 3 changed files) before being added
  to baseline with a reason. Zero unexplained new regressions from this session.

## Honest, separate, lower-priority finding (NOT this P0, not fixed here)
Under `MOCK_LLM=true`, the mock model does not reliably emit a valid `aurem-handoff` fence for a
realistic fix request — so the fallback banner (not the green button) is what a live MOCK session
usually sees. This is a model-quality gap, not a regression of the fixed code path; the fixed code
path handles it gracefully exactly as designed. Verify the green button path under a real model at
R8.

## Service worker (Task 5)
`frontend/public/sw.js` CACHE_VERSION = "aurem-v3" — IDENTICAL to what the founder observed live.
Not stale. R11's stale-service-worker hypothesis is dropped/de-ranked; it does not explain any
symptom in this P0.
