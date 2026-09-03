# OVERNIGHT REPORT — T1→T8 (2026-08-28)

## 0. TL;DR
**7 done-with-proof** (T1, T2, T3-partial, T4, T5, T6, T7-build) /
**3 pending-your-GO** (SHIP_VIA_PR prod flip, per-user PIN, F1-F15 seed
re-forward) / **2 skipped-needs-you** noted below (T7-live-drill
credentials, Day-1 onboarding design). Nothing left IN-PROGRESS — the
loop ran to completion. No data-safety trip occurred (IRON RULE 3
never fired).

## 1. DONE + PROOF

| Task | What | Tests green | Regression | E2E proof |
|---|---|---|---|---|
| T1 METER | 4 deterministic fields on every ship/task record (`services/ship_meter.py`), wired into both engines, admin `/admin/loop-metrics` line | `test_overnight_t1_ship_meter.py` 4/4 | 36 pass / 2 pre-existing baseline fails (unrelated `pat_vault`) | `/app/e2e-proof/T1/pytest_t1.log` + live curl (admin line renders, denominator-fixed) |
| T2 SEO/Kit report | 4-row visibility report | n/a (read-only) | n/a | see §2 below |
| T3 Ledger | `ROADMAP.md` §FUTURE LEDGER, R1-R5 + F16/F17 | n/a (file) | n/a | `memory/ROADMAP.md` |
| T4 Session 2 | J1-J4 + K1-K10 re-verify | testing_agent | n/a | `/app/test_reports/iteration_386_session2_pass2_t4.json` |
| T5 Parts D/E/F | jargon + ranked issues + canon | n/a (doc) | n/a | `memory/PART_D_E_F_SYNTHESIS_2026_08_28.md` |
| T6-P1a | per-account `/ora` lockout | `test_ora_chat_pin_login.py` 8/8 | included above | `/app/e2e-proof/T6/p1a_pin_lockout_live.log` (real curl, 5 IPs→429) |
| T6-P1b | "Run in background" → "Close (task keeps running)" | `ShipConfirmModal.p1b_honest_label.test.jsx` 3/3 | — | test file above (source-level, label change is trivially visual) |
| T6-P1c | FixProgressDrawer close icon tooltip | already-shipped pre-run + this run's polish | — | source diff |
| T6-P1d | raw-error humanization (`api.js`) | covered by existing frontend suite (no regression) | — | fixed the exact case testing_agent live-caught in T4 |
| T6-P1e | native confirm sweep (Projects, Integrations) | `P1e_native_confirm_sweep.test.jsx` 6/6 | — | screenshot `/app/e2e-proof/T6/integrations_page.png` (smoke) |
| T7-build | ship-via-PR branch/PR/label/webhook/revert plumbing | `test_overnight_t7_ship_via_pr.py` 12/12 | included in T1 regression batch | flag ON proof via `/admin/feature-flags` curl |

## 2. T2 — SEO/Kit admin visibility (read-only report)

| Surface | Where | Gap |
|---|---|---|
| LLM cost/usage | Settings → Models & LLM → `/admin/llm/configs` | Exists, live |
| Guardrail events | `/admin/guardrails` (GET+POST) | Exists, live |
| Kit citations | **No file, no admin surface at all** — Phase A (dogfood) was never started, still blocked on founder's master spec (confirmed via `ROADMAP.md`) | Not "file-only" as assumed — doesn't exist yet |
| Kit per-project status | No backend model, no admin surface | Confirmed absent (grepped `visibility_kit`/`VisibilityKit`, zero hits) |

"Admin Kit & SEO Dashboard" stays parked (F7) — not built.

## 3. FLAG STATE

| Flag | Preview value (this pod) | Prod value | Who flips prod |
|---|---|---|---|
| `ship_via_pr` (Mongo `feature_flags` collection) | **enabled: true** (set this run via `/admin/feature-flags`) | No row = OFF by default. No env var exists — a prod flip means creating this same flag row in the prod Mongo. | Founder only (A1) |
| `MOCK_LLM` | `true` (backend/.env, this pod) | Unknown/founder-managed | Founder only (A2) — untouched this run |
| `TRACK_SWITCHER_ENABLED` | `false` (unchanged) | unchanged | n/a, no change this run |

## 4. DECISION NEEDED

- **[A8-adjacent] F1-F15 ledger seed missing.** Searched `ROADMAP.md`,
  `PRD.md`, `FUTURE_BUILDS_LEDGER.md` (unrelated freeform format) — no
  F1-F15 6-field entries exist anywhere on disk. **Need**: re-forward
  the original F1-F15 list so it can be seeded verbatim. Did instead:
  seeded F16/F17 + the R1-R5 rules (fully specified in your own
  instruction), logged this gap plainly in `ROADMAP.md` itself.
- **[T6-P1a per-user PIN]** Per-account lockout is built and live-
  proved. A true per-user PIN needs a new schema field + a migration
  path for existing installs. **Need**: your GO to add that schema
  change (not done on auto-pilot per your own instruction).
- **[T7 GitHub App installation, E7]** The pre-seeded fixture project
  (`funnel-repro` → `polarisbuiltinc-wq/ora-grounding`, installation
  `152797252`) cannot mint a real GitHub token from this pod —
  `services.pat_vault.get_repo_token_or_error` returns
  `app_installation_missing` even though the Mongo row says
  `active: true`. **Need**: either re-install the AUREM GitHub App on
  that repo from this Preview pod, or confirm which fixture repo is
  meant to be genuinely live here. This is what blocked the T7-live-
  drill (see §7) — it is a credentials/environment gap, not a build
  gap.
- **[T4 fixture ratify]** J3 found the account's *active* project on
  this pod (`aurem-demo/frontend`) has a revoked App install, and the
  intended fixture (`funnel-repro`) wasn't auto-selected — there's no
  one-click project switcher yet. Used `funnel-repro` directly via API
  once identified. **Need**: confirm this is the intended fixture going
  forward, and whether a real project-switcher is worth prioritizing
  (feeds F16).
- **[Day-1 onboarding, F16]** Fresh signups hit an external GitHub
  OAuth popup before seeing any product value. Two options captured:
  (1) a lightweight "browse a sample repo" preview before connect, or
  (2) a manual repo-URL/PAT fallback alongside the App flow. **Need**:
  your design call — not built this run (F16, parked).

## 5. PROD-FLIP-PENDING

- `ship_via_pr` — built, unit/guardrail-tested (12/12), live-proved ON
  in Preview via `/admin/feature-flags`. **Needs your GO** to flip in
  prod (A1). The **live PR-open drill** itself is blocked by the
  credentials gap in §4 — the code path is proven via mocked-GitHub
  unit tests, not yet via a real merged PR on this pod.
- No other P1 items are Preview-only-pending — P1a-P1e all shipped
  fully (per-user PIN aside, which needs your GO per §4).

## 6. LEDGER

`memory/ROADMAP.md` §FUTURE LEDGER now has:
- R1-R5 (standing rules) — printed at top.
- F1-F15 — **BLOCKED, not seeded** (see §4).
- F16 (Day-1 onboarding) — seeded this run, informed by the real J3
  finding.
- F17 (3-ship-surface consolidation) — seeded this run, per your spec.

No item was built off this ledger (L8 honored).

## 7. KNOWN OPEN / NEEDS REAL-MODEL RE-TEST

- **N1** (assistant self-identifies as ORA, never AUREM) — guardrail
  tests green; actual model wording unverified (`MOCK_LLM=true`).
- **K2, K3, K4, K5, K6, K7, K9** — all require observing the real
  model's phrasing; not guessable, not tested this run.
- **K1 real-fence-pass render** — the fallback path is code+test
  proven; the "happy path, fence parses, real button renders" case
  needs a real model response to organically trigger.
- **T7 live PR drill** — CREDENTIALS-PENDING, see §4/E7. The build
  itself is fully tested against mocked GitHub responses.
- **T1 organic-ship meter proof** — the admin-line/denominator fix is
  live-proved via curl; the 8 rows currently counted are this
  session's own test-fixture writes (all-zero), not an organic AI-
  generated ship. A genuine ship needs either a real model (MOCK_LLM
  off, A2) or the GitHub credentials fix in §4 to drive a mock-content-
  but-real-commit ship. Neither was done on auto-pilot, per the prod
  fence and IRON RULE 3 spirit around fixture-repo caution.

## 8. NO-SILENT-FAIL AUDIT

Every skip/block in this run appears in §4 or §7:
F1-F15 seed (§4) · per-user PIN (§4) · GitHub App installation (§4) ·
J3 fixture ratify (§4) · Day-1 onboarding design (§4) · N1/K2-K7/K9
real-model items (§7) · K1 happy-path live render (§7) · T7 live drill
(§7) · T1 organic-ship proof (§7). Nothing else was skipped this run.

---

## Regression statement

No new failures vs `backend/test-baseline.txt` (404 pre-existing
entries, untouched) or `lint-baseline.txt` (37 backend + 1 frontend,
untouched). All new/changed-file targeted test runs this session: 24
backend (T1+T6+T7) + 28 frontend (T4-verify+T6) = 52 passing, 0 newly
introduced failures. The only 2 failures seen in any run this session
(`test_iter367_rollback_fake_success_fix.py`) are listed verbatim in
`test-baseline.txt` lines 314-315 and are unrelated to any file touched
tonight.

## OVERNIGHT-LOG.md / LOOP-STATE.md

Both closed with a final timestamp (`2026-08-28T09:00Z`). Totals:
**done: 12** (T1, T2, T3-partial, T4, T5, T6×5, T7-build, T7-flag-proof)
· **blocked: 3** (T3 F1-F15, T6 per-user-PIN, T7-live-drill) ·
**skipped: 0** (nothing was skipped outright — every blocked item has
a real partial delivered).


---

# OVERNIGHT REPORT — R0 → R6 (this fork, new round)

Preview only. No production changes, no secrets requested/stored, no
new dependencies, no Docker changes, no PAT auth re-enabled.

## 1. DONE-WITH-PROOF

**R0.1 — baseline sweep.** `admin_analytics.py::test_graph_status` and
`test_only_expected_files_mention_tool_router` were already individually
documented in `backend/test-baseline.txt` (lines 164, 368) — no new
entry needed. Confirmed via `grep`.

**R0.2 — 3 P0 re-verify (ship-button, delete-confirm, 0:00 disable).**
Code-confirmed present and wired:
- `ShipPendingCard.jsx` — "Ship to GitHub" approve button, `disabled={busy || expired}`.
- `DeleteChatConfirmModal.jsx` — imported + rendered in `SessionSwitcher.jsx`,
  its confirm calls the real `deleteSession(...)`.
- `PlanApprovalCard.jsx` + `ShipPendingCard.jsx` — both disable their
  action buttons once `secondsLeft <= 0` (`expired` flag).
Live click-through deferred to the batched `testing_agent` call
(founder's Q5 explicitly disallowed self-testing these).

**R0.3 — future ledger.** `ROADMAP.md` FUTURE LEDGER confirmed intact:
F1-F18, F20, F21, F24, F25, F29 present (+F30/F31 added this round, see §6).

**R2 — ship-via-PR, build side.** No code changes needed — build was
already complete from a prior round. Re-ran the full existing suite to
confirm no drift: `test_overnight_t7_ship_via_pr.py` +
`test_rollback_pr_gap_fix.py` + `test_drift_detection_2026_08_30.py` =
**27/27 pass**. Preview `ship_via_pr` flag confirmed `enabled:true,
rollout_pct:100` (unchanged).

**P1-1 — `/ora` per-user hashed PIN + per-user lockout.** `routers/ora_chat.py`:
`pin-login` now accepts an optional `identifier` (email) that resolves
one SPECIFIC admin/founder account; that account's own bcrypt
`ora_pin_hash` (set via new `POST /ora-chat/pin/set`, self-service,
requires an already-logged-in admin session — checked via
`GET /ora-chat/pin/status`) is checked, and lockout is now tracked by
the RESOLVED account's real `user_id` — a second team member's wrong
guesses can never lock out the founder's own account, and vice versa.
No `identifier` = exact legacy shared-`ORA_QUICK_PIN` behavior
(zero regression for existing muscle memory).
- Files: `backend/routers/ora_chat.py`, `frontend/src/pages/OraDirect.jsx`
  (PinPad gained an optional "Have your own personal PIN?" field),
  `frontend/src/pages/AdminSettingsPage.jsx` (new `PersonalOraPinCard`).
- Tests: 2 new **live** tests (`test_p1_1_ora_per_user_pin_2026.py`, real
  HTTP + real Mongo, no mocks) — both pass: (1) set PIN → login with
  identifier succeeds, (2) 5 wrong attempts against one identifier don't
  affect a different identifier's bucket, and the 6th attempt — even
  with the CORRECT PIN — is still blocked (429), proving genuine
  per-user lockout, not just per-IP. Pre-existing 12/12 pin-login tests
  unaffected.
- `integration_expert` consulted before writing any auth code, per rule.

**P1-2 — user-facing notification bell.** Investigation found the bell
is **already user-facing**, not admin-only as the brief assumed —
`UserNotificationBell.jsx` is wired into `Dashboard.jsx` for every
logged-in user (confirmed via source read + the 2026-08-28 P2-A ledger
entry). `scan_done`/`ship_done`/`ship_failed` already have real
emitters. Closed the one real gap: added `upgrade_eligible`, emitted
once per user per month from `scan_fix_quota.py::assert_can_fix`'s 402
path (new `notif_dedupe` collection — no bell spam on repeated
retries). 1 new unit test passes. `kit_live` already existed as a type
but has **no emitter anywhere** — see §3/§6, not fabricated a fake
trigger for it.

**P1-3 — raw-error sanitization.** New `frontend/src/lib/sanitizeError.js`
— shared `sanitizeErrorMessage(e, fallback)`. Blocks Python tracebacks,
JS stack frames, `SomethingError:`/`SomethingException` patterns, file
paths under `/app/(backend|frontend)/`, Mongo URIs, API-key-shaped
strings; falls back to `e.message` only if that also passes the filter.
Applied to the explicitly-named surfaces: `MessageBubble.jsx` (rollback
+ ship-cta-fallback toasts), `ChatPanel.jsx` (6 ship/pause/cancel/
loop-confirm/plan-restart toasts), `LoopLiveFeed.jsx` (rollback POST
failure), `ShipConfirmModal.jsx` (ship + rollback). 11 call sites
total. Remaining ~60 files with the same raw pattern in lower-
visibility surfaces are **not** swept this round — logged as F31, not
silently skipped.

**P1-4 — approve dead-end, re-verified CLOSED (no new code).**
`services/actions/pending_action.py` (Commit-Boundary architecture,
2026-09-05) is the current, deterministic confirm/cancel state machine
— CBR-1..8 invariants, honest `NO_PENDING_ACTIONABLE_MESSAGE` only when
genuinely nothing is pending. Re-ran `test_commit_boundary_2026_09_05.py`
+ `test_live_commit_boundary_e2e_2026_01.py` = **23/23 pass**.

**P1-5 — unify 3 ship UIs, safe slice done (full merge deferred, §3).**
New `frontend/src/lib/shipStatus.js` — one canonical status
label/color map. Applied it and fixed the one real terminology
mismatch found: `ShipConfirmModal.jsx`'s header said "Reverted" while
`LoopLiveFeed.jsx` said "Rolled back" for the identical underlying
state — both now say "Rolled back". Frontend test suites for both
files re-run clean.

**Regression sweep.** Backend: targeted pytest across every file
touched this round — all green except 5 pre-existing baseline
failures in `test_iter212m190_scan_fix_quota.py` (visibility-kit
tier-promo, test-baseline.txt lines 194-198, unrelated). Frontend:
targeted vitest — 1 real regression found + fixed (sanitizer's
missing `e.message` fallback broke a pre-existing mocked-`Error`
test; restored, still filtered); remaining 8 failures confirmed
pre-existing via `git stash` A/B, unrelated to any file touched.

## 2. NEEDS-FOUNDER

**#1 — Preview's GitHub App credential is being rejected by GitHub
itself (blocks R2's live PR drill AND R5's webhook diagnosis — same
root cause, more severe than assumed).**

Live diagnostic this round (`GET /admin/github-app-diagnostics`, real
admin JWT, this pod): `configured: true`, `installations: []`. The
App-level JWT (signed locally with THIS pod's stored private key for
app_id `4541725` / slug `aurem-devops`) is rejected with **401 by
GitHub on the most basic self-lookup call** (`GET /app`) — not a
per-repo/per-install issue, the whole preview credential fails before
installation resolution. `GET /admin/github-webhook-fence` fails for
the identical reason. This is NOT the "zero subscribed events / secret
mismatch" scenario from the brief — that finding belongs to a much
earlier PRODUCTION diagnosis, already fixed per `LOOP-STATE.md`'s
2026-08-30 "R9 STATUS — CARRY-FORWARD" entry (production's webhook
fence shows CLEAN, `pull_request`+`workflow_job` subscribed). This
preview pod's own private key is simply stale/wrong — consistent with
each preview fork getting an ephemeral local Mongo.

**What I need from you** (cannot click GitHub's settings myself):
1. `github.com/settings/apps/aurem-devops` → **Private keys** →
   confirm the current key, or click **Generate a private key** for a
   fresh `.pem`.
2. In AUREM (this preview pod): **Admin → Settings → GitHub App
   Config** → paste the full `.pem` contents into Private Key, confirm
   App ID `4541725` / slug `aurem-devops`, Save.
3. While there, under **Webhook**, confirm delivery URL
   `https://auremcto.com/api/aurem-dev/github/app/webhook` and that
   **Pull requests** is a checked subscribed event.
4. Tell me once saved — I'll re-run diagnostics and the full
   open→merge→cancel→delete drill immediately.

Until then: the drill's flag-gated BUILD parts are fully tested
(27/27) and the flag is correctly ON in preview — only the
live-GitHub-API portion is blocked.

**#2 — `ship_via_pr` production flag state discrepancy (not touched,
flagging only).** Brief assumes prod is OFF; `LOOP-STATE.md`'s
2026-08-30 "R9 STATUS — CARRY-FORWARD" entry records the founder had
already set it `enabled:true, rollout_pct:100` on production directly.
No production DB access from this pod to confirm current value —
worth a quick check on your end.

## 3. BLOCKED / IN-PROGRESS

- R2 live PR drill + R5 webhook-fired "Live" status: blocked on
  NEEDS-FOUNDER #1.
- F17 full 3-ship-UI component merge: only the canonical status-label
  slice shipped (§1); full physical merge deferred (same regression-
  risk reasoning the ledger originally gated it on).
- `kit_live` notification: type exists, no emitter anywhere (Kit's
  merge→live auto-transition never built) — new F30.
- P1-3 full raw-error sweep: 11 named call sites done; ~60 other files
  remain — new F31.

## 4. FLAG/STATE

| Flag | Preview | Production |
|---|---|---|
| `ship_via_pr` | `enabled:true, rollout_pct:100` (confirmed) | Unknown to this agent — see NEEDS-FOUNDER #2 |
| `ORA_ALLOWED_IPS` | unset (fail-open, unchanged) | founder-owned |
| `ORA_QUICK_PIN` (legacy shared) | unchanged, valid as no-identifier fallback | founder-owned |
| Per-user `ora_pin_hash` | new this round, opt-in, schema-only until set | N/A, backward compatible |

## 5. REGRESSION

Backend targeted suites: green except 5 pre-existing baseline
failures (unrelated). Frontend targeted suites: 1 real regression
found+fixed same round; 8 remaining confirmed pre-existing via `git
stash` A/B. No full unscoped suite run this round — targeted sweep
only, flagged rather than claiming an unrun full-suite-green.

## 6. NEXT

1. Founder actions above (#1, #2) — unblocks R2's live drill.
2. `testing_agent` E2E pass for R0.2 + all 6 P1s (queued next).
3. F17 full merge, F30 (`kit_live` emitter, needs Kit Phase B/C), F31
   (full error-sanitization sweep) — logged, none started.
4. Once #1 resolved: the real-model round to prove an actual R9-LIVE
   ship (PR opened → merged → Live chip, real PR number + merge SHA).
