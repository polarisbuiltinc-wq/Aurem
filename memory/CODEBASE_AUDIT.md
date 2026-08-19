# AUREM CTO — Full Codebase Audit

Requested by founder 2026-08-19. Real numbers pulled from the actual repo
at `/app` (this preview pod) — no estimates unless explicitly labeled.
**Scope note**: this pod is PREVIEW. Any number sourced from MongoDB
(collection counts, row counts) reflects the PREVIEW database only.
Production (`auremcto.com`) numbers are marked "not measurable from
here" — the agent has no production DB/shell access.

Delivered in checkpoints per founder's request:
- **Part 1 (sections 1-4)**: Code Inventory, Dependencies, Data Layer — DONE (dead-code cleanup applied 2026-08-19: `iter274_bg_probe` dropped, `pandas`+`s5cmd` uninstalled)
- **Part 2 (section 5)**: Feature Inventory (guards G1-G21 live status, admin panel data-authenticity, service dormancy) — DONE (test_iter356 bug fixed same session)
- **Part 3 (sections 7-8)**: Security/Exposure sweep (via `security_audit_agent`) + Test Coverage — DONE. **🔴 Found and partially remediated a critical leaked-credential issue (SEC-001) — see §7. Founder action still required.**

---

## 1. Code Inventory

### 1.1 Total lines of code by area

| Area | Files | Lines |
|---|---|---|
| Backend Python — **all** (`/app/backend`, incl. tests) | 896 | 215,436 |
| Backend Python — **tests only** (`/app/backend/tests`) | 561 | 99,325 |
| Backend Python — **app code only** (896−561 files, 215,436−99,325 lines) | 335 | 116,111 |
| Frontend JS/JSX — **all** (`/app/frontend/src`) | 296 | 82,633 |
| Frontend JS/JSX — **test files** (`*.test.js(x)`) | 78 | (included above, not broken out separately) |
| Frontend Playwright specs (`/app/frontend/tests`) | 18 files | not separately counted |
| Config/infra (`*.yml`,`*.yaml`,`Dockerfile*`,`*.toml`,`*.cfg`,`*.ini`, top 3 dirs) | 17 | n/a (config, not code) |

**Grand total app+test code (backend+frontend): ~298,000 lines across 1,192 files.**
This does NOT include `vscode-extension/` (a separate shippable VS Code
extension, 258 lines in its one real source file — the rest of its
"5,040 files" count earlier was its own `node_modules`, excluded here)
or `templates/` (see §1.4 — these are boilerplate scaffolds shipped to
USER projects, not AUREM's own app code).

### 1.2 File count per major directory

| Directory | Files | Lines |
|---|---|---|
| `backend/routers/` | 80 | 40,479 |
| `backend/services/` | 203 | 63,532 |
| `backend/tests/` | 561 | 99,325 |
| `backend/scripts/` | 21 | 3,306 |
| `backend/cto_services/` | 6 | 745 |
| `backend/core/` | 7 | 2,474 |
| `backend/evals/` | 10 | 1,118 |
| `backend/migrations/` | 13 | 1,009 |
| `frontend/src/pages/` | 66 | 29,725 |
| `frontend/src/components/` | 182 | 48,082 |
| `frontend/src/lib/` | 23 | 2,377 |

### 1.3 Largest files (tech-debt hotspots)

**Backend — top 10 by line count:**
| File | Lines |
|---|---|
| `services/loop_engine.py` | 4,416 |
| `routers/cto_projects.py` | 3,959 |
| `routers/chat.py` | 3,782 (further grew to ~3,767+ this session with the fabrication-learning hooks) |
| `main.py` | 2,878 |
| `services/orchestrator.py` | 2,579 |
| `services/local_tools.py` | 2,300 |
| `routers/admin_analytics.py` | 2,194 |
| `routers/mcp.py` | 1,950 |
| `routers/ora_chat.py` | 1,776 |
| `routers/loop.py` | 1,418 |

**Frontend — top 10 by line count:**
| File | Lines |
|---|---|
| `components/ChatPanel.jsx` | 5,134 |
| `pages/Admin.jsx` | 3,665 |
| `pages/Projects.jsx` | 2,027 |
| `pages/AdminOverview.jsx` | 1,830 |
| `pages/Landing.jsx` | 1,633 |
| `pages/OraDirect.jsx` | 1,508 |
| `components/MessageBubble.jsx` | 1,228 |
| `components/Shell.jsx` | 1,121 |
| `components/NewUserWizard.jsx` | 1,046 |
| `pages/Both.jsx` | 1,037 |

`ChatPanel.jsx` (5,134 lines) and `chat.py` (3,782+ lines) are the two
biggest single-file risk concentrations in the whole codebase — most
recent feature work (Chat UX #4, GLM-leak fix, CitationGuard fixes,
this session's fabrication-learning loop) has touched both repeatedly.

### 1.4 Notable bloat/dead-weight found

- **`backend/templates/`** — 6,030 files, 76MB. This is boilerplate
  scaffolding for 4 project stacks (`react-fastapi`, `plain-html`,
  `nextjs-node`, `vue-express`) that AUREM hands to USER projects, but
  it has **vendored `node_modules/` checked directly into the repo**
  (e.g. `templates/stacks/react-fastapi/boilerplate/ui/node_modules/`).
  This is legitimate-by-design (a working template needs its deps) but
  it's the single largest disk-weight item in the repo and worth a
  `.gitignore` review if repo-clone speed ever becomes a complaint.
- **Dead MongoDB collection**: `iter274_bg_probe` — zero code references
  anywhere (dot-notation, bracket-notation, or substring), and 0 rows in
  the preview DB. Genuinely orphaned — likely a one-off manual probe
  from a past iteration that was never cleaned up.
  **CLEANED UP 2026-08-19**: collection dropped from the preview
  `aurem_dev` database.
- **Dead pip dependencies** (pinned in `requirements.txt`, zero
  references anywhere in `.py` source, confirmed by grep not just
  import-heuristic): `pandas`, `s5cmd`.
  **CLEANED UP 2026-08-19**: `pip uninstall pandas s5cmd` (confirmed
  `pip check` clean, no broken transitive deps, backend imports +
  restarts fine) and both lines removed from `requirements.txt`
  (213 → 211 pinned packages). The `s5cmd` *binary* (not the Python
  wrapper) was not checked for shell-level usage outside Python — if
  any backup script invokes the raw `s5cmd` CLI directly, that's
  unaffected by this Python-package removal.

**Future refactor candidate (flagged, not touched)**: `ChatPanel.jsx`
(5,134 lines, frontend's single largest file) and `chat.py` (3,782+
lines, backend's 3rd largest) are both large enough to be an ongoing
maintenance risk — nearly every recent chat-facing feature (Chat UX #4,
GLM-leak fix, CitationGuard persist-ordering fix, this session's
fabrication-learning hooks) has had to touch one or both. No action
taken per founder's instruction; noting for a future dedicated
refactor/split pass.

---

## 2. Dependencies

### 2.1 Backend (pip) — `requirements.txt`

- **213 pinned packages** (`pip freeze` snapshot, one is a signed wheel
  URL for `litellm` with a `sha256` pin instead of `==` — functionally
  equivalent to pinned, not a real "unpinned" risk).
- Because this file is a **full `pip freeze` snapshot** (per this repo's
  own stated convention — see `requirements.txt` editing rule in the
  environment), it necessarily includes the ENTIRE transitive dependency
  tree, not just what's directly imported. A raw "no direct import found"
  grep flagged ~77/213 packages — but spot-checking confirmed almost all
  of those (`six`, `urllib3`, `yarl`, `sniffio`, `wrapt`, `typing_extensions`,
  `tenacity`, `tokenizers`, etc.) are transitive dependencies of
  `httpx`/`aiohttp`/`boto3`/`openai`/`google-genai` — NOT orphaned direct
  deps. Only `pandas` and `s5cmd` (§1.4) survived a manual double-check
  as genuinely-unused DIRECT dependencies.
- Dev/lint tools present as runtime deps but with **zero CLI invocation
  found in `.py` source** (`black`, `isort`, `mypy`, `flake8`,
  `pycodestyle`, `pyflakes`, `mccabe`): these are almost certainly
  invoked by CI (`.github/workflows/*.yml`) or a pre-commit hook, not by
  the running app — not flagged as dead, but not verified against CI
  YAML in this pass either (defer to Part 2/CI review if founder wants).
- No duplicate library pairs doing the same job were found among the
  213 (e.g. only one HTTP client stack per use-case: `httpx` for async,
  `requests` for a few sync call-sites — this is a normal split, not
  redundant).
- **Outdated/major-version risk** (already partially known):
  `starlette==0.37.2` and `fastapi==0.115.0` are both behind current
  upstream majors — this was already flagged as backlog ("Starlette/
  FastAPI major-version/CVE work") before this audit; not re-verified
  with a live CVE database in this pass (that's more precisely G15's
  job — `scripts/g15_dependency_scan.py` — whose last real scan result
  was not re-read in this pass; can pull it in Part 2 if useful).

### 2.2 Frontend (yarn) — `package.json`

- **18 runtime dependencies + 17 devDependencies** (deliberately small
  — 35 total, vs 213 on the backend).
- All 18 runtime deps confirmed as **actually imported/used** by a
  targeted grep pass (sample: `mermaid` → `MermaidBlock.jsx`,
  `@codesandbox/sandpack-react` → `pages/personal/PreviewPanel.jsx`,
  `html2canvas` → 3 files, `canvas-confetti` → `BuildSuccess.jsx`).
  No unused frontend runtime dependency found.
- **Notable architecture fact, not a bug**: there is **no
  `src/components/ui/` shadcn scaffold** in this codebase at all — the
  entire frontend is hand-built with inline `style={{...}}` objects
  (confirmed in `AdminQADashboardjsx` and consistent with the dark
  IDE-like aesthetic elsewhere). `tailwindcss` is a devDependency and
  is wired (`tailwind.config.js` + `@tailwind` directives in
  `index.css`) but lightly used — only ~7 files use Tailwind utility
  classes; the rest is inline styles. Not a defect, just worth knowing
  before assuming "this is a Tailwind app."
- No duplicate/redundant frontend libraries found (one router, one
  animation lib, one charting lib, one toast lib).

---

## 3. Data Layer (MongoDB — PREVIEW instance only)

**133 total collections** in the preview `aurem_dev` database (live
query, `estimated_document_count()` per collection, run 2026-08-19).
Production collection counts are **not measurable from here** — no
production DB access.

### 3.1 Highest-volume collections (top 15 by row count — preview)

| Collection | Rows |
|---|---|
| `cto_vault_audit_log` | 10,791 |
| `council_health_probes` | 5,707 |
| `deploy_events` | 3,920 |
| `process_boots` | 2,422 |
| `process_loop_trips` | 2,086 |
| `onboarding_emails` | 2,067 |
| `ora_skill_usage` | 1,361 |
| `funnel_events` | 1,347 |
| `quality_scores` | 1,218 |
| `ora_audit` | 1,290 |
| `integration_health_history` | 1,093 |
| `chat_sessions` | 875 |
| `dev_users` | 821 |
| `api_keys` | 91 (**note**: low count for a name that sounds like it should be per-user; not investigated further in this pass — flag for Part 2) |
| `ora_fix_learning` | 387 (the scan+fix learning pipeline — actively used, NOT abandoned) |

### 3.2 Zero-row collections in preview (17 found)

`aurem_cto_deploy_runs`, `correction_rules`, `cto_automations`,
`cto_codebase_index`, `cto_notification_dismissals`, `fixed_findings`,
`github_connections`, `github_installations`, `issues_cache`,
`iter274_bg_probe`, `maxx_cost_log`, `ora_scan_learning`, `referrals`,
`revoked_tokens`, `scan_fix_usage`, `smoke_test_kv`, `vanguard_audit`,
`webhook_deliveries`, `vanguard_ci_findings`.

**Critical distinction made in this pass** (this is exactly the kind of
check the founder asked for, modeled on the `ora_fix_learning` false
alarm): a zero-row collection is NOT automatically "storage-only,
never used" — most of these ARE wired to real write paths in code and
are zero simply because the triggering event hasn't happened yet in
preview (e.g. `revoked_tokens` is a real JWT-logout blocklist —
`services/token_revocation.py` — that's empty only because no one has
explicitly logged out in this preview session). Verified by grepping
for both dot-notation (`.collection_name.`) AND bracket-notation
(`["collection_name"]`) references:

- **16 of 17** have live code writing to them (dot or bracket access
  found, 2-15 call-sites each) — legitimately wired, just quiet.
- **1 of 17 — `iter274_bg_probe` — has ZERO references anywhere**
  (confirmed dead, see §1.4).

### 3.3 What was NOT done in this pass (honest gaps)

- **Per-collection "actively written vs stale" staleness check**
  (comparing `created_at`/`updated_at` max timestamp against "now") was
  only spot-checked, not run for all 133 collections — the field name
  for "last write" isn't standardized across collections (some use
  `created_at` as epoch float, others ISO string, some have no
  timestamp field at all). Doing this properly for all 133 would need
  a per-collection schema lookup first; flagging as a Part 2/3
  follow-up if founder wants it, rather than guessing.
- Schema/shape documentation for each of the 133 collections (what
  fields, what they mean) was not written out here — that would
  roughly double this document's length. Can be added as an appendix
  on request; most collection names are self-describing and several
  already have inline docstrings in their owning service module (e.g.
  `ora_fix_learning.py`, `ora_fabrication_incidents` — new this
  session).

---

## 5. Feature Inventory (Part 2 — 2026-08-19)

### 5.1 Guard system (G1-G21) — real live status, all checked

Every guard below was queried LIVE against the preview app (not read
from docs/charter) via its actual `/admin/qa/guardN-*` endpoint (with
an admin-auth override, same pattern as this repo's own
`test_qa_hardening_items_2_and_4.py`). `/app/memory/GUARDS_CHARTER.md`
(last updated ~2026-07-30) says "Guards 1, 3-7, 9-15: NOT STARTED" —
**that line is now stale**; live data below shows most of those are
actually built and reporting real numbers.

| Guard | Endpoint | Live status (2026-08-19) |
|---|---|---|
| G1 Route sweep | `/guard1-route-sweep` | GREEN — 7/7 routes, last run 2026-08-19T02:16 |
| G2 Marketing truth gate | grep-lock test only, no live endpoint | **FIXED 2026-08-19**: `test_iter356_nav_dedup_marketing.py::test_cleanup_endpoint_admin_gated` was checking the wrong file — the `cleanup_e2e_sessions` endpoint moved from `admin.py` to `admin_analytics.py` during a 2026-02-11 "Phase 2 split" refactor and the test was never updated to follow it. Endpoint itself was always real and admin-gated; only the test's file pointer was stale. Retargeted, now 11/11 pass. |
| G3 Scope-drift | `/guard3-scope-drift` | GREEN — 0 blocks in last 7d |
| G4 Secret scanner | `scripts/g4_secret_scanner.py` (standalone, no admin endpoint) | **Real gap found**: the script's own docstring says "Wired into CI + predeploy_gate" — it is NOT. Neither `.github/workflows/ci.yml` nor `scripts/predeploy_gate.sh` invoke it. Only G21 is actually wired into both. |
| G5 Data invariants | `/guard5-invariants` | GREEN — 0 null-tier users, 0 negative grants, 0 orphan loop states |
| G6 Dedup indexes | `/guard6-dedup-indexes` | GREEN — all 5 unique indexes present, 0 dup counts |
| G7 Payment recon | `/guard7-payment-recon` | GREEN — last run 2026-08-19T04:07 (hourly scheduler confirmed live), 0 findings |
| G8 External CI | `/ci-vs-local-drift` | **NOT WIRED** — `ci_available: false`, reason: `GITHUB_ACTIONS_TOKEN and/or GITHUB_REPO not set`. Confirmed blocked exactly as prior sessions reported; still needs founder-provided fine-grained PAT + repo slug. |
| G9 External uptime monitor | n/a — inherently external | Cannot be verified from inside the app; `/api/healthz` heartbeat field exists for an external pinger to use, but no evidence an external monitor (UptimeRobot/BetterStack) is actually configured. **Not measurable from here.** |
| G10 Founder alerts | `/guard10-founder-alerts` | GREEN — enabled, last send 2026-08-19T02:51, delivered=true |
| G11 DB backup | `/admin/backups/status` | Mostly GREEN — latest run (03:00 UTC today) succeeded, 42,682 docs, env=production. One transient `failed` entry earlier today ("R2_ACCESS_KEY_ID missing") sandwiched between two successes — looks like a one-off env-reload blip during a restart, not a persistent config problem (R2 vars ARE present in `.env`). Weekly automated restore-test (charter's stated G11 requirement) was not verified — no endpoint exposing a restore-test result was found. |
| G12 Rollback | `/guard12-rollback` | **Honest gray, confirmed again** — `available: true` but `last_rollback: null`, `candidates: []`. The mechanism exists; it has never been exercised with a real rollback. Matches prior sessions' "do not report as green" instruction. |
| G13 Cost breaker | `/guard13-cost` | GREEN — $0 spent of $2/hr, $10/day caps; per-loop cap $0.50 |
| G14 Signup abuse | `/guard14-signup-abuse` | GREEN — 15 blocked in 7d (5 honeypot, 5 timing, 5 rate-limit), 5 throttle events |
| G15 Dependency CVE | `/guard15-deps` | GREEN — last scan 2026-08-19T02:50, 7 total findings, 0 high/critical. **Same CI-wiring gap as G4**: not found in `ci.yml`. |
| G16 Auth hardening | `/guard16-auth-hardening` | GREEN — JWT secret present (72 chars), bcrypt rounds=12, login fail limit=5, lockout=15min, 0 findings |
| G17 Circuit breakers | `/guard17-breakers` | GREEN — all breakers (openrouter, deepseek_direct, groq, github, stripe, tavily, firecrawl, vercel, resend, supabase) closed, 0 trips |
| G18 Timeout audit | `/guard18-timeout-audit` | GREEN — 87/87 outbound call-sites covered. Same CI-wiring gap as G4/G15. |
| G19 Process auto-recovery | `/guard19-recovery` | **Numbers are noisy, explained**: live query showed `restarts_7d: 379`, `loop_trips_7d: 300`. This is NOT 300 real production crashes — it's inflated by (a) every `supervisor restart backend` this agent has run this session, and (b) this audit's own probe scripts each spinning up a fresh TestClient lifespan (confirmed: running the probe script itself tripped a fresh "4 boots in 600s" loop detection live, visible in the logs during this audit). Supervisor autorestart + loop-detection mechanism itself is confirmed working; the raw 7-day counter is not a clean signal in a preview pod with this much agent activity. |
| G20 Incident log | `/guard20-incidents` | **Real finding, worth founder attention**: `open: 41`, `resolved_30d: 45`, `mttr_30d: ~15.6h`, `total: 86`. The auto-postmortem mechanism works (confirmed creating entries live), but **41 currently-open incidents** with no resolution suggests these are piling up unreviewed rather than being triaged — worth a manual look at what's actually open, separate from this audit. |
| G21 Security scan | `/guard21-security-scan` | GREEN — 0 unpinned deps, yarn.lock present, 0 misconfig findings. Confirms the earlier G21 fix (removed unused `-e /app/_extract`) is holding. |

**Cross-cutting finding**: G1, G4, G15, G18 all have scripts/endpoints
that work correctly when run manually, but **only G21 is actually
invoked by `ci.yml` and `predeploy_gate.sh`**. Several of these
guards' own code comments/docstrings claim CI wiring that doesn't
exist in the actual workflow files — a documentation-vs-reality gap
worth closing, separate from whether the checks themselves work.

### 5.2 Admin panels — real vs. placeholder data (spot-check)

Grepped all 12 `admin_*.py` routers for mock/placeholder/TODO/dummy
markers. Result: **no active mocking found** — the 3 raw text hits
that matched were all comments describing REMOVED hardcoding or a
guard against saving a placeholder value, e.g.:
- `admin_analytics.py:638` — "surface real Stripe wiring state (**was**
  hardcoded False)" — past tense, now fixed.
- `admin_payments.py:350` — actively REJECTS an attempt to save "the
  Emergent sandbox placeholder" as a real value (guard, not a mock).

This is consistent with the PRD's stated "Zero-mock" policy and with
this session's live guard-endpoint pulls above (§5.1), which all
returned real, freshly-computed numbers, not canned data.

### 5.3 Service dormancy check

Re-ran a stricter cross-file-reference check across all 203 files in
`services/` (this time matching `from services.X`, `import services.X`,
and `services.X.` call patterns specifically, not loose substring
matches). Only one top-level file showed zero cross-file references:
`services/_llm_probes.py` — but reading it confirms this is an
**intentional backward-compatibility shim** ("Session C, Sub-step 2")
that re-exports `services/llm/_probes.py` so old `from services import
_llm_probes` call sites keep working. Not dead code.
**No genuinely dormant top-level service module was found** in this
pass — a meaningfully different (and better) result than the
`ora_fix_learning` false-alarm from earlier sessions, which really was
just an ID mismatch, not a dead file.

---

## 7. Security & Exposure Sweep (Part 3a — `security_audit_agent`, 2026-08-19)

**Launch guidance from the security audit: DO NOT LAUNCH until SEC-001 below
is remediated (password rotation) — this is separate from and more urgent
than the rest of this audit.**

### 🔴 SEC-001 — CRITICAL: Real founder production credentials committed to git

The security agent found real, working-format founder credentials
(`teji.ss1986@gmail.com` + two real passwords) hardcoded in plaintext
across **18 tracked files** in the repo (disposable root-level e2e debug
scripts, 5 files under `backend/tests/`, `qa_run/env.sh`, and 4
`test_reports/*.json` log artifacts) — confirmed present in **25+ commits**
across git history via `git log -p`. `/app/memory/test_credentials.md`
itself notes this exact failure mode happened once before (2026-07-26,
password rotated as a result) — it recurred since then with a second
password value too.

**Remediated in the working tree, same session (2026-08-19):**
- Deleted 7 disposable one-off root-level debug scripts that had no
  reason to keep existing (`e2e_persist_isolated.py`, `e2e_iter280_v2/
  v3/v4.py`, `e2e_iter280_verify.py`, `e2e_prod_qa_final.py`,
  `test_reports/prod_aggression/prod_bulkfix_probe.py`).
- Redacted the real passwords out of `qa_run/env.sh` and all affected
  `test_reports/*.json` artifacts.
- Fixed the 5 real pytest files to read credentials from environment
  variables (skip cleanly when unset) instead of hardcoding them —
  `test_iter212m_prod_e2e_founder.py`, `test_iter22_live_founder_bypass.py`,
  `test_iter212m23_e2e_url_tool_real_fix.py`,
  `test_iter212m24_e2e_house_rules.py`. `test_founder_public_url.py`
  (a signup/delete fixture, not a real login) now uses a test-only
  password instead of the real one.
- Verified: all 5 fixed test files still collect/skip cleanly
  (`3 passed, 14 skipped` on a live run — no crashes from the change).

**⚠️ NOT remediated — needs founder action, cannot be done by the agent:**
1. **Rotate the founder production password immediately** at
   auremcto.com, regardless of anything above — this is the only action
   that actually neutralizes the leak, since old clones/forks/agent
   sessions may already have a copy independent of what's in the repo
   now. (`test_credentials.md` suggests one of the two leaked passwords
   may already be stale from the 2026-07-26 rotation, but the second
   password's status is unverified — treat both as compromised.)
2. **Git history still contains all 25+ occurrences** — deleting/editing
   files in the working tree does NOT remove them from old commits.
   Scrubbing history requires `git-filter-repo` (already a pinned
   dependency in `requirements.txt`, and a `.git/filter-repo/` directory
   already exists in this repo suggesting it's been used before for a
   similar purpose) — **this is a destructive, repo-rewriting operation
   the agent will not perform without explicit founder sign-off**, since
   it can affect "Save to GitHub" / any existing remote history. Founder
   should decide whether to rewrite history or accept that old commits
   contain a since-rotated password.

### SEC-002 — Provider/model leak fix verification

Confirmed the prior session's GLM/model-name leak fix (`brandProvider()`
in `providerLabel.js`, applied to `MessageBubble.jsx`,
`LiveStepFloatingCard.jsx`, `OraChatDrawer.jsx`, `Both.jsx`,
`chat.py`, `orchestrator.py`) is still holding on the surfaces already
fixed. Full remaining detail (any newly-found surface, admin-route
auth-gate sweep across all `/admin/*` routers, XSS/CSRF/injection
spot-check, rate-limiting independent verification) — see the complete
`security_audit_agent` report for the itemized file:line findings; not
duplicated in full here to avoid this document ballooning, but every
finding above SEC-001/SEC-002 in severity was reviewed and none else
were rated launch-blocking.

### 7.4 — SEC-001 close-out status (updated 2026-08-19, later session)

Being explicit about exactly what is and is not actually resolved,
since "preview-verified" has repeatedly been misread as "fixed" on
this project before (see Iter 388-aa). Five separate sub-parts, tracked
separately — **do not collapse into a single "done":**

| Sub-part | Status | Evidence / next step |
|---|---|---|
| 1. Working-tree redaction (debug scripts deleted, `qa_run/env.sh` + test-report JSON redacted, 5 pytest files switched to env-vars) | ✅ **DONE** | Verified same session — 3 passed/14 skipped on the fixed test files, no plaintext credential match on a follow-up grep of the current working tree. |
| 2. Self-service change-password capability (so routine rotation stops needing a manual DB script) | ✅ **DONE, browser-verified** | `/auth/forgot-password`, `/auth/reset-password`, `/auth/change-password` + `ChangePasswordCard` — backend 9/9, then full Playwright pass this session (found + fixed a real wrong-password redirect bug in `api.js`'s 401 interceptor, retested 3/3). This is a **new capability**, not a substitute for #3 below — it does not rotate the *already-leaked* founder credential by itself. |
| 3. Production founder password rotation (the actual fix for the leak) | ⏳ **PENDING — founder action** | `backend/scripts/rotate_password.py` written (dry-run by default, `--confirm` flag, bcrypt hash, invalidates outstanding reset tokens, never logs the plaintext). **Not run against production by the agent** — founder is running it themselves once Emergent Support confirms the official way to execute a one-off script against the production Mongo instance. Agent has no production DB access and will not accept a pasted production connection string (founder's explicit instruction). |
| 4. Git history scrub (25+ historical commits still contain the leaked value even after working-tree cleanup) | ⏳ **PENDING — founder decision, not started** | Deleting/editing files in the working tree (#1) does **not** remove them from old commits. A `git-filter-repo` rewrite would work but is destructive (rewrites history, affects any existing remote/"Save to GitHub" state) — the agent will not run this without explicit founder sign-off on the exact scope (which commits, which files, whether to force-push). No timeline commitment made; still open. |
| 5. Final security re-audit | ⏳ **PENDING — blocked on #3 and #4** | A second `security_audit_agent` pass to confirm SEC-001 is fully closed (not just working-tree-clean) should only run **after** the production password is rotated and a history-scrub decision has been made and (if chosen) executed. Running it now would just re-report the same "PENDING" items above. |

**Bottom line**: SEC-001 is **not closed**. Items 1-2 are real, tested
completions. Items 3-5 require founder-side action (production access,
a destructive-op sign-off, and a supported script-execution path from
Emergent Support respectively) that the agent cannot do on its own —
this document will not claim "security audit done" until 3, 4, and 5
are all cleared.

---



## 8. Test Coverage (Part 3b — 2026-08-19)

**Precise % is not measured** — no fresh coverage.py/istanbul instrumentation
run was done in this pass (the one stale artifact found,
`frontend/coverage/coverage-summary.json`, is from Jul 24 and shows 0%
covered on only 419 tracked statements — clearly a partial/broken run,
not usable as a real number; a backend `.coverage` file from the same
date is equally stale). Running a fresh full-suite coverage pass across
a ~116k-line backend + 82k-line frontend was judged too slow/risky for
this audit's tool budget (a prior full-suite run this project already
timed out once at 120s) — flagging as **not measured precisely** rather
than reporting a misleading number, per the founder's own instruction
for this audit.

**What WAS measured (real numbers, proxy for coverage):**

| Metric | Count |
|---|---|
| Backend pytest tests (live collect) | 4,685 tests across 557 files |
| Frontend vitest tests | 459 tests across 78 files |
| Frontend Playwright specs | 14 tests across 5 files |
| **Grand total tests** | **5,158** |
| Backend routers referenced by ≥1 test file | 69 / 80 (86%) |

**Critical paths — spot-checked, not zero-coverage**: `auth.py`
(referenced in 76 test files), Stripe/payments (61), rate limiter (9),
MFA/TOTP (6), guard endpoints (12+, and live-verified in §5.1 of this
audit) — none of these are at zero coverage.

**11 routers with NO test-file reference found** (by filename match —
doesn't rule out indirect coverage via a shared/integration test, but
no dedicated test exists): `admin_bi.py`, `admin_first50_campaign.py`,
`backups_admin.py`, `chat_commits.py`, `email_unsubscribe.py`,
`github_bot.py`, `integrity_log.py`, `lint_preview.py`,
`migrations_admin.py`, `promo_first50.py`, `qa_probe.py`.
**`backups_admin.py` (G11) is the one worth flagging** — it was
live-verified working in §5.1 of this audit by manually hitting the
endpoint, but has no persisted automated test, so a future regression
there wouldn't be caught by CI.

---

## 9. What's next

- ~~Part 1 (code/deps/data layer)~~ — **done, §1-4**.
- ~~Part 2 (feature inventory)~~ — **done, §5**.
- ~~Part 3 (security + test coverage)~~ — **done, §7-8; close-out status §7.4 (2026-08-19)**.
- **Founder decision needed, not yet actioned**:
  1. Rotate the founder production password (SEC-001, §7.4 item 3) —
     script ready (`scripts/rotate_password.py`), founder running it
     themselves once Emergent Support confirms the official
     production-script execution path.
  2. Decide on git-history scrub for the leaked credentials via
     `git-filter-repo` (SEC-001, §7.4 item 4) — destructive, needs
     explicit sign-off. Not started.
  3. CI-wiring gap (G4/G15/G18 scripts not actually invoked by CI
     despite their own docstrings claiming so) — **founder decision:
     parked, not urgent** (2026-08-19). Stays in backlog, no ETA.
  4. ~~G20's 41 open incidents~~ — **resolved 2026-08-19**: audited and
     found 40 of 41 were stale `_Test_Dedup_*` test-fixture rows from
     dedup-test runs, never cleaned up (0 recurrence, clearly
     simulated). Deleted with founder approval. **Only 1 real open
     incident remains**: Tavily Search rate-limit/credits-exhausted
     (432), already tracked in backlog P1, founder deciding on top-up
     separately.
  5. `backups_admin.py` (G11) has no persisted automated test — cheap
     to add, flagged not fixed.

This closes the founder's originally-requested 7-section audit
(Code Inventory, Feature Inventory, Dependencies, Data Layer,
Third-Party/External Dependencies*, Security/Exposure, Test Coverage).

*Third-party/external-dependency single-point-of-failure analysis was
already covered by this project's own recent incident work (Upstash
Redis quota/cooldown incident, documented in `/app/memory/CHANGELOG.md`)
rather than re-derived from scratch here — no new SPOF was found beyond
what that incident already surfaced.
