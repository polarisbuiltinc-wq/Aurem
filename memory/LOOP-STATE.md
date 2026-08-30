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

# MASTER BUILD LOOP P1/P2/P3 (2026-08-28, continuation same day)

- PHASE 0 setup: DONE. Added 1 new entry to `backend/test-baseline.txt` (the R5-R7-found `test_iter2026_08_28_ora_chat_v2_e2e.py::TestActionFlow::test_approve_reversible_action`, unrelated pre-existing design gap, reason documented inline). Baseline now 404+1=405 documented entries. `lint-baseline.txt` (37+1) untouched. MOCK_LLM confirmed `true`.
- PHASE 1 (Real Model + Safe-Ship): PREP DONE, full report `/app/memory/PHASE1-RESULTS.md`. P1-a (USD-cap cost simulation, 10/10 PASS, `/app/e2e-proof/P1-a/usd-cap-sim.log`), P1-b (`/app/memory/R5e-VERIFY-PLAN.md`), P1-c (`/app/memory/R9-PROD-FLIP-CHECKLIST.md`), P1-d (gate chain documented) all DONE. R5e/R8/R9 execution: **PENDING-FOUNDER** (GitHub webhook checklist not yet run; no real-model round started; no R9 GO given) — none attempted, per phase contract.
- STATUS: **PHASE 2 GO'd** — founder confirmed "CONTINUE ALL" = "GO PHASE 2" (2026-08-28, this round). Phase 3 (Kit B+C+admin dashboard/F7) not started.

---

# PHASE 2 — UX Fix Wave (2026-08-28, this round)

- **P2-A User-facing Notification Bell: DONE, live-verified.** `services/notifications.py` + `routers/notifications_bell.py` (`GET/POST /notifications*`) + `frontend/.../UserNotificationBell.jsx` wired into TopBar/Dashboard. 5 real call sites emit events: `loop_engine.py` (scan_done), `fix_pipeline.py` (ship_done/ship_failed), `payments.py` (payment_failed), `founder_offer.py` (offer_claimed), `github_app.py` (repo_revoked). Persistent types (payment_failed/ship_failed/repo_revoked) stay flagged until explicitly marked read. 4 backend + 3 frontend named tests, all pass. Live E2E via `testing_agent` (`/app/test_reports/iteration_387_p2a_notification_bell.json`, 100% frontend, 0 action items): seeded 2 real rows via the actual `emit_notification()` prod function (`/app/e2e-proof/P2-A/seed_and_verify.py`), confirmed badge count, panel render, persistent-vs-info styling, mark-all-read, and reload-persistence (real backend round-trip, not client state). 2 screenshots captured. Non-blocking review notes (silent error-catch on mark-read, 30s poll latency for persistent alerts) logged as future polish, not required for this task.
- **P2-F GitHub Webhook Fence alerts: DONE, live-verified.** Registered a new `int_webhook_fence` check in `services/health_checks.py` (calls the REAL `github_app.webhook_fence_status()`, maps ok→green/red/gray) into the EXISTING `services/health_registry.py` + `health_notifier.py` pipeline — zero new alert plumbing. This automatically gets the existing debounce (2 consecutive ticks), cooldown, admin bell row (`health_notifications`), and Resend founder-alert on green→red / red→green transitions "for free". 4 new backend tests pass. Live-curl-verified against the real broken fence: `status=red, detail="missing subscriptions: pull_request · 15/15 recent deliveries failing"` — matches R5's diagnosis exactly. No alert fired on this first tick (correct — pre-existing red at registration is a baseline observation per `_should_fire`'s documented rule, not a transition); it WILL fire the next time this flips in either direction.
- **R5e webhook drill: NOT RUN — pre-flight FAILED, contradicts founder's belief the GitHub-side checklist was done.** Founder said "yes, done" when asked, but a live `GET /admin/github-webhook-fence` check this round shows the App is STILL not subscribed to `pull_request` (`subscribed_events: []`) and all 15 recent deliveries are still failing 401 — byte-identical to R5's original finding. Per R5e-VERIFY-PLAN.md's own explicit rule ("do not retry the drill again until the fence tile itself shows green... running it earlier just reproduces R2's known gap for no new information"), the live GitHub PR drill was correctly NOT re-run. **Founder action needed**: re-check `/app/memory/R5-WEBHOOK-FIX.md`'s steps were actually saved/applied on github.com/settings/apps/aurem-devops (event subscription + webhook secret) — the new `int_webhook_fence` health check (P2-F, above) will now surface this live on the admin cockpit + bell automatically, no need to ask again.
- **P2-B (unified ship-UI/F17), P2-C (canonical status chips), P2-E (PR mini-guide tooltip): BLOCKED on a missing prerequisite, not built this round.** Investigation this round found: (1) the alleged P2-B bug carried over from the prior fork's handoff — a native `window.confirm()` in `LoopLiveFeed.jsx`'s rollback flow — does NOT exist; it was already fixed at Iter 362 (themed `RollbackConfirmModal`, confirmed by reading current source, not the stale comment text). (2) The T7 Wave-2 ship-via-PR flow (branch/PR/label/webhook-dispatch) is backend-plumbing-only — a repo-wide grep of `frontend/src` confirms **zero** frontend references to `ship_via_pr`/the PR flow. No user can trigger it from any UI today; PRD.md's 2026-08-28 claim that a "PR mini-guide tooltip" was "wired as a first-ship-only permanent asset in the T7 build" does not match the actual source (no matching UI text found anywhere) — flagging this discrepancy rather than repeating it. Since P2-C's 7-word canonical status set (`missing → ready → PR open → merged → live → skipped → error`) and P2-E's mini-guide both presuppose a PR-ship UI surface that doesn't exist, and P2-B's own ROADMAP.md entry (F17) has its own explicit trigger — "After Wave 2 (ship-via-PR) is stable in Preview for 2 weeks" (not met; Wave 2 was built the same day) — and flags "real regression risk" merging 3 live surfaces — building any of these now would mean building a brand-new ship-via-PR frontend feature under the banner of a "quick UX fix," not fixing an existing rough edge. Recommend founder explicitly re-scope this as its own dedicated pass (or confirm the 2-week Preview-stability clock has elapsed) before it's attempted.
- **P2-D (jargon sweep): reviewed, no new findings — items already resolved by prior sessions.** MAXX chip already has a `title` tooltip ("Maxx mode ON — Claude reviews DeepSeek output before commit"); `ClearCacheButton` already has icon+label+tooltip; `chat/ToolButton.jsx` icon buttons already pass a `title` prop; "Council A"/"test files" are dev-native terms already inside the explicitly-allowed developer-founder lens (repo/commit/branch/PR-adjacent), not raw jargon. No code changes made — avoided guessing at unconfirmed gaps.

**Next**: Phase 2 partially closed (A + F done+verified; B/C/E blocked pending founder re-scope decision; D reviewed clean; R5e still blocked on the founder's own GitHub App settings, now self-monitoring via the bell). Awaiting founder direction before Phase 3.

---

# PHASE 2 continuation (same day, "CONTINUE ALL ONE BY ONE")

- **P2-C/P2-E narrow fix (ship-status truthfulness), DONE, self-tested.** Re-investigated the P2-B/C/E "blocked" call above and found a real, narrowly-scoped, SAFE slice worth shipping without the risky 3-surface merge: when `ship_via_pr` is ON and a Loop-mode ship lands a commit on a throwaway branch + opens a PR, the app had **zero auto-merge anywhere** — confirmed by grep (no `merge_pr`/auto-merge call exists) — yet the terminal narration/UI unconditionally said "Shipped {sha}" and linked to the commit on the unmerged branch, which reads as live when it is not. Fixed in the ONE existing surface (`LoopLiveFeed.jsx`'s `ShippedRow`/`extractShipInfo`, no new component, no 3-way merge): when `pr_url` is present, label reads "PR opened for {sha}", links to the PR (not the commit), and shows an info icon with the Part-F 3-step mini-guide text ("nothing is live yet... review the diff... merging it there makes it live") — no fake "Approve here" button, since none exists yet. Backend (`loop_engine.py::_do_ship` narration/terminal-emit block) now conditionally emits `pr_url`/`pr_number` and the corrected text; zero behavior change when `pr_url` is absent (the always-on direct-commit path). 6 new frontend tests (`LoopLiveFeed.p2c_pr_ship_status.test.jsx`) + 25 pre-existing ShippedRow/rollback regression tests + 12 pre-existing T7 backend tests all pass. Did NOT touch rollback semantics for the PR-open case (found `loop_rollback.py` has zero PR-awareness — a separate, deeper, real gap, flagged below, not fixed — touching it risked exactly the regression F17 warns about).
- **Notification bell poll speed-up, DONE, self-tested.** Per `testing_agent`'s own P2-A review note, cut `UserNotificationBell.jsx`'s poll interval 30s → 10s (still plain polling, no new SSE/websocket infra — that's a bigger follow-up, not done). 3 pre-existing tests re-verified passing.
- **R5e re-checked, still blocked — founder action not yet reflected.** Live `GET /admin/github-webhook-fence` re-queried this pass: still `ok:false, subscribed_events:[], failing_count:15` — byte-identical to before. No further action possible from this side; the new P2-F alert (above) will fire automatically the moment this changes in either direction.
- **New gap found, NOT fixed, flagged for a future pass (per Standing Rule R2, needs its own F-ID or explicit founder decision)**: `services/loop_rollback.py` has no PR-awareness — for a `ship_via_pr` commit, rollback would attempt to revert `full_sha` on the BASE `branch`, but the commit actually landed on the throwaway `pr_ship_branch`, not the base branch. Likely a silent no-op/harmless-fail today (nothing to revert on `branch`), but not verified either way — needs a dedicated investigation before `ship_via_pr` is ever turned on for real customer traffic, not a same-day guess-fix.

---

# PHASE 2 continuation 2 (same day, "continue then whats left all one by one")

- **Rollback-gap FIXED, self-tested (12 tests, 0 new regressions).** Closed the gap flagged above. Root cause confirmed by reading `loop_safety.py`'s own docstring for `close_and_retract` — the original T7 design intent was always "unmerged PR → close+retract; merged PR → revert-commit", but nothing ever WIRED that branch into the actual rollback endpoint (`routers/loop.py::rollback_loop`) — it always called `run_rollback_bg`'s revert-commit path unconditionally.
  - `services/loop_engine.py`: `self.context["commit"]` now also carries `pr_number` + `pr_branch` (was only `pr_url`) — needed so rollback can identify and close the right PR/branch. `None` for both on the direct-commit path, zero shape change there.
  - `services/loop_safety.py`: new `get_pr_status(owner, repo, pr_number, token)` — live GitHub lookup, fails closed (`merged=False`) on any error/exception so a network blip pushes toward the SAFER close+retract path, never toward silently skipping a needed revert.
  - `routers/loop.py::rollback_loop`: now checks `commit.get("pr_url")` first. If set AND the PR is confirmed unmerged (live check) → calls the existing `close_and_retract` (closes PR + deletes ship branch), returns `rollback_status="done"` with a human-readable `detail`. If merged, OR no PR at all → falls through unchanged to the existing `run_rollback_bg` revert-commit path.
  - 6 new tests (`test_rollback_pr_gap_fix.py`, all named, all pass) + full regression pass on `test_iter367_rollback_fake_success_fix.py` / `test_aurem_rollback.py` / `test_overnight_t7_ship_via_pr.py` / `test_iter212m60_loop_engine.py` (40 tests; 29 passed + 9 skipped by design + exactly the 2 pre-existing baseline failures already listed in `test-baseline.txt` line 325-326 — confirmed via `grep`, NOT new).
  - Did not touch the `ShippedRow` rollback button's label/UI (still says "Rollback" for both cases) — the fix was the backend correctness gap, not a copy pass; left as a small future polish item, not required for correctness.

**Now genuinely nothing left that I can self-action.** Remaining open items all require your own real-world action:
1. GitHub App webhook settings (R5e/R8/R9 chain) — still red on re-check, needs your github.com action.
2. P2-B full ship-UI merge (F17) — needs your explicit re-scope call (its own 2-week-stability trigger isn't met yet).
3. Phase 3 — needs the exact phrase "GO PHASE 3".

---

# PHASE 3 — "GO PHASE 3" given (2026-08-28, same fork). Visibility Kit master spec received + Phase A (dogfood) started.

Founder pasted the full "VISIBILITY KIT — SEO + GEO + AEO" master spec. **MANDATORY ORDER per spec §4: Phase A (dogfood, this AUREM repo) must be DONE+verified before ANY Phase B product code** (new router, migrations, VisibilityKitPanel, etc.) — so Phase B/C/F7 backend/frontend build has correctly NOT started yet.

Phase A status (A1-A7):
- **A1 robots.txt, A2 llms.txt/llms-full.txt, A4 sitemap.xml: ALREADY DONE, pre-existing.** Discovered these were already built in an earlier, unrelated "Iter 212m-68 SEO+GEO+AEO overhaul" session — genuinely comprehensive (more AI-bot coverage than spec's minimum list, curated llms.txt with citation-safe quote blocks, image-rich sitemap). No changes needed.
- **A3 JSON-LD (Organization/WebSite/FAQPage/Person + og tags): 95% pre-existing, 1 gap closed this round.** `index.html` already had all 4 schema blocks (Organization w/ founder+sameAs, WebSite+SearchAction, SoftwareApplication, FAQPage) from the same prior overhaul. Only gap vs spec: the founder `Person` sub-entity had no `sameAs` of its own (spec asks for it). Added `sameAs: [linkedin, x, github]` to it — 1-line addition, JSON validated parses clean (4/4 blocks).
- **A5 PreferredSourceButton: NEW, built + tested.** New `frontend/src/components/PreferredSourceButton.jsx` per spec §6.1 — idempotent Google widget script tag + ALWAYS-visible deeplink fallback (survives AdBlock) + 2 new analytics hooks in `analytics.js` (`trackPreferredSourceClicked`/`DeeplinkFallback`, same silent-fail-safe gtag pattern as existing `trackSignup`). Wired into `LoopLiveFeed.jsx`'s `ShippedRow` as the "moment of delight" (ship only fires after scan passes, so a successful ship = a completed scan) — shows exactly once ever per browser (localStorage-gated, no new backend schema, per spec R9 thin-implementation rule). 6 new tests (4 component + 2 integration) + full 556-test frontend suite re-run clean, 0 regressions.
- **A6 ChatGPT site verification: NOT DONE — manual, external, founder-only.** This is a checkbox-only process on ChatGPT's own site-owner verification page; nothing for an agent to automate. Flagged as your action item.
- **A7 Day-14 measurement: DAY-0 BASELINE CAPTURED, honestly caveated — NOT the literal spec protocol.** `/app/marketing/kit-citations-day14.md`. I do not have direct automated access to run controlled queries against ChatGPT/Perplexity/Gemini's own consumer products (the Emergent LLM key covers raw model text generation, not their web-search-augmented citation UIs specifically) — ran a citation-style web-search proxy instead, clearly labeled as a proxy in the file. Result: ORA/AUREM has zero presence on all 4 competitive/generic locked keywords today (dominated by CiteFlow, SE Ranking, Agent Ready, isready.ai, etc.), cited only on the branded "what is auremcto" query (expected, not a signal). Real day-14 re-run + the actual 3x/engine protocol is flagged as a manual/future task — genuinely can't be rushed (hard 14-day time gate, not a same-day close).

**Gate A is NOT fully closed** (A6 + A7's real protocol are founder/time-gated) — Phase B/C/F7 correctly not started. This mirrors the same honesty discipline applied to P2-B: didn't force a "done" claim past a real gate.

---

# PHASE 3 continuation — parallel tracks directive (2026-08-28, same fork)

Founder answered the 4 action items + gave a NEW parallel directive after a separate live production admin-panel audit (54.3% lifetime task success rate, 5 most recent tasks 4-failed/1-blocked). Full detail below; report format matches founder's own §5 spec.

**Q1 (ChatGPT verify)**: founder will do manually — no action.
**Q2 (day-14 reminder)**: DONE. New `kit_day14_reminder` check in `services/health_checks.py`, registered into the SAME `health_registry`/`health_notifier` cron already ticking (same pattern as P2-F). Gray until 2026-09-11, then red until `marketing/kit-citations-day14.md` gets a "Day-14 results" section (the file doubles as its own "done" signal — no new DB flag). 4 new tests pass; live-curl-confirmed: `status=gray, "13 days left"`.
**Q3 (webhook — precisely which Kit items depend on it)**: **NONE of what's built today.** Verified by reading spec §5 + §8 against my own apply.py: `apply` opens the PR via a direct GitHub API call (same mechanism as T7 ship-via-PR, proven live in R2) — doesn't touch the webhook. `revert` (close+retract) is user-initiated from the app, also direct API, no webhook. The ONLY webhook-shaped piece in the whole spec is the AUTOMATIC "PR merged → pr_merged/live" state transition (§8) — and that piece **isn't built in this v1 slice at all yet**, webhook-blocked or not. When I do build it, it doesn't need to depend on the webhook — I already proved a live-poll substitute works (the same `get_pr_status()` I built today for the rollback-gap fix). **So: the webhook does not block Kit's critical path, and won't need to even once that piece is built.** Don't let it become an excuse to stall Kit work.
**Q4 (try the badge)**: deferred by founder — no action.

## Track A — Kit Phase B backend, v1 slice built + tested

Chose to **proceed on Gate A's existing evidence** (A1-A5 solid, A6/A7 are marketing/measurement gates, not code-correctness gates) rather than wait for Sept 11 — per the founder's own explicit option to time-box this.

Built (per spec §5, reusing this app's own router/service conventions, not the spec's generic path names):
- `migrations/003_visibility_kit.py` — seeds the 7-row `visibility_items` catalog (weights sum to 100), indexes for `visibility_bot_policies`/`visibility_state`/`visibility_applications`. Applied to Preview.
- `services/visibility/{detect,robots,schema,sitemap,apply}.py` — detect.py (framework detection), robots.py (R5 read-modify-write managed block, R8 training-bot choice, retrieval always-allow), schema.py (JSON-LD Organization/WebSite+SearchAction/FAQPage/Person, R6 idempotent merge), sitemap.py (deterministic, 0 LLM tokens), apply.py (orchestrator — REUSES `github_api_writer.commit_files` + `loop_safety.create_or_reuse_branch`/`open_draft_pr`, the exact same primitives T7's live PR drill already proved work against a real repo).
- `routers/visibility.py` — `GET /catalog`, `GET /projects/{pid}/state` (score calc per §3 formula), `PUT /bot-policy`, `POST /apply` (402 billing gate reusing the EXISTING `dev_users.tier` field — free/pro/team, matches §11 pricing exactly, no new entitlement system), `POST /applications/{id}/revert`. Registered in `main.py`.
- 10 named tests (`test_visibility_kit.py`) covering t_detect_frameworks, t_robot_preserve, t_robot_idempotent (+ training-bot default-deny), t_author_schema, t_sitemap_idempotent, t_catalog_seeded, t_branding_present, t_billing_gate, t_apply_no_copy_edit. All pass. Live-curl-verified: catalog returns 7 items, state/apply correctly 404 on projects that aren't yours.

**v1 scope, explicitly**: only the 3 fully-deterministic, zero-LLM AUTO items are wired end-to-end — `ai_crawler_policy` (b), `structured_data` (c), `sitemap_auto` (e). `preferred_sources` (a) and `llms_txt` (d) have catalog rows seeded but **no generator built yet** (`apply` returns them in `not_implemented`, never silently drops them — R2). Advisory items (f, g) are correctly never applied (R3), always returned in `advisory_skipped`. Frontend `VisibilityKitPanel` (§9) and the automatic pr_merged/live state transition (§8, see Q3 above) are **not built yet** — next slice.

## Track B — reliability diagnosis, hit a real evidence wall, reported honestly

**CONFIRMED**: Preview's own MongoDB (`cto_tasks`: 26 docs, `loop_sessions`: 82 docs, `loop_failures`: 19, `fix_jobs`: 214) does **not contain** the 5 specific failing tasks the founder's production admin-panel audit named (`orchestrator.py`, `payments.py` comment tasks, `test_dynamic_30_percent_discount.py`, `test_admin_panel_features.py`). I checked by filename/content across every task-shaped collection in this pod — zero matches.

**CONFIRMED**: every failure record that DOES exist in this Preview pod is test/QA fixture noise, not organic usage:
- 10/17 `cto_tasks` failures: `github_owner` = `auto-<hex>`, `github_repo="demo"`, **no `installation_id` field at all** — auto-generated synthetic test projects, correctly failing `app_installation_missing` (verified the check itself in `pat_vault.py` is a simple, correct `if not installation_id` guard — not a resolution bug).
- 4/17: deliberate drill data on the one real project (`does-not-exist-xyz-branch`, `aurem-rollback-testbed` — a since-deleted test repo, old PAT-auth path).
- 40/42 historical `loop_sessions` "failed" (Jul 27 - Aug 22): stale `pat_invalid_or_expired` messages predating the June PAT-removal migration — not current code's error format, not reproducible today.
- Several `loop_sessions` "failed"/"aborted"/"expired" are **not bugs at all**: "Loop cancelled by user" and "Plan ready — awaiting your approval" (timeout) are expected user behavior, not code defects — inflating any naive failure-rate count.
- 5x "Rollback failed" sessions: all `iter330-harness-*`/`repro-*` named test-harness fixtures from Jul 28-29, unrelated to today's rollback-gap fix.

**UNCERTAIN / genuinely blocked**: I cannot root-cause the founder's actual named failures without either (a) production DB read access, which I don't have from this Preview pod and won't request a bypass for, or (b) the founder exporting/pasting the real error payloads for those exact tasks. Per the standard rule "real evidence, not inference," I'm not going to fabricate a root-cause narrative from Preview noise that provably doesn't match what was reported. **Action needed from founder**: export the error field (or a screenshot of the raw doc) for the 5 named failing tasks, or confirm a way to query production from here.

**What I did NOT do**: apply a "fix" for `app_installation_missing` (Preview evidence says it's correctly-behaving code hitting fixture data without real installations, not a bug) or invent a plausible-sounding but unverified root cause for the real production failures. No before/after measurement is reported because no verified fix was made against real evidence — reporting a number here would violate the "no claim of improvement without a real before/after" rule.

## Time-split (stated as executed, not as originally planned)
Planned ~55% Track B (severity-weighted — a reliability issue affecting real usage outranks a growth feature) / 45% Track A. Actual execution shifted to roughly **75% Track A / 25% Track B** once Track B's evidence-gathering hit the production-data wall within the first pass — continuing to dig in Preview past that point would have been manufacturing false confidence, not diagnosis. The 25% spent on Track B produced a real, checked list of what's NOT the cause (ruled out 3 categories with evidence) and a precise ask back to the founder, which is more useful than a guessed "fix."

- R5 GitHub App webhook fix: DONE (forensics + AUREM-side verify + fence tile + founder checklist). Full detail `/app/memory/R5-WEBHOOK-FIX.md`. Root cause: production's `webhook_secret` almost certainly mismatched/unset (15/15 recent real deliveries failing 401, URL confirmed correct, `pull_request` not subscribed at all). AUREM-side code confirmed fully correct (signature check, uniform-401 guardrail, label dispatch) — nothing to fix there. New live "GitHub Webhook Fence" tile on AdminSystemHealth (`services/github_app.py::webhook_fence_status()` + `GET /admin/github-webhook-fence` + new card), 6 tests (3 backend incl. 1 live, 3 frontend), live-screenshot-verified showing the real broken state. Founder checklist produced (R5d, 4 copy-paste steps, ~10 min) — NOT executed (founder action, next round). R5e revert-check done early: R2's drill marker file was already cleaned up in the same R2 round — `ora-grounding` confirmed still exactly `["main"]` branch, no stray files. No revert needed.
- R7 Switcher shows project NAME: DONE. `ProjectSwitcher.jsx` now renders each project's `name` above the owner/repo line when it differs from the repo name, so same-repo projects are distinguishable. 1 new named test (`t_r7_project_name_distinguishes_same_repo_projects`), full ProjectSwitcher suite 5/5 passing.
- R6 USD cap (per-plan dollar ceiling): DONE. Full detail `/app/memory/R6-USD-CAP.md`. New `services/llm_rate_table.py` (real DashScope rates, cited 2026-08-28) + `services/llm_usd_cap.py` (per-plan + global-kill-switch caps, pre-call enforcement, idempotent backfill) wired into `services/ora_chat_v2/llm_client.py`'s single choke point (before the provider client is ever constructed). New admin API `routers/admin_llm_usd_cap.py` (rate-table + usd-caps + spend lookup + backfill). Live-verified: real backfill against 1,211 real usage rows ($0.0132), idempotent re-run confirmed via curl. 5 named tests passing. E2E done via the real `stream_chat()` function with only the network boundary mocked (a live-UI E2E would require flipping MOCK_LLM, forbidden this round — queued for R8). Regression: 1 non-baseline failure found+diagnosed as pre-existing/unrelated (ora_chat_v2 audit-trail design, not touched by R6); frontend 541/541 clean.
- GATE (R5+R6+R7): R5 done (webhook forensics + AUREM-side verify + fence tile + founder checklist — 1 founder action pending, not executed by me). R6 done (build only, no flip). R7 done. STOPPING here per instruction — awaiting founder review + R5's GitHub-side config action before R5e/R8/R9.

- T1 METER: DONE. Code + 4 named tests + regression (36 pass / 2 pre-existing baseline fails, unrelated) + live admin-surface proof (curl) + denominator bugfix from T4 finding. Organic-fresh-ship proof NEEDS REAL-MODEL RE-TEST (MOCK_LLM=true this pod).
- T2 SEO/Kit report: DONE (read-only, see REPORT-overnight.md §2).
- T3 Future Ledger: DONE (F16/F17+R1-R5) / BLOCKED (F1-F15, seed text absent from disk — DECISION NEEDED).
- T4 Session 2 (J1-J4/K1-K10): DONE. testing_agent iteration_386. J3 hit a repo-connect gap (folded into F16). K8 code-verified. HIGH finding (raw error leak) fixed same session.
- T5 Parts D/E/F: DONE. /app/memory/PART_D_E_F_SYNTHESIS_2026_08_28.md.
- T6 P1 wave: DONE (5/5). P1a per-account lockout (integration_expert consulted first) + live E2E. P1b/P1c/P1d/P1e all done+tested. Per-user PIN (P1a sub-item) = DECISION NEEDED (schema+prod migration).
- T7 Wave 2: BUILD DONE + tested (12/12). Flag live-ON in Preview (proof). Live-drill = CREDENTIALS-PENDING (GitHub App installation unreachable from this pod, see report).
- T8 Final report: DONE. /app/memory/REPORT-overnight.md.

Loop closed clean — no task left IN-PROGRESS. All skips documented in REPORT-overnight.md §4/§7.


## 2026-08-28 (continuation) — Loop N: 5 scoped items, P0 stays OPEN (founder confirming in parallel)

Explicit constraint honored: did NOT re-verify or touch the P0 commit_files/rollback-approve-button fix from the prior loop — that stays open on the founder's own production confirmation.

- **Item 1 (R10 risk memo)**: DONE, analysis only, no code. `/app/memory/R10-ROLLOBACK-PR-GAP.md`. Verdict: rollback-on-PR is NOT SAFE — stale pre-merge SHA never updated to the real `merge_commit_sha`, a network-blip on the live PR check can make rollback silently no-op while reporting "done". `ship_via_pr` stays flag-OFF-in-prod; this memo does not clear it for R9.
- **Item 2 (sign-in diagnosis)**: DONE, diagnosis only. `/app/memory/R11-SIGNIN-DIAGNOSIS.md`. Ruled out stale/different deployed commit (verified via live `/version` on both prod and this pod — same lineage). Code has zero env-sensitive path for this button. Top hypothesis: stale/older service-worker registration in the founder's own browser profile — exact DevTools check provided, not yet founder-confirmed.
- **Item 3 (graph cap)**: DONE + tested. `MAX_FILES` env-overridable (`GRAPH_MAX_FILES`), default raised 200→600. Benchmarked locally (1,627 real files) + analytically for the network-bound half — numbers in CHANGELOG. Test updated (`test_iter165_codebase_graph.py::test_cost_caps_are_locked`), 20/20 graph tests pass.
- **Item 4 (lint engine)**: DONE. `frontend/eslint.config.js` added — root cause was NO config file existing at all (ESLint v9 requires one). Confirmed local `eslint`+plugins install is NOT viable here (breaks `@eslint/config-array`'s minimatch against the existing `resolutions.brace-expansion` pin) — reverted that attempt, `package.json`/`yarn.lock` confirmed clean. `oxlint` remains the real zero-config linter (0 errors).
- **Item 5 (Track B dry-run)**: PREPARED + one real dry-run executed (labeled pending production confirmation, NOT the official number). `/app/scripts/track_b_rerun.py` — single-command rerun. Live result on this pod's `ora-grounding` fixture: 4/5 (80%), 1 failure was an unrelated intentional security-file guardrail, not the original crash class.

**DO NOT** (per explicit founder instruction this loop): rerun R5e, build P2-B/unified ship-UI, start R8/R9/Phase 3, re-verify P0 or touch ship-via-PR flags. None of these were touched.

**New risks surfaced**: the R10 memo's squash/rebase-merge SHA mismatch is a previously-undocumented gap (existing test `test_rollback_merged_pr_falls_through` cannot catch it — it uses the same SHA for pre- and post-merge, so it isn't testing the thing that's actually broken). Flagged in R10, not fixed (analysis-only per this loop's scope).

**Next actions**: founder confirms P0 on production (parallel) → then run the official Track B rerun against the real repo/tasks; founder checks the sign-in DevTools hypothesis; R10 gap needs an actual code fix (3-4 sub-items scoped in the memo) before `ship_via_pr` can be considered for R9.

---

# NEW P0 (2026-08-28) — ship-approve false-success + no-button close-out. DONE, tested, awaiting founder.

Founder live-repro'd a fresh, more severe P0 superseding everything above: no Approve button after a
fix diagnosis, "yes please ship it" pointed at a missing button, and bare "approve" got a fabricated
"Approved! Let me know what you need" with no real commit. Full detail + proof:
`/app/e2e-proof/NEW-P0-2026-08-28/summary.md`.

- **Step 0**: 2 stale background pytest processes from the prior session killed and confirmed dead
  before any regression work.
- **Step 1 — CORE FIX, done + tested**: root cause was NOT a frontend rendering bug (proved via a new
  deterministic mount test — button renders from a valid injected fence regardless of `m.provider`).
  Real defect: the casual-tier intent-gateway LLM call (`casual_direct_reply`) had zero guard against
  claiming a ship/approve action already happened. New `contains_false_success_claim()` /
  `apply_no_false_success_guard()` in `response_confidence.py`, wired into both `chat_send` and
  `chat_stream` at 2 chokepoints each. `is_confirmation_reply()` widened (+ its
  `intent_gateway.py` mirror) to also catch "yes please ship it"-style phrasing. Honest fallback
  (Task 1d): `MessageBubble.jsx`'s "please retype the fix" text-only dead-end replaced with a real
  `ship-cta-fallback-retry-{idx}` button wired to `ChatPanel.jsx`'s new `retryLastFix()`. The
  `test_only_expected_files_mention_tool_router` guardrail some earlier notes suspected was broken
  by this work was checked directly — it's pre-existing (caused by `admin_analytics.py`, already in
  baseline), not touched by this fix.
- **Step 2 — full reconcile, done**: full backend suite (6595 tests) + full frontend suite (567
  tests) run. Every failure not already in `test-baseline.txt` individually confirmed pre-existing
  via `git stash` A/B (including isolating this session's exact 3 changed prod files) and added with
  a documented reason. Zero unexplained new regressions.
- **Step 3 — live drill, ran clean (option ii)**: read-only clean check + a REAL ship-then-rollback
  drill against `polarisbuiltinc-wq/ora-grounding` via the reused GitHub App installation token
  (152797252, same credential path as the earlier R2 T7 drill — no personal token needed), using the
  SAME `POST /cto/tasks/submit` + `POST /cto/tasks/{id}/rollback` endpoints the real Approve button
  calls. Real commit `cf64ac7` landed, real revert `689217d` landed, repo restored byte-identical,
  zero orphan branches throughout. Proof: `/app/e2e-proof/P0-prod-repro/summary.json`. Honest
  separate finding (not this P0): the MOCK_LLM codegen for that drill task deleted 181 README lines
  instead of appending one comment — a real mock-codegen quality gap, flagged, not fixed here.
- **Step 4 — service worker verdict**: `CACHE_VERSION = "aurem-v3"` in current source, IDENTICAL to
  what the founder observed live. Not stale. R11's stale-SW hypothesis dropped/de-ranked.
- **testing_agent verification**: `/app/test_reports/iteration_p0_ship_approve_fix_verify_2026_01_29.json`
  — 0 critical issues; found the "On it—shipping now!" present-tense promise gap live, fixed same
  session, re-verified live via curl afterward.
- **NOT started this round (per "one workstream at a time")**: Step 5 First-Experience Wave, the
  MOCK_LLM chat_stream short-circuit follow-up task, and the Responsive/Layout-scan feature — all
  queued, none touched, per founder's own explicit sequencing.

**STOPPING here, awaiting founder review**, exactly as instructed.

---

# TRUST SURFACES ROUND (S0-S5), 2026-08-29 — S0 report (read-only, L17 reuse-first)

1. **Preview panel** — EXISTS: `frontend/src/components/PreviewPanel.jsx`. Already has 3 `viewMode`s
   ("preview"/"code"/"deploy" — this IS the existing 3-surface toggle the round targets). Live-site
   iframe = `data-testid="preview-iframe-live"`, plain full-width, no device framing today. Already
   has honest loading/timeout(10s)/retry/"open in new tab" states (L13 partially pre-built) — extend
   in place, do not replace.
2. **AddLiveSiteModal** — EXISTS: `frontend/src/components/AddLiveSiteModal.jsx`, rendered from
   `Dashboard.jsx` (not from inside PreviewPanel). Testids: `add-live-site-overlay/-dialog/-input/
   -error/-save/-cancel`. Reuse unchanged; P4 auto-detect sits in front of it.
3. **Live-site URL storage** — EXISTS: `PATCH /cto/projects/{project_id} {preview_url}` (Dashboard.jsx
   line ~495), read everywhere as `activeProject.preview_url`.
4. **Code tab** — EXISTS inside PreviewPanel.jsx: `fetchCodebaseTree()` → `GET /cto/projects/{id}/tree`,
   `fetchFileContent()` → `GET /cto/projects/{id}/file`, rendered as file tabs under `viewMode==="code"`.
   Reuse as the S2 "All files" tab; do not rebuild the browser.
5. **Deploy tab** — EXISTS and is MATURE: `frontend/src/components/DeployPanel.jsx` +
   `backend/routers/deploy.py` — full BYOH SSH deploy (config/deploy/dry_run/rollback), live log
   polling, run history, `aurem_cto_deploy_runs.head_sha` per run. This is a real, working deploy
   engine, not a stub — S3 extends this file, it does not build a parallel one.
6. **Browser service** — PARTIAL: no `services/browser/` directory exists (the earlier "D1 round"
   referenced in Phase-1-item-4 was never actually built — confirmed absent, contradicts the
   assumption it already exists). What DOES exist: `services/browser_self_test.py` — Playwright
   IS an installed dependency, and this file has a working headless-Chromium launch+navigate
   pattern (`run_smoke`), currently used only for AUREM's own red-flag text smoke tests (no
   screenshot capture). Decision: EXTEND this file with a new `capture_screenshot()` function
   reusing its exact Playwright launch pattern — zero new deps (L14), one browser-launch site, not
   two. Its `classify_frontend_change()` is scoped to AUREM's OWN page routes only and is NOT
   reusable for arbitrary connected user repos — S1-P3 needs a small new, separate classifier for
   that (genuinely new logic, not duplicate-drift).
7. **Media/screenshot storage** — PARTIAL: no dedicated media bucket, but `services/db_backup.py`
   already has a working, credentialed Cloudflare R2 (S3-compatible boto3) client factory
   (`_r2_client()`, env: `R2_ENDPOINT`/`R2_ACCESS_KEY_ID`/`R2_SECRET_ACCESS_KEY`) currently used only
   for DB backup blobs under the `mongo/` prefix. Decision: reuse the SAME client/credentials with a
   new `deploy-receipts/` key prefix — no new storage system, no new deps.
8. **Notification bell (P2-A)** — GREEN now: `tests/test_p2a_notification_bell.py` 4/4 pass,
   `UserNotificationBell.p2a.test.jsx` 3/3 pass. The reconciliation-audit failure was already fixed
   in an earlier session (conftest.py env fix). S1.0 needs no further fix, just re-confirmed here.
9. **AdminSystemHealth** — EXISTS: `frontend/src/pages/AdminSystemHealth.jsx`, reusable `Card`
   component (line 43), Webhook Fence tile at `testid="card-webhook-fence"` (~line 713). S4 inserts a
   new `Card` (`testid="card-preview-deploy-monitor"`) right after it, same pattern.
10. **Ship-SHA / rollback tracking** — EXISTS for the deploy surface specifically:
    `aurem_cto_deploy_runs.head_sha` + `_deploy_command()`'s `mode="rollback"` (`git reset --hard
    HEAD~1` + redeploy) in `routers/deploy.py`, already wired to `deploy-rollback-btn` in
    DeployPanel.jsx. S3-D5 reuses this directly — no new revert mechanism.

**Build list (genuinely new, nothing else):** device-frame CSS wrapper + segmented control (S1-P1);
Live-now/After-fix tabs + affected-pages classifier for arbitrary repos + screenshot capture via
item 6's extension (S1-P2/P3); URL auto-detect via existing GitHub file-read endpoints (S1-P4);
"nothing changed yet" line (S1-P5); What-changed/diff summary view + All-files tab reorg (S2);
context pre-fill + last-look confirm + Go-live receipts (post-deploy re-navigate+screenshot+verify)
using item 6/7's extensions (S3); admin monitor Card (S4); meter line + zero-LLM spy test + F25
ledger entry (S5).

---

**2026-08-29 — OVERNIGHT LOOP COMPLETE.** W1 answered, W2 P0-B closed for UI/code
(real root cause: MessageBubble.jsx Gate 6 slash-requirement bug, blocking any
fix confined to a repo-root file), W2 Step 2 mock short-circuit shipped +
zero-spend live-proven, W3 S1-S5 all built + backend-tested, S1/S2/S4
live-verified by testing_agent (100%/100%, 0 bugs), S3 modal code-complete but
not live-clicked (missing AUREM_CTO_MASTER_KEY vault secret in this pod).
Full detail: `/app/memory/REPORT-final-loop.md`. Awaiting founder.

---

# X1 + CROSS-PROJECT DATA-SAFETY ROUND (2026-08-30)

**Trigger:** founder paused Overnight Master Loop 2 (T2-T5) after finding a
NEW P0 in their own regression test — a session-wide mock-mode incident
(X1) plus a cross-project active-project silent-switch incident (W0-W4).
Full detail, all 10 sections, in `/app/memory/REPORT-x1-crossproject.md`.

**X1 root cause (confirmed, evidence-based):** `services/llm/_meta.py`
(the orchestrator/loop-plan/Council gateway) had zero MOCK_LLM awareness
before this round — only `chat.py`'s `chat_stream` was gated. `is_mock()`
also re-read `os.getenv` per-call with no boot-time immutability. This
pod's own `.env` mtime history (from the PRIOR round's own T1/R8 testing)
is a real, on-disk example of the exact global-single-process-flip failure
mode. This pod's DB has zero data for the founder's specific named
incident projects (`TJSNDHU/Aurem`, `RerootsBeauty/ReRoots-`) — the
founder's live incident most likely happened on a separately-deployed
instance this agent cannot directly inspect. NEEDS-FOUNDER logged.

**W1 root cause (confirmed via source read + test, not guessed):**
`ProjectSwitcher.jsx`'s auto-heal effect conflated `"unreachable"`
(explicitly transient, per `repo_status.py`'s own comment) with real
`"disconnected"`, silently switching the active project on a bare network
blip. Fixed: removed the silent switch entirely (H1); real revocation now
only shows a toast, user must click the switcher themselves.

**Shipped this round:** X1 F1/F2/F3 (boot-cached mock flag, mock gate
extended to the whole LLM gateway, new "Live Model Mode" admin tile +
durable `mock_detected_in_live` event trail), loop-ship mock-refuse guard
(belt-and-suspenders), ProjectSwitcher H1 fix, `repo_status.invalidate()`
(B1, partial — loop_engine path only), `R9-PROD-FLIP-CHECKLIST.md` gained
a 5th, NOT-yet-satisfied stop-gate line for H3 (loop repo pinning).

**Explicitly NOT done this round (honest, in report + ledger, not
silently skipped):** H3 (loop repo pin-and-assert-before-write),
`t_loop_repos_pinned`/`t_breadcrumb_matches_active_project` guardrail
tests, B1/refuse-guard extension to `cto_projects.py`'s direct
`/tasks/submit` commit path.

**Regression status:** full backend suite (fewer FAILED/ERROR lines than
the 2026-08-28 baseline: 360 vs 410). A real but test-suite-only
import-order issue (caused by combining the new boot-cached mock flag with
the newly-mock-gated LLM gateway) was found and fixed with one new
autouse fixture in `tests/conftest.py`. All other apparent "new" failures
proven pre-existing via `git stash` A/B. Full method in
`/app/e2e-proof/X1/regression_comparison.md`.

**W4 (battery restart conditions): NOT MET.** H3 not done -> W2 not
all-green -> the 5+5 regression battery must not restart yet, per the
founder's own explicit gate. Context-pinning rule for whenever it does
resume is documented in the report §5.

**Loop 2 (T2-T5) handoff note:** still explicitly PAUSED per the founder's
own instruction ("T2-T5 resume ONLY after X1 + W0 are green AND the
founder's resume signal"). Not silently merged with this round's work —
separate workstream, separate proofs (`/app/e2e-proof/T2/` untouched this
round), separate report file.

**MOCK_LLM state:** `true` in `/app/backend/.env`, confirmed unchanged
before and after this entire round.

**STOPPING here, per founder's explicit "STOP. Awaiting founder."**

---

# PART A — H3 → B1-extend → W0-residue → T2 (2026-08-30, founder follow-up GO)

Founder gave explicit sequencing: H3 → B1-extend → W0-residue(GitHub) →
T2 → T3 → T4 → T5. H3/B1-extend/W0-residue were already executed and
ledgered by a prior turn this same round (see PROOF-LEDGER.md
2026-08-30T04:00-04:20Z); this turn:

- **W0 finalize verify step**: founder required checking whether
  installation `155986962` (RerootsBeauty) maps to a founder-owned
  fixture in this pod's DB. Scanned all 182 collections — 0 hits. Per
  founder's own fallback rule, W0 is now **CLOSED**: real-user repo,
  read-only, no cleanup, root cause fixed via H1+H3, notification to
  the user is the founder's own call (not made here).
- **T2 (rollback-on-merged-PR fix)**: DONE, agent-tested, NOT
  founder-confirmed. Full detail `/app/e2e-proof/T2/T2_SUMMARY.md`.
  Closes 3 of R10's 4 documented gaps (SHA truth via live
  `merge_commit_sha`, no-false-success on an unconfirmed PR-state
  lookup, bounded verify-landed poll before reporting "done" + a new
  `ship_rollback_failed` trust event on timeout). Ship-branch drift
  detection (gap #4) explicitly NOT built — outside this round's
  literal ask, flagged open in R10. Live-drilled against real GitHub
  (`TJSNDHU/Aurem`, since `ora-grounding`'s installation is currently
  unreachable — pre-existing gap): real commit + real revert, both
  bounded-verified, zero orphan branches, repo left clean. 7 new named
  tests + 2 updated pre-existing test files, 17/17 pass. Targeted
  regression (`-k "loop or rollback or ship"`): 667 passed, 22
  pre-existing (baseline+stash-confirmed), 0 new.
- `ship_via_pr` remains Preview-only / prod flag OFF — T2 makes the
  path safer for whenever the founder flips it, does not flip anything
  itself.

**Next**: T3 (First-Experience Wave) → T4 (deployed-build verify,
read-only) → T5 (final Loop-2 report + 5+5 battery). Per founder: do
not stop, report at the end.

## PART A COMPLETE — T2/T3/T4/T5 all done, full report written

Full 10-section report: `/app/memory/REPORT-loop2.md`. Summary:
- T2 (rollback-on-merged-PR fix): 3/4 R10 gaps closed + tested (17/17)
  + live-drilled on real GitHub (TJSNDHU/Aurem). Gap #4 (drift
  detection) explicitly open.
- T3 (First-Experience Wave): B4 real-model window ($0.038668/$3
  spent, 2 new P1 findings) + testing_agent journey (12/12 PASS, 3
  advisory bounce moments).
- T4 (deployed-build verify): production SHA matches this pod's HEAD,
  zero drift. Authenticated-screen check honestly not performed (no
  safe credential).
- T5 (5+5 battery): 10/10 chat+loop calls correctly pinned to the same
  project across a real regression pass. Final report written.
- R9 verdict: **NOT READY TO FLIP** — H3 now satisfied, R1a/T2
  partially satisfied (3/4), but R5e webhook delivery + R8's full
  acceptance numbers remain open from prior rounds (unchanged by this
  round's work).

**STOPPING here per founder's explicit "On T5: STOP + full report."**
PART B (V1 — server-side browser deploy-verify) starts only after
founder review, as its own clean workstream per the founder's explicit
sequencing rule.

## M1 + M2 — bounded real-model window (2026-08-30, founder follow-up "SMALL focused round")

Founder accepted Loop 2, then authorized ONE more bounded real-model
window ($3 cap) to fix M1's 2 P1 model-quality findings + complete
M2's R8 acceptance numbers, BEFORE V1 (which still awaits a separate
"GO V1"). Full report: `/app/e2e-proof/M1-M2/M1_M2_REPORT.md`.

- Window: flip OFF `16:49:41Z` → flip ON `17:00:43Z`. Spend $0.210012/$3.
- **M1a (wrong product description)**: FIXED, verified 100% on retest
  — `PRODUCT_IDENTITY` pinned constant added, real reply now grounded.
- **M1b (context-anchored repeat)**: PARTIALLY fixed — new persona
  rule added; worst symptom (repeating an unrelated stale answer) not
  reproduced on retest, but short-recap compliance not achieved
  (model-instruction-following cap, logged not chased, per founder's
  own "one attempt then move on").
- **M2 fence rate**: 2/5 raw / 67% effective — NEW root cause found on
  the 1 real miss (`output_guard.py` filename redaction collision,
  named not fixed).
- **M2 low-confidence**: not suppressed this retest (16s, real answer)
  — same 60s infra-cutoff gap still fires elsewhere in this window
  (fence prompt #4), so flakiness is real, just didn't hit this exact
  retest. No threshold changed.
- **M2 cost baseline**: $0.0191/msg final number for $9/Pro tiering.
- Mock restored + zero-spend proven. Regression: 0 new (2 persona-
  budget guardrail tests caught an over-budget edit mid-round, fixed
  before finishing — exactly what those tests are for).
- R9: still NOT READY TO FLIP — R5e (founder, in progress), 48h
  warn-window (unreviewed), R1a gap#4 (open) remain.
- NEEDS-FOUNDER: `ora-grounding`'s GitHub App installation needs
  reconnecting (one-liner, not fixed this round).

**STOP. V1 starts only on founder's separate "GO V1", per explicit
instruction.**

## M3 + V1 round (2026-08-30, founder follow-up after M1/M2 acceptance)

Founder accepted M1/M2, gave 2 decisions + a GO: (0) stated the exact
R5e webhook config location (no env var — Mongo-only, Admin UI card;
secret never requested/accepted, standing no-secret-in-chat rule
honored); (1) M3 (output_guard.py context-aware fix, own PR, before
V1); (2) GO V1 (server-side deploy-verify, revised v2 spec).

**M3 DONE** — full detail `/app/e2e-proof/M3/M3_SUMMARY.md`. Fixed the
blanket file-path redaction (now exempts user-named files), fixed a
latent 2nd bug (bare-root-file regex never matched at all), added
missing secret/token redaction. 4/4 new tests, 0 new regressions
(investigated one false-alarm via direct git-checkout A/B — a
`/chat/stream` history-persistence bug, confirmed pre-existing,
flagged not fixed).

**Next: V1 (server-side deploy-verify, revised v2 spec)** — starts now.

## M3 E2E close-out + V1 (a/c/d/e) round (2026-08-30, founder follow-up)

Founder ran a parallel webhook-handling correction (read-only, no
secret — see `/app/e2e-proof/V1/V1_SUMMARY.md` §8 for the Q-NAME/
Q-TARGET/Q-VERIFY answers) alongside confirming M3 E2E fix → V1
completion → regression + report → STOP.

**M3 CLOSED for real this time** — the prior "E2E" check was a false
signal (mock reply had no filename/secret to prove anything either
way). New deterministic combined test
(`test_t_output_guard_m3_e2e_combined_no_llm_no_network`) proves all 3
guarantees together in one constructed reply. M3 suite 5/5.

**V1 CLOSED for this round's scope** — full report
`/app/e2e-proof/V1/V1_SUMMARY.md`. Fixed the 2 real test bugs found
(0-byte fixture PNG causing false breakage failures; a code comment
literally containing "storage_state" tripping its own source-scan
test) — 22/22 V1 tests now pass, up from 8 failed at last handoff.
Added a genuine mid-run DNS re-verify for changed-route navigation
(V1c rule 3, was source-scan-only before). V1b (LLM judgment) left as
a genuine no-op stub per founder's explicit override this round — zero
model calls in mock or real mode. V1d wired the engine additively into
the existing deploy-receipt row, trust events, bell (new PERSISTENT
`verify_failed` type), and the admin monitor's meter line — live-curl-
verified on this pod's real admin account. Explicitly not built:
DeployPanel/AdminSystemHealth frontend visual cards for the new data
(backend-only wiring this round, per the founder's own framing that
V1 isn't a user-facing feature yet). V1e's 6 scenarios all pass against
the local disposable fixture only. F29 (cloud fallback) ledgered,
flag-only. Full new-work suite 29/29; targeted regression sweep found
0 new failures vs `test-baseline.txt`.

R9 verdict unchanged: NOT READY TO FLIP (R5e founder-action pending,
48h warn-window unreviewed, R1a gap#4 open).

**STOP per founder's explicit instruction — no work started after
this report.**

## R1a gap#4 + V1-dashboard round (2026-08-30, 2 small items)

**Item 1 — R1a gap#4 (ship-branch drift detection) CLOSED.** Full
writeup `/app/e2e-proof/drift/DRIFT_SUMMARY.md`. `services/github_api_writer.check_branch_drift`
+ `expected_branch_head_sha` recorded at ship time
(`services/loop_engine.py`) + drift-gated in BOTH rollback paths
(`routers/loop.py::rollback_loop`) — direct-commit revert and
unmerged-PR close+delete. Blocks with `rollback_status="drift_detected"`
until `acknowledge_drift=true`. 6/6 new tests
(`test_drift_detection_2026_08_30.py`) + all 7 pre-existing T2 tests
still green (13/13 together). **Live drill** against real GitHub
(TJSNDHU/Aurem): shipped a commit, simulated a 3rd-party push landing
on the branch, `check_branch_drift` correctly detected it live,
acknowledged, reverted the EXPECTED commit specifically (not the
drifted head), confirmed drift cleared post-revert, cleaned up both
markers — repo left genuinely clean (live-verified). R9 checklist
item 4: PARTIALLY → **FULLY SATISFIED**. R9 remains NOT flip-ready
(webhook + 48h window, founder-owned, unchanged) — no flip performed.

**Item 2 — V1-dashboard (user-facing Verify card) CLOSED.** Full
writeup `/app/e2e-proof/V1-dashboard/V1_DASHBOARD_SUMMARY.md`. New
`GET /deploy/verify-summary` (user+project-scoped, 30d) + new
`VerifyEngineCard.jsx` wired into `DeployPanel.jsx` — one pass-rate
number, one last-fail one-liner + evidence link, one current state,
honest empty state for new users. 5/5 vitest tests pass. Live-curl-
verified against the real endpoint with realistic seeded data (cleaned
up after); rendered-component screenshot proven via the existing
`/dev/visual` hermetic-fixture pattern (same component/styles the real
panel mounts) — full nested chat-workspace navigation to trigger the
live panel mount wasn't reachable in this round's scripted pass,
flagged honestly rather than claimed.

**Regression**: targeted backend sweep (loop/deploy/rollback/drift/
output_guard/notifications/trust_surface/admin_analytics/preview_capture
keywords) — 19 failed, all confirmed pre-existing (17 direct
baseline-file matches + 2 more `test_loop_gate_parity_and_mode_d_2026_01.py`
tests confirmed failing identically on unmodified code via `git stash`
A/B, unrelated live-Mongo-state flakiness in chat_stream gate logging,
untouched by this round's files). Frontend: 27/27 rollback+verify-
adjacent component tests green. **Zero new regressions.**

**No R9 flip. No production changes. `MOCK_LLM` unchanged (true). No
V1b, no cloud fallback, no webhook secret handling this round.**

## Drift alerts + V1 full-page round (2026-08-30, 2 small items)

**Item 1 — Admin Drift Alerts CLOSED.** New `GET /admin/drift-alerts`
(same `_require_admin` pattern as every other admin tile, 0 new deps)
+ a new "Drift-Blocked Rollbacks" tile on `AdminSystemHealth`, next to
the Webhook Fence tile. READ-ONLY — admin sees, only the loop owner
acknowledges. 4 backend tests + 3 frontend tests, all pass. **Live
E2E**: seeded 1 real `ship_rollback_drift_detected` trust event,
logged in live as admin, screenshotted the real `/admin/system-health`
page — tile shows "1", expand reveals the exact loop_id/branch/
expected/current/timestamp row. Cleaned up after.
`/app/e2e-proof/drift-alert/DRIFT_ALERT_SUMMARY.md`.

**Item 2 — V1 full-page screenshots CLOSED.** The verify engine now
takes a `full_page=True` shot (Playwright built-in, no new dep) on the
SAME already-loaded page, alongside the existing mobile/desktop
viewport shots — no re-navigation. Persisted as its own receipt key
in the wiring layer. `run_judgment` (V1b, still pending) hardened with
a real `TypeError` guard against ever receiving raw image bytes. 3 new
engine tests + 1 new wiring test, all pass — including a genuine live
visual proof (a tall test page showing the viewport shot crops
below-fold content while the full-page shot captures it, saved to
`/app/e2e-proof/V1-fullpage/`). Found and fixed a stale leftover
`http.server` process from earlier manual debugging that was masking
the new navigation-counting test — documented, not a code bug.

**Regression**: targeted sweep (deploy/output_guard/notifications/
trust_surface/admin_analytics/preview_capture/drift keywords) — same
3 failed + 1 error as the prior round, all pre-existing baseline
items, zero new. Frontend 21/21 green across the new + existing
rollback/verify/webhook-fence component tests.

**R9 re-readiness**: dev-side is now FULLY SATISFIED (drift detection
+ visibility both closed). Remaining: (1) webhook secret — founder's
own production action, (2) 48h warn-window review — founder. R9 flips
when founder completes both + main agent's GO. **No R9 flip performed
this round.**

## R9 unblock round (2026-08-30) — 2 analysis items + 1 channel confirm

**Read-only. No code changed** (confirmed via `git status` — the only
diff is the pre-existing, unrelated `frontend/.env` pod artifact).

**P1 — warn-window, corrected.** Prior round read the wrong source
(production Gate Parity ≠ the actual write-guard warn log). Correct
source: `guard_config`/`guardrail_events` + real ship-write volume
through `github_api_writer.commit_files`. Organic 48h writes = **1**
(below the 5-write bar). Per explicit instruction, filled the window
with **4 more real, clean drill writes** (TJSNDHU/Aurem, cleaned up
after) → **5 total, 0 warn events**, guard independently confirmed
live via a positive control. **Verdict: CLEAN.**
`/app/e2e-proof/R9-unblock/warn-window/WARN_WINDOW_SUMMARY.md`.

**P2 — Fabrication Learning Loop.** Infra confirmed:
`ora_fix_learning.py` write (`record_fabrication_incident`, wired in
`chat.py`) + read (`get_recurring_fabrication_patterns`, admin
endpoint) + recall (`recall_fabrication_caution`, ALREADY wired into
`orchestrator.py`'s live prompt for `customer_chat`+`chat_stream`).
Live data: 31 signatures/30d, 1 recurring — but that one
(`definitely_fake_invoice_engine.py`, 96×) is confirmed a self-seeding
pytest fixture (`user_id: test-customer-1`), not organic traffic.
Same failure CLASS as the M2 fence-miss; no pin proposed because the
generic self-correction mechanism already exists and is already
firing for this exact scope. One real gap found instead: the
`admin_ora_chat`/`general` route has NO caution recall wired at all —
flagged for a future round, not fixed this round.
`/app/e2e-proof/R9-unblock/FABRICATION_LOOP_ANALYSIS.md`.

**P3 — Webhook channel.** Confirmed: no env var exists; the channel
is production's own Admin UI form (`POST /admin/github-app-config`),
a direct browser-to-prod submission that never needs to cross this
chat. Confirmation via Recent Deliveries/Webhook Fence tile (any event
type) + one real PR on `TJSNDHU/Aurem` to close the `pull_request`-
specific R5e gate. `/app/e2e-proof/R9-unblock/P3_WEBHOOK_CHANNEL.md`.

**R9 re-readiness (updated)**: dev-side analysis complete for both
remaining gates. Still remaining: (a) founder actually sets the prod
webhook secret via the channel confirmed in P3 and it shows 200, (b)
founder reviews/accepts P1's CLEAN verdict. **R9 flips when both are
done + founder GO — not performed this round.**

---

## R9 STATUS — CARRY-FORWARD (2026-08-30, this round)

Founder ran the R9 pre-flight checks directly on production (correct
paths turned out to be `/api/aurem-dev/admin/*`, not the ones in this
checklist doc — checklist needs a path-correction follow-up) and
pasted back real JSON:
- **Webhook fence: CLEAN.** `subscribed_events: [pull_request,
  workflow_job]`, `missing_subscriptions: []`, a real `pull_request`
  delivery today, 200/success. R5e now genuinely closed on
  PRODUCTION itself (previously only founder-read-GitHub-UI).
- **Flag state: `ship_via_pr` already `enabled: true, rollout_pct:
  100`** on production — founder flipped this earlier in the same
  session, not this agent; no code/agent action taken to flip it.
- **Loop metrics (7d): failed_ratio 33.3% -> 54.5%, +21.2pp** since
  the flip. Founder's own caveat: some of this window's failures may
  be their own live regression-testing noise (an Item-9 ship failure,
  an intent-confusion bug), not necessarily organic. Not yet
  separated by user_id — real signal vs noise still UNKNOWN.
- **Real ship attempt (founder's own live test): FAILED.** No file
  edits generated; self-heal retry also failed. No PR number / merge
  SHA exists. Per the founder's OWN stated acceptance bar ("flag-on +
  model-real is R8, not R9 LIVE... a production-safe ship is the
  proof"), **this is NOT R9 LIVE.**

**Decision (founder, explicit, this round): CARRY-FORWARD — do not
re-attempt right now.** Flag stays as-is (already ON on production,
untouched by this agent); move on to 2 new prod bugs (Issue A/B,
below) this round; R9 proof-of-ship resumes in a future round.

**R9 CARRY-FORWARD ITEM (top of next round's list):** still need ONE
clean successful ship on production — PR opened -> merged -> Live
chip, PR number + merge SHA captured — before "R9 LIVE" can be said.
Also investigate the +21.2pp failure-rate regression (filter by
user_id to separate today's manual-test noise from real signal)
before dismissing it as noise-only.

**No production credentials were used, requested, or stored by this
agent this round.** All production reads/writes were performed by
the founder directly; this agent only reviewed pasted-back JSON.

---

## ISSUE A + ISSUE B — production chat-UI bugs (2026-08-30, this round, started)

Founder-reported, live production screenshot evidence
(`RerootsBeauty/ReRoots-`). Two separate root causes, two separate
fixes/PRs, read-only investigate first. NOT R9, NOT the post-R9
batch, NOT the chromium/build_hash V1 issue (separately queued).

- Issue A: "Fixed N file(s) — committed as {sha}" banner re-appears
  on refresh AND re-login on the same project (should show once,
  auto-vanish, never re-show). Must determine exact persistence path
  (localStorage vs server-side shipped-task-state vs SSE-rehydrate)
  vs the earlier, already-fixed sibling bug before patching.
- Issue B: ORA drops the in-progress "find issues" intent on a
  one-word follow-up ("i didnt find any ?"), asking "can you clarify
  what you're looking for?" instead of answering in-thread. Need to
  determine if this is context-window truncation or a missing
  anchor-instruction gap, and which model/context length the Pro
  chat path resolves to.

Investigation starting now.

## ISSUE A + ISSUE B — CLOSED, testing_agent verified (2026-08-30)

**Issue A**: root cause CONFIRMED category (b) — `GET /onboarding/first-scan/status`
re-surfaced `commit_sha` every call once present (Phase A read-back design), zero
acknowledge mechanism → perpetual banner. Different component/path than the
already-fixed sibling (`MessageBubble.jsx` `shipped_task_id` gate,
`test_iter89_ship_button_no_reappear.py`) — not a regression of that fix.
Fix: new `POST /onboarding/first-scan/acknowledge-fix` (idempotent,
ownership-checked) + `fix_acknowledged` on `/status`. `FirstScanCard.jsx`:
"Got it" button + 7s auto-vanish, both call the ack endpoint; once acked,
component renders null. Resets per-project on switch (no false suppression
of a different project's own fresh banner).

**Issue B**: root cause CONFIRMED (source read, not guessed) — both
`chat.py` call sites hardcode `history=[]` into `intent_gateway.classify()`,
and `casual_direct_reply()` called the LLM with ONLY the current message,
zero history, by construction. Not a model/context-length cap — literally
no history ever reached the model. Fix: new
`response_confidence.prior_turn_context_text()` (same cheap `$slice:-1`
query as the existing `prior_turn_had_fix_signal`) threaded into
`casual_direct_reply(prompt, prior_assistant_text=...)`, appending an
explicit in-thread-answer anchor instruction only when a prior turn exists
— zero change for genuine fresh chit-chat.

**Tests**: 13 new (`test_first_scan_ack_2026_08_30.py`,
`test_issue_b_context_anchor_2026_08_30.py`) + 116 regression
(intent-gateway/casual-boundary/onboarding/workcard suites), all pass, 0
new failures. `testing_agent` live-verified both end-to-end (real browser
clicks + reload + relogin-equivalent for A, real 2-turn LLM chat sequence
for B) — 0 bugs, 0 action items
(`/app/test_reports/iteration_issue_ab_first_scan_ack_and_context_anchor_2026_08_30.json`).

Note: test fixture `p_0fdafaa365`'s `fix_acknowledged_at` was left `True`
after testing — harmless, reset script in the test report if needed.

**Next up (per founder's earlier explicit sequencing)**: R9 proof-of-ship
(carry-forward item above) whenever founder resumes it; post-R9 batch
(infra root-cause → contrast CI guard → A7 reminder → bulk-revoke last)
remains queued.

## ISSUE C — "ORA doesn't remember" — CLOSED, testing_agent verified (2026-08-30)

Root cause CONFIRMED (source read): (1) `chat_with_tools` capped session
history at a fixed `[-20:]` regardless of the actual token budget left by
persona+tools+state that turn. (2) `casual_direct_reply` (the path most
bare recall questions get routed into, same resource-noun classifier gap
as Issue B) carried ZERO memory before this round. (3) `/chat/send` never
fetched `project_brain` context at all (single-surface drift vs
`/chat/stream`). (4) No rolling summary existed for content beyond the
history window.

Fix (ONE combined change, per founder's "not 4 PRs" instruction):
`_select_history_window()` (dynamic, token-budgeted, replaces fixed cap)
+ new `services/session_summary.py` (fire-and-forget, every 10 turns,
`chat_sessions.summary` field, always included in transcript) + MEMORY
anchor instruction in `base_system` and `casual_direct_reply` + `/chat/send`
brain_ctx drift fixed.

Tests: 11 new (`test_issue_c_memory_2026_08_30.py`, 10 unit + 1 live E2E)
+ 167-test regression sweep, 0 new failures (2 pre-existing unrelated
failures confirmed identical on baseline via `git stash`; one line-lock
test re-snapshotted). `testing_agent` live-verified with a REAL 12-turn
LLM conversation: multi-turn recall (4 bugs + names + dates), recall
crossing the 10-turn summary threshold (pinpoint "Marco/webhook/PROMO50"
recall), session isolation (fresh session leaks nothing), session
switch-back (recalls the right session), Issue A/B regression checks —
6/6 live scenarios PASS, 0 bugs, 0 action items
(`/app/test_reports/iteration_issue_c_memory_multiturn_2026_08_30.json`).

## VISIBILITY KIT — PHASE 1 BUILT, testing_agent verified (2026-08-30)

Backend for this already existed (20+ rounds old: `services/visibility/*`,
`routers/visibility.py`, `migrations/003_visibility_kit.py`, `loop_safety.py`'s
`visibility_kit_pr_events` webhook plumbing) — never had a frontend. This
round: built the missing VISUAL PANEL + the 2 previously-not-implemented
generators (`preferred_sources` badge, `llms_txt`) + an R9-gate + admin tile.

**Founder's 4 resolutions this round**: (1) R9 NOT live — built full Kit,
Apply gated behind new `kit_apply_enabled` flag, DEFAULT OFF, seeded in
`init_prod_collections.py`; only a real ship + the failure-rate dig can
un-gate it, not the flag being on. (2) Preferred Sources badge —
web-searched Google's real current docs (confirmed 2026-08-30):
`<script async src="https://news.google.com/swg/js/v1/publisher.js">`,
`<div google-add-preferred-source-btn data-theme="light" data-lang="en">`,
deeplink fallback `google.com/preferences/source?q={domain}` always
rendered beside it (no-silent-fail). (3) Scope — Phase 1 (build+test) now;
Phase 2 (real live PR->merge->revert on `ora-grounding`) explicitly
DEFERRED, awaiting founder go-ahead. (4) Admin citation-data section is an
honest placeholder ("No citation data yet — day-14 recheck pending (~Sept
11)") — zero fake numbers, A7 wiring NOT built this round.

**Built**: `VisibilityKitPanel.jsx` (readiness donut, 7 rows, canonical
5-chip set, Apply CTA + per-row Apply fully R9-gated both client- and
server-side, badge code preview, advisory "View report" modal), new "Kit"
tab in `TopBar.jsx`, `AdminVisibilityKit.jsx` tile (`/admin/visibility-kit`).
Backend: `services/visibility/preferred_sources.py` + `llms_txt.py` (both
deterministic, 0 LLM tokens), wired into `apply.py`'s `IMPLEMENTED_AUTO_ITEMS`
(now all 5 auto items done, 0 remaining `NOT_YET_IMPLEMENTED`). R9-gate
added to `POST .../apply` (403 before the billing-tier gate) and exposed
via `apply_enabled`/`apply_disabled_reason` on `GET .../state`. New
`GET /admin/visibility-kit/dashboard` admin endpoint.

**Failure-rate dig (parallel, per founder's ask)**: discovered
`/admin/loop-metrics` ALREADY has an owner-classification breakdown
(founder/admin/test/user/orphan) + a documented priority rule ("P0 live
regression if `delta_failed_ratio > +0.05` OR `failed_owner_counts.user
>= 3`; otherwise fixture-shape/dogfood signal") — no new endpoint needed.
Founder's earlier paste only had the aggregate numbers (33.3%→54.5%,
+21.2pp), not the `failed_owner_counts`/`failed_sample` breakdown needed
to apply that rule. **Action needed**: re-run `GET /admin/loop-metrics` on
production and paste the FULL response (esp. `failed_owner_counts`) —
verdict (real regression vs. testing noise) will be given from that.

**Tests**: 21 new (`test_visibility_kit_v2_2026_08_30.py`) + 1 existing
test updated for the new gate ordering, 37 total pass, 0 regressions.
`testing_agent` verified live: state/apply endpoints correctly R9-gated,
panel renders with real data, Apply provably inert everywhere (no way to
trigger a real PR from the UI), advisory report modal works, badge
preview shows the real SDK. Found + fixed 1 bug: `visibility_kit` was
missing from `Admin.jsx`'s icon map, crashing `/admin/visibility-kit` —
fixed (`Sparkles` icon added). 0 remaining issues
(`/app/test_reports/iteration_visibility_kit_panel_2026_01_31.json`).

**Phase 2 (live 13-step E2E on `ora-grounding`) is NOT started** — awaiting
founder review of this Phase 1 report + the failure-rate dig re-run.


## KIT TRUTH-UPDATE + Phase 2 attempt (2026-08-30, this round)

**KIT TRUTH-UPDATED.** Copy patch, no schema/reweight change:
- `llms_txt` row: weight stays 15. Before → After (both catalog file
  AND the live seeded `visibility_items` DB row — see HONEST-COPY
  CHECK below): "A curated map of your site that AI assistants
  fetch. Claude & Perplexity confirm they use it; ~4% of major sites
  ship one — your competitors haven't. No downside if you're wrong."
  → "A map of your site that helps Claude, ChatGPT and coding agents
  find you. Google ignores it for Search and AI Overviews. Cheap to
  add, low-risk, useful for the assistants that do read it."
- `preferred_sources` row: weight stays 25 (still the top-weighted
  hero item). Before → After: "Let your visitors make your site a
  'preferred source' on Google. For them, your links then get a
  'preferred' badge in AI Mode & AI Overviews, and you appear more in
  Top Stories. Google reports these links are clicked 2x more. ~2
  minutes to install." → "Your visitors can mark you a 'preferred
  source' on Google — a PER-VISITOR choice, not a global ranking
  signal. Once they do, they see a 'preferred' badge on your links in
  AI Mode, AI Overviews and Top Stories. Google reports preferred
  links get about 2x the clicks (May 2026). ~2 minutes to install."
- `VisibilityKitPanel.jsx` — 2 new lines, both previously absent:
  score note under the donut ("This is a preparedness checklist, not
  live citation tracking.", `data-testid="kit-score-note"`) and a
  positioning line under the header ("Others measure your AI
  visibility. AUREM fixes it — and we ship the fix.",
  `data-testid="kit-positioning-line"`).
- Escape key now closes the panel (and its report/confirm sub-modals
  first, if open) — small addition, no new component.

**HONEST-COPY CHECK result: the OLD copy WAS what was actually shipped.**
The migration *file* (`migrations/003_visibility_kit.py`) already had
the honest wording from an earlier edit this fork, but `GET
/visibility/catalog` and `/state` read from the SEEDED
`visibility_items` Mongo collection, not the file directly — and that
collection still had the stale copy above (confirmed by a direct read
before touching anything). Fixed by running the existing, idempotent
`VisibilityKitMigration.up()` against this pod's real DB (via the
same Motor/`.env` init pattern `main.py` itself uses — not the two
earlier broken standalone-script attempts). Re-read both rows after:
DB now matches the honest file copy exactly (pasted above). No
schema change, upsert-by-key only, safe to re-run.

**Named tests, all 4 green** (`tests/test_visibility_kit_v2_2026_08_30.py`):
`test_t_kit_llms_row_honest`, `test_t_kit_pref_sources_is_hero`,
`test_t_kit_score_not_overclaimed`, `test_t_kit_no_oversell_number`.
Full file + `test_visibility_kit.py`: 28/28 pass, 0 regressions.

**Customer-facing numbers audit**: exactly one remains in the catalog
copy — "Google reports preferred links get about 2x the clicks (May
2026)" — dated + attributed to Google, not a flat AUREM claim. The
old unsourced "~4% of major sites ship one" stat is gone. No other
digit-bearing customer claim exists in the 7-row catalog.

**PHASE 2 — NOT RUN, hard live blocker found (confirmed, not guessed).**
Checked connectivity via the ACTUAL running backend (not a standalone
script, so the boot-hydrated GitHub App JWT cache is real) —
`GET /cto/projects/connection-status` as test@aurem.dev returns **0 of
46** projects `connected` right now, including every `ora-grounding`
row (`polarisbuiltinc-wq/ora-grounding`, installation `152797252`) —
all show `status: "disconnected", error: "github_rejected"`. This is
wider than the previously-documented "ora-grounding specifically
unreachable" gap (which is why earlier rounds substituted
`TJSNDHU/Aurem`) — right now NOTHING is connectable from this Preview
pod, `TJSNDHU/Aurem` included per the same call. Root cause is at the
GitHub-App-credential/installation level (the App's own JWT or a
mass-revoke on GitHub's side), not this pod's code — `admin_settings.
github_app_config` has app_id/private_key/webhook_secret all present,
so it's not a "never configured" gap either. **Did not attempt the
13-step drill against a repo I cannot reach — that would only
produce fabricated/fake artifacts.** Kit engine's file-generation
code (badge/robots/llms.txt/sitemap/JSON-LD) already has non-live
unit-test coverage (28/28 above); only the real GitHub PR/merge/
revert leg needs a reachable installation, which is currently 0-for-46.

**Action needed from founder**: re-verify the AUREM DevOps GitHub App
install (github.com/settings/apps/aurem-devops/installations, or
production's own Admin → GitHub App card) — confirm at least one
installation among the 46 preview project rows is actually still
authorized, or reconnect one. Phase 2 resumes the moment any repo
shows `connected: true` on this pod.

**Live Score Preview (Q4) — answered, not built.** Read `GET
/state`'s scoring code (`routers/visibility.py::get_state`): an item
only earns weight when its `visibility_state.status` is
`pr_created`/`pr_merged`/`live` — there is no "detected-but-unshipped"
credit anywhere in the formula. A fresh, never-applied project's every
item is `status="missing"` → score is always exactly **0** before any
Apply. That is answer **(b) APPLIED-STATE** ("what's been shipped"),
not (a) diagnostic-readiness. Rationale for NOT building "show score
instantly after scan": it would show 0 for every single new user by
construction, which is not informative and would look broken — the
already-existing per-row "Missing"/"Needs you" chips (spec §6) are the
honest pre-apply UX, no new surface needed. Low priority, did not
block anything else this round.

**Apply button**: unchanged, still `kit_apply_enabled: enabled=false`
(re-confirmed via direct DB read, no flag touched this round).
Un-gate criteria unchanged: Phase 2 green (blocked above) AND the
failure-rate verdict (still awaiting founder's full `/admin/
loop-metrics` `failed_owner_counts` paste — not received this round,
no verdict given).

**Regression**: no-new-vs-baseline — only files touched this round
were `VisibilityKitPanel.jsx` (2 copy lines + 1 keydown effect) and
the DB content push (idempotent upsert, no code path changed). Ran
`test_visibility_kit_v2_2026_08_30.py` + `test_visibility_kit.py`
(28/28 pass) and confirmed frontend hot-reload compiled clean (no
new console/supervisor errors). No live E2E run, per founder's
explicit instruction for the truth-update patch.

**STOP**, per founder's instruction, pending: (1) founder's
loop-metrics paste for the failure-rate verdict, (2) founder
reconnecting/re-verifying a GitHub App installation before Phase 2 can
be attempted for real.
