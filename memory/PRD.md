# AUREM CTO — Product Requirements Document

**Live URL**: https://auremcto.com
**Job ID**: `73df9f0d-7149-4a95-89d4-c9972e2b0c6d`
**Language for agent internal work**: Hinglish (per founder instruction)

## Product mission

AUREM CTO is a full-stack AI product where founders can bring GitHub repos
and have ORA (the AI CTO) do end-to-end engineering: understand the repo,
answer questions, propose fixes, apply them via GitHub commits/PRs, and
run scan+fix pipelines (health, security, quality). Zero-mock — every fix
is a real GitHub commit with a verified SHA.

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

