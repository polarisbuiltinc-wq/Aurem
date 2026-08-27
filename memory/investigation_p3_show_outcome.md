## 2026-08-27 — "Show the Outcome, Never the Engine" P3 (final phase) — DONE, testing_agent-verified

P3a + P3b + P3c all shipped. Report: `/app/test_reports/iteration_p3_show_outcome_2026_08_27.json`. 10/10 new tests pass, 0 new regressions (only the same 4 pre-existing failures from P2 recur).

### P3a — "ORA remembers this" chip
- `frontend/src/components/ChatPanel.jsx` (~line 4683): new plain chip, `data-testid="ora-remembers-chip-{i}"`, literal text "ORA remembers this", no engine wording. Gated on `m.plainEnglishContractActive && (m.councilRecalled || 0) > 0`.
- Rides the SAME `explain_plain_english_v1` flag (documented choice, no sibling flag) via new `onDone` field `plainEnglishContractActive: !!d.plain_english_contract_active` (~line 2640).
- Pre-existing detailed caption (`council-recall-caption-{i}`, "📚 ORA recalled N similar past answers") now gated on `!m.plainEnglishContractActive` so the two never double-render.
- Live backend proof: `POST /chat/send` with `{"prompt":"hi"}` (no project_id) → `council_recalled=2` AND `plain_english_contract_active=true` simultaneously, independently re-verified by testing_agent.
- **Could not get a live browser screenshot** — every `test_admin_001` project currently lacks a working GitHub App installation (`app_installation_missing`, same root cause as P2 item 3), and the UI always forces a project scope ("Home tab removed per founder request"). This is an environment gap, not a P3a code bug — covered by structural tests + curl proof instead. Will be screenshot-able once the founder's GitHub App install lands.

### P3b — Quiet Leak Digest
- `backend/services/leak_digest.py` (new) — weekly (Monday 07:00 UTC default), reuses `ora_audit`/`loop_run_log` collections + `daily_digest.py::_send_via_resend()`. No new email system.
- Plain 3-5 line body with week-over-week counts + one-line spike flag (>3x, and 0→N is never flagged as a spike per founder's note).
- Wired into `main.py` alongside `leak_alert_cron`. 5 tests in `test_iter2026_08_27_p3_show_outcome.py` pass.

### P3c — Rollout prep (documented, not executed)
- `memory/investigation_p3_rollout_prep.md` — documented flag-removal diff (4 call sites in `chat.py`) + 6-item widening precondition checklist (3 green, 3 pending founder action/elapsed time).
- **Not executed.** Founder owns 10%→50%→100% widening.

### Promptfoo tag
- `qa/simulated-user/promptfooconfig.yaml` — "1f deploy intent" tagged with explanatory comment (pre-existing/unrelated to P0-P3), NOT deleted. YAML re-validated (19 tests).

## STATUS: Entire "Show the Outcome, Never the Engine" build (P0-P3) is complete.
Remaining founder actions only: (a) finish GitHub App install on drill repo → flips P2 item 3 ship-E2E test green + unblocks P3a live screenshot, (b) rollout widening decision when ready.
