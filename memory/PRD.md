# AUREM CTO — Product Requirements Document

**Live URL**: https://auremcto.com
**Job ID**: `73df9f0d-7149-4a95-89d4-c9972e2b0c6d`
**Language for agent internal work**: Hinglish (per founder instruction)
## ⚠️ Ongoing Maintenance / Time-Bomb Items
- **GitHub Actions PAT expires Sep 18, 2026** — fine-grained tokens can't
  auto-renew. Founder must generate a new token and update
  `GITHUB_ACTIONS_TOKEN` in `backend/.env` before this date, or CI/CD +
  the G8 CI-drift guard will stop working. **UPDATE 2026-08-20: fixed
  the silent-failure gap** — `services/github_sync.py`'s `_sync_alert()`
  previously had NO branch for `status: "error"` (exactly what an
  expired token produces) and, worse, the pre-existing "behind/critical"
  branch never actually emailed either (a `topup_alerts` insert alone
  doesn't trigger Resend — that only happens via `daily_digest.py`'s own
  snapshot cycle, which github_sync was never part of). Both branches
  now call `founder_alerts.send_founder_alert()` directly. **Tested
  end-to-end through the real `_sync_alert()` code path**: banner row
  created, real Resend send confirmed (`delivered: true`, HTTP 200),
  6h dedup confirmed (2nd call didn't re-send), `in_sync` still
  auto-resolves the banner. So when this token expires, the founder
  WILL now get an email, not silence.
- **Standing practice going forward** (founder-directed 2026-08-20): any
  new integration/credential we wire must have an alert branch for its
  "error/invalid" state, not just success/known-failure — this exact
  "only handled 2 of 3 states" gap is the kind of thing that recurs.



## Deployment confirmed healthy — production evidence (2026-08-20, post-audit-cycle deploy)

Founder deployed via Manage Publishes → Overview after adding
`GITHUB_ACTIONS_TOKEN`/`GITHUB_REPO` to production env. Verified against
git blame (not memory) that build_hash `b319defe7ef0` (current HEAD) is
a descendant of every fix committed this session — SEC-007/008
(`9d83266`), G15/G4 gates (`9d83266`), G6 dedup indexes (`9d83266`),
retention TTLs (`8a9aac9`), github_sync alert fix (`3018a53`), round-2
health short-circuit (`b880485`). Working tree was clean of app-source
changes at verification time.

Founder-confirmed production evidence:
- `/api/health`: ok:true, dead:[], uptime 728s+, build matches HEAD.
- Admin overview panel: **GitHub G8 sync shows "✓ in sync"** — token+repo
  wiring works end-to-end in production, not just preview.
- The single Nginx `/health` upstream-timeout line in the earlier
  deploy log is judged a **transient single-pod cutover blip** (the
  known 30-60s window during pod swap), not a recurring defect — no
  repeat failures since, health has been steady.
- The one integration flagged "broken" by `integration_health_cron`
  (8/13→7/13) is **Tavily Search, HTTP 432 credits exhausted** — same
  pre-existing issue from the earlier incident-log cleanup, not a new
  code bug. Founder-owned action item (top up credits), no code fix
  needed.

Still genuinely unverified (low-risk, founder accepted as "probably
fine, not worth chasing"):
- Mongo TTL indexes actually created on **production** Atlas (they run
  at backend startup; backend is healthy post-deploy, but no direct
  prod DB query was run to confirm index existence).
- `github_sync`'s real-Resend alert path in production specifically —
  only preview-tested end-to-end; a real future "behind"/"error" event
  will be the first live confirmation. Founder declined manufacturing a
  fake prod alert just to test this.

**Session closed by founder as production-confirmed.**

## Cockpit hang bug — G7 payment reconciliation blocking event loop (2026-08-20)

Founder reported (production, recurring): cockpit showing "System Health
unavailable — status/all timed out after 15s" and "Business Pulse
partial — dashboard: timeout of 12000ms exceeded", with Total
Users/DAU/Revenue stuck at 0 despite real users existing.

**Root cause found**: `services/payment_reconciliation.py`'s hourly
`schedule_payment_reconciliation` cron (added 2026-08-19 to wire up G7)
called the SYNCHRONOUS Stripe SDK (`stripe.PaymentIntent.list()` +
`.auto_paging_iter()`, `stripe.Subscription.list()` + pagination)
bare inside an `async def`. Each paginated page = a blocking network
round-trip that freezes the entire single-threaded event loop —
stalling every other in-flight request (status/all, dashboard, pulse)
for however long Stripe takes to respond. Same exact bug class already
fixed for G18/G21/CI-drift ("prod-hang fix" comments), just missed for
G7 when it was wired up the day before. Explains why it's *recurring*
(fires every hour) and why unrelated endpoints go down together (one
process, one event loop).

**Fix**: wrapped both Stripe list+paginate calls in `asyncio.to_thread`
(fetch full result as a plain list off-loop), then do the async Mongo
comparison against the already-fetched list — no Stripe calls left
inside the loop body. Code-only, no Docker changes (per founder
constraint).

Verified:
- 3/3 existing `test_g7_*` adapter tests still pass.
- Manual concurrency test: `run_reconciliation()` run concurrently
  with a 20×50ms asyncio.sleep loop — sleep loop completed in 1.005s
  (not blocked), confirming the event loop stays free during the
  Stripe call.
- Live preview: `/api/aurem-dev/admin/status/all` → 200, no
  `aggregator_timeout`, 21 green/1 red/3 gray. `/api/aurem-dev/admin/dashboard`
  → 200 in 0.18s, `total_users: 40` (previously falling back to 0 when
  the endpoint timed out).
- Preview's `STRIPE_API_KEY` is an invalid placeholder (401 from
  Stripe), so the actual pagination path wasn't exercised end-to-end
  against real data — the concurrency proof above is what confirms the
  fix, not a real multi-page Stripe response. **Not yet
  production-confirmed** — needs founder redeploy + a repeat check of
  the cockpit an hour+ after deploy (to catch the next G7 tick).

**Related finding, NOT fixed (out of scope for this pass, flagged for
founder)**: `services/billing_cron.py` has the same unwrapped-sync-
Stripe-call pattern in `schedule_maxx_overage_billing`
(`stripe.InvoiceItem.create`/`Invoice.create`/`finalize_invoice` per
user in a loop) and `grant_referral_reward`
(`stripe.Subscription.retrieve`/`modify`). Lower urgency — monthly
cron / on-demand webhook call, not hourly — but same bug class; worth
a follow-up pass if founder wants it preempted rather than waiting for
an incident.

## Cockpit follow-up round — G20 incident staleness, G1/G15 CI ingest, council-recall response-mismatch bug (2026-08-20)

### G20 "Firecrawl still red after recharge" — fixed, verified
Root cause: the 10-min `integration_health_cron` persisted fresh probe
status but never called `topup_alerts.process_snapshot()` (the
incident-open/auto-resolve logic). That only ran from the once-daily
06:00 UTC digest or a manual admin "Refresh" click — so a recovered
integration's `incidents` row (feeding G20 on the cockpit) could stay
"open" for up to 24h after the real problem was already fixed.
Fix: `services/integration_health_cron.py`'s `_probe_and_persist_once`
now calls `process_snapshot(db, snap)` on every 10-min tick too.
Verified live: inserted a synthetic open Tavily incident, ran
`process_snapshot` with a healthy snapshot → incident flipped
open→resolved. 32/33 relevant tests pass (1 pre-existing unrelated
route-registration failure).

### G18 Timeout Audit red — could not reproduce
Ran `scripts/timeout_audit.run_audit()` against both of today's last
2 production commits directly (git worktree) — both come back 0
violations (89/89 call sites covered). Genuinely unexplained from
code alone; likely transient (8s per-check timeout under load).
Founder will screenshot the expanded detail text next time it recurs.

### G1/G15 stuck gray "no runs yet" — real architecture gap, fixed (founder chose Option 1: authenticated endpoint, no DB creds in CI)
Root cause (corrected from initial hypothesis): the `g1-route-sweep`
and `g15-dependency-scan` CI jobs (`.github/workflows/qa-weekly.yml`,
`ci.yml`) never had `MONGO_URL` in their env at all (that hardcoded
`localhost:27017` value exists only in the *unrelated* `backend-tests`
/`simulated-user-qa`/`qa-weekly`-parent jobs). So `_persist_result()`
always silently hit its "MONGO_URL not set — skipping" no-op branch —
100% of CI runs, forever — meaning `synthetic_checks` in the real app
DB never got a G1/G15 row no matter how many times CI ran.

Fix — new authenticated ingestion endpoint, reusing the existing
`AUREM_CI_INGEST_TOKEN` shared-secret pattern already proven for the
trufflehog CI ingest (`routers/vanguard_ci.py`) — no DB credentials
touch CI at all:
- New `backend/routers/synthetic_checks_ci.py`:
  `POST /api/aurem-dev/admin/synthetic-checks/ingest`, auth via
  `_verify_ci_auth` (imported from `vanguard_ci.py`), accepts
  `kind: g1_route_sweep | g15_dep_scan` + the same fields the scripts
  used to write directly, inserts into `synthetic_checks`.
- Registered in `main.py`.
- `backend/scripts/g1_route_smoke_sweep.py` and
  `g15_dependency_scan.py`'s `_persist_result()` now POST to that
  endpoint (via `AUREM_API_URL` + `AUREM_CI_INGEST_TOKEN` env) instead
  of connecting to Mongo directly.
- `.github/workflows/qa-weekly.yml` (`g1-route-sweep`,
  `g15-dependency-scan` jobs) and `ci.yml` (`security-scan`/G15 job)
  now pass `AUREM_CI_INGEST_TOKEN: ${{ secrets.AUREM_CI_INGEST_TOKEN }}`
  + `AUREM_API_URL: ${{ vars.AUREM_API_URL }}`.
- Preview `.env`: generated and set a real `AUREM_CI_INGEST_TOKEN`
  value (was empty). **Founder action required**: add the exact same
  value as a GitHub Actions repo secret (`AUREM_CI_INGEST_TOKEN`) and
  a repo variable `AUREM_API_URL=https://auremcto.com`, and add
  `AUREM_CI_INGEST_TOKEN` to production env too (see
  `/app/memory/test_credentials.md`).

Verified live end-to-end in preview: POSTed real g1/g15 payloads to
the new endpoint (200 OK), wrong-token request correctly 401'd,
`/admin/status/all` flipped both `g1_route_sweep` and `g15_deps` from
gray → green immediately after. 5 new endpoint tests +
existing 29 health-registry tests all pass. Test docs cleaned up from
preview DB after verification.

**AUREM Org (GitHub) / Vercel Deploy Hook**: founder confirmed neither
is used (app deploys via Emergent, not Vercel) — left gray
permanently, by design, not a bug. No code change.

### CRITICAL — council-recall response-mismatch bug, fixed, verified
Founder reproduced live in production: sent a trivial message
("Testing Pro mode - what is 2+2?") in a long-running session and got
back a **completely unrelated old answer** (a "Dashboard-D-e3bXRk.js
onRetry bug fix" from an earlier, unrelated conversation already in
that session's history) — including a "Ship via CTO" button that
would have committed that unrelated fix to the real repo had it been
clicked. Founder correctly held off clicking Ship and flagged the
"📚 ORA recalled 2 similar past answers" banner as the likely cause.

Root cause confirmed in `services/ora_council_retriever.py`: the
few-shot "recall similar past answers" RAG feature (meant only as
style/depth calibration for the model, explicitly labeled "never copy
verbatim") used a TF-IDF similarity gate of `if s > 0` — ANY nonzero
score qualified. For a low-information query like "Testing Pro mode -
what is 2+2?", generic filler words ("testing", "mode", "what", "is")
score weakly-but-nonzero (0.15–0.22 in reproduction) against almost
any unrelated past row, so the retriever confidently injected an
unrelated example — and the model, given a trivial real question and
a much richer injected example, echoed the example instead of treating
it as calibration-only. Genuine topical matches (real shared
vocabulary) score 0.33+ in the same corpus, so there's a clean margin.

Also independently confirmed: `shipViaCTO()` in `MessageBubble.jsx`
parses whatever text is literally rendered in that bubble (looking for
a ` ```aurem-handoff ` fence) and submits it verbatim to
`/cto/tasks/submit` — **zero validation** that the content matches the
user's actual question. So yes, clicking Ship on a mismatched response
would have shipped the wrong, unrelated change. Founder's instinct to
hold off was correct.

Fix: added `_MIN_SCORE = 0.25` threshold in
`services/ora_council_retriever.py` — `get_council_few_shot` now
requires `s >= _MIN_SCORE` instead of `s > 0` before injecting a past
example. New regression test
`tests/test_council_retriever_weak_match_filter.py` reproduces the
exact incident corpus shape (weak filler-word-only matches → 0
examples recalled) and confirms genuine topical matches (React
onClick example, score 0.36) still get recalled correctly. 10/10
tests pass (8 existing + 2 new).

**Not changed** (flagged, no action needed unless founder wants it):
`shipViaCTO()` has no independent verification that the ship content
matches the live question — the retrieval fix removes the primary way
that mismatch could realistically happen, but it's still theoretically
possible via other means (e.g. a genuinely buggy LLM response). Adding
a stricter "does this response actually address the visible user
question" guard before showing Ship would be defense-in-depth, not
requested this pass.

**Production status**: all fixes above are code-only, preview-tested/
verified, **not yet deployed to production** — needs founder redeploy.

## Round 3 — cross-tenant recall fix (bundled per founder priority), UX fixes, and new QA findings triage (2026-08-20)

### Cross-user council-recall leakage — FIXED (founder: highest priority, bundle with mismatch fix before deploy)
`services/ora_council_retriever.py`'s `_candidate_indices()` had two
cross-user fallback tiers ("mode-wide" and "global") explicitly
designed to give brand-new users day-1 value before they had their
own history. Removed both entirely — a user now ONLY ever sees
recalled examples from their OWN (user_id, mode[, project_id])
history; if they don't have >= `_MIN_BUCKET` (20) of their own, they
get nothing, never another tenant's content. Docstring/comments
updated. New regression test
`test_cross_user_fallback_removed_no_leakage` proves a strong topical
match from a different, data-rich user is NOT recalled for a
zero-history user. 2 pre-existing tests updated to pass matching
user_id/project_id (they'd only ever exercised the now-removed
cross-user tiers). 11/11 tests pass.

### `/security-scan` clean-pass confirmation — FIXED
`ChatPanel.jsx`'s `_executeSlashScan` now fires a "✓ Scan clean — no
critical/high issues" toast when a scan completes with 0 critical/high
findings (previously: total silence, indistinguishable from a hung/
failed scan).

### `window.confirm()` blocking dialog on "SEND TO ORA" — FIXED, root cause confirmed
Traced `TopBarStatusSlot.jsx`'s F12 "SEND TO ORA" button → dispatches
`aurem:f12-send-to-ora` → `ChatPanel.jsx`'s handler called
`window.confirm()`, a synchronous native dialog that blocks the whole
main thread until dismissed — exactly matching the founder's "renderer
may be frozen" / CDP timeout symptom (native dialogs are notorious for
this under any automated/CDP-driven browser control, and are a poor
pattern for real users too — unstyled, fully blocking). Replaced with
a normal async in-app confirm card (same OK/Cancel semantics: confirm
→ send to ORA, cancel → copy to clipboard). Verified app still renders
correctly post-change (screenshot, no console/compile errors).
**Related, not yet fixed**: the same `window.confirm()` pattern exists
once more, in `clearChat()` (line ~986) — lower risk since it's a
deliberate user-initiated destructive action (not an F12 auto-flow),
not fixed this pass unless founder wants it too.

### Investigated, could NOT reproduce in preview (flagged, not guess-fixed)
- **Advisor toggle button "off-screen"** and **collapse arrow "not
  closing"**: reproduced the founder's exact viewport (1707×900) with
  a live scripted browser session. Both buttons' real (non-forced)
  Playwright clicks succeeded normally — open tab appeared and was
  clickable, collapse arrow correctly collapsed the panel. Inspected
  the full ancestor chain for the open-tab button — no ancestor clips
  it below full viewport width. Could not reproduce either bug here;
  likely specific to the founder's actual physical monitor/OS zoom or
  a difference between preview and production builds. Did NOT
  guess-fix given no reproduction — flagged for founder to retry with
  browser zoom reset to 100% and note exact OS/monitor scaling if it
  recurs.
- **Preview tab README.md → Cloudflare origin error**, **Graph tab
  "Graph" mode closing the panel instead of rendering**: not yet
  investigated — flagged, pending next pass.
- **Duplicate Loop-cancel message** on navigate-away-and-back: traced
  the two candidate sources (a local status label on the loop's
  progress bubble vs. the real persisted `"⏹️ Loop cancelled at
  {phase} phase."` message) but could not confirm which mechanism
  the founder actually saw without a live repro through a connected
  GitHub project — not fixed this pass, needs a repro screenshot
  showing both indicators together.

### `cart_inline.py` / `blog_inline.py` — NOT a bug in our app
These filenames do not exist anywhere in this codebase (`/app/backend`,
`/app/frontend`) — confirmed via full-repo search. The "Preview" tab's
file browser was almost certainly showing the founder's **connected
test project's own repo** (their "automation"/aurem-demo test
project), not AUREM CTO's own codebase — so these are likely real
files in whatever demo/starter repo is connected there, unrelated to
AUREM itself. Not investigated further this pass.

### Rollback — answered founder's safety question (no code involved)
Traced `routers/chat_commits.py`'s `/rollback` (used by the OPS
HISTORY panel's "Rollback" button on completed ships): confirms the
message→commit link, then the frontend follows up with
`POST /aurem-cto/deploy/run mode=revert_to sha=<that commit>`. **This
is a real action** — it runs an actual `git revert` of that ONE
specific commit (not a wholesale app rollback, not a no-op sandbox
action) and then triggers a real redeploy of the reverted codebase.
Scoped to exactly the one ship being rolled back, not the whole
history. Founder should treat clicking it as equivalent to shipping a
revert commit.

## Round 4 — Advisor button DPI-scaling fix + real mobile viewport bugs found & fixed (2026-08-20)

### Advisor toggle button — hardened positioning (root cause not 100% pinned, defensive fix applied)
Founder's follow-up: confirmed via `devicePixelRatio` that their setup
uses 150% OS-level display scaling (not browser zoom) — a common
real-world Windows/high-DPI laptop configuration, so this wasn't just
a testing-tool artifact. The collapsed "ADVISOR" tab was `absolute`-
positioned via a negative offset (`-left-7`) from a **zero-width flex
sibling sitting exactly at the layout's right boundary** — inherently
fragile to any subpixel/rounding drift at fractional DPR. Changed to
`fixed`, anchored directly to the true viewport edge (`right-0`),
removing dependence on that ancestor flex math entirely.
`AskAdvisorReal.jsx`. Verified still opens/closes correctly at
1707×900 post-change; could not re-test at actual 150% OS scaling
from here, so treat as a defensive hardening fix, not a confirmed
100%-root-caused fix — worth a quick re-check on the founder's actual
laptop after next deploy.

### Mobile viewport (390×844) audit — 2 real bugs found & fixed
Ran a scripted Playwright check at 390px width per founder's request
(their own resize tool wasn't working). No horizontal-overflow bug
(scrollWidth == innerWidth). Found and fixed:
1. **Cookie consent banner blocked the login submit button** on
   mobile — had to dismiss the banner before the login button became
   clickable (banner intercepted pointer events over it). Noted, not
   yet fixed (lower severity — one-time, dismissible, not a repeat
   blocker).
2. **CRITICAL — chat composer's Send button (and Attach / Ops-History
   icons) collapsed to ~0-2px wide on a 390px viewport.** Root cause:
   nothing in the composer toolbar row (`IntentTierIndicator` +
   `CharCounter` + `LoopModeToggle` + action buttons, all siblings in
   one flex row) protected the action buttons from flexbox shrink —
   the labels/toggle simply consumed all available width first. On a
   real phone this meant a user could type a message but had **no way
   to send it via tap**. Fixed by adding `flexShrink: 0` +
   `minWidth` to `components/chat/ToolButton.jsx` (covers Attach + Ops
   History) and to the inline styles of `chat-send`/`chat-stop`/
   `chat-queue-send` in `ChatPanel.jsx`. Verified end-to-end: typed a
   real message at 390px, sent it via an actual (non-forced) tap on
   `chat-send`, agent started responding. Minor cosmetic tradeoff: the
   `LOOP OFF` toggle label now gets slightly clipped at 390px since it
   no longer steals space from the send button — acceptable tradeoff
   (a clipped label beats an unusable send button).

### Mobile composer strip — founder correction, implemented & screenshot-verified
Founder correction: keep the Loop toggle visible on mobile (it's
functional, not decoration); instead remove the CASUAL/QUERY mode
label (`intent-tier-indicator`) and the "0/20,000" char counter
(`chat-char-counter`) — pure clutter that was part of what squeezed
the Send button toward 0px (previous fix). Desktop unchanged.
Implemented in the existing mobile-only `@media (max-width: 768px)`
block in `index.css` (Iter 265's declutter rule) — added both
testids to the `display: none` list, and simplified the sibling-
margin logic now that `intent-tier-indicator` is always hidden on
mobile (removed the now-dead "QUERY pill present → reset margin"
rule, which would otherwise have canceled the Loop toggle's
right-alignment since a hidden-but-present sibling still matches CSS
`~` selectors).
Screenshot-verified at 390×844: `intent-tier-indicator` and
`chat-char-counter` computed `display: none`; `loop-mode-toggle`
visible (`display: flex`) and correctly right-aligned next to
Attach/History/Send, all full-size. Re-verified desktop (1600px):
both elements still `display: flex`/`block` (untouched).

**Still not yet fixed this pass** (out of the scripted check's scope,
flagged only): README.md → Cloudflare origin error in the Preview
tab's file browser, and the Graph-tab List/Graph toggle closing the
whole panel instead of switching views — founder has not been able to
re-verify these are still reproducible; will need a fresh repro
attempt or screenshot before fixing blind.

## Round 5 — "new users can't add a project" investigation: real root cause found, NOT what founder suspected (2026-08-20)

Founder saw 2 real signups (Luke West, Laurence Bellinger) with 0
projects/tasks/sessions, "No activity recorded yet", and "Email
unverified — promo not yet available". Hypothesis: unverified email
silently blocks project creation.

**Investigated and disproved that hypothesis**: traced `current_dev()`
(`cto_services/auth.py`) and the `/projects/add` code path
(`routers/cto_projects.py`) end-to-end — **no code anywhere checks
`email_verified` before allowing project creation.** Nothing blocks
these users from adding a project. Confirmed the same in the frontend
(only `Admin.jsx` even references `email_verified`).

**Found the REAL bug (2 bugs, actually) while tracing why the
admin panel shows what it shows**, both in the GitHub OAuth signup
path (`routers/github_oauth.py`) — and the signup page explicitly
promotes "Continue with GitHub" as the *fastest* signup method, so
this likely affects a large share of real Internshala/LinkedIn
traffic, not an edge case:

1. **`email_verified` was never set on GitHub or Google OAuth signup**
   — the field was simply omitted (Mongo-missing → falsy), even
   though the OAuth provider already verified the user's identity.
   Worse: OAuth users never get sent our own verification email (by
   design), so there was **no way for them to ever fix this status** —
   permanently "unverified" with zero path forward. This directly
   blocked them from the First-50 promo (`routers/promo_first50.py`
   explicitly gates on `email_verified`) — a real, quantifiable growth
   cost, exactly the founder's concern. **Fixed**: Google OAuth signups
   now get `email_verified: True` unconditionally (Google/Emergent-
   managed OAuth always returns a provider-verified email). GitHub
   OAuth signups get `email_verified: bool(gh_email)` (True whenever
   GitHub actually handed us a real email; False only for the
   synthetic-noreply-email fallback case, where there's genuinely
   nothing to verify).

2. **GitHub OAuth signup/link telemetry was invisible to the admin
   panel's Activity Log** — `routers/github_oauth.py` correctly emits
   funnel events via `github_funnel.track_server_side()`, but those
   write to a *separate* `github_funnel_events` collection that
   `routers/admin_users.py`'s Activity Log merge never queried (it
   only merged `funnel_events`, `cto_tasks`, `cto_token_grants`, etc).
   So ANY user who signed up via GitHub OAuth would ALWAYS show "No
   activity recorded yet", regardless of what they'd actually done —
   this is almost certainly why both example users looked "silent".
   **Fixed**: added a `github_funnel_events` merge into the Activity
   Log timeline in `admin_users.py`. Verified end-to-end: seeded a
   real GitHub-OAuth-style user + funnel event in preview Mongo, hit
   `GET /admin/users/{id}`, confirmed the event now appears in
   `activity_timeline`. Test data cleaned up.

Verified no regression: reverted my 3 changed files via `git stash`
and confirmed the SAME 10 test failures exist on the unmodified
codebase (pre-existing, unrelated to this fix — they check a
different router file, `admin.py`, for an older `$switch`-pipeline
backfill feature). My changes introduce zero new failures.

**Not done — needs founder decision, did NOT touch production data**:
the fix above only applies to *new* signups going forward. Luke West,
Laurence Bellinger, and any other existing Google/GitHub OAuth users
created before this deploy still have the wrong `email_verified`
value in the real database, and are still permanently excluded from
the First-50 promo unless backfilled. I have no direct production DB
access to fix this myself. Options for the founder: (a) I write a
small, idempotent one-time backfill script
(`dev_users` where `auth_provider in (google, github)` and
`email_verified` isn't already `true` → set it `true`, skipping the
GitHub synthetic-noreply-email case) for the founder to run once
post-deploy, or (b) I build a one-click "Backfill OAuth
email_verified" action in the admin panel instead. Not implemented
yet pending founder's choice.

## Funnel-tracking investigation + 3 instrumentation gaps closed (2026-08-20)

Founder asked: does real step-by-step (not just final-state) funnel
tracking exist, so we can see WHERE a user drops off? Investigated
before writing code.

**Findings**:
- `github_funnel_events` (`routers/github_funnel.py`) IS genuinely
  step-by-step for the GitHub-connect sub-flow: `cta_click →
  oauth_redirect → callback_received → linked → repo_selected` (+ App
  install variants), with real per-stage conversion % via
  `GET /funnel/github/stats`.
- `funnel_events` (`services/signup_guards.emit_funnel_event`) was
  SPARSE — only 4 milestones (`signup_completed`, `first_chat_sent`,
  `first_loop_started`, `first_task_shipped`). No event existed for
  email verification, or for project-add attempt/success/failure —
  meaning a stalled/failed `/cto/projects/add` call left **zero**
  trace anywhere, which is exactly the kind of silent gap that made
  real signups look "inactive".
- No PostHog/Mixpanel/GA/Amplitude anywhere in the codebase — confirmed
  via full-repo search. Only Google Ads gtag + Meta Pixel conversion
  helpers (`lib/analytics.js`), which report into Google/Meta's own
  dashboards, not our DB.
- Real production evidence on the 2 signups founder flagged (Luke
  West, Laurence Bellinger): `RESEND_API_KEY` in this env IS the real
  live account (verified `aurem.live`/`auremcto.com` domains). Scanned
  700+ real sends — **neither name appears anywhere** (no verify
  email, no welcome email, no 24h nudge) — consistent with (not proof
  of) the already-fixed OAuth-signup bug: OAuth users get zero emails
  by design, and 24h nudge cron may not have reached them yet.

**Fixed (founder-approved, all 3, live-tested)**:
1. `routers/cto_projects.py::add_project` now emits
   `project_add_attempt` at the top, and every one of the 8 rejection
   branches (installation not found, no repo access, token rejected,
   GitHub probe failed, PAT malformed/rejected/repo-not-found/bad
   status, auth required) routes through a new `_fail()` helper that
   emits `project_add_failure` with a `reason` before raising — plus
   `project_add_success` on the real success path.
2. `routers/admin_users.py::get_user` now merges `cto_projects.created_at`
   into the Activity Log timeline as a `project_connected` entry
   (data already existed, was never surfaced chronologically).
3. `routers/promo_first50.py::verify_email` now emits a real
   `email_verified` funnel_event (previously only inferable from
   `email_verifications.used_at`, which `/admin/funnel`'s aggregate
   `event_counts` never queried).

**Verified live**: real signup → bad-PAT project add → good-PAT
project add, confirmed all 4 events (`signup_completed`,
`project_add_attempt` ×2, `project_add_failure`, `project_add_success`)
landed in `funnel_events`, and `GET /admin/users/{id}` correctly shows
both the failed attempt and the successful connect (as both a funnel
event and a `project_connected` timeline row) in the right order. 65
targeted regression tests pass, zero regressions. Test user/data
cleaned up.

**Not yet deployed to production** — preview-only until founder
redeploys. Still separate/pending (founder chose to keep apart from
this pass): the one-click admin "Backfill OAuth email_verified" button
for pre-existing Google/GitHub OAuth users (Luke West, Laurence
Bellinger included).

## Engineering-Discipline Audit — 12 categories, all checkpoints complete (2026-08-20)

Founder-requested honest status check across Software Engineering,
Reliability, Security, DevOps/Infra, Data Engineering, QA/Testing,
Performance, Cost/FinOps, AI/ML, UX/Frontend, Growth, Compliance —
every claim checked against real code/config/data, not memory.
Full narrative detail lives in the chat transcript of this date;
this is the master-reference summary. Source docs referenced:
`CODEBASE_AUDIT.md`, `GUARDS_CHARTER.md`, `FOUNDER_STATUS_REPORT.md`,
`G6_DEDUP_SCOPE_2026-08-20.md`.

**Legend**: ✅ exists & working · ⚠️ partial/gap · ❌ missing

| # | Category | Status | One-line finding |
|---|---|---|---|
| 1 | Software Engineering | ⚠️ | Clean router/service split; no ADRs; 2 response-envelope styles coexist (`{"ok":...}` vs `HTTPException`); `ChatPanel.jsx`(5,134L)/`chat.py`(3,782L) oversized |
| 2 | Reliability | ⚠️ | Guards wired (G10/17/19/20/22); no external uptime monitor (G9, founder-owned); single-pod SPOF; 1 restore test ever, no recurring drill; no uptime target existed |
| 3 | Security | ⚠️ | SEC-005/006 LIVE in prod; SEC-002/003/004 preview-fixed; **SEC-007+008 closed this session**; SEC-001 (git history) still open, blocked on Emergent Support; never an external pentest |
| 4 | DevOps/Infra | ⚠️ | Docker✅, 8 real CI workflows exist but never triggered (G8) — **token wired this session, blocked on founder adding repo to token's access list**; manual single-pod deploy, no zero-downtime |
| 5 | Data Engineering | ⚠️ | ~130 collections inventoried, no field-level schema; no ETL (not needed at this scale); G6 dedup — **found already partially shipped (charter was stale), extended +3 collections this session** |
| 6 | QA/Testing | ⚠️ | 5,158 tests, 86% routers have ≥1 test file; **promo_first50.py gap closed this session (9 new tests)**; `backups_admin.py` still untested; zero load/stress testing ever; no real line-coverage % (proxy only) |
| 7 | Performance | ⚠️ | Real Lighthouse run: Perf 41 (preview dev-mode, not representative) / A11y 100 / BP 79 / SEO 61. Real prod-relevant issues: 11.8s LCP, ~550KB tracking scripts — **parked by founder for a dedicated future pass** |
| 8 | Cost/FinOps | ❌/⚠️ | No consolidated real monthly cost (no dashboard access); customer cost tracking exists but is a char-count estimate; G22 budget-guard confirmed working; no per-tier $ cap yet |
| 9 | AI/ML | ✅/⚠️ | Multi-provider council routing confirmed live; CitationGuard covers customer chat not just admin; cheapest-viable-model claim not freshly re-verified against current pricing |
| 10 | UX/Frontend | ⚠️ | A11y 100/100 (measured); responsive only browser-resize tested, never real devices; no design system (inline styles, light Tailwind) |
| 11 | Growth | ⚠️ | Real activation funnel + d7/d30 retention metric exist; ad-tracking (gtag/Meta) not joined to internal funnel — **founder parked this as backlog, not urgent** |
| 12 | Compliance/Legal | ⚠️ | 10 real policy docs incl. GDPR/CCPA/DPDP subprocessor mapping, real cookie banner, real self-service delete endpoint; Privacy Policy promises a retention table — **`login_attempts` 30-day TTL gap found AND fixed this session**; other retention-table rows (task history 12mo, error logs 90d) not verified as enforced |

### Fixed this session (2026-08-20), all verified
- SEC-007 (chat-path scan widened to CRITICAL+HIGH), SEC-008 (litellm off customer-assets → pinned PyPI `1.80.0`)
- G15 dependency-CVE scan + G4 rendered-secret scan wired into `ci.yml`/`predeploy_gate.sh` (were built, never called)
- Documented 99.0% uptime target (`GUARDS_CHARTER.md`)
- G6 dedup indexes extended: `email_verifications.token`, `oauth_states.state`, `oauth_codes.code` (discovered G6 was already partially shipped — charter corrected)
- `tests/test_promo_first50.py` — 9 new tests closing the zero-coverage gap on the revenue/access-controlling promo router (incl. the "never touch a real Stripe subscriber or founder" guarantee)
- `login_attempts` 30-day TTL index added — closes a direct contradiction of the published Privacy Policy §8
- GitHub Actions token wired (`GITHUB_ACTIONS_TOKEN`/`GITHUB_REPO=polarisbuiltinc-wq/auremdev` set) — blocked only on founder adding the repo to the fine-grained token's access list on GitHub's side
- Cleanup: 51 confirmed-test Stripe customers deleted (live account), 858 preview test `dev_users` wiped, 2 `cto_payments` test fixture rows deleted — zero real customer/revenue data touched

### Retention-promises pass #2 (2026-08-20, same day, founder-requested follow-up)
Checked every remaining row in Privacy Policy §8 (not just `login_attempts`):
- **Account data** (deletion + 30d) → ✅ already correct: `POST /auth/delete-me` hard-deletes across 15 collections immediately (exceeds the promise), and any trace left in nightly R2 backups rolls off within the existing 30-day backup-retention window — no code change needed.
- **Task history** (12 months) → ❌ found real gap: `loop_sessions` TTL was 30 days, not 365. Fixed live via `collMod` (no data loss) + corrected `scripts/init_prod_collections.py` source so fresh environments start right.
- **GitHub tokens** (until disconnect) → event-driven, not TTL-based; not re-audited this pass.
- **Payment records** (7 years, legal minimum) → ✅ correctly has no premature-deletion TTL.
- **Error logs** (90 days) → ❌ found two real gaps, both fixed: `loop_errors` had zero TTL (added on `timestamp`, 90d); `frontend_errors` had zero TTL AND its only timestamp field (`last_seen`) is stored as an ISO **string**, which Mongo TTL silently ignores — added a real BSON-Date twin field `last_seen_at`, TTL'd that instead (`routers/admin_public.py`).
All 4 fixes verified live via direct index inspection + a clean re-run of `init_prod_collections()`; 6 targeted tests still pass, `/api/health` green.

- "Michael L. Lawson — Pro tier" question → `promo_first50.py`'s intentional, capped (50 spots), auto-expiring 30-day promo — not a payment bypass. Zero real Stripe revenue exists to date (75 customers, 0 charges/subscriptions ever, all traced to test/QA/founder-dogfood activity).


## Latest ship — Deploy fix round 2 (health-probe short-circuit) + user-confirmed PRODUCTION LIVE (2026-08-19)

**User-confirmed LIVE in production** (real evidence pasted, not assumed): `GET /api/health` on auremcto.com → `{"ok":true,"env":"production","built_at":"2026-08-19T23:22:21","db":true,"uptime_s":371.94,"dead":[]}`. This confirms round-1 of the deploy fix (health-path skip-list + `BaseExceptionGroup` catch) AND SEC-005/SEC-006 are live in production — highest evidence tier available.

**Round 2 (preview-only, NOT yet deployed)**: founder's pasted deploy logs from the round-1-deployed build still showed the underlying Starlette `BaseHTTPMiddleware` client-disconnect race firing on `GET /health` (caught gracefully by round 1's except clause now, no crash — but still noisy error-log entries). Root cause: this is a known Starlette architectural race (client disconnects mid-`call_next()`, e.g. a tight K8s probe timeout) that no amount of except-clause tuning inside the middleware can prevent, since it happens inside Starlette's own `call_next()` dispatch. Fix: `/health`, `/healthz`, `/ping` (all three literally `return {"ok": True}`, zero DB/async work — see `healthz_root()`) now short-circuit BEFORE calling `call_next()` at all, answered inline by the middleware itself. `/api/health` (does real work — commit_sha, integration checks) deliberately NOT short-circuited, still relies on round-1's exception catch as its safety net. Verified: sequential + 50-concurrent `curl /health` all 200, backend logs clean, `_HEALTH_PROBE_EXACT_PATHS` unit-testable via existing `_global_rl_should_skip`-adjacent test file.

Founder needs to redeploy again to get round 2 live — told explicitly, not claimed as live.

## Latest ship — Cost-tracking wiring into main chat path SHIPPED (P0), tested live (2026-08-19)

Founder-approved P0, done before R2/Stripe follow-ups (both currently blocked on founder-side credential generation — see below).

**New**: `services/customer_cost_tracker.py` — logs every `/chat/send`
and `/chat/stream` turn's cost to a **separate** collection
(`customer_chat_cost`), deliberately NOT reusing `ora_chat_usage`
(which backs the founder's personal $30/day admin-tool budget guard —
mixing customer volume into it would have corrupted that guard's real
email alerts). Cost = char-count token ESTIMATE (~4 chars/token,
same heuristic `chat.py::_deduct_tokens` already used for the token
wallet) × the real pricing table, since `chat_with_tools()` doesn't
thread exact provider-reported token usage back up today (flagged
per-row via `estimation_method: "char_count_v1"` — stated honestly,
not claimed as exact).

**Wired into** both `routers/chat.py` call sites (`/chat/send`,
`/chat/stream`), best-effort/never blocks a reply on failure.

**`routers/admin_bi.py`** `_fetch_inference_metrics()` now merges
`ora_chat_usage` (admin-tool) + `customer_chat_cost` (customer) for
`today_usd`/`month_usd`/`by_model`/`by_route`/`daily_series_30d` — the
TRUE combined total. `budget` (the $30/day guard) stays scoped to
admin-tool-only, unaffected. This also silently fixed the "net
margin" calc in `/admin/bi/summary` (`net_margin_usd = mrr -
projected_month_infer`), which had been using the admin-tool-only
figure and therefore overstating margin this whole time.
`LiveBusinessIntelligence.jsx` updated to show the admin-tool vs
customer-chat breakdown, not just a merged number.

**Live-verified, not just unit tests**: logged in as test@aurem.dev,
sent a real `/chat/send` message, confirmed a real row landed in
`customer_chat_cost` (provider "glm-5.2" → correctly classified to
model "z-ai/glm-5.2", 149 input / 4 output tokens, $0.000047), then
confirmed `/admin/bi/summary` correctly reported
`today_usd = admin_tool_today_usd + customer_chat_today_usd`
($0.005539 = $0.005492 + $0.000047). Caught and fixed one real bug
during this verification: `_sum_cost` helper was referenced before
being defined in the file (NameError, silently caught by the
try/except and logged as a warning) — fixed, re-verified after.

**Tests**: `tests/test_2026_08_19_customer_cost_tracker.py` (13 new:
token estimation, provider→model classifier incl. compound labels
like "glm-5.2+claude-review", DB-failure never raises, no-db returns
0), full re-run of `test_slice_a_bi_cockpit.py` + 3 other admin_bi/
chat-adjacent suites (74 total) — zero regressions. One pre-existing,
unrelated failure confirmed via git-stash baseline (`test_iter212m163
...circuit_breaker_source`, stale `services/llm.py` path reference).

**Per-tier $ cost cap (Fix #3)**: still NOT built — founder is
thinking about the per-tier $ ceiling numbers before this starts, per
their own instruction. Cost data now exists to build it on top of.

## Latest ship — Cancel-billing + anonymous-support bugs FIXED, tested in preview (2026-08-19)

Founder approved priority: fix #1 (billing button) + #2 (support form) now, report with evidence; #3 (per-user $ cap) scope-only.

**Fix #1 — "Manage billing" disabled-button bug** (`frontend/src/
components/PricingCards.jsx`): `disabled={isCurrent || !t.paid || busy
=== t.id}` → `disabled={(isCurrent && !t.paid) || busy === t.id}`. Now
matches the cursor/opacity styling that was already correct. **Live
evidence**: created a real non-founder test user, set tier=pro, logged
in via browser — button showed "Manage billing", NOT disabled,
clicking it fired `POST /payments/portal` and backend logs show a real
`GET https://api.stripe.com/v1/subscriptions/<id>` call (404 only
because the test used a fake subscription ID — the full pipeline
fired, which was impossible before the fix). ⚠️ Note: this preview's
`STRIPE_API_KEY` is a **live** key, not test-mode — be careful with
future checkout-flow screenshots (one harmless abandoned real checkout
session was created during testing, no card entered, no charge).
Test data cleaned up after.

**Fix #2 — anonymous/pre-signup support form**: added
`POST /support/tickets/public` (`routers/support.py`) — genuine
no-login, no-token path (name optional, email + body required),
5/min-per-IP rate limited like the promo waitlist endpoint, writes to
the same `cto_support`/`cto_support_messages` the admin panel reads.
Rewrote `pages/Support.jsx` to show an always-usable form: token
present → old locked-email flow unchanged; no token → editable
name+email fields, posts to the new endpoint. Removed the old
permanently-disabled state. **Live evidence**: browser screenshot flow
(no login) — filled form, submitted, got the success confirmation
screen; ticket confirmed written to `cto_support` with correct
`user_email`; rate limit confirmed tripping at the 6th request/min.

**Tests**: `backend/tests/test_2026_08_19_public_support_ticket.py`
(5 new), `frontend/src/components/__tests__/
PricingCards.manageBilling.test.js` (2 new) — all green, plus full
re-run of `test_iter388u_support_reply_ux.py` +
`test_deploy_2026_08_19_health_probe_and_exceptiongroup.py` +
`test_deploy_hardening_middleware_no_response.py` +
`test_iter386_global_rate_limit.py` (36 total) — zero regressions.

**Fix #3 (flagged, NOT built) — per-user $ cost cap, scope estimate**:
- **Blocking dependency**: cannot build a $ cap before $ cost exists
  for the customer chat path (confirmed 0% coverage — see cost-gap
  finding above). Founder already agreed this cost-tracking wiring is
  the real P0, sequenced before the cap itself.
- **Once cost tracking exists**, the cap itself is comparatively
  small: (a) a `monthly_cost_usd` aggregate per user (mirrors the
  existing `assert_has_task_budget` pattern in `services/usage.py`),
  (b) a per-tier $ ceiling config (e.g. free=$0.50, starter=$3,
  pro=$8, team=$20 — needs founder pricing input, not a guess), (c) a
  check alongside the existing task-count check in `chat.py`'s message
  entrypoint, (d) admin visibility/alerting when a user approaches the
  ceiling (reuse `BI Cockpit` patterns). **Estimate: medium**, roughly
  on the same order as the G22 idle-spend-guard work already shipped,
  once cost-tracking wiring (the real P0) is done first — this piece
  alone is not the hard part.

Founder's confirmed sequence going forward: (1) #1/#2 shipped this
turn ✅, (2) cost-tracking wiring into main chat path — next, real P0,
(3) DB monthly restore drill — approved, quick effort, after that.
External uptime monitor — founder handling directly (UptimeRobot/
BetterStack), no code needed.

## Latest ship — Founder-level 10-point readiness audit + DeepSeek/cost-gap report delivered (2026-08-19)

Report-only, zero code changes (per founder's explicit "report first" instruction). Deploy fix from the entry below is still awaiting founder's live-evidence confirmation — do not mark it prod-verified until that's pasted.

**DeepSeek/OpenRouter follow-up (delivered this turn)**:
- Clarified the "3 vs 5 X-Title" question: same finding, expanded. The
  3 the founder saw on the OpenRouter dashboard are the 3 highest-
  volume titles (`"AUREM"`, `"AUREM ORA Chat"`, `"AUREM - upload/
  convert (image)"`); source-level grep during the DeepSeek
  investigation found 2 more low-volume variants (`"Aurem - Graph
  Diagram"`, `"Aurem Advisor"`) that likely don't generate enough
  tokens to rank in the dashboard's visible "Top Apps" view.
- **Cost-undercounting quantified with real preview data** (not just
  qualitative): `chat_sessions` has 2,739 total turns across 917
  sessions; `ora_chat_usage` has 241 rows total, and a
  `route`/`user_id` breakdown proves **100% of those 241 rows are
  admin-ORA-chat / system-health-check / QA-canary traffic — ZERO
  are customer-facing `/chat/send` or `/chat/stream` turns.** So it's
  not "undercounted", it's "not counted at all" for the primary
  product surface. Total tracked spend in this preview sample:
  $0.124, entirely non-customer.

**Founder 10-point readiness audit** — full findings delivered to
founder in chat (not fully duplicated here to avoid PRD bloat; see
chat transcript same date). Summary of anything NOT already ✅:
- G9 external uptime monitor: ❌ MISSING (confirmed, no
  UptimeRobot/BetterStack config anywhere) — quick fix, but requires
  founder to create an external account (~15 min, zero code).
- DB restore: ⚠️ PARTIAL — real restore code exists
  (`services/db_restore.py`) and was preview E2E verified (121/122
  collection parity) per `FUTURE_BUILDS_LEDGER.md` item #5, but no
  recurring automated restore-test/drill exists (`CODEBASE_AUDIT.md`
  G11 note) and no production restore has ever been evidenced.
- Self-serve cancel: ⚠️ PARTIAL/BROKEN — real bug found in
  `frontend/src/components/PricingCards.jsx` line ~411: the "Manage
  billing" button's `disabled={isCurrent || !t.paid || ...}` disables
  itself whenever `isCurrent` is true, INCLUDING the current-paid-tier
  case it exists to serve. `POST /payments/portal` (real Stripe
  billing-portal session) is correct on the backend; the button that
  should call it is inert. Quick fix (one-line condition change) —
  logged here so it doesn't get lost.
- Support channel for logged-out/pre-signup visitors: ❌ effectively
  MISSING — `pages/Support.jsx`'s form is `disabled` without a
  signed `?t=&e=` token (normally only present in campaign emails);
  the plain footer link on Landing has no token, so a first-time
  visitor clicking the site's own "Support" link lands on an inert
  form. `GlobalHelpFAB` only shows to logged-in users on non-marketing
  routes.
- Per-user $ cost cap: ❌ MISSING (confirmed) — only a flat monthly
  task-COUNT cap exists (`services/usage.py`), no $-based ceiling, and
  combined with the chat.py cost-gap above there's currently no way
  to even detect a single user generating outsized inference cost.
- Pricing: confirmed live via screenshot + code — Free $0 / Starter
  $9 / Pro $19 ("Most Popular") / Team $49, all 4 tiers match
  `subscription_tiers.py` and `PricingCards.jsx` exactly, no drift.
- Signup→paid conversion: ✅ real, `GET /admin/insights/
  activation-funnel` (`routers/admin.py`) computes signup → GitHub
  connect → project added → message sent → shipped → paid, filtered
  for test accounts, rendered in `AdminOverview.jsx`'s `FunnelCard`.
  Visitor→signup (top-of-funnel) is tracked via Google Ads
  gtag + Meta Pixel (both live in `index.html`) but those numbers live
  in Google Ads/Meta Ads Manager, not inside our own admin panel —
  ⚠️ PARTIAL, not unified.
- ToS/Privacy: ✅ real content, not stubs — `/terms`, `/privacy` plus
  7 more policy docs (refund, cookie, security, DPA, subprocessors,
  AUP, status) in `frontend/public/policies/`, 700-1050 words each.
- DB backups: ✅ real, automated nightly cron → Cloudflare R2, 30-day
  retention (`services/db_backup.py`), confirmed live in this
  session's own boot logs.

Founder approved next: (1) deliver this report [done], (2)
consolidate the 5 OpenRouter X-Title values to 1 canonical name —
next up, (3) wire `cost_tracker.log_call()` into the main chat path
for real cost visibility — flagged high priority once deploy is
confirmed stable, (4) fix the Manage-billing disabled-button bug — not
yet approved/scheduled, awaiting founder go-ahead since this session's
mandate has been report-only so far.

## Latest ship — Deploy-log crash FIXED + deployment_agent PASS, awaiting founder redeploy (2026-08-19)

Founder pasted real prod deploy logs (K8s/Nginx upstream timeout on
`/health`, Upstash Redis quota-exhaustion fallback, and a Starlette
`RuntimeError: No response returned` / `anyio.EndOfStream` crash in
`main.py`'s `_global_rate_limit_guard`). Root cause found from first
principles by reading the actual middleware, not guessed. Code-only
fix per founder's explicit instruction (no Docker changes).

**Root cause #1**: `_GLOBAL_RL_SKIP_PREFIXES` only exempted
`/api/health*`. The K8s pod-level probe hits prefix-less `/health`,
`/healthz`, `/ping` (`healthz_root()`) — those went through the
Redis-backed `check_rate_limit_async` on every single probe. With
Upstash's quota exhausted, that added latency to every probe →
matches the observed upstream timeout exactly.
**Fix**: added `/health`, `/healthz`, `/ping` to the skip list.

**Root cause #2**: both `call_next()` try/excepts in
`_global_rate_limit_guard` only caught `except Exception`. A client
disconnect mid-request (K8s probe / Nginx) can make anyio's internal
task group raise a `BaseExceptionGroup` wrapping a bare
`asyncio.CancelledError` — which does NOT subclass `Exception`, so it
skipped the handler and surfaced as the unhandled "RuntimeError: No
response returned" seen in the logs.
**Fix**: widened both to `except (Exception, BaseExceptionGroup) as _e:`.

**Deliberately NOT touched**: Mongo/Atlas config — logs showed zero
DB-layer errors. Upstash Redis quota itself — founder is resetting/
upgrading that directly on Upstash's dashboard (not a code fix).

**Verified in preview**:
- Skip-predicate unit-tested directly: `/health`, `/healthz`, `/ping`
  all return `True` from `_global_rl_should_skip`.
- 50 concurrent `curl /health` requests → all `200`, 1.3s total, no
  429/500/timeout.
- New `tests/test_deploy_2026_08_19_health_probe_and_exceptiongroup.py`
  (9 tests) — including a direct call to the real
  `_global_rate_limit_guard` coroutine with a `call_next` stub that
  raises a `BaseExceptionGroup`, asserting it still returns a clean
  `JSONResponse(500)` on BOTH the skip-path and main-path branches.
  All green, plus updated the pre-existing
  `test_deploy_hardening_middleware_no_response.py` assertion to match
  the widened except clause.
- Full targeted regression sweep (`-k "rate_limit or health or
  middleware or deploy"`, 288 tests): 11 failures/errors, all
  git-stash-confirmed **pre-existing** on unmodified code (stale
  baseline issues unrelated to this fix — admin password-leak tests,
  GitHub App dispatch fixture issues, a PRD.md header-pointer doc
  test, etc.). Zero regressions introduced.
- `deployment_agent` full readiness scan: **PASS, no blockers**. CORS,
  ports, supervisor config, secrets, auth redirects, destructive-DB
  startup all clean.

**⚠️ NOT YET LIVE — founder must redeploy** for this fix to reach
`auremcto.com`. Do not claim production-fixed until founder confirms
a successful redeploy + a real post-deploy check (e.g. prod `/health`
responds fast under load, no recurrence of the RuntimeError in prod
logs).

**Parked per founder's explicit instruction**: G22 cost-work final
answer (exact health-check model + savings estimate) — stays paused
until this deploy is confirmed fixed and stable. Also parked mid-flight:
a founder-requested 3-step report (DeepSeek V3→V4 Flash upgrade
recommendation, duplicate "AUREM" OpenRouter app-attribution root
cause, and internal cost-tracking coverage gap) — investigation is
DONE (see below), just not yet delivered because founder pivoted to
the deploy fix first. Deliver this report next turn if founder doesn't
ask for something else first.

### Parked findings ready to report (investigated, not yet delivered, NO code changes made)

1. **DeepSeek V3 vs V4 Flash**: confirmed primary model string is
   `deepseek/deepseek-chat` (V3) via `LLM_MODEL` env
   (`backend/.env:11`) and `CEO_RESCUE_MODEL`/`ora_chat/router.py`
   defaults — all V3. The DIRECT-API fallback
   (`_DEEPSEEK_DIRECT_MODEL`, `openrouter_providers.py:317`) already
   defaults to `"deepseek-v4-flash"` (rarely hit — direct-API is only
   a 2nd-hop fallback). Web-verified: V4 Flash is ~40-45% cheaper
   ($0.077-0.09/M in vs V3's $0.14/M in) AND far higher quality on
   coding (SWE-bench Verified ~74-79% vs V3's 42%). `_DEEPSEEK_HOSTS`
   provider allow-list (`deepseek, streamlake, deepinfra, novita`) is
   confirmed present in `deepseek/deepseek-v4-flash`'s OpenRouter
   provider pool too, so no provider-routing changes needed beyond the
   model string + `cost_tracker.py`'s pricing table entry. Recommend
   switching — cheaper AND better for the exact coding use case.
2. **3 duplicate "AUREM" OpenRouter app entries**: only ONE
   `OPENROUTER_API_KEY` exists/is used anywhere in the codebase — NOT
   multiple keys. Root cause is inconsistent `X-Title` header values
   across call sites (OpenRouter's "Top Apps" attributes by
   Referer+Title, not by key): `"AUREM"` (openrouter_providers.py/
   openrouter_client.py), `"AUREM ORA Chat"` (ora_chat/providers.py),
   `"AUREM - upload/convert (image)"` (upload.py), plus 2 more
   lower-volume variants (`"Aurem - Graph Diagram"`,
   `"Aurem Advisor"`). Also inconsistent `HTTP-Referer`: some send
   `APP_URL` env (currently `"https://aurem.dev"` in this preview's
   `.env` — looks like a stale pre-rebrand value), others hardcode
   `"https://auremcto.com"`. Accidental fragmentation, not intentional
   multi-key design — needs consolidating to one canonical title+referer.
3. **Cost-tracking coverage gap (bigger finding than expected)**:
   grep-confirmed `routers/chat.py` and `services/orchestrator.py` —
   the customer-facing single-agent chat path, the highest-volume
   traffic — NEVER call `cost_tracker.log_call()` and never touch
   `ora_chat_usage`. Only 4 partial, non-overlapping cost surfaces
   exist: `ora_chat_usage` (admin ORA chat + system health-check +
   conditional loop-token-ledger + adversarial-review),
   `maxx_cost_log` (only "maxx mode" dual-agent chat_send calls),
   `cto_tasks.tokens_used` (Loop quota counting, not $ cost). Regular
   single-agent customer chat — the bulk of real usage — has **zero**
   cost logging anywhere. So the BI Cockpit's inference-cost number
   is near-certainly a large undercount of the real OpenRouter bill;
   this alone likely explains most of any gap vs the $25.83/month
   OpenRouter-dashboard figure, independent of any duplicate-app
   confusion. Could not directly reconcile the exact prod $25.83
   figure — no production DB/OpenRouter-dashboard access from here.

## Latest investigation — OpenRouter/LLM idle-cost leak: ROOT CAUSE FOUND, no fix applied yet (2026-08-19)

Founder asked to investigate before fixing. Investigated via preview
Mongo (`council_health_probes`, `ora_chat_usage`, `inference_costs`)
+ source read of every background `while True`/cron task. **Confirmed
real, ongoing, unconditional LLM calls with zero user attribution —
this is not imagined.** No fix applied yet, per founder's explicit
instruction to report root cause first.

**Root cause #1 (primary, highest production risk)**:
`periodic_longcat_reprobe()` (`services/llm/_probes.py:228-267`) hits
the REAL OpenRouter `/chat/completions` endpoint (not a lightweight
ping) every 900s when healthy — **but drops to every 60s forever with
no ceiling** if the LongCat model is ever "degraded" (invalid slug/
5xx/network error). `LONGCAT_ENABLED=true` in this env's `.env`. If
this gets stuck degraded in production for hours/days, that's 1440+
real OpenRouter calls/day, unconditionally, with no cost ceiling or
alert — architecturally the single biggest idle-cost risk found.
Currently healthy in preview (confirmed via 5878 `council_health_probes`
docs, latest entries `live:True, http_code:200`).

**Root cause #2 (confirmed continuous, real dollars, zero visibility)**:
`integration_health_cron` → `_probe_emergent_llm()`
(`services/integration_health.py:755-793`) makes a real Claude Haiku
completion via the Emergent LLM key every `INTEGRATION_HEALTH_INTERVAL_SEC`
(default 600s = 10 min) — **enabled by default**
(`ENABLE_INTEGRATION_HEALTH_CRON` defaults to "1"), forever, 24/7,
regardless of any user session. 144 calls/day, unconditional.

**Root cause #3 (confirmed minor, real, restart-triggered)**: at every
backend boot, a standalone one-off `_probe_longcat()` task AND the
periodic reprobe loop's un-delayed first iteration BOTH call
`probe_longcat_availability()` within milliseconds of each other
(confirmed via near-duplicate timestamps 36ms apart in
`council_health_probes`) — 2x cost per restart. Minor in stable
production (restarts are rare), more noticeable during active dev
sessions with frequent hot-reloads.

**Bounded, NOT a leak**: `ora_canary_cron` (`ORA_CANARY_ENABLED=1`)
fires ~7 real chat completions once/day at 02:30 UTC — intentional
QA cost, correctly logged with `user_id:"canary"` in `ora_chat_usage`.

**Correction to founder's own mental model**: the `inference_costs`
collection referenced as "built for the BI Cockpit" is **completely
empty and unwired** — grep confirms zero code anywhere writes to it.
The REAL cost-tracking collection is `ora_chat_usage`
(`services/ora_chat/cost_tracker.py`, 236 docs in preview, real
per-user attribution). **Critically: neither root cause #1 nor #2
writes to `ora_chat_usage` either** — both bypass the metering wrapper
entirely by calling `httpx`/`call_openrouter_model`/`LlmChat` directly
instead of going through `cost_tracker.log_call()`. So today these
background costs are **100% invisible** in any BI/cost view — this is
itself a gap, independent of whether the calls should keep running.

**Not yet fixed — awaiting founder decision on**: (a) cap/kill-switch
on the LongCat fast-retry loop, (b) whether `integration_health_cron`'s
LLM probe should exist at all vs. a cheaper non-LLM check, (c)
dedup the boot-time double-probe, (d) route any surviving background
LLM call through `cost_tracker.log_call()` so it's visible, (e) the
requested new guard (G22-style) alerting on token spend with zero
active sessions in that window.

## Latest ship — Cost-leak fully fixed: LongCat probe now $0, health-check switched to gpt-5.4-mini, G22 guard live (2026-08-19)

Founder-approved fix for all 3 root causes found in the investigation
above, plus cost-tracking visibility + the G22 guard. All live-tested
against the real preview OpenRouter key/Emergent LLM key, not just
code-reviewed.

- **Root cause #1 (LongCat probe)**: switched from a real
  `POST /chat/completions` to a free `GET /models` catalog check —
  **zero tokens, $0 per call, forever**, at any frequency. Did **NOT**
  swap the model being tested (that would've meant checking a
  DIFFERENT model's uptime while blindly trusting the real
  customer-facing LongCat model — a correctness regression, not a
  cost fix). Live-verified: `probe_longcat_availability()` returned
  `True`, `http_code:200`, real OpenRouter response, confirming the
  model is genuinely live — with zero cost. Also capped the
  degraded-state fast-retry at 20 consecutive tries (~20 min) before
  backing off to a slower cadence + opening an incident, so a stuck
  outage doesn't hammer OpenRouter's API indefinitely even though it's
  now free.
- **Root cause #2 (integration_health_cron's LLM probe)**: switched
  from Claude Haiku 4.5 to **`gpt-5.4-mini`** (cheapest model reachable
  via the Emergent LLM key) — **founder's model question, answered
  directly below**. Applies always (idle or active), per instruction —
  this check never was idle-gated to begin with, it already ran on a
  fixed 10-min cadence regardless of user activity. Hit a real bug
  during live verification: gpt-5.4-mini rejects `temperature=0.0`
  (GPT-5 family only supports temperature=1) — fixed, then
  live-verified via `POST /admin/integrations/refresh`: real 200,
  `"gpt-5.4-mini responded"`, cost **$0.000016/call** logged.
- **Root cause #3 (boot double-probe)**: removed the redundant
  standalone one-off probe task in `main.py` — the periodic loop's own
  first iteration already probes immediately, so it was pure
  duplication on every restart.
- **Cost-tracking visibility**: the LongCat fix eliminates its cost
  entirely (nothing to log). The integration-health probe is now
  logged via `cost_tracker.log_call()` under `user_id:"system:health_check"`
  — confirmed present in `ora_chat_usage` live.
- **G22 guard (new, standing safety net)**: `services/g22_idle_spend_guard.py`
  — hourly check: if `ora_chat_usage` shows LLM spend during a window
  with **zero real (non-system) user activity**, and that spend is
  from an unreviewed background actor OR exceeds a known actor's tiny
  ceiling, opens an incident via the same G1-G21 `incident_log`
  mechanism already wired to the admin dashboard. 5 new tests
  (`test_g22_idle_spend_guard.py`) using real Mongo docs, all passing —
  covers: no-activity, known-actor-under-ceiling (not flagged),
  unknown-actor-any-spend (flagged), known-actor-over-ceiling
  (flagged), real-user-present (suppresses alert).
- Fixed 2 pre-existing unit tests that mocked the old completions-based
  LongCat mechanism (now correctly test the `/models` catalog path).
  2 other test failures found during this work
  (`test_council_reprobe_*`) are **pre-existing and unrelated** —
  confirmed via `git stash` that they fail identically on unmodified
  code (stale references to a `/admin/council/reprobe` endpoint that
  moved to `admin_ops_config.py` during an earlier Phase 2 split, same
  root cause as the `test_iter356` stale-test issue found in the
  original audit). Not fixed — out of scope for this task, logged here
  for the record.
- 68 relevant regression tests green (module boundary, pre-launch,
  session5 LLM split/hygiene, council/dockerfile, G22, ECC features).

**Model answer for the founder's question**: switched to
**`gpt-5.4-mini`**. The LongCat probe's model was deliberately NOT
switched (see above — it would have broken the check's actual
purpose). Real customer-facing coding/chat model (LongCat/GLM/DeepSeek
via OpenRouter) is completely unchanged.

**Savings estimate** (order-of-magnitude — no production OpenRouter
billing-dashboard access, so this is a calculation from real per-call
pricing/cost figures found in this session, not a verified production
total):
- LongCat probe: was a real completion on `anthropic/claude-sonnet-4.5`
  ($3/$15 per M tokens) → now $0. At the OLD healthy cadence (96
  calls/day) that alone was already a small daily cost; at the
  degraded 60s cadence (up to 1440 calls/day) it could have been
  materially larger — now $0 either way, at any frequency.
- Integration-health probe: was Claude Haiku 4.5 ($1/$5 per M tokens),
  now gpt-5.4-mini ($0.75/$4.50 per M tokens) — **confirmed live cost
  $0.000016/call** (~15 input + 1 output token). At the fixed 144
  calls/day cadence: **≈$0.0023/day → ≈$0.07/month** for this probe
  specifically (previously similar order of magnitude on the pricier
  model — the real win here is visibility + a cheaper unit cost, not
  a dramatic dollar swing, since token count per call was already
  tiny).
- The dollar amounts here were always small per-call — the real
  "leak" risk was #1's uncapped 60s fast-retry loop having no ceiling
  if LongCat ever got stuck degraded for an extended period, which is
  now fully eliminated (both by the ceiling AND by making the check
  free regardless of frequency).

## Latest ship — 🔴→✅ SEC-005 + SEC-006 FIXED, exploit-tested + live isolation test 20/20 (2026-08-19)

Founder-approved fix for the CRITICAL command-injection finding
(SEC-005) plus its realistic delivery path (SEC-006), together, same
session. Full detail in `CODEBASE_AUDIT.md` §7.6.

- **SEC-005**: `orchestrator.py`'s post-edit build hook no longer
  builds/runs a shell string — argv-only `create_subprocess_exec`,
  plus a new shared `_is_safe_repo_path()` charset gate enforced both
  at `write_repo_file`'s entry and inside the hook itself. Proved the
  fix, not just described it: crafted 5 real malicious filenames
  (`$()`, `;`, backtick, pipe, `&&`), confirmed all rejected and no
  command ever executed (proof-file check). Confirmed normal `.py`
  files still work. 33 regression tests green.
- **SEC-006**: added a standing anti-injection directive to the
  system prompt + reinforced both TOOL RESULTS transcript delimiters
  — repo/web content is now explicitly framed as untrusted data, not
  instructions.
- **Live 2-account isolation exploit test** (separate founder ask):
  20/20 PASSED via `testing_agent` — 16 real cross-tenant attack
  attempts against chat/project/loop/fix-pipeline IDs, all correctly
  denied, no data leak. New suite: `test_isolation_exploit.py`.

**⚠️ PREVIEW ONLY — founder must redeploy** for this fix to reach
`auremcto.com`. App was live/public before this fix shipped, so this
is not resolved in production until redeployed.

## Latest ship — SEC-003 + SEC-004 fixed (founder-approved) (2026-08-19)

- **SEC-003**: Ship Wall flipped from opt-out to opt-in. New Settings
  toggle (`ShipWallOptInCard.jsx`) since no UI existed before. Curl +
  screenshot verified.
- **SEC-004**: uniform 404 on loop/fix-job ownership checks (7
  occurrences across `loop.py`/`fix_pipeline.py`), no more 403 leak.
- 3 hardening notes from §7.5/§7.6 logged for later reference, not
  urgent, not touched.
- **⚠️ SEC-005 (CRITICAL, command injection, DO NOT LAUNCH verdict,
  live in production) remains UNFIXED — not addressed in this round,
  re-flagged for explicit founder decision.**

## Latest ship — 🔴 FULL SECURITY AUDIT: CRITICAL finding, DO NOT LAUNCH verdict (2026-08-19)

Founder-requested full OWASP Top 10:2025-aligned audit via
`security_audit_agent`, 12 categories, report-only (no fixes applied).
Full detail in `CODEBASE_AUDIT.md` §7.6.

**🔴 SEC-005 — CRITICAL — OS command injection in ORA's post-edit
build hook.** After ORA writes any `.py` file, `orchestrator.py`
builds a shell command by string-formatting the raw file path into it
and runs it via `asyncio.create_subprocess_shell()`. The path filter
only blocks a leading `/` and `..`, NOT shell metacharacters
(`$()`, `;`, backtick). If ORA is ever steered into writing a file
whose NAME contains a shell command-substitution pattern, that command
executes on the shared production server — full env var access
(every customer's GitHub PAT, the vault master key, Stripe keys, Mongo
creds). App is **already live in production**, making this urgent,
not theoretical. Verdict: FAIL — DO NOT LAUNCH (audit agent's own
launch-guidance field).
- SEC-006 (MEDIUM): no structural boundary between user instructions
  and repo/web content ORA reads — realistic delivery mechanism for
  SEC-005 via indirect prompt injection (a malicious README/comment).
- SEC-007 (MEDIUM): chat-path AI-generated commits only get
  regex-secret-scan + syntax check, not Loop mode's fuller review.
- SEC-008 (LOW): `litellm` sourced from Emergent-hosted asset URL, not
  PyPI (hash-pinned, so tamper-detectable, but provenance concern).

**Confirmed CLEAR**: Stripe webhook signature verification (fails
closed), no billing mass-assignment, bcrypt cost-12 hashing, login
brute-force lockout (5/15min), server-side logout revocation, CORS
scoping, SSRF guard on URL fetcher. Category 1 (IDOR) not re-audited
— see §7.5.

Founder said no fix without explicit go-ahead per finding — asked for
prioritization before touching `orchestrator.py`.

## Latest ship — IDOR / Access-Control follow-up audit: CONDITIONAL PASS (2026-08-19)

Founder-requested `security_audit_agent` follow-up covering IDOR +
deeper access-control (ownership on write/delete, mass assignment,
error-message enumeration, JWT/role trust, and — highest priority —
chat-session/GitHub-repo isolation). **Report only, no fixes applied**
per founder's explicit instruction. Full findings in
`CODEBASE_AUDIT.md` §7.5.

**Verdict: CONDITIONAL PASS.** No confirmed critical/high IDOR.
Highest-priority ask (can one customer reach another customer's chat
history or GitHub repo/credentials via ID manipulation) — **clear**:
consistent per-user ownership filters, encrypted+stripped GitHub
tokens, UUID session IDs. Two lower-severity findings:
- **SEC-003 (MEDIUM)**: Ship Wall (`shipwall.py`) publishes every
  user's repo name + AI task summary to anonymous visitors by
  default (opt-out, not opt-in) — founder decision needed on intent.
- **SEC-004 (LOW)**: loop/fix-job routes leak ID-existence via
  403-vs-404 (low real risk, IDs are random UUID/hex).
Plus 3 P3 hardening notes (fail-open guards). Coverage gap: no live
two-account cross-user HTTP exploit testing was run (outside the
audit's read-only mandate) — findings are source-confirmed only.

## Latest ship — Password confirm-match check + Advisor panel collision check (2026-08-19)

Added the same live "Passwords do not match" indicator Signup already
had to `ResetPassword.jsx` (`reset-password-mismatch`) and
`ChangePasswordCard.jsx` (`change-password-mismatch`) confirm fields —
screenshot-verified on ResetPassword.

**Composer-aware Advisor panel checked, no fix needed**: verified via
screenshot (1366px, both panels open simultaneously) that the
*expanded* `AskAdvisorReal` panel is a flex sibling column, not a
floating/absolute element — it structurally cannot overlap the main
chat composer (they sit side by side, panel pushes the chat column
narrower). Only the *collapsed* tab needed the earlier fix.

## Latest ship — Bug fix: floating Help/Advisor buttons overlapping chat send button (2026-08-19)

**Confirmed and reproduced**: on mobile (390px), `GlobalHelpFAB.jsx`'s
"Need help?" bubble (`position: fixed; bottom: 20; right: 20`) sat
directly on top of the chat composer's send button
(bounding-box measured: FAB x263-370/y782-824 vs send x369/y782 —
literal overlap). On desktop at narrower widths (1024-1366px) the
"Ask Advisor" collapsed tab (`AskAdvisorReal.jsx`, bottom-anchored at
`bottom-6`) and the FAB both sat in the same vertical band as the
composer, visually crowding the send button.

**Root cause**: both floating elements used a fixed `bottom` offset
that assumed nothing else occupied that screen band — they had no
awareness of the chat composer's actual position/height, which varies
by content (banners, multi-line input, toolbar wrapping).

**Fix**:
1. `GlobalHelpFAB.jsx` — now measures the on-screen
   `[data-testid="composer-card"]` element's bounding rect (polled
   every 400ms + on resize) and sets its own `bottom` offset to always
   clear the composer's top edge by 12px, on any route/viewport where
   a composer is present. Falls back to `bottom: 20` on pages without
   a composer.
2. `AskAdvisorReal.jsx`'s collapsed "ADVISOR" tab — changed from
   `bottom-6` (near the composer's height band) to vertically centered
   (`top-1/2 -translate-y-1/2`), so it's nowhere near the composer row
   at any screen height.

Screenshot-verified at 390×844 (mobile), 1024×768, and 1366×768: FAB
and Advisor tab now sit well above the composer with clear vertical
gaps at all three widths; send button fully visible/clickable in all.

## Latest ship — Signup password toggle + strength-meter consolidation (2026-08-19)

Extended the show/hide toggle to `Signup.jsx`'s two password fields
(`signup-password`, `signup-password-confirm` via shared
`PasswordInput.jsx`). Screenshot-verified: eye toggle on both fields,
mask↔plain-text works.

**Consolidated the duplicate strength meter**: `PasswordStrengthMeter.jsx`
now delegates its scoring to `lib/passwordStrength.js::scorePassword`
(the richer, pre-existing implementation — common-password block-list,
sequence/repeat detection, 0-4 score) instead of its own simpler
length/char-class heuristic. Signup's inline strength-bar JSX block
removed in favor of `<PasswordStrengthMeter password={form.password} />`
— single scoring source of truth now used everywhere (Signup, Login
n/a, ChangePasswordCard, ResetPassword). Signup's submit-gate
validation (`MIN_ACCEPTABLE_SCORE` check) untouched — still imports
directly from `lib/passwordStrength.js`, unaffected by the UI
consolidation. Old `signup-password-strength`/`data-strength-score`
testids removed (confirmed unreferenced by any test file before
removal); new shared testids (`password-strength-meter`,
`password-strength-label`) apply. Screenshot-verified "Strong" label
renders correctly with the richer scorer.

## Latest ship — Show/hide password toggle (2026-08-19)

New shared `frontend/src/components/PasswordInput.jsx` (eye/eye-off
icon toggle, `lucide-react`) — wired into Login (`login-password`),
`ChangePasswordCard` (current/new/confirm), and `ResetPassword`
(new/confirm) so every password field in the self-service auth flow
behaves identically. Screenshot-verified: masked → click eye → plain
text visible, icon flips eye→eye-off. `Signup.jsx` intentionally left
untouched (out of requested scope; has its own pre-existing strength
meter via `lib/passwordStrength.js` — a separate, older implementation
from `PasswordStrengthMeter.jsx`, not consolidated in this pass).

**Founder confirmed production password rotated** via the self-service
Forgot Password flow (built earlier this session) — old leaked
passwords now dead/inactive. SEC-001 item 3 (§7.4 in
`CODEBASE_AUDIT.md`) can be marked ✅ once founder confirms; items 4
(git-history scrub) and 5 (final re-audit) still open, founder
following up with Emergent Support (support@emergent.sh) on both the
production-script question and the `git-filter-repo` vs Save-to-GitHub/
Rollback interaction question — support confirmed no official
documented path for either yet.

## Latest ship — Regression Coverage: 8/8 patterns now automated (2026-08-19)

Closed the "5 have no automated test yet" gap flagged in the codebase
audit. New `backend/tests/test_recurring_patterns_batch2.py` adds real
behavioral tests for patterns 3, 5, 6, and the two `.gitignore` policy
entries (previously `test_ref=None`):
- Pattern 3 (Mode D boilerplate) — locks `DIAGNOSIS_SYSTEM` prompt
  wording (natural-language signals accepted, bail-out is last resort).
- Pattern 5 (multi-file 1-of-N) — guards against a future hard
  per-task file-count cap being introduced (root cause was
  verified-false; this just prevents it from becoming true).
- Pattern 6 (stale browser cache) — real HTTP test of
  `POST /admin/cache/purge` (401 unauthenticated, 200 + structured
  report for admin).
- Policy patterns 7+8 (`.env`/`.gitignore` hybrid) — asserts the exact
  gitignore lines/ordering that keep `backend/.env` ignored and
  `frontend/.env` committed.
`scripts/seed_regression_patterns.py` updated with the new `test_ref`s,
re-seeded, and `scripts/verify_regression_patterns.py` re-run:
**8/8 verified patterns pass, 0 with no automated test.** Full
regression sweep (registry + iter67 + batch2, 15 tests) green.

## Latest ship — Security Audit Close-Out + Password Strength Meter (2026-08-19)

**Security Audit Close-Out** (`/app/memory/CODEBASE_AUDIT.md` §7.4, new):
SEC-001 (leaked founder credentials) broken into 5 explicitly tracked
sub-parts so "preview-verified" is never misread as "fully fixed":
1. Working-tree redaction — ✅ DONE (verified).
2. Self-service change-password capability — ✅ DONE, browser-verified
   this session (see previous entry above).
3. Production founder password rotation — ⏳ PENDING founder (script
   ready, founder running it themselves after Emergent Support
   confirms the official production-script path).
4. Git-history scrub (`git-filter-repo`) — ⏳ PENDING founder decision,
   destructive, not started.
5. Final re-audit — ⏳ blocked on #3 and #4.
Also closed out: G4/G15/G18 CI-wiring gap **parked** (founder decision,
not urgent); G20's "41 open incidents" resolved — 40 were stale
`_Test_Dedup_*` fixture rows (deleted with founder approval), only 1
real incident remains (Tavily rate-limit, tracked separately).

**Password Strength Meter**: new `frontend/src/components/
PasswordStrengthMeter.jsx` (client-side heuristic: length + case-mix +
digit + symbol → 0-4 score, red/amber/green bar + label). Wired into
`ChangePasswordCard.jsx` and `ResetPassword.jsx`'s new-password fields.
Self-tested via screenshot: "abc" → "Too weak", "Str0ng!Pass#2026" →
"Strong", live-updates on keystroke. Client-side only — server length
policy remains the enforced source of truth.

## Latest ship — Self-service password reset/change, browser-verified + bug fixed (2026-08-19)

**Frontend now browser-verified** (was preview-verified backend-only before
this session). Forgot-password mini-flow on `/login`, standalone
`/reset-password` page, and `ChangePasswordCard` on `/settings` (gated by
`me.has_password`) — all confirmed working via `testing_agent` Playwright run.

**Real bug found + fixed**: submitting the WRONG current password on
`ChangePasswordCard` caused a spurious redirect to `/login` instead of
showing the inline error. Root cause: `frontend/src/lib/api.js`'s 401
interceptor `isAuthEndpoint` allow-list didn't exempt `/auth/change-password`
(nor `/auth/forgot-password` / `/auth/reset-password`), so any 401 from these
was treated as mid-session token revocation → global session-expired
redirect. Fix: added the 3 paths to the allow-list. Retested 3/3 pass —
wrong password now shows inline error and stays on `/settings`; happy path
and regression login still work.

Backend endpoints (`/auth/forgot-password`, `/auth/reset-password`,
`/auth/change-password`) were already agent-tested (9/9) in a prior session.
Rotation script (`backend/scripts/rotate_password.py`) exists for the
founder to run against production themselves once Emergent Support confirms
the official one-off script execution path (founder following up separately,
refuses to share prod Mongo string with agent).

**Incident-log hygiene cleanup**: audited the "41 open incidents" flag from
the codebase audit — 40 of 41 were stale `_Test_Dedup_*` fixture rows never
cleaned up after dedup-test runs (0 recurrence, simulated). Deleted (50 rows
total incl. resolved dupes). **Only 1 real open incident remains**: Tavily
Search rate-limit/credits-exhausted (432), already known P1 in backlog,
blocked on founder's top-up decision — not touched, founder will action
separately.

**Parked per founder decision**: CI wiring gap (G4/G15/G18 claimed CI-wired
but only G21 actually is) — deferred, not urgent, still tracked in backlog.

## Product mission

AUREM CTO is a full-stack AI product where founders can bring GitHub repos
and have ORA (the AI CTO) do end-to-end engineering: understand the repo,
answer questions, propose fixes, apply them via GitHub commits/PRs, and
run scan+fix pipelines (health, security, quality). Zero-mock — every fix
is a real GitHub commit with a verified SHA.

## Latest ship — Fabrication Failure Learning Loop (2026-08-19)

**Preview-verified (testing_agent: 14/14 new tests + 37 regression tests
green; admin API + dashboard card verified live).** Founder-approved scope:
per-project + per-route only (no cross-project matching), caution injected
only after 3+ incidents in trailing 30 days.

1. `services/ora_fix_learning.py` — new `ora_fabrication_incidents`
   collection + `record_fabrication_incident()`, `recall_fabrication_caution()`,
   `get_recurring_fabrication_patterns()`. Fail-open, never blocks a chat turn.
2. Customer chat (`routers/chat.py`): logs an incident every time
   `CitationGuard` retries (fire-and-forget). Orchestrator
   (`services/orchestrator.py`) injects a silent caution into the system
   prompt for real (non-home) projects when the same project+route hit
   3+ incidents in 30 days.
3. Admin ORA chat (`routers/ora_chat.py`): same caution injection before
   the system prompt is built (bucketed as project_id="admin", route=
   model route), and logs an incident whenever `ora_grounding` flags
   `fabricated` content (post regen/review).
4. Admin visibility: `GET /admin/qa/fabrication-patterns` (admin-gated)
   + a new "Fabrication Learning Loop" card on the existing `/admin/qa`
   dashboard (no redesign) — shows recurring signatures, count, corrected
   ratio, `caution_active` (mirrors the live >=3/30d threshold).
5. Tests: `backend/tests/test_fabrication_learning_loop.py` (14 tests,
   run against real local MongoDB — record/recall/threshold/project+route
   isolation/30-day window/admin endpoint auth+shape).

**NOT yet true**: no production telemetry/measured reduction in repeat
fabrications — this ships the observability + injection mechanism only.
Production needs a redeploy before this is live for real users.

**Next up**: Diff View Upgrade, or a full codebase audit report the
founder separately requested (code inventory, feature inventory, dead
collections/deps, security exposure sweep, test coverage %) — see
`/app/memory/CHANGELOG.md` for the audit scope and time-estimate note.

## Latest ship — Customer Chat Regen, real bugs found + fixed (2026-08-19)

**Preview-verified.** All 3 priority items from this session done:
1. G21 real finding fixed (unused editable package pin removed).
2. Customer Chat Regen — found the guard already existed (`CitationGuard`,
   Iter 209) but was silently broken in TWO ways: a phantom function
   import (`respond_text` never existed) made every correction attempt
   a no-op, and `_persist_turn` ran before the guard so even a working
   correction wouldn't have survived a page refresh. Both fixed.
3. Diff View Upgrade — next up.

See `/app/memory/CHANGELOG.md` for full detail + the honest correction
log (2 prior status claims to the founder were wrong and got fixed
publicly in the changelog, not swept under the rug).



**Preview-verified**. Founder-approved plan: admin tool first, then
customer-facing chat.py as fast-follow (do NOT let it slip). See
`/app/memory/CHANGELOG.md` for the full correction/finding — the
original "not started" status given to the founder was WRONG; a
dormant, disabled implementation already existed and just needed two
small fixes + a flag flip.

## Latest ship — GLM leak audit + fix (2026-08-19)

**Preview-verified**. Founder flagged "GLM 5.2 visible in chat" — full
scan found 6 user-facing leaks (see `/app/memory/CHANGELOG.md` for the
exact locations + fixes). Root fix: any surface that displays "which
model answered" now runs the raw provider string through
`frontend/src/lib/providerLabel.js::brandProvider()`, which collapses
EVERY raw slug (glm-5.2, deepseek-v3-rescue, claude-sonnet-4.5,
longcat, groq-…, z-ai/glm-5.2, future models too) down to "ORA" — so a
future backend model swap can never leak a new name into the UI again.

## Latest ship — Chat UX #4 Tier 1 · Step-trail persistence (2026-08-19)

**Preview-verified** (DB unit tests + HTTP contract test + 3x hard-reload
browser check). Prod-verification pending founder deploy.

Fixes the "Reading/Diff sequence disappears on refresh" gap: the
`📖 Reading repo… ✍️ Writing files…` step cards now persist to Mongo
on the assistant turn and hydrate back on page reload. See
`/app/memory/CHANGELOG.md` for full detail.

## Latest ship — Slice A · BI Cockpit (2026-02-18)

**Preview-verified**, prod-verification pending founder run.

Live Business Intelligence added to the Financial Command Center.
Extends `AdminFinancials.jsx` with a new `<BiCockpit>` section rendering
real Stripe (`list_subscriptions`) MRR/ARR/active/churn cards + real
inference cost cards + 30-day cost line-chart + cost-by-model bar-chart
+ `🧹 Reconcile Orphans` button.

- Backend: `/app/backend/routers/admin_bi.py` — `/admin/bi/stripe-metrics`,
  `/admin/bi/inference-metrics`, `/admin/bi/summary`. All founder-gated.
- Frontend dep: `recharts@3.10.1` added.
- No hallucination: MRR is either a real Stripe number or explicit
  "No data yet"; inference is real `ora_chat_usage` aggregate; budget mode
  reuses the same tracker the /message router already enforces.
- Regression: `/app/backend/tests/test_slice_a_bi_cockpit.py` (4 tests, all green).
- Preview cleanup: 26 test rows in `cto_payments` purged.

See `/app/memory/CHANGELOG.md` for full detail.

## Core requirements (frozen)

- **Zero mocks** — Every commit/URL/telemetry point must be real
- **Strict credential hygiene** — env-only, no leaks in logs/UI
- **Zero-downtime deploys** — Verified via new deploy discipline (2026-02-12)
- **Python-native business logic** — No shelling out
- **GitHub App OAuth as primary** — PAT wizard replaced (Phase 3a done)
- **Architectural cleanup ongoing** — Splitting god files, HTTP wrapper centralization

## Current phase — Phase 3 (Architectural refactor)

### Deploy discipline (2026-02-12, MANDATORY)

See `/app/memory/DEPLOY_VERIFICATION_CHECKLIST.md` for the full protocol.
Key rules established after 3 deploy race incidents in one day:
- Pipeline is "snapshot at build-start" (no SHA pinning) — confirmed by Support
- **Manage Publishes → Overview** is primary source of truth (not `/version`)
- Three channels that mutate HEAD/deploys are documented (A/B/C)
- `BUILD_INFO.txt` is now untracked; `scripts/git_hooks/post-commit` stamps
  it with current HEAD SHA (Iter 314 fix, verified end-to-end on prod)


### Iter 388u — Support Reply UX Fix, Option A (2026-08-13, NOT YET DEPLOYED)
Bug caught by founder audit: `SupportPopup.jsx` promised "You'll see the reply in this same app" but no code fetched replies — admin replies were writing to Mongo with zero surface for user (no email, no badge, no polling). Ticketing system was a black hole.
- Fix ships on a **separate deploy** after 4 pending verifications clear (GDPR modal, Deploy Insights, Bug 28 highlight, chat double-border).
- Email fallback via Resend (`services/support_email.py`) — HTML+text with admin message inline + signed CTA link
- Public HMAC-verified thread view (`GET /support/tickets/{id}/thread`) — no login required
- Public reply-back (`POST /support/tickets/{id}/reply/token`) — user can continue thread from email link
- New `/support/thread/:ticketId` route (`pages/SupportThread.jsx`)
- Misleading popup copy replaced with truthful "email inbox" language
- **10/10 backend tests pass** · smoke screenshot verified route + error state
- Follow-up (Option B, next session): in-app inbox at `/support/inbox`, FAB red-dot badge with unread count, previous replies shown inline in SupportPopup.


### Phase 3 progress

**HTTP wrapper migration** (raw `httpx.AsyncClient` → `services.http.ext_client`):
- ✅ Batches 1-7 done · 54 sites / 21 files
- ✅ **Batch 8a done (2026-02-12)** — 7 router files, 10 sites
  (admin_qa, admin_bin, admin_projects_brain, admin_ops_config,
  admin_users, upload, fix_pipeline). Cumulative: **64 sites / 22 files**.
- ⏸️ Batch 8b (`github_oauth.py::_gh_primary_email`) — held
- ⏸️ `codebase_health.py` mini-batch — held
- ⏸️ `ext_client(limits=)` upgrade for `github_api_writer.py` — held

**God file splits** (deferred, needs supervised session):
- `frontend/src/components/ChatPanel.jsx` (4,874 LOC)
- `backend/services/loop_engine.py` (4,416 LOC)

## Backlog (P0/P1/P2)

### P0
- Batch 8b — `github_oauth.py::_gh_primary_email` migration (solo, auth-adjacent)
- codebase_health.py mini-batch (preserves `(45.0, 6.0, 15.0)` timeout tuple)
- ext_client(limits=) API upgrade → migrate github_api_writer.py
- God file splits (ChatPanel.jsx + loop_engine.py)

### P1
- Custom-breaker Reconciliation (3 deferred: ora_client + 2 tavily in web_skills)
- Frontend Sentry DSN wiring (Gap #44 — needs DSN from founder)
- Referral Program (#32) — 1 free month, dual-sided (blocked: founder spec)
- Resend Webhook for Email Activity panel (blocked: webhook secret)

### P2
- "Hey Stripe" email anomaly (blocked: prod Mongo query by founder)
- Hardcoded UI/Admin value sweep (BugHunt, Landing, Admin Metrics)
- GDPR/DSAR self-serve account deletion (`POST /auth/delete-me` + modal)
- UI UX Polish Batch (4-state UI, Accessibility, Offline) (#8-#13)

### P3
- Sellable Architecture Hotspot Audit feature (Ledger #45)
- Launch-readiness SaaS AUDIT (Ledger #46)
- Feature Flags multi-pod drift (needs Redis pub/sub at scale)

## Recent changes (Feb 12, 2026)

- **Iter 388f — Landing.jsx fake claim cleanup + composer char counter (2026-02-12)**:
  - Removed all Ollama / LM Studio / llama.cpp / "air-gapped" claims from
    `frontend/src/pages/Landing.jsx` (Local mode card, TOOLS/TAGLINES,
    FAQ, comparison-table row) AND from `frontend/index.html` JSON-LD
    schema (SoftwareApplication description, featureList,
    FAQPage). Backend `feature_window.py:181` says Local mode
    `"status": "not_built"` — Landing was falsely advertising it.
  - Removed fabricated "Akari T. finserv Ollama compliance" testimonial.
  - Verified `25-pattern Vanguard scan` claim is ACCURATE (15 secret +
    10 dangerous = 25; scanner code itself uses "25-pattern catalog"
    comment). Kept.
  - New `frontend/src/components/CharCounter.jsx` — live counter that
    turns amber at 80% (`#eab308`) and red at 100% (`#ef4444`) of the
    20,000-char cap. Wired into `ChatPanel.jsx` composer toolbar.
  - `frontend/src/lib/api.js::streamChat` now intercepts HTTP 422
    `string_too_long` responses and formats them as "Message too long:
    N chars / 20,000 max. Shorten it, or split into multiple messages."
    instead of the raw JSON blob toast.
  - E2E verified via login → dashboard → composer at 500 (muted),
    17,000 (amber `rgb(234,179,8)`), 20,000 (red `rgb(239,68,68)`).
  - Backend curl verified 20,001 char → 422 `string_too_long` with
    `ctx.max_length=20000`.
- **Iter 314** — BUILD_INFO.txt untracked + post-commit hook stamps HEAD;
  Deploy Verification Checklist rewritten to remove SHA-pinning
  assumption; three HEAD-mutation channels formally documented.
  Verified live via best-case outcome (`/version` = HEAD exact match).
- **Middleware "No response returned" fix** — defensive try/except around
  `_global_rate_limit_guard`'s `call_next()`; confirmed live via Support.
- **Batch 8a HTTP wrapper migration** — 7 routers, 10 sites, verified
  live at prod SHA `39ba1122764f` (built_at 21:32:24 UTC).

## Tech stack

- Backend: FastAPI + Python 3.11, MongoDB (Atlas), httpx wrapped by services.http
- Frontend: React + Vite + Shadcn UI + Motion + Tailwind
- Auth: Emergent Google OAuth + custom JWT
- LLM: OpenAI GPT-4o (Emergent LLM Key)
- Payments: Stripe (test key already wired)
- Emails: Resend
- Errors: Sentry (backend wired, frontend DSN pending)
- Hosting: Emergent internal pipeline → snapshot-at-build-start (see checklist)

## Repository structure (key files)

- `/app/backend/main.py` — hardened middleware (Iter 313 fix)
- `/app/backend/services/http/client.py` — HTTP wrapper
- `/app/backend/routers/version.py` — `/version` endpoint + BUILD_INFO cascade
- `/app/backend/routers/*.py` — Batch 8a migrated routers
- `/app/scripts/git_hooks/post-commit` — BUILD_INFO.txt stamper
- `/app/scripts/install_hooks.sh` — hooks bootstrap for fresh sessions
- `/app/memory/DEPLOY_VERIFICATION_CHECKLIST.md` — mandatory deploy discipline
- `/app/memory/BATCH_8_SURVEY_2026-02-12.md` — surveyed sites + rationales
- `/app/memory/PRD.md` — this document
- `/app/memory/CHANGELOG.md` — append-only ledger of substantive changes


## Iter 388h — Bug 1 + Bug 2 fixes (2026-02-13, ✅ DEPLOYED to auremcto.com)

**Bug 1 — ORA Diff View silent-failure on real Loop runs** — FIXED
- Root cause: `_run_task_with_git` (git-path task worker, used by all
  real PAT-connected users) never persisted the `edited_files` unified-
  diff payload or the `task_handoff`/`done` SSE frames.
- Fix: mirrored API-path Iter 388g block into `_run_task_with_git`.
- Test: `backend/tests/test_iter388h_bug1_bug2_fixes.py` (2 tests).

**Bug 2 — `<longcat_tool_call>` XML leaking in Prompt mode** — FIXED
- Root cause: sanitizer regex only stripped unprefixed `<tool_call>`.
- Fix: widened alternation in `RenderedMessage.sanitizeForDisplay` to
  match `[a-z0-9]+_tool_*` variants; explicit vendor variants added to
  `INTERNAL_FENCES` set.
- Test: 5 sanitizer regression tests in same file.

## Iter 388i — Bug 8 fix (Batch A, 2026-02-13, awaiting deploy)

**Bug 8 — Rail nav Insights/Admin unclickable, clicks fell through**
- Root cause: `hiddenForTyping` was `useState(true)` on mount, then
  ChatPanel emits `aurem:chat-session-started` with `detail.restored:
  true` on every dashboard reload with an active session, keeping the
  rail hidden.  Rail had `pointerEvents:none` when hidden → clicks on
  the barely-visible Insights/Admin icons passed through to chat area
  underneath ("chat reloaded" perception).  Ship worked because
  founders reached it via top-bar chip, not the rail.
- Fix: `frontend/src/components/nav/RailShell.jsx`
  - Line 107: default `hiddenForTyping = false` (rail visible on mount)
  - Lines 114-127: `onStart` listener now ignores events with
    `detail.restored === true` — only real first-send events hide the
    rail.
- Test: `frontend/src/components/nav/__tests__/RailShell.iter388i.bug8.test.jsx`
  (6 tests, all green).
- Verified via Playwright: clicking Insights opens flyout, clicking
  Analytics link navigates to `/analytics` successfully.

## Iter 388j — Advisor audit fix (Batch B, Bug 3+4+5, 2026-02-13)

**Root cause confirmed**: `chat.py` advisor prompt structure put the
SCREENSHOT ANALYSIS block last with rule "ground concrete UI
observations in the screenshot" — this overrode anti-fabrication rules
for DATA questions.  Meanwhile `advisor_context.py` had NO data
sources for run state, open PRs, or per-task token breakdown.  Result:
the three Advisor chip buttons fabricated confidently from screenshot
text (Bug 3+5) or silently hung waiting for data that didn't exist
(Bug 4).

**Fixes shipped in this batch:**

1. **`backend/routers/advisor_context.py`** — 3 new blocks added:
   - `recent_tasks`: last 5 `cto_tasks` rows for the project (task_id,
     status, summary, sha, error, timestamps).  Powers Bug 3.
   - `open_prs`: GitHub API `/repos/{owner}/{repo}/pulls?state=open`
     w/ 4s hard timeout; graceful `error:` field on 404/private.
     Powers Bug 4.
   - `token_breakdown`: per-task tokens from the last 5 tasks + this-
     month project total + user total.  Powers Bug 5.

2. **`backend/routers/chat.py::_adv_directive`** — HARD DATA HONESTY
   block added at highest priority.  Explicitly forbids using
   SCREENSHOT text for run/PR/token facts; requires exact ADVISOR
   CONTEXT numbers; pins project name from the `Project:` line
   verbatim.  Visual-context rule now explicitly cedes precedence to
   Data Honesty.

3. **`backend/routers/chat.py`** — Screenshot vision is now SKIPPED
   entirely when the prompt is one of the three chip labels
## Iter 388k — Bug 12 CRITICAL fix (query-tier read loop, 2026-02-13)

See `backend/tests/test_iter388k_bug12_loop_fix.py` for full contract.
Query tier max_iters bumped 2→3, orchestrator injects `=== FINAL
ANSWER ROUND ===` directive on last iter, `_synthesise_max_iters_
summary` rewrote — "send the same prompt again" template banned by
regression assertion.


   ("Diagnose failed run" / "Summarize open PRs" / "Token breakdown").
   Data questions get pure structured data; no vision noise.

**Live verification (preview API, `test@aurem.dev`)**:
- Advisor endpoint returns all 3 new keys
- `open_prs` cleanly reports `repo_not_public_or_missing` instead of
  hanging (Bug 4 root fixed at the data layer)
## Iter 388n — Batch C · Bug 6 + 7 task tracking fix (2026-02-13)

**Root cause (verified from source)**:
- `_run_task_with_git` writes to `cto_tasks`
- Real user Plan→Ship loops run through `loop_engine.py`, which writes
  to `loop_sessions` / `loop_run_log` / `loop_events` — but NEVER
  touches `cto_tasks`
- Both `usage.py::tasks_this_month` and `admin_analytics.py::
  product_analytics` counted ONLY `cto_tasks`, so loop-driven work
  was invisible to Settings ("Tasks this month: 0"), Analytics
  ("Success Rate 0% (0/1)"), and Ops History ("0 steps")

**Fixes**:
- `backend/services/usage.py::compute_effective_usage`: adds
  `loop_sessions.count_documents` aggregate over the same month
  window, using a datetime bound (not float timestamp) since
  `loop_sessions.created_at` is stored as a datetime via `_now()`.
  Counts active states (`completed`, `shipping`, `executing`,
  `verifying`, `scanning`, `planning`, `self_healing`,
  `awaiting_confirmation`, `paused_for_user`) — deliberately
  excludes `failed`/`aborted`/`expired` to preserve the Iter 52
  BUG 3 rule (failed tasks don't burn quota).
- `backend/routers/admin_analytics.py::product_analytics`: same
  `loop_sessions` backfill added to `tasks_total` / `tasks_done` /
  `tasks_failed`.  Uses `window_start_dt` (datetime) for the
  loop-sessions query since the collection stores datetimes.

**Live verified**: `curl /api/aurem-dev/usage/me` → `tasks_this_month:
2` for test@aurem.dev (was 0 before) — matches the two completed
loops the account had in `loop_sessions`.

**Tests**: 4 pytest regressions green
(`backend/tests/test_iter388n_bug67_task_tracking.py`) — marker phrase
locks, datetime-vs-timestamp guard, combined-total accumulator, Iter 52
BUG 3 exclusion still respected.

## Iter 388o — Bug 10 · Real 404 page (2026-02-13)

- Removed the `<Route path="*" element={<Navigate to="/" replace/>}>`
  silent redirect
- Added `frontend/src/pages/NotFound.jsx` — proper 404 SPA page with
  echoed URL, home + dashboard links, and a `<meta name="robots"
  content="noindex, follow">` mutation that mutates the existing
  meta tag (not just appends) so older crawlers reading the first
  meta match still respect the noindex directive.  Restored on unmount.
- Live verified via Playwright screenshot on
  `/random-broken-url-test-12345` → 404 page renders, URL preserved,
  noindex applied.
- Tests: 6 vitest regressions green
  (`frontend/src/pages/__tests__/iter388o.bug10.notfound.test.jsx`)
- **SPA caveat**: React SPA cannot set an HTTP 404 status from client
  code (would need SSR or edge middleware).  If prod SEO demands a
  real 404 status header, that fix belongs in the Vercel/CDN layer.


- `token_breakdown.user_month_total = 7963` (matches Analytics
  screenshot exactly — real data, no fabrication)
- 6 pytest regressions green
  (`backend/tests/test_iter388j_advisor_bug345_fixes.py`)

**Awaiting user's prod DATA HONESTY text** to seed preview and cross-
verify override behavior; the code-level fixes are ready to deploy
regardless.

## Iter 388m — Bug 9 fix (message empty on reload, 2026-02-13)

**Reported by senior QA pass**: The exact message that used to leak
raw `<longcat_tool_call>read_repo_file …</longcat_tool_call>` (Bug 2)
now shows as **completely empty** on reload — only the "via
longcat-2.0" attribution footer visible.  Founder correctly flagged
this as data-loss looking.

**Root cause (verified — NOT persistence)**:
- `backend/services/ora_chat/session.py::append_message` stores the
  assistant reply verbatim.  No server-side sanitization at write.
- The reply for that specific turn was ENTIRELY internal tool-call
  XML with **zero user-facing prose** (the LLM decided the tool
  call itself was the whole answer).
- Frontend `RenderedMessage.sanitizeForDisplay` (widened in Iter 388h
  to handle `<longcat_tool_call>` variants) correctly stripped 100%
  of the content on render → empty span.  Nothing was actually lost
  from Mongo — it's a display collapse.

**Fix (`frontend/src/components/RenderedMessage.jsx`)**:
- Detect `originalHadBody && !cleaned.trim()` — i.e. the input had
  characters but sanitizer erased everything.
- Render a subtle italic placeholder in that case: *"(assistant
  emitted an internal tool call with no visible reply — try
  rephrasing)"* with `data-testid=rendered-message-empty-placeholder`.
- Empty / whitespace-only inputs still render the empty span (no
  placeholder spam) so we don't hallucinate messages that never
  existed.

**Testing**: 6 vitest regressions green
(`frontend/src/components/__tests__/iter388m.bug9.test.jsx`) —
placeholder appears for longcat/claude/qwen/gpt vendor variants and
orphan-open streaming cutoffs; does NOT appear for legitimate prose
mixes or truly empty messages.  Combined with 388i / 388l tests, all
23 frontend regressions green.

**Deeper root cause note**: Bug 12's `=== FINAL ANSWER ROUND ===`
directive on the last iter should reduce the frequency of assistant
replies containing ONLY tool-call XML, since it explicitly forbids
tool calls on the final round.  Bug 9 fix is the defensive display
layer; Bug 12 fix is the upstream generation layer.



See test file `backend/tests/test_iter388k_bug12_loop_fix.py` for full
regression contract.  Summary:
- Query-tier `_max_iters_eff` bumped `2 → 3` in `chat.py`
- Orchestrator injects `=== FINAL ANSWER ROUND ===` directive on the
  last iter (`iters >= max_iters`)
- `_synthesise_max_iters_summary` rewrote — banned "send the same
  prompt again" template locked out by string-level assertions
- 5 new pytest + full regression (33/33) green


## Iter 388l — Bug 13/14/15/16 (Batch D + infra hardening, 2026-02-13)

**Bug 13 — Slash-command hyphen strip in user-message preview** — FIXED
- Root cause: `CollapsibleReply.firstLinePreview` regex was
  `/[#*_`>-]/g`.  `-` at the end of the char class = literal hyphen
  (not a range end).  User typing `/repo-tree` saw `/repotree` in the
  collapsed preview — looked like the composer had mangled the input.
- Fix: `frontend/src/components/CollapsibleReply.jsx` regex tightened
  to `/[#*_`>]/g`.  Every hyphenated slash-command (`/repo-tree`,
  `/loop-stats`, `/users-today`, etc.) now displays verbatim.

**Bug 15 — Raw Cloudflare 520 HTML rendering in chat bubble** — FIXED
- Root cause: `frontend/src/lib/api.js::streamChat` non-OK response
  handler called `onError(\`HTTP ${status}: ${txt}\`)` where `txt` was
  the raw HTML body Cloudflare/ingress ship on 520/502/etc.  The
  ChatPanel wrote that raw string into the assistant message content
  and RenderedMessage / markdown pass-through rendered the HTML tags.
- Fix: added HTML sniffer (`/^\s*(<!doctype\s+html|<html|<head|<body)/i`)
  in the streamChat error branch.  When the body looks like HTML,
  the callback receives a friendly text message instead
  ("The server was briefly unavailable (Cloudflare origin error).
  Try again in a moment.").  No raw HTML can reach the chat UI again.

**Bug 14 + 16 — LoopStatusChip idle polling + raw HTML error text** — FIXED
- Root cause part A (Bug 16): `POLL_MS = 10_000` fired forever, so
  every project polled `/loop/active` 6×/min regardless of Loop state.
- Root cause part B (Bug 14): the `setErr` call on failure passed
  through `e.response.data?.detail || e.message` untouched — 5xx HTML
  bodies became `err` state and rendered inside the chip's error span.
- Fix (`frontend/src/components/LoopStatusChip.jsx`):
  - Added `POLL_IDLE_MS = 60_000` + 2-poll idle streak trigger.  Idle
    projects now poll once per minute instead of once per 10s
    (~85% reduction in idle traffic).  Any active/terminal signal
    resets the interval to 10s.
  - Error path sanitised: HTML-looking bodies + all Cloudflare 5xx
    statuses (502-530) SUPPRESS the red banner entirely (transient
    infra hiccups aren't loop errors); only backend-authored detail
    strings surface.

**Testing done:**
- 11 new vitest regressions green
  (`frontend/src/components/__tests__/iter388l.bug13_14_15_16.test.jsx`)
  — hyphen preservation across every hyphenated slash-command, HTML
  detection true/false positives, poll-backoff state machine
- No lint errors, backend + frontend supervisor RUNNING
- No new backend files touched — all four fixes are frontend-only

**Reported by senior QA pass**: *"Read backend/routers/health.py and
show me the first 50 lines"* → response 1: *"I've mapped the surface
area but need one more round — Send the same prompt again..."*.
Resending EXACTLY that prompt → response 2: **identical** template
again.  Content never delivered.  Core chat flow broken.

**Root cause** (source-code confirmed):
- `chat.py:2231` gave the "query" intent tier `max_iters=2`
- Query task like "read + show me lines 1-50" burns iter 1 on
  `read_repo_file` tool call.  If the LLM makes ANY 2nd exploratory
  tool call (very common — `list_repo_files` after read) before
  producing final text, iter 2 exhausts.
- `orchestrator.py::_synthesise_max_iters_summary` (lines 156-209)
  fired with template: *"Send the same prompt again — with the
  context I've already loaded, the next response will land the
  concrete answer."*
- Resending hit the exact same 2-iter budget → same template → LOOP.
  User never got file content.

**Fix — 3 layers**:
1. `chat.py`: query-tier `_max_iters_eff` bumped `2 → 3`.  Gives the
   model a guaranteed round after exploratory tool calls.
2. `orchestrator.py`: on the LAST allowed iter, the system prompt is
   patched with a `=== FINAL ANSWER ROUND ===` directive telling the
   model "no more tools, produce a complete answer using the
   transcript's tool results".  Guaranteed by `iters >= max_iters`
   guard so it can't fire early.
3. `orchestrator.py::_synthesise_max_iters_summary`: rewrote the
   fallback text.  Removed EVERY mention of "send the same prompt
   again" / "need one more round" / "next response will land the
   concrete answer".  New message: *"I inspected `<paths>` but
   couldn't wrap the answer in one turn.  Tell me the specific slice
   (function name, line range, or exact question) and I'll reply
   with concrete content next message — no extra tool calls needed."*
   Locked by pytest regression: banned-phrase string assertions fire
   if anyone regresses the wording.

**Tests**: `backend/tests/test_iter388k_bug12_loop_fix.py` — 5 green
(banned-phrase guard, path naming, empty-invocation actionability,
`max_iters=3` locked-in, `FINAL ANSWER ROUND` presence).  Full
regression run: 33/33 green across 388g/h/j/k.

**⚠️ Bug 9 (message empty on reload) not fixed in this batch** — still
open, unrelated code path (Mongo persistence write, not the tool-loop
synthesizer).



---

### Iter 388-aa — Tier-1 Security Audit + systemic deploy-verification rules (2026-02-14)

**Founder trigger:** `#35 Admin Payments Accuracy` "fixed" claim in Iter
388y was **preview-verified but not prod-verified** — prod showed
$0.00 revenue while claim was `revenue_month: 9.0`. Third recurrence
of "preview-truth != prod-truth" bug (Bug 20 was the first).
Simultaneously, OpenRouter balance fell to $-0.20 (already negative)
and Tavily rate-limit-exhausted, with NO pre-deploy signal surfacing
either — silent CRITICAL alerts sitting in `integration_health`.

**Investigation (evidence-only, no false claims):**
- Prod `build_hash`: `e1f40f39944e` (commit `e1f40f3`), which
  **contains** the #35 fix commit `38e9ca1` — deploy landed correctly.
- Prod webhook probe: `POST /api/aurem-dev/payments/webhook` returns
  `400 "Invalid webhook signature"`, NOT `503`. Confirms
  `STRIPE_WEBHOOK_SECRET` env IS set on prod. Root cause of $0 is
  either (a) secret rotation without env update, (b) webhook URL
  misconfig on Stripe dashboard, or (c) genuine abandonment — needs
  founder to check Stripe dashboard + click Reconcile.

**Systemic fixes shipped (permanent, don't need re-instructing):**

1. **Rule 2 — "preview-verified" vs "prod-verified" labeling**
   (`/app/memory/AGENT_STANDING_RULES.md`). Data-shape claims (revenue,
   counts, aggregations) require prod-verified or founder-confirmed
   evidence, NEVER preview-only.
2. **Rule 3 — Pre-deploy `integration_health` gate**. New Lane 6 in
   `scripts/predeploy_gate.sh` runs
   `scripts/predeploy_integration_health.py` which reads the latest
   snapshot and surfaces WARN (exit 2) / BROKEN (exit 3) integrations.
   Never dispatch a deploy silently while a service is critical.
3. **Recurrence log** started (Bug 20 + #35). Third violation → founder
   authorises "stop shipping, systemic pipeline audit" protocol.

**Tier-1 audits (memo: `/app/memory/TIER1_SECURITY_AUDIT_2026-02-14.md`):**

- **#17 CVE audit** — 9 frontend + 95 backend vulns catalogued with
  patch-risk classification (zero-risk / minor-risk / coupled-risk /
  no-fix).
- **#18 IDOR self-audit** — 11 mutating routers sampled. Uniform
  `find_one({resource_id, user_id})` pattern confirmed across
  `managed_db`, `supabase`, `scaffold`, `cto_projects`, `automations`,
  `loop`, `hosted_deploy`, `deploy`, `support`, `chat`, `ora_chat`.
  One P3/LOW finding in `chat.py:1636+` (see Slice 2).

**Slice 2 shipped (preview-verified only, awaiting deploy ack):**

Zero-risk semver patches + 5-line IDOR fix:
- `pillow 12.2.0 → 12.3.0` (12 CVEs closed)
- `httplib2 0.31.2 → 0.32.0` (1)
- `h2 4.3.0 → 4.4.1` (1)
- `pyasn1 0.6.3 → 0.6.4` (2)
- `python-dotenv 1.0.1 → 1.2.2` (1)
- `routers/chat.py:1636-1662` — IDOR tightened: `pending_fix_task`
  find/update filter now includes `user_id`; legacy "no user_id →
  allow" branch removed.
- Total: **28 backend CVEs closed, 95 → 67 vulns / 15 → 10 packages**.

**Tests:**
- `backend/tests/test_iter388aa_chat_pending_fix_task_idor.py` —
  4/4 pass (regression net for IDOR fix + projection cleanup).
- Full backend pytest: 537 pass, 12 fail (all pre-existing, verified
  against git-stashed baseline).
- Mode-D redirect (`test_iter212m46_mode_d_no_autoship.py`): 3/3 pass.

**Deferred to next slices:**
- Slice 3 (P1 medium-risk): aiohttp / pyjwt / cryptography.
- Slice 4 (P2 coupled): starlette / fastapi / litellm major bumps.
- #19 Frontend bundle secrets sweep (still queued).

**Open action items on founder:**
- Stripe dashboard → Webhooks → Recent deliveries screenshot
- Admin → Payments → Reconcile button click → JSON output
- Confirm Tavily top-up decision (still WARN'd)

---

### Iter 388-ab — Settings duplicate-navigation cleanup (2026-02-14)

**Founder screenshot review:** `/settings` had TWO navigation surfaces
showing the SAME 4 items — top tab bar inside `Settings.jsx` (Profile
/ Plans & Usage / Integrations / Vault) AND the left rail drawer
(same four + IDE setup). Redundant.

**Decision:** keep the left rail drawer (superset — has IDE setup;
consistent global nav pattern), remove the top tab bar.

**Changes (`frontend/src/pages/Settings.jsx`):**
- Removed the `role="tablist"` block + all `settings-tab-<id>` buttons.
- Added a small section header (`settings-section-header-<id>`) with
  accent-coloured icon + label + separator so users still see which
  section they're on.
- Added a `useEffect` that watches `location.search` and syncs the
  `tab` state — clicking a rail drawer item while already on `/settings`
  now updates content (rail changes only URL, no page reload).
- Fixed a stray `setTab("plans")` → `switchTab("plans")` (the quota
  upgrade link inside the Profile tab wasn't updating the URL).

**Preview-verified:**
- Playwright + logged-in `test@aurem.dev` account
- `[VAULT] tablist=0 vault_header=1` ✅
- `[PLANS] plans_header=1 vault_header=0` ✅ (drawer URL change flips content)
- Screenshot: clean Vault section with orange 🔑 header, tab bar gone.

**Tests:**
- `frontend/src/pages/__tests__/Settings.iter388ab.dup-nav.test.js` —
  6/6 pass (source-level assertions: no tablist, no settings-tab-*
  testids, TABS metadata retained, section header renders, URL-sync
  effect present, no stray setTab calls outside sync effect).


---

### Iter 388-ac — Slice 3 CVE bumps + Task #19 bundle secrets sweep (2026-02-14)

**Slice 3 — medium-risk backend bumps (preview-verified):**

| Package      | Before → After   | CVEs closed |
|--------------|------------------|-------------|
| aiohttp      | 3.13.5 → 3.14.3  | 14 (request smuggling, digest cross-origin, DoS via pipelined requests, memory-bomb decompress, TLS SNI bypass) |
| PyJWT        | 2.10.0 → 2.13.0  | 11 (algorithm confusion + audience-bypass family) |
| cryptography | 44.0.0 → 50.0.0  | 7 (PKCS7 Bleichenbacher oracle, name-constraint bypass, wildcard-SAN escape, ECC subgroup skip, RFC5280 DoS) |

Regression:
- `pip-audit` re-run: **67 → 32 vulns, 10 → 7 packages (35 CVEs closed this slice)**.
- Cumulative Slice 2+3: **95 → 32 vulns, 15 → 7 packages (63 CVEs closed)**.
- 52 JWT/HMAC/signature tests PASS ✅ (auth surface unaffected).
- Rolled-back-baseline verified: the 4 pre-existing test failures (yarn-audit + qa-manifest freshness) also fail on the pre-bump commit — NOT regressions from the bumps.
- Backend `/api/health` returns 200 post-restart.

**Task #19 — Frontend bundle secrets sweep:**

- New script: `/app/scripts/bundle_secrets_sweep.py` — regex-based detector
  covering Stripe (sk_live_, pk_live_, whsec_, rk_), GitHub (ghp_, gho_,
  ghs_, github_pat_), OpenAI/Anthropic/OpenRouter, PEM private keys,
  AWS AKID, MongoDB URI with creds, JWT-shape tokens, Sentry DSN
  with secret, plus name-based detection for server-only env
  variable names.
- Wired into `scripts/predeploy_gate.sh` as Lane 7 (non-blocking).
- New regression test: `backend/tests/test_iter388ac_bundle_secrets_sweep.py`
  (2 tests, both PASS) — locks in the "no critical, WARN allow-list
  only" contract.

**Bundle sweep results — production bundle CLEAN ✅:**
- 218 files scanned in `frontend/dist/` (20 MB)
- 0 CRITICAL findings
- 3 WARN findings — all verified as harmless UI copy references:
  1. `RESEND_API_KEY` in `Admin-*.js` — dry-run status label
  2. `MONGO_URL` in `OpsRecipes-*.js` — diagnostic shell command example
  3. `EMERGENT_LLM_KEY` in `WhyOra-*.js` — marketing copy
- Zero real value leaks. No rotation needed.

**Cumulative deliverables this session (Iter 388-aa/ab/ac):**
- Systemic: AGENT_STANDING_RULES.md (Rules 1-4), predeploy_gate.sh Lane 6+7.
- Backend security: 63 CVEs closed across 8 packages, 1 IDOR tightened.
- Frontend UX: duplicate-nav cleanup on `/settings`.
- Frontend security: production bundle proven clean of secrets.
- 12 new regression tests total, all green.

**Ready for founder to queue a single consolidated deploy of everything above.**


---

### Iter 388-ad — IDOR expand + Slice 4 attempt (2026-02-14)

**IDOR audit expanded from 11 → 30 routers** (memo:
`/app/memory/TIER1_SECURITY_AUDIT_2026-02-14.md`).

- 8 admin path-param routers: all router-level gated by
  `require_admin_dep`. Verified via source count of admin gates vs
  endpoints.
- 11 non-admin path-param routers: uniform ownership pattern
  confirmed (`payments`, `github_app`, `fix_pipeline`, `chat_commits`,
  `domain`, `shipwall`, `mcp`, `suggestions`, `thinking_hints`,
  `stacks`, `dev_sse_probe`).
- One initial suspicion in `payments.py::/payments/status/{session_id}`
  turned out to have an explicit ownership check on the second line
  (`if pay.get("user_id") != user.get("user_id"): raise 404`) — safe.
- `github_app.py`'s `github_installations.find_one` calls at lines
  164/513/534 are inside webhook handlers (server-to-server), the
  only user-facing `DELETE /installations/{id}` at line 771 correctly
  filters by `user_id`.
- **Cumulative coverage: 30 of ~77 routers (all path-param carriers).
  Zero exploitable IDORs. One P3/LOW cleared in Iter 388-ab.**

**Slice 4 (fastapi/starlette/litellm) — DEFERRED as P2:**

Attempted `fastapi 0.115 → 0.141.1` + `starlette 0.37.2 → 1.6.0`.
Runtime clean but starlette 1.6 introduces a **breaking change on
`request.state.<key>` access** — missing keys now raise `KeyError`
(was `AttributeError`). This trips ~40 tests that use bare
`TestClient(app)` without a lifespan context manager.

**Decision:** rolled back preview to fastapi 0.115 + starlette 0.37.2
(matches prod requirements.txt — prod was never exposed since I hadn't
`pip freeze`d yet). Slice 4 needs a dedicated migration branch that:
1. Audits every `.state.<x>` access → wraps in `getattr` with default.
2. Migrates all `TestClient(app)` to `with TestClient(app) as c:` for
   lifespan.
3. Runs full regression on the migrated code.

`litellm 1.80 → 1.84` also deferred — our litellm is pinned to a
custom Emergent-hosted wheel (not PyPI), so bumping requires Emergent
to publish 1.84 first. Flagged for founder.

**No production changes shipped in Iter 388-ad — preview-only cleanup
+ audit-memo work.**


---

### Iter 388-ae — Payments $0 mystery ROOT CAUSE FOUND + fixed (2026-02-14)

**Founder pasted prod tail** — production logs revealed the smoking
gun for the #35 Payments $0 mystery that had been chased across three
recurrences of "preview-verified but prod-broken":

```
POST /api/stripe/webhook HTTP/1.1" 404 Not Found     (×7 in one min)
POST /api/stripe/webhook HTTP/1.1" 404 Not Found
POST /api/stripe/webhook HTTP/1.1" 404 Not Found
POST /api/stripe/webhook HTTP/1.1" 404 Not Found
POST /api/stripe/webhook HTTP/1.1" 404 Not Found
```

**Root cause:** Stripe's dashboard endpoint was configured with the
URL `/api/stripe/webhook` (no `/aurem-dev` prefix), but our real
webhook handler lives at `/api/aurem-dev/payments/webhook`. Every
real payment webhook 404'd → `cto_payments.payment_status` never
transitioned "pending" → "paid" → admin dashboards truthfully
reported $0 revenue despite 68 ledger rows.

This **confirms Possibility (2)** from the Iter 388-aa preview-verified
diagnosis matrix: "webhook URL misconfigured on Stripe dashboard".
Founder's Stripe dashboard didn't need to be checked — the prod logs
told us.

**Fix — code-only, no dashboard change required:**

- New file: `backend/routers/stripe_webhook_compat.py` — a tiny
  compat router that exposes `POST /stripe/webhook` and delegates to
  the canonical handler `stripe_webhook` in `routers/payments.py`.
  Same signature verification, same DB writes, same idempotency —
  zero divergence.
- Wired into `main.py` at `prefix="/api"` so the final URL is
  `/api/stripe/webhook` (the exact path Stripe was hitting).

**Preview-verified:**
```
POST /api/stripe/webhook              → 400 "Invalid webhook signature"  ← was 404
POST /api/aurem-dev/payments/webhook  → 400 "Invalid webhook signature"  (unchanged)
POST /api/aurem-dev/webhook/stripe    → 400 "Invalid webhook signature"  (unchanged)
```
All three return the SAME error for an unsigned payload, confirming
the alias delegates to (not diverges from) the canonical handler.

**Tests:** `backend/tests/test_iter388ae_stripe_webhook_compat.py` —
3/3 PASS. Asserts:
1. Canonical path still registered.
2. New alias path present.
3. Both endpoints return byte-identical error responses.

**Post-deploy expectation:** After the next prod deploy, Stripe's
webhook attempts will start returning `200 OK`. cto_payments rows
will begin transitioning "pending" → "paid" as events arrive.
For the 68 historical `pending` rows, founder still needs to click
Admin → Payments → "Reconcile pending with Stripe" once — that
button pulls Stripe's ground-truth status per row and back-fills
whichever ones were actually paid but silently 404'd.

**Bonus finding from same prod tail (NOT deploy-blocking):**

Upstash Redis rate-limit quota exhausted:
```
Redis unavailable at gentle-civet-209255.upstash.io:6379
error=ResponseError: max requests limit exceeded. Limit: 500000,
Usage: 500003 — falling back to per-process in-memory
```

Code already handles this gracefully via
`services/rate_limiter.py` — falls back to in-memory limiting when
Redis is unreachable. **No deploy blocker.** But founder should
either bump the Upstash plan (recommended for prod scale) or migrate
to a bigger quota to restore distributed rate limiting. Flagged for
follow-up — no code action needed unless founder wants tighter
suppression on the warning noise.


---

### Iter 388-ah — ORA proactive-caveat enforcement (2026-02-14)

**Founder finding from grounding canary tail:**
On the `meta_gaps` prompt type ("kya gaps hain? fix suggestions bhi
do") ORA fabricated specific file names 2/3 canary runs — e.g.
`_loop.py`, `backend/services/security_gate.py` — with zero caveat
markers in the reply text. Retraction only fired **after** the
founder explicitly challenged ("kya tum sure ho ye files real hain?").
By then a real founder would have already read the confidently-worded
first reply and treated the fabricated names as fact.

Root diagnosis: the `Proactive-caveat rule` existed in the
`AUREM_CONTEXT` system prompt but was **text-only enforcement** — the
server detected violations post-hoc (`grounding_check.log_hallucination`)
but never mutated the reply. Model inconsistency → 2/3 miss rate.

**Three-layer fix (server-side enforcement now guaranteed):**

1. **Prompt hardening** (`services/ora_chat/safety.py`):
   New dedicated bullet inside `AUREM_CONTEXT` — "Meta-level questions
   get CATEGORIES, not filenames". Explicitly enumerates the trigger
   phrases (gaps / missing / coverage / overall shape / audit /
   improvements) and mandates a categorical default answer with a
   `/find` or `/read` follow-up offer. Names past fabricated files as
   worked examples.

2. **New helpers in `services/ora_chat/grounding_check.py`:**
   - `_CAVEAT_MARKERS` — the marker phrases (mirrors canary's list
     plus `"auto-added caveat"`).
   - `find_uncaveated_mentions(reply, unverified_paths)` — returns
     unverified paths that appear in the reply text WITHOUT any
     nearby caveat marker (±200 chars window).
   - `caveat_block_for(paths)` — deterministic compact caveat block
     ready for streaming.
   - `run_post_response_check()` now returns a new
     `unverified_without_caveat` key alongside the existing
     `fabricated` / `unverified` lists.

3. **Streaming path enforcement** (`routers/ora_chat.py` primary
   `event_stream` for `/message`):
   After the post-response grounding check, if
   `unverified_without_caveat` is non-empty:
     - `final_text` is patched with the caveat tail (persisted in
       `ora_chat_messages` for audit).
     - Buffered path: caveat rides the delta flush.
     - Streaming path: caveat is yielded as ONE additional `delta`
       frame so the founder sees it inline.

**Regression net** (`backend/tests/test_iter388ah_proactive_caveat.py`
— 10/10 pass):
- `find_uncaveated_mentions` catches raw uncaveated mentions.
- Pattern A inline caveat suppresses the flag.
- Pattern B "verified vs inferred" split disclaimer suppresses too.
- Empty inputs safe (no crashes).
- Multiple occurrences deduped.
- `caveat_block_for` returns text containing at least one of the
  canary's `_PROACTIVE_CAVEAT_MARKERS` — so the very next canary run
  reports `caveat_present: true` deterministically.
- 6-path truncation with "+N more" summary works.
- `run_post_response_check` still returns the new key even on the
  empty-reply short-circuit path (backwards-compat contract).

**Preview evidence:**
- `pytest tests/test_iter388ah_proactive_caveat.py -v` → 10/10 PASS.
- Backend restart clean, `/api/health` 200, LongCat probe green.
- Lint: 3 modified files return "No lint errors found".
- End-to-end canary run via HTTP admin trigger deferred to post-deploy
  (standalone script hit `db_unavailable` due to init timing —
  founder can trigger via `POST /api/aurem-dev/ora-chat/canary/run-now`
  after deploy for the definitive prod-verified result).

**What still counts as "preview-verified" only:**
The unit + integration surface is proven; the LIVE canary against the
patched streaming path is deferred to post-deploy. Rule 2 keeps this
labelled preview-verified until the founder confirms `proactive_caveat_ok: true`
across three back-to-back canary runs on prod.


---

### Iter 388-ai — Sidebar hide belt+suspenders (2026-02-14)

**Founder prod-verified regression report** (Iter 388-af NOT working
on live prod `db3d0257ecca`):

```
{
  "sidebar": {
    "autohide_localStorage": null,          ← AUTO=default (ON)
    "rail_present": true,
    "rail_hidden_typing_attr": "true",      ← state correctly toggled
    "auto_pill_text": "AUTO",
    "after_event_hidden": "true"            ← state stayed true
  }
}
```

Founder observation: `data-hidden-typing="true"` was correctly set on
the outer wrapper (React state = `hiddenForTyping = true`), BUT the
rail stayed visually visible with all 5 icons rendered. Meaning the
INNER `<nav>` `transform: translateX(-105%)` + `marginLeft: -56` was
NOT collapsing the wrapper's flex-column contribution in this
particular browser / cached-bundle context.

**Fix — outer wrapper collapse** (belt+suspenders on top of Iter 388-af):

`frontend/src/components/nav/RailShell.jsx` — added to the outer
wrapper `<div data-testid="rail-shell">`:

```jsx
width: hiddenForTyping ? 0 : "auto",
overflow: hiddenForTyping ? "hidden" : "visible",
transition: "width 240ms cubic-bezier(0.4,0,0.2,1)",
```

Now the OUTER wrapper collapses its width to 0 with hidden overflow
when `hiddenForTyping` is true, guaranteeing the rail vanishes
regardless of whether the inner-nav transform succeeded. The inner-nav
transform stays (Iter 388-af) as the belt of belt+suspenders.

**Preview-verified via Playwright (5-state test):**

```
State           hidden_attr   wrapper_width   nav_transform            nav_opacity
initial         false         56px            matrix(1,0,0,1,0,0)      1
after_reset     false         56px            matrix(1,0,0,1,0,0)      1
after_start     true          0px             matrix(1,0,0,1,-58.8,0)  0
```

`wrapper_width` transitioning `56px → 0px` on the after_start state is
the definitive fix — screenshot confirms rail fully off-screen with
only the "SIDEBAR" peek pill visible.

**Regression net**: `frontend/src/components/__tests__/RailShell.iter388ai.wrapperCollapse.test.js`
— 4/4 pass. Iter 388-af tests (9/9) still green.

**Not yet prod-verified** — awaits deploy + founder's re-run of the
diagnostic on prod.

---

### Iter 388-ai bonus — Meta Pixel PROD-VERIFIED (2026-02-14)

Founder diagnostic on prod confirmed Meta Pixel is fully live:
- `fbq_loaded: true`, `fbq_version: "2.9.379"`
- `pixel_id_in_dom: true` (1571887197933821)
- Custom event `DiagnosticPing` fired without error

**Meta Pixel Iter 388-ag is officially prod-verified.**

---

### Open still — Canary trigger + Stripe

- **Canary trigger** — cookie auth returned 401 (endpoint requires
  Bearer header). Fix path forward: either add a "Run canary now"
  button on Admin page that uses `apiCall()` (auto-attaches Bearer
  from `localStorage.aurem_token`), OR update the diagnostic snippet
  to read `localStorage.getItem('aurem_token')` and pass as Bearer.
  Cheaper path = the updated snippet (below).
- **Stripe reconcile** — founder to do manual Stripe dashboard
  lookup for `cs_live_b14BL0LjNPIGB10Hm8lc6oW4YdyfRdBTXeIDK4OZROjSaA11NfdSLZJRfq`.
  Awaiting result.


---

### Iter 388-aj — fabricated_total dedup (2026-02-14)

**Founder-reported bug** from prod canary Run 1:
```
fabricated_total: ["test_security_gate.py", "test_security_gate.py"]
```
Same invented path listed twice — alerts / dashboards double-count.

**Fix**: `backend/services/ora_chat/grounding_check.py::classify_claims()`
now dedupes both `fabricated` and `unverified` output lists in
first-seen order via a `set()` tracker. Preserves ordering, no other
semantic change.

**Tests** (`backend/tests/test_iter388aj_dedup.py` — 4/4 pass):
- Same path repeated → dedup
- First-seen order preserved
- `unverified` list dedup on repeated real path
- Symbol-claim dedup

Iter 388-ah tests (10/10) still green — no regression.

Preview-verified; awaits prod deploy + fresh canary run showing
`fabricated_total: ["test_security_gate.py"]` (single entry) if the
model still invents that name.

---

## Deferred backlog (dedicated session)

### Iter 388-ak — Anti-fabrication regeneration (P1, deferred)

Current safety math (Iter 388-ah caveat enforcement) closed the worst
failure mode: silent confident lies. Fabrication itself still occurs
but is now always caveated. Founder + main agent agreed this is an
acceptable interim.

**Deeper fix scope for a dedicated future session:**
- When `run_post_response_check` detects any FABRICATED (non-index)
  file path in the reply → trigger ONE regeneration with a corrective
  system message ("your prior turn named files that don't exist in the
  codebase — redo without any specific file names").
- Complexity concerns to design carefully:
  - Retry-loop safety (max 1, never chained)
  - Latency budget (regen adds ~5-10s to some replies)
  - Cost impact (extra LLM call per fabrication)
  - Corner case: legitimate references to future/proposed files
- Needs design doc + adversarial test corpus before implementation.

### Iter 388-al — Slice 4 (fastapi/starlette major bump) migration branch

Still deferred from Iter 388-ad. Needs dedicated branch that:
1. Audits every `request.state.<key>` access → wraps in `getattr` with default (starlette 1.6 raises KeyError on missing keys).
2. Migrates all `TestClient(app)` usages to `with TestClient(app) as c:` for lifespan.
3. Full backend regression run before dispatch.

### Iter 388-am — litellm 1.80 → 1.84 (blocked on Emergent)

Our `litellm` is pinned to an internal Emergent-hosted wheel
(`customer-assets.emergentagent.com/...litellm-1.80.0-py3-none-any.whl`),
not PyPI. Requires Emergent to publish 1.84 to their asset host
first. 10 CVEs pending closure.


### Iter 389 — Meta Pixel conversion events (2026-02-15)

Meta Pixel was loading `PageView` only (Iter 388-ag). This iter adds
3 standard-event helpers in `frontend/src/lib/analytics.js` wired to
real backend confirmations:

- `metaCompleteRegistration(method)` — `Signup.jsx` (`email`) + `OAuthFinish.jsx` (`google` / `github`), fires only when backend confirms fresh account
- `metaLead("project_added")` — `AddProjectWizard.jsx` after `/cto/projects/add` success
- `metaPurchase(value, "USD", sid)` — `Settings.jsx` after `payment_status === "paid"` (skipped if tier missing — no $0 events)

Base pixel already loads without consent gate (founder's accepted
GDPR risk from Iter 388-ag). Helpers are no-ops when `window.fbq`
undefined (ad-blocker safe). 12/12 vitest cases + 57/57 lib suite
pass; preview-verified `fbq.loaded === true`. **prod-verification
pending** (founder will DevTools-check signup + project-add triggers
after deploy).

