# AUREM CTO — Product Requirements Document

**Live URL**: https://auremcto.com
**Job ID**: `73df9f0d-7149-4a95-89d4-c9972e2b0c6d`
**Language for agent internal work**: Hinglish (per founder instruction)

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

