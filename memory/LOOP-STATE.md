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
