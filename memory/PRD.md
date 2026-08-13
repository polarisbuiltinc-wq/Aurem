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
   ("Diagnose failed run" / "Summarize open PRs" / "Token breakdown").
   Data questions get pure structured data; no vision noise.

**Live verification (preview API, `test@aurem.dev`)**:
- Advisor endpoint returns all 3 new keys
- `open_prs` cleanly reports `repo_not_public_or_missing` instead of
  hanging (Bug 4 root fixed at the data layer)
- `token_breakdown.user_month_total = 7963` (matches Analytics
  screenshot exactly — real data, no fabrication)
- 6 pytest regressions green
  (`backend/tests/test_iter388j_advisor_bug345_fixes.py`)

**Awaiting user's prod DATA HONESTY text** to seed preview and cross-
verify override behavior; the code-level fixes are ready to deploy
regardless.

## New bugs tracked from senior QA pass (2026-02-13)

- **Bug 9** (P0): Chat message content EMPTY on reload — same message
  that previously showed the `<longcat_tool_call>` leak (Bug 2) now
  shows only the "via longcat-2.0" footer.  Persistence path is
  dropping content.  URGENT — needs investigation into message
  storage path (Mongo `chat_messages` collection + how Bug 2's
  sanitizer might be stripping content pre-persist).
- **Bug 10** (P0): No 404 page — broken URLs silently redirect to
  homepage.  SEO + UX issue.  React Router catch-all `<Route path="*">`
  is missing.
- **Bug 11** (P2): Two "founder spots" counters conflict on landing —
  hero says "48/50 First-50" and lower section says "496 of 500 founder
  spots" — either duplicate/wrong or need clearer naming to
  distinguish two separate promos.
- **Vercel styling** (P2): Settings → Integrations shows Vercel "NOT
  CONNECTED" in red error style even though disable was intentional
  (Option B). Should be neutral "not enabled" style.
- **Bug 6+7 scope narrowed**: Settings → Profile confirms `Tasks
  this month: 0 / unlimited` (broken) while `Tokens Used: 448,583`
  (correct).  Root cause is specifically in the TASK-count tracking
  pipeline, not tokens.

