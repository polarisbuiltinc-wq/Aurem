# T3 — First-Experience Wave: full summary (2026-08-30, T2-T5 GO chain)

Two parts: **B4** (bounded real-model LLM-quality probe, done by main
agent, MOCK_LLM briefly off under $3 cap) + **journey verification**
(done by `testing_agent`, MOCK_LLM=true throughout, screenshots + verdict
+ top-3-bounce-moments).

## B4 — real-model window
Full detail: `/app/e2e-proof/T3/B4-real-model/B4_SUMMARY.md`.
- Flip OFF 2026-08-29T16:07:22Z → flip ON 2026-08-29T16:10:43Z (~3m21s).
- Spend: $5.226704 → $5.265372 (**$0.038668** used, 1.3% of $3 cap).
- Post-restore: 3 more real calls attempted, spend unchanged — mock
  gate confirmed holding.
- **2 new findings** (not previously documented at this specificity):
  1. **P1** — casual-tier first message can give a factually wrong
     product description ("audio data" instead of describing the
     actual repo-connected coding/CTO assistant).
  2. **P1** — an agentic-tier follow-up can return a verbatim-repeated
     answer from an earlier, unrelated turn in the same session
     (context-anchoring, not a real answer to the new question).
- K2 (complaint handling): observed healthy on this sample.
- K5 (over-explaining/off-topic drift): observed FAILING (finding #2
  above). K1/K9: not conclusively observed either way this round
  (small sample).

## Journey verification — `testing_agent`, MOCK_LLM=true
Full report: `/app/test_reports/iteration_t3_first_experience_wave_2026_01.json`.
**12/12 required flows PASS**, zero crashes, zero fake-success paths,
zero real GitHub writes attempted despite a ship-intent chat message.

Verified: fresh signup + empty-state connect-repo prompt (no dead end),
admin login + existing project open, Preview/Code/Deploy tabs, chat
casual/agentic/ship-intent (all honest mock responses, ship correctly
refused), notification bell, ProjectSwitcher no-silent-auto-switch on
refresh (W1/H1 guard intact), AdminSystemHealth (all cards incl.
Preview & Deploy Monitor + Webhook Fence).

### Top 3 bounce moments (testing_agent's required deliverable)
1. **Returning-admin dashboard shows 4 warning banners/badges at once**
   on first paint (revoked-access banner, "12 unreachable repos"
   banner, console-error badge, ORA-GUIDE tooltip) — reads as "the
   product is broken," not as a normal state.
2. **Copy mismatch**: fresh-user ORA-GUIDE tooltip says "Connect
   GitHub" but the actual button says "Connect repo →".
3. **"SEND TO ORA →" console-error badge visually outcompetes the
   real chat-send button** in the composer — a first-timer with a
   typed message would likely click the wrong control first.

None of the 3 are code bugs (no `updated_files`, no
`backend_issues`/`ui_bugs` reported) — they are product/design
findings, carried into this report and the backlog, not silently
dropped.

## T3 STATUS: CLOSED (agent-tested; UX findings are advisory,
journey flows are testing_agent-verified)
