# LOOP-STATE — R1-R4 focused round (2026-08-28, post-overnight)

- R1 Future Ledger seed/reconcile: DONE. `/app/memory/ROADMAP.md` has exact F1-F18 (founder-supplied text, verbatim) + standing rules R1-R7. No duplicates confirmed by direct read.
- R2 T7 live PR drill: 5/6 PROOF ARTIFACTS CAPTURED, 1 BLOCKED (root cause identified, not a stop-condition). See `/app/e2e-proof/T7-live/`:
  - Target resolved LIVE (not guessed): `funnel-repro` → `polarisbuiltinc-wq/ora-grounding`, installation `152797252`, status `connected`. The `aurem-rollback-testbed` note in test_credentials.md is confirmed STALE.
  - `pr_open.json`, `pr_merge.json` (merged:true + merge_commit_sha `9c676bb...`), `pr_close.json` (closed, unmerged), `branch_delete.json` (404 confirmed), `no_orphans.json` (0 `auremcto/*` branches, clean) — ALL REAL, captured against live GitHub API.
  - `webhook_payload.json` — EMPTY, could not capture. ROOT CAUSE (two-part, pre-existing, not caused by T7 code): (1) GitHub App `aurem-devops` has `events: []` at both App-level and installation-level — it is NOT subscribed to `pull_request` webhook events at all, so GitHub never sends this event anywhere, in any environment; (2) the 3 event types it IS subscribed to (`installation`, `installation_repositories`, `github_app_authorization`) are ALL failing delivery with HTTP 401 to the configured webhook URL `https://auremcto.com/api/aurem-dev/github/app/webhook` (confirmed via `GET /app/hook/deliveries`). Fixing requires GitHub App-admin action on github.com/settings/apps/aurem-devops (add "Pull requests" event subscription) + a production-side signature/secret fix — both out of scope for this round (prod fence + founder-owned App settings). Logged as founder action item, NOT attempted.
  - `ship_pr_events.json` — empty for both drilled PRs, consequence of the above (ship_pr_opened only fires inside a full loop_engine ship, not this raw-primitives drill; ship_pr_merged/closed depend on the missing webhook).
  - Repo left clean: marker file added-then-deleted from `main`, only `auremcto/*` branches created were both deleted, final branch list = `["main"]` only. No repo test suite run (no code files touched, doc-only marker).
  - GATE: NOT FULLY MET — 5/6, webhook proof blocked on a diagnosed pre-existing GitHub App config gap.
- R3 Repo Quick-Switch: DONE. New `components/dashboard/v2/ProjectSwitcher.jsx`, wired into `TopBar.jsx` (new `projectSwitcherSlot` prop, rendered right after the breadcrumb — no second picker added to the sidebar/RailShell) + `Dashboard.jsx` (passes real `projects`/`activeProject`/`setActiveProjectIdGlobal` — same localStorage key + `aurem:project-changed` event the sidebar already uses, no backend schema change).
  - Revoked/unreachable detection reuses existing `GET /cto/projects/connection-status` (same endpoint RevokedRepoBanner already polls).
  - 4 named tests in `components/dashboard/v2/__tests__/ProjectSwitcher.r3.test.jsx`: `t_switch_repo_a_to_b`, `t_revoked_repo_non_selectable`, `t_login_landing_avoids_revoked`, `t_login_landing_noop_when_active_is_healthy` — all pass.
  - Live E2E (screenshot, test@aurem.dev on this pod, which genuinely has one revoked project `aurem-demo/frontend` + many connected `ora-grounding` projects): dropdown rendered 43 real projects, revoked ones shown dimmed with "⚠ repo unreachable" and non-selectable; the login-landing auto-heal ACTUALLY FIRED live — toast read "Your last project frontend is unreachable — showing ora-grounding instead." exactly per spec.
  - Full frontend suite: 537/537 passed (93 files) — zero regressions vs the pre-round baseline (524/90).
- R4 Billing/cost guard status report: DONE — full detail in `/app/memory/R4-BILLING-AUDIT.md`. Headline: (a) pre-call check YES (per-user token/task budget in `services/usage.py` + global USD breaker in `services/llm_cost_breaker.py`, both checked before any LLM call); (b) Free user CAN call a real model but IS metered (1,000 tokens/10 tasks/mo hard 402 stop) — NOT a P0 leak, the one zero-check client (`ora_chat_v2/llm_client.py`) is admin-only/unreachable by customers; (c) literal per-plan USD cap NOT BUILT — existing per-plan caps are token/task-count denominated, hardcoded, not live-admin-editable; the only USD cap is a global org-wide breaker, not per-plan; (d) all 4 limit messages are human-readable 402/429s, never raw errors.
- GATE (R1+R2+R3+R4): R1 done clean. R2 5/6 (webhook proof blocked, root cause diagnosed, founder action item). R3 done + live-verified + 0 regressions. R4 done, report-only, nothing built. STOPPING here per instruction — awaiting founder review before any real-model round.

---

# LOOP-STATE — overnight T1→T8 run (CLOSED 2026-08-28T09:00Z)

- T1 METER: DONE. Code + 4 named tests + regression (36 pass / 2 pre-existing baseline fails, unrelated) + live admin-surface proof (curl) + denominator bugfix from T4 finding. Organic-fresh-ship proof NEEDS REAL-MODEL RE-TEST (MOCK_LLM=true this pod).
- T2 SEO/Kit report: DONE (read-only, see REPORT-overnight.md §2).
- T3 Future Ledger: DONE (F16/F17+R1-R5) / BLOCKED (F1-F15, seed text absent from disk — DECISION NEEDED).
- T4 Session 2 (J1-J4/K1-K10): DONE. testing_agent iteration_386. J3 hit a repo-connect gap (folded into F16). K8 code-verified. HIGH finding (raw error leak) fixed same session.
- T5 Parts D/E/F: DONE. /app/memory/PART_D_E_F_SYNTHESIS_2026_08_28.md.
- T6 P1 wave: DONE (5/5). P1a per-account lockout (integration_expert consulted first) + live E2E. P1b/P1c/P1d/P1e all done+tested. Per-user PIN (P1a sub-item) = DECISION NEEDED (schema+prod migration).
- T7 Wave 2: BUILD DONE + tested (12/12). Flag live-ON in Preview (proof). Live-drill = CREDENTIALS-PENDING (GitHub App installation unreachable from this pod, see report).
- T8 Final report: DONE. /app/memory/REPORT-overnight.md.

Loop closed clean — no task left IN-PROGRESS. All skips documented in REPORT-overnight.md §4/§7.
