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


## Iter 388h — Bug 1 + Bug 2 fixes (2026-02-13, awaiting deploy)

**Bug 1 — ORA Diff View silent-failure on real Loop runs**
- Root cause: `_run_task_with_git` (git-path task worker, used by all
  real PAT-connected users) never persisted the `edited_files` unified-
  diff payload or the `task_handoff`/`done` SSE frames. Only the
  `_run_task_via_api` path was wired for Iter 388g. Result: real prod
  loops shipped code but the ChatPanel `LiveTaskPopup.onDone` handler
  had no `edited_files.files` to attach → inline `EditedFileBubble`
  never rendered.
- Fix: mirrored the API-path Iter 388g block into `_run_task_with_git`
  in `backend/routers/cto_projects.py` (rich `files_changed` via
  `build_files_changed`, unified diff hunks via `build_unified_diff_hunks`,
  `wrap_edited_files` envelope, `github_url`, `time_taken_seconds`,
  `task_handoff` + terminal `done` SSE emit). Also added a
  `commit_full_sha` fetch so the github_url matches API-path shape.
- Test: `backend/tests/test_iter388h_bug1_bug2_fixes.py` (2 tests).

**Bug 2 — Raw `<longcat_tool_call>` XML leaking in Prompt mode**
- Root cause: `RenderedMessage.sanitizeForDisplay` regex only stripped
  the unprefixed `<tool_call>` shape. LongCat / Claude / Qwen / GPT
  variants (`<longcat_tool_call>`, etc.) passed through into the
  bubble.
- Fix: widened the internal-tag alternation in
  `frontend/src/components/RenderedMessage.jsx` to match any
  `<vendor_tool_call>` / `<vendor_tool_result>` / `<vendor_thinking>`
  variant via a `[a-z0-9]+_tool_*` regex class, and added the explicit
  longcat_/claude_/qwen_/gpt_ variants to the `INTERNAL_FENCES` set so
  fenced-code-block variants are also stripped.
- Test: 5 sanitizer regression tests in the same file above.

**Awaiting user GO for deploy.** After deploy, user will retest on a
real Loop run to verify the inline diff bubble renders + Prompt mode
no longer leaks raw tool-call XML.

## Bug 3 + Bug 4 — Advisor panel investigation (pending, next session)

- Bug 3: "Diagnose failed run" fabricates confident claims from stale
  scrollback (says loop is stuck when it's already shipped; hallucinates
  project name). Violates ORA's own anti-fabrication rule.
- Bug 4: "Summarize open PRs" silently hangs after "One moment while I
  fetch that information for you" — no error, no timeout.
- Suspected shared root cause: `backend/routers/advisor_context.py` +
  `backend/services/advisor_vision.py` + system-prompt anti-fabrication
  layer. Investigate together in one audit session per user request.
