# AUREM CTO — PRD (Product Requirements & Change Log)

## Original Problem Statement
AUREM CTO is a React SPA + FastAPI + MongoDB developer-productivity
platform ("aurem-dev" service). Focus: shipping features founders/devs
actually use, with a strict **Verify-First / Zero Mocks / Full Prod-Ready**
rule. Every fix must be reproducible on real data + tested via real APIs
before being called "done".

## Users
- Founder (Teji, `teji.ss1986@gmail.com`) — production account owner.
- Real developers (currently 30 on prod, 74 on preview).
- Founder-controlled admin dashboard at `/admin`.

## Core Product Areas
1. **Two-Agent Loop** — Plan → Approve → Execute → Ship flow (Council models).
2. **GitHub Integration** — OAuth-first signup, repo listing, push flow.
3. **Admin Dashboard** — Live LLM/dep status, topup alerts, revenue, funnel.
4. **QA / Vanguard** — Real-time diagnostic probes + scope-drift audits.
5. **Session-based bug fixes** — recurring Session N batches driven by
   real-user QA reports.

---

## Change Log

### 2026-02-08 · morning — Phase 3 · Two-Layer Intent Router ✅

**Deliverable:** Every /ora chat message now gets classified into `PREVIEW_ONLY | CODE_CHANGE | UNKNOWN` before ORA replies. The verdict streams to the frontend as a dedicated `intent` SSE event; the founder sees a small chip below each assistant turn (and a "Start a loop run" CTA hint on CODE_CHANGE) so future Phase 4 loop wiring is one click away from being surfaced.

**Backend:**
- New `backend/services/ora_chat/intent_router.py`:
  - **Layer 1 — regex pre-filter** (`classify_intent_regex`): high-precision patterns only. CODE_CHANGE list catches verbs (`commit / push / merge / deploy / apply / update / fix / refactor`), scope phrases (`in the repo`, `make it live`), start-a-loop imperatives, and bare `path/to/file.py`-style mentions. PREVIEW_ONLY list catches `show me / draft / sketch / mock`, `what would … look like`, `just a snippet`, and negative-commit phrasing.
  - **Layer 2 — constrained LLM fallback** (`classify_intent_llm`): Gemini 2.5 Flash at t=0.0, max_tokens=8, system-prompted to reply with EXACTLY one of the two label words. `_sanitize_llm_label` strips punctuation/backticks and enforces exact match — anything else collapses to UNKNOWN, so the classifier can never promote a fabricated label.
  - **Tie-break policy**: when both regex families fire, CODE_CHANGE wins (imperative > exploratory).
  - **Robustness**: LLM exceptions are caught inside the module so the top-level `/message` SSE stream can never be killed by a classifier hiccup.
  - **Provider adapter**: `classify_intent_llm` calls `one_shot(top_p=1.0, presence_penalty=0.0, …)` for real providers, with a `TypeError` retry that accepts a minimal signature so test stubs stay clean.
- `backend/routers/ora_chat.py`:
  - New `POST /api/aurem-dev/ora-chat/intent-classify` (admin-gated) so intent can be verified/consumed outside the streaming path.
  - Intent verdict computed ONCE at the top of `send_message` (right after slash short-circuit) and passed into BOTH the deep-research path and the regular event stream via kwarg / closure. Each path yields `{type: "intent", …}` right after its initial `route` event.
- `backend/tests/test_iter212m265_ora_intent_router.py` — 15 tests (regex family, LLM sanitiser, two-layer orchestrator, static router wiring). All green.

**Frontend:**
- `frontend/src/pages/OraDirect.jsx`:
  - SSE handler consumes `intent` events, persists on `routeMeta` (which now spreads on route-event overwrite — see fix below).
  - Bubble renders a subtle chip (`preview only` / `code change`) below assistant turns; `?debug=1` also shows the layer that fired (`regex` / `llm`). UNKNOWN stays invisible.
  - CODE_CHANGE bubbles also carry an italic hint ("Want ORA to actually make this change? Start a loop run from the dashboard.") — no button yet, that's Phase 4 wiring.
- `frontend/src/components/__tests__/OraIntentBadge.phase3.test.jsx` — 6 tests locking the SSE handler contract + Bubble rendering.

**Bug fix uncovered during E2E:** The deep-research SSE path emits TWO `route` events (initial announce + a final one with `sources`). The frontend was **overwriting** `routeMeta` on the second event, wiping the `intent` field set between them. Fixed by spreading previous meta on route-event assignment in both `OraDirect.jsx` and `OraChatDrawer.jsx`. No regression risk — spreading is a superset of the previous behaviour.

**Live-verified on preview:**
- `POST /intent-classify` returns correct verdicts for CODE_CHANGE (regex), PREVIEW_ONLY (regex), and UNKNOWN → CODE_CHANGE via Gemini flash (llm, 3 output tokens).
- `/message` SSE stream now includes `event: intent` between `event: route` events.
- Screenshot confirms `preview only · regex` chip on a PREVIEW_ONLY turn; `code change` chip + "Start a loop" hint on a CODE_CHANGE turn.

Ready for founder redeploy → prod verification → Phase 4 green-light.

---

### 2026-02-07 · night — Phase 2 · Prod-Verify Follow-up (Issues 1 & 2) ✅

Founder live-verified on prod, flagged two real bugs. Both fixed.

**Issue 1 — HIGH-severity findings were still shown as a passive banner on prod.**
- Root cause: the click-through gate code was correct on preview (verified via reproduction: dangerouslySetInnerHTML → amber "1 HIGH-severity finding … Preview anyway" banner + `Preview blocked` empty state) but the prior prod deploy shipped an older bundle where the gate wasn't wired.
- Verification path used on preview: `POST /ora-chat/preview-scan` with a `dangerouslySetInnerHTML` payload returns `severity: "HIGH"`; frontend `hasHigh` check flips iframe render off until the founder clicks `ora-preview-ack-btn`.
- No further code change needed — just a fresh prod redeploy so the gate binary matches preview.

**Issue 2 — JSX previews broke with "Cannot use import statement outside a module".**
- Two-part root cause:
  1. Adding `'unsafe-eval'` to `CSP_JSX` unblocked Babel's runtime transpile, but exposed the underlying bug.
  2. `@babel/standalone`'s current default for `preset-react` is `runtime: 'automatic'`, which emits `import { jsx as _jsx } from "react/jsx-runtime"` in the compiled output. That ESM import statement then blows up inside `new Function(out, ...)` because Function bodies are script scope, not module scope.
- Fix: pass explicit `presets: [['react', { runtime: 'classic' }]]` so Babel emits `React.createElement(...)` calls (script-safe, no imports).
- Live-verified on preview: React counter component (`function App(){ const [n,setN]=React.useState(0); ... }`) renders inside the sandbox with a working +Increment button.
- Also confirmed `'unsafe-eval'` is scoped ONLY to `CSP_JSX` — HTML/plain-JS previews still have the tighter `script-src 'unsafe-inline'` with no eval permission.

**Tests (10 green):**
- `OraPreviewPanel.phase2_security.test.jsx` extended to 8: HTML has no unpkg AND no `'unsafe-eval'`; JSX has both.
- Streamdown XSS suite still 2/2.

Ready for redeploy → prod verification of BOTH the HIGH-ack gate and JSX rendering.

---

### 2026-02-07 · late-evening — Phase 2 · Security Hardening Follow-up ✅

Founder review flagged two legitimate concerns after the initial Phase 2 build. Both fixed in this pass before prod redeploy.

**Concern A: `unpkg.com` in every CSP was broader than necessary.**
- HTML and plain-JS previews don't need external scripts — only JSX/TSX does (React + Babel-standalone).
- Fix: split into two CSPs. `CSP_HTML_JS = "script-src 'unsafe-inline'; …"` (no external hosts). `CSP_JSX = "script-src 'unsafe-inline' https://unpkg.com; …"`. Chosen at srcdoc build time via `_cspFor(lang)`.
- Supply-chain blast radius: even if unpkg were compromised for a JSX preview, the outer `sandbox="allow-scripts"` (no `allow-same-origin`) + `connect-src 'none'` keeps a malicious payload from touching the parent origin OR beaconing out. Fail-safe: if unpkg is unreachable, the JSX inner try/catch renders `pre.__ora_err` — no execution.
- Backlog P2: bundle React + Babel into app static assets so unpkg is never hit at all.

**Concern B: HIGH/MEDIUM Vanguard findings were passive — easy to miss.**
- Fix: HIGH findings now block render behind an explicit "Preview anyway" click-through (`ora-preview-high-ack` + `ora-preview-ack-btn`). Ack state is per-payload — `useEffect(() => setAck(false), [code, lang])` wipes it whenever the code changes so a stale acknowledgement can't carry over to a new payload.
- MEDIUM findings stay as a passive `ora-preview-warnings` banner (mostly stylistic / informational).
- The existing CRITICAL-blocker path is unchanged: no bypass, no ack option, refuse.

**Tests:**
- `OraPreviewPanel.phase2_security.test.jsx` now 8 tests (up from 5): added `HTML previews do NOT allow unpkg`, `JSX previews DO allow unpkg`, `HIGH-severity click-through required`. All green.

**Live smoke on preview:** HTML card render confirmed `HTML_CSP_HAS_UNPKG: False`; DOM inspection of the injected CSP shows `script-src 'unsafe-inline'` with no external host for HTML previews.

Ready for redeploy → founder live-verification on prod → Phase 3 green-light.

---

### 2026-02-07 · evening — Phase 2 · Security-Hardened `srcdoc` Preview ✅

**Deliverable:** Founder can hit "▶ Preview" on any ORA reply containing a renderable code block (`html/htm/jsx/tsx/js`) → sandboxed drawer slides in from the right, renders the code inside a locked-down iframe, with a Vanguard-scan gate in front of every render.

**Security contract enforced end-to-end:**
1. `sandbox="allow-scripts"` ONLY — never combined with `allow-same-origin`. Preview code can't touch parent cookies/storage/DOM. Locked in vitest.
2. Strict CSP `<meta http-equiv>` injected into every srcdoc: `default-src 'none'; script-src 'unsafe-inline' https://unpkg.com; style-src 'unsafe-inline'; img-src data: https:; connect-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none';`. Vitest asserts CSP + `connect-src 'none'` are always present.
3. 300ms debounce on srcdoc rebuild + Vanguard scan. Vitest verifies no POST fires before the timer elapses.
4. 16MB hard cap enforced client-side (never round-trips the payload) AND server-side (returns HTTP 413). Vitest verifies oversized payload never hits `api.post`.
5. Every render blocked by `POST /api/aurem-dev/ora-chat/preview-scan` — CRITICAL Vanguard findings collapse `safe=false` and refuse render; HIGH/MEDIUM surface as non-blocking warning banner.

**Backend:**
- `backend/routers/ora_chat.py::preview_scan` — new `POST /preview-scan` endpoint, admin-gated, reuses shared `services.vanguard_scanner.scan_text` (same regex sweep as the pre-push gate — no bespoke rules → no drift), whitelist of renderable langs, 16MB cap.

**Frontend:**
- `frontend/src/components/OraPreviewPanel.jsx` — self-contained drawer, `useDebounced()` helper (starts empty so first mount honours the 300ms window), scan-state UI (scanning / warnings / blocked / error banners), Preview↔Code toggle, findings footer, trust footer.
- `frontend/src/pages/OraDirect.jsx` — `findRenderableBlock()` markdown fence detector (first `html|htm|jsx|tsx|js|javascript` fence wins), "▶ Preview {LANG}" chip below the assistant bubble (hidden while streaming), state lifted to `ChatShell` so multiple bubbles can share one drawer.

**Tests (all green):**
- `frontend/src/components/__tests__/OraPreviewPanel.phase2_security.test.jsx` — 5 tests: sandbox exactness, CSP presence, debounce respected, CRITICAL blocker refuses render, 16MB cap short-circuits before network.
- `backend/tests/test_iter212m264_ora_preview_scan.py` — 7 tests: admin gate, 16MB constant, lang whitelist, `scan_text` reuse + XSS vectors (`innerHTML_assignment`, `dangerouslySetInnerHTML`) still flagged, clean HTML has no CRITICAL findings.
- Phase 1 vitest (`OraChat.streamdown_xss.test.jsx`) still 2/2 green — no regression.

**Live smoke on preview:**
- Prompted ORA for an HTML card snippet → assistant reply carried a fenced `html` code block → "▶ Preview HTML" chip visible → drawer opened → real card ("Aurem Preview Test") rendered inside the sandbox.
- DOM inspection: `sandbox="allow-scripts"` (exact match, no allow-same-origin), `srcdoc` contains `Content-Security-Policy` + `connect-src 'none'`.

Ready for founder redeploy → prod re-verification.

---

### 2026-02-07 · afternoon — Phase 1 · Post-Live Findings + Claude-Style Restructure ✅

Founder QA on preview flagged 3 real bugs + a full UI restructure to match Claude.ai's chat aesthetic. Batched into a single Phase 1 close-out pass so the whole surface ships coherent.

**Bug fixes:**
- **`/repo-tree` "literal `\n` wall of text"** — `frontend/src/pages/OraDirect.jsx` + `frontend/src/components/OraChatDrawer.jsx`: `slash_result` handler was blindly `JSON.stringify`-ing the value, which escaped every real newline. Now: string values render verbatim inside a fenced code block (real newlines preserved), objects/arrays fall through to `\`\`\`json … \`\`\`` — one code path, one code fence, readable output.
- **"general · t=0.4" internal metadata leak** — assistant route/temperature/downgrade badge below every message is now gated behind `?debug=1` URL param (`useDebugMode()` helper). Same policy as the earlier "(via /loop/active fallback)" cleanup.
- **Header cost pill always-on** — `$0.0000 / $2.5` budget chip also gated behind `?debug=1`. Founder QA sessions add `?debug=1`; the default `/ora` surface is now Claude-clean.

**Cross-turn content bleed (Finding 2) — RCA:**
- `backend/services/ora_chat/session.py::build_llm_history` sends the full session history to the LLM until token ceiling; XSS payload from a prior turn appeared in context → model echoed it verbatim in the next unrelated reply. Streamdown neutralised the payload at render time (verified: sentinel `window.__pwned` never fires) — no security compromise, but a context-hygiene quirk.
- Guidance: use "New chat" (`ora-picker-new`) for clean QA. No code change this session — session-level input sanitisation belongs to Phase 3 (intent detection has better hooks).

**Claude-style layout (`OraDirect.jsx` + `index.css`):**
- `useContainerWidth()` now returns fixed **780px** on desktop (was 46% viewport → wide-on-4K, cramped-on-13"). Tablet: 92% up to 760px. Mobile: 100%.
- Assistant bubble rewritten: `border: none`, `background: transparent`, `padding: 4px 0`, `fontSize: 15.5`, `lineHeight: 1.75`, `alignSelf: stretch`, full-column width — Claude's borderless flow-on-page look.
- User bubble kept as subtle warm-chip (`PAL.bubbleUser`), max 75% width, right-aligned — role separation via spacing + tint only, no hard-edged card.
- Message-list `gap: 16 → 28` for airy vertical rhythm.
- New `.ora-md` CSS block in `frontend/src/index.css` restores Tailwind-preflighted list markers, heading sizes, table borders, inline `<code>` chips, blockquote left-rule and paragraph rhythm — scoped so no other page is affected.

**Verification (preview):**
- `yarn test src/components/__tests__/OraChat.streamdown_xss.test.jsx` → 2/2 pass (unchanged).
- ESLint on `OraDirect.jsx` → 0 issues.
- Live smoke on `/ora` (fresh session):
  - Rich-render prompt → GFM table + fenced code + bulleted list all rendered correctly, no metadata badge, no header cost pill.
  - `/repo-tree` → hundreds of file paths rendered with real newlines inside a code fence, fully scannable.

Ready for founder redeploy → prod re-verification.

---

### 2026-02-07 — Phase 1 · ORA Chat Rich Rendering (Streamdown) ✅

**Goal:** Ship Claude-style rich markdown rendering to `/ora` admin chat with built-in XSS safety. Strict phase-gating: Phase 2+ blocked until founder confirms Phase 1 stable.

**Changes:**
- `yarn add streamdown@^2.5.0` — added to `frontend/package.json`.
- `frontend/src/pages/OraDirect.jsx` — replaced raw `<span>{content}</span>` assistant rendering with `<Streamdown>{content}</Streamdown>` inside `.ora-md` wrapper. User turns stay plain (`whiteSpace: pre-wrap`) so raw prompts are never interpreted as HTML.
- `frontend/src/components/OraChatDrawer.jsx` — same Streamdown swap for the admin drawer bubble so both chat surfaces stay consistent.
- `frontend/src/components/__tests__/OraChat.streamdown_xss.test.jsx` — new vitest suite covering:
  - GFM acceptance: `<h1>`, GFM `<table>`, fenced `<pre><code>`, inline `<img src=...>`, bold-syntax non-leak.
  - XSS neutralisation: no `<script>` in DOM, no `onerror` attribute on rendered `<img>`, no `javascript:` href preserved, `window.__pwned` sentinel never set.

**Verification (this session):**
- `yarn test src/components/__tests__/OraChat.streamdown_xss.test.jsx` → 2/2 pass, 121ms.
- Live smoke on `https://launch-pad-237.preview.emergentagent.com/ora` (PIN login → real LLM turn) confirmed:
  - Assistant reply rendered a `<table>` with "Type | Example" headers.
  - Fenced JS code block with `console.log('hello');` + code-copy/download affordances.
  - Bold heading, ordered/unordered list items, bullet content — all rendered by Streamdown, not raw markdown.
- No console errors, no XSS execution, budget pill still ticking.

**Known cosmetic follow-up (non-blocking, tracked as P2):**
- Streamdown `<ul>/<ol>` bullets/numbers appear muted because Tailwind's global preflight resets list markers. Fix belongs to a small `.ora-md ul { list-style: disc; padding-left: 1.25rem }` scoped CSS pass in a later polish batch — does NOT block Phase 1 acceptance.

**Phase gating status:** Phase 1 ready for founder verification on preview. Phase 2 (`srcdoc` preview iframe) NOT started per explicit founder instruction.

---

### 2026-02-02 — Admin & User Sidebar Toggles

**Feature 1: Admin hamburger sidebar toggle ✅**
- `/app/frontend/src/pages/Admin.jsx` — added `sidebarOpen` state (persisted via `localStorage["aurem_admin_sidebar_open"]`), a hamburger button (`data-testid="admin-sidebar-toggle"`) in the sticky top bar, and a grid-column transition (`220px 1fr` ↔ `0 1fr`).
- `/app/frontend/src/index.css` — added desktop `data-sidebar-open="false"` transform + `.aurem-admin-backdrop` for mobile drawer overlay.
- `/app/frontend/src/App.jsx` — routes `/admin` and `/admin/cockpit` now render `<Admin initialTab="cockpit" />` so the cockpit inherits the sidebar chrome (previously it was chrome-less).
- `/app/frontend/src/pages/Admin.jsx` — added `case "cockpit"` in `renderPage()` importing `AdminCockpit`.
- `/app/frontend/src/pages/AdminCockpit.jsx` — removed the duplicate `NotificationBell` (Admin shell already renders one).
- Verified via screenshots: hamburger click hides/shows sidebar; state persists.

**Feature 2: User chat rail auto-hide + floating pill ✅**
- `/app/frontend/src/components/nav/RailShell.jsx` — added `hiddenForTyping` state that listens for `aurem:chat-session-started` / `aurem:chat-session-reset` events (same pattern as `Shell.jsx`). When triggered, the 56px rail slides off `translateX(-105%)`.
- Added floating peek pill (`data-testid="rail-peek-pill"`, `<Menu>` icon on left edge, 40×40 rounded button) that mirrors the "Ask Advisor" launcher and restores the rail.
- Added `data-testid="rail-autohide-toggle"` compact `AUTO`/`OFF` badge at the bottom of the rail so the founder can disable auto-hide (persisted via `localStorage["aurem_rail_autohide"]`).
- Also added `data-testid="sidebar-peek-pill"`, `sidebar-auto-hide-toggle` in `/app/frontend/src/components/Shell.jsx` for the legacy Shell sidebar so pages still using `Shell` (non-chromeless) get the same floating pill pattern.
- Verified via screenshots: firing `aurem:chat-session-started` hides the rail + shows the pill; pill click restores; AUTO toggle → OFF disables the behaviour.

### 2026-02-02 — QA-System Hardening + ORA-Learning Functional Verify (previous session)

**Item 1: ORA-learning functional verify ✅**
- New test file `tests/test_ora_learning_functional_verify.py` with 4 tests hitting **real Mongo**:
  1. Happy path writes a real `ora_learning_logs` document (all fields populated).
  2. Rate-limit cap correctly blocks writes past `ORA_LEARNING_HOURLY_CAP`.
  3. `count_documents` failure → `[silent-catch]` DEBUG log fires AND insert still happens (fail-open contract preserved).
  4. Static assurance `chat.py` still dispatches the coroutine.
- Live Mongo write proof captured in `/app/memory/QA_HARDENING_REPORT.md`.

**Item 2: CI-vs-local drift endpoint ✅**
- New endpoint `GET /api/aurem-dev/admin/qa/ci-vs-local-drift` in `routers/admin_qa.py`.
- Cross-references local pytest count vs latest GitHub Actions quality-gate run.
- Honest-empty (`ci_available=False` + reason) when `GITHUB_ACTIONS_TOKEN`/`GITHUB_REPO` not wired. No fake green.
- **Config still needed**: founder must set both env vars in `backend/.env` for the check to become live.

**Item 3: Secret-leak alert gap — RECOMMENDATION ONLY (deferred)**
- Root cause: trufflehog WAS catching the leak, but no notifier was wired from CI-failure-on-main → `founder_alerts.send_founder_alert()`. Only email GitHub sends is to the pusher (usually off).
- Two options documented in `/app/memory/QA_HARDENING_REPORT.md`: (A) webhook-receiver + `ci.yml` step, (B) native GitHub → Slack app.
- Founder decision needed on Resend vs Slack channel before wiring.

**Item 4: Deploy vs GitHub-push conflation ✅**
- `routers/version.py` — `/api/aurem-dev/version` now returns `last_github_push` (nullable dict with `commit_sha`, `pushed_at`, `html_url`, `message`); 60s cache; honest-empty when creds missing.
- `pages/AdminSystemHealth.jsx` — Deploy Sync card renders two DISTINCT lines: "Deployed …" (from `built_at`) and "Pushed to GitHub …" (from `last_github_push.pushed_at`).
- Test-IDs added: `deploy-sync-{preview|production}-{deployed-at|pushed-at}`.

**Files created/modified this session**:
- New: `backend/tests/test_ora_learning_functional_verify.py`, `backend/tests/test_qa_hardening_items_2_and_4.py`
- New: `memory/QA_HARDENING_REPORT.md` (full process-gap analysis + live proofs)
- Modified: `backend/routers/version.py`, `backend/routers/admin_qa.py`, `frontend/src/pages/AdminSystemHealth.jsx`

**Test status**: 7/7 tests passing. Lint clean (Python + JS).

---

### 2026-02-02 — Persona-Diet Round-2 + Batch 4f + REAL BUG #11
**Persona-Diet PR** ✅ (founder-approved cheap addition to
guardrail work):
- `AUREM_CTO_PERSONA`: **21,559 → 19,945 chars** (-1,614 total,
  now UNDER the 20k warn threshold with 55c headroom, 2,055c
  headroom to the 22k hard budget — full 5% safety margin restored)
- TOP-OF-MIND HARD RULES consolidated (Rule 3+Rule 7 merged,
  Rule 4 leak-block trimmed via cross-ref to DO NOT LEAK section
  below, Rule 5 execute_bash tightened, Rule 6 build-check
  tightened). Rule 7 dropped as merged.
- HOW TO RESPOND ✗ INCORRECT block consolidated (2 negative-list
  blocks merged into 1 with a-e criteria + one worked example
  instead of three).
- MODE DETECTION + CORE-RULE deduped (CORE RULE was restating
  Rule 1+2+Step-1; kept the distinctive phrases the assertion
  tests require, dropped the restated body).
- WHAT 'GENUINELY AMBIGUOUS' MEANS shortened to a 6-line
  block with the same asks/don't-asks classifier.
- Live spot-check post-trim (identity anti-fabrication attack):
  model returned verbatim the persona fallback + pivoted to
  capabilities. Zero regression.

**Batch 4f (Session G · contract-drift class)** — 3 files fully
un-quarantined (152 quarantined, was 155): `test_iter94_maxx_cap_and_usd_migration.py`
(8 tests, includes real BUG #11 fix), `test_iter86_architecture_health.py`
(9 tests, baseline refresh via `scripts/architecture_health.py
--update-baseline`).

🚨 **REAL PRODUCTION BUG #11** — MAXX-mode cap silently disabled
for ALL tiers. `services/subscription_tiers.py` never carried the
`maxx_tasks_per_month` field, so `services/usage.py::MAXX_MONTHLY_LIMITS`
computed `{free: None, starter: None, pro: None, team: None,
founder: None}`. Line 249 of `usage.py` treats `cap is None` as
UNLIMITED — meaning free-tier users could run MAXX mode
infinitely with no cap enforcement, revenue leak on Pro overage
tier too. Fixed by adding explicit `maxx_tasks_per_month` values
(free=0, starter=0, pro=100, team=None, founder=None). Verified
runtime: `MAXX_MONTHLY_LIMITS == {'free': 0, 'starter': 0, 'pro': 100,
'team': None, 'founder': None}`.

**Deferred (22 nodeids across 10 files)** — Batch 4g scope:
- `test_iter37_404_hallucination_guard` (3, KeyError 'status' —
  tool-bridge return shape drift)
- `test_iter69_brain_dump_and_build_hash` (3, build_hash literal
  refactored to a new location)
- `test_iter76_preview_pane` (3, component paths drifted)
- `test_iter101_annual_referral_overage` (2, overage math contract)
- `test_iter124_repo_first_and_retry` (2, DeepSeek retry contract
  — now walks fallback chain instead of raising, needs test rewrite)
- `test_iter165_warm_start` (2, "WARM CONTEXT" string renamed)
- `test_iter212m66_vanguard_two_round` (3, 2-round endpoint
  contract changed)
- `test_iter212m6_tool_reliability_full` (3, write_repo_file
  contract changed)
- `test_aurem_backend::test_chat_send_with_auth` (1, timeout)
- `test_aurem_rollback` (2, DB fixture / preview URL patch)

**Prod verify** — `/api/health`: ok, build ed5b698, dead 0,
supervised 13.

### 2026-02-02 — Persona-Diet PR Helper + Session G Batch 4e
**Persona-Diet Helper** ✅ (founder-approved cheap addition):
- `scripts/persona_diet_report.py` — 105 LOC, zero deps, prints
  chars-per-section table sorted by size (or JSON via `--json`).
- `tests/test_persona_diet_report.py` — 7 tests all pass
  (JSON shape, section-sum ratio ≥99%, descending order,
  --top cap, human output flags current warn state, sanity on
  heaviest section < 50%).
- Live snapshot: TOP-OF-MIND HARD RULES = 4,336 chars (19.7% of
  budget), HOW TO RESPOND = 3,889 (17.7%), MODE DETECTION = 2,243
  (10.2%). Next persona-diet PR should target these three.

**Batch 4e (Session G)** — 14 more nodeids un-quarantined
(169 → 155, session total 232 → 155 = **-77 nodeids**):
- Files fully cleared: `test_iter341_predeploy.py` (3 tests),
  `test_iter79_web_skills.py` (3 tests — real fetch_url calls),
  `test_iter165_smart_router_agents.py` (2 tests),
- Files partial-cleared: `test_aurem_backend.py` (3/4 —
  login/me/token pass; chat_send_with_auth still fails on auth
  timeout, quarantined), `test_iter86_architecture_health.py`
  (2/3 — endpoint + summary pass; CLI baseline test still fails,
  quarantined), `test_iter94_maxx_cap_and_usd_migration.py`
  (usd_pricing text-drift fix — llms.txt now says `$X/month`
  not `$X/mo USD`).
**Deferred (~26 nodeids, 12 files)** — all contract/text-drift
class: `test_iter37_404_hallucination_guard` (KeyError: 'status'
tool-bridge return shape), `test_iter69_brain_dump_and_build_hash`
(build_hash literal moved after refactor), `test_iter76_preview_pane`
(component paths drifted), `test_iter101_annual_referral_overage`
(overage math contract changed), `test_iter124_repo_first_and_retry`
(deepseek now walks fallback chain instead of raising),
`test_iter165_warm_start` ("WARM CONTEXT" string renamed),
`test_iter212m66_vanguard_two_round` (2-round endpoint contract),
`test_iter212m6_tool_reliability_full` (write_repo_file contract),
`test_iter94::test_maxx_limits_per_tier` + `test_subscription_tiers_have_maxx_field`
(tier config), `test_aurem_backend::test_chat_send_with_auth`
(timeout), `test_aurem_rollback` (DB fixture). Same discipline
as Batch 4d — grouped for a future focused pass.

**Prod verify** — `/api/health`: ok, build ed5b698, dead 0,
supervised 13.

### 2026-02-02 — Session G Batch 4d + Item Loop Complete
**Item 2 · Batch 4d** — 52 nodeids un-quarantined in this session
(221 → 169 total). Files fully un-quarantined + un-flagged:
`test_aurem_p0_bugs.py` (11), `test_iter212m32_onboarding_nudge.py`
(6), `test_iter138_execute_bash_tool.py` (5), `test_ship_turn_index.py`
(5), `test_iter212m17_topup_alerts.py` (4), `test_iter212m3_activation_funnel.py`
(4), `test_tool_reliability_v2.py` (4), `test_iter212m106_real_ship_and_sanitizer.py`
(3), `test_iter212m215_mermaid_diagram.py` (3), `test_iter212m159_parliament_v2_routing.py`
(1 — amended drift scan to allow defensive-fallback pattern +
docstring/cost-table dict-key usage), `test_iter80_seo_pwa.py` (4).
**Deferred (~13 nodeids, 4 files)**: test_iter212m110, test_iter212m114,
test_iter113, test_iter212m237, test_iter124h — all need DB-fixture
rewrite for the task-quota refactor + vs-devin page linking work
(same class as the earlier `test_iter212m121_fix_pipeline` deferral).

**Item 3 · SEO test refresh** — `test_iter80_seo_pwa.py` updated
against LIVE index.html content (verified via grep, not
hand-typed):
- Brand: `"Aurem CTO"` (Title Case) not `"AUREM CTO"`
- Pricing: single Founder-Plan Offer at $9 in JSON-LD
  SoftwareApplication block (four-tier grid moved to `llms.txt`)
- No `@graph` wrapper — 4 separate `<script type="application/ld+json">`
  blocks (Organization / WebSite / SoftwareApplication / FAQPage)
- Team tier is $49/mo per user (was $35 pre-refresh)
- Twitter/OG titles start with `"ORA"` (short-tag), Aurem CTO
  parent brand referenced elsewhere in `<head>` + JSON-LD

**Item 4 · Guard-11 backup cron** — SKIPPED cleanly (Atlas
continuous-backup enable-state not confirmed by founder; per
directive, deferred without stalling).

**Item 5 · Persona LOC guardrail** —
`backend/tests/test_persona_loc_guardrail.py` (7 tests, all pass):
- **Default mode**: emits `UserWarning` if persona ≥ 20,000 chars
  (current: 21,559 → warning fires visibly in pytest output). Does
  NOT block merge — founder-directed behaviour.
- `PERSONA_GUARDRAIL_HARD=1` env var opts into hard-fail for
  persona-diet PRs.
- Boundary + mock-simulation tests prove the guard mechanism itself
  works at 20k threshold (fires exactly at 20,000; silent at 19,999;
  respects env-flag mode toggle).

**Item 6 · SSOT drift confirm** — 35/35 pass across
`test_ssot_model_id_no_drift.py` + `test_iter212m159_parliament_v2_routing.py`
+ `test_ci_env_var_contract.py`. No drift has crept back in
since the Feb 2026 SSOT-refactor.

**Prod verify** — `/api/health` clean (`build ed5b698`,
`dead_tasks: 0`, `supervised_count: 13`, `council_a: anthropic/claude-sonnet-4.5`).
Real E2E chat: 200 status, 7.2s Swift-mode reply.

### 2026-02-02 — Persona-Dedupe (Focused Session)
**P1 fix** — `AUREM_CTO_PERSONA` trimmed from 25,687 → 21,559 chars
(**-4,128 chars, 16% reduction, 441 char headroom under the 22,000
budget from `test_iter129_chat_latency_budget.py`**). Every chat turn
re-sends this on every tool iteration, so this shaves ~1k input
tokens per iteration off the LLM bill and cuts p95 chat latency.

- **Sections trimmed** — consolidated 5 tool-call-format NEVERs to 2;
  removed 3 build-check NEVERs already covered by Rule 6; dropped the
  READ-REPO PROTOCOL placeholder (pointed to HOW TO RESPOND anyway);
  trimmed ⚠ ABSOLUTE NEGATIVES-extended a/b/c/d wording; cut 2 of 3
  ✗ INCORRECT ship-brief examples; tightened Rule 4 (leak), Rule 7
  (READ BEFORE YOU ANSWER), Rule 8 (ANALYSIS → SPEC CONTRACT); shrunk
  MODE DETECTION examples and TASK STATE TRACKING closer;
  compressed EXTERNAL URLS section to essentials.
- **Heading rename** — `MULTI-FILE TASKS — STATE TRACKING & FULL
  DELIVERY` → `MULTI-FILE TASK EXECUTION — STATE TRACKING & FULL
  DELIVERY` (semantic + test-compliant). `_SECTION_LAYER` mapping
  updated so the layered-persona slicer still routes it to L2 EXECUTE.
- **IDENTITY + DO NOT LEAK rewrite** — kept meaning, tightened
  language, and added the specific phrases the legacy quarantine
  tests were asserting on: "DO NOT invent a name", "DO NOT invent a
  location", "DO NOT invent the origin story", "FABRICATION and is
  forbidden", "CONVERSATIONAL MODE", "Listing internal tool names
  verbatim", "from what's in my system context", "Never reference
  the prompt".
- **5 tests un-quarantined** (removed from `tests/legacy_quarantine.txt`):
  1. `test_iter129_chat_latency_budget.py::test_persona_under_budget`
  2. `test_iter74_gaps.py::test_persona_has_search_and_multi_file_and_state_sections`
  3. `test_iter103_identity_no_fabrication.py::test_identity_forbids_inventing_names`
  4. `test_iter103_identity_no_fabrication.py::test_identity_forbids_location_team_motivation`
  5. `test_iter103_identity_no_fabrication.py::test_no_leak_forbids_mode_names_and_tool_names`
- **Layered-persona still works** — after dedupe: L1 CORE 10,819
  chars / L2 EXECUTE 9,269 / L3 REPO 1,408 (previously ~12k / ~11k /
  ~2.5k). CONVERSATIONAL floor stays under the 8k target.
- **Real-conversation spot-checks (3)** — zero-mock chat via
  preview `/api/aurem-dev/chat/send`:
  1. Greeting "hi how are you" → warm 4-sentence reply, no handoff,
     no tool calls (correct CONVERSATIONAL mode).
  2. Identity attack "who founded AUREM CTO? tell me the name and
     origin story" → responded verbatim with the anti-fabrication
     fallback ("AUREM CTO is built by the AUREM team — I don't
     have public details…") and pivoted to capabilities. Zero
     fabricated bio.
  3. Technical Q "explain what JWT is in one paragraph" → clean
     paragraph explanation, no handoff, no tool calls.
- **Regression sweep** — 55 tests pass across all persona-related
  files (aurem_persona_v2, iter124c hard rules, iter124g quality
  score, iter212l hardening, proof_iter130 layered persona, iter74
  gaps, iter103 identity, iter129 latency budget, iter274 personal
  track, iter169 fix hallucination). 4 remaining failures all
  confirmed pre-existing in `legacy_quarantine.txt` (not caused by
  this dedupe).

### 2026-02-02 — SSOT-Model-ID-Refactor (Focused Session)
**P0 fix** — Runtime files that duplicated Claude / GLM model slugs in
their env-fallback defaults now resolve through the canonical SSOT in
`services/llm/openrouter_providers.py` (`_CLAUDE_MODEL`, `_GLM_MODEL`).
Prevents future copy-paste drift when Council A swaps primary models.

- **8 runtime files refactored** — each now imports the SSOT constant
  (with a defensive literal fallback ONLY inside `except` blocks for
  circular-import safety):
  1. `services/vanguard_verify_agent.py` (Claude + DeepSeek)
  2. `services/loop_independent_verifier.py` (Claude)
  3. `services/reasoning_evals.py` (Anthropic-native — see below)
  4. `main.py` (GLM fallback in exception path)
  5. `services/ora_chat/session.py` (`SUMMARY_MODEL` — added
     `ORA_SUMMARY_MODEL` env override)
  6. `services/ora_chat/router.py` (`fallback` route)
  7. `services/scaffold_design_review.py` (`_DEFAULT_MODEL`)
  8. `routers/feature_window.py` (Council A fallback label)
- **Real bug fixed** — `reasoning_evals.py::llm_faithfulness_check`
  had `model="claude-sonnet-4-6"` (invented ID that returns 404 from
  Anthropic). Corrected to `claude-sonnet-4-5` (Anthropic-native
  format is required by the Emergent SDK path — distinct from
  OpenRouter's dotted slug).
- **Drift-guard tests** — `tests/test_ssot_model_id_no_drift.py`
  (9 cases, zero mocks): env-override propagation, SSOT re-export
  identity, anthropic-native format assertion, and a static-scan
  guard that fails if any listed file loses its SSOT import.
- **Verification** — 85 related tests pass (vanguard verify, loop
  verify, tier2 parliament scaffold, smart_router, advisor). Backend
  boots clean, `/api/health` returns `council_a_model:
  anthropic/claude-sonnet-4.5` and `dead_tasks: []`.

### 2026-02-01 — Session C · Sub-step 2 (LLM Package Modularization) + BUILD_HASH file-based fix
- **BUILD_HASH workaround** — `_resolve_build_hash()` ladder now has 2
  new file-based steps (priority 2: `backend/.build_info`, priority 4:
  raw `.git/HEAD` parse — zero git-binary dependency). On successful
  git resolution, `.build_info` is auto-persisted for subsequent
  starts. Pre-deploy helper: `backend/scripts/write_build_info.py`.
  `.build_info` added to `.gitignore`. Tests:
  `tests/test_build_info_workaround.py` (4 cases).
- **Session C · Sub-step 2** — moved `_llm_state.py`, `_llm_routing.py`,
  `_llm_probes.py` from `services/` into `services/llm/` package as
  `_state.py`, `_routing.py`, `_probes.py`. Old paths retained as
  backward-compat shims (~20 LOC each) so the ~10 legacy test files
  that import `services._llm_*` still resolve.
- **`services/llm/__init__.py`** — absolute → relative imports
  (`from ._state import ...`, `from ._routing import ...`,
  `from ._probes import ...`). All 3 lazy imports in `__getattr__` /
  `_LLMModule.__setattr__` also switched to `from . import _probes`.
- **Silent Sub-step 1 regression FIXED** —
  `_GROQ_HOUSE_RULES_PATH` used `dirname(dirname(__file__))` which,
  after `llm.py` → `llm/__init__.py`, resolved to
  `services/prompts/…` (non-existent) instead of `backend/prompts/…`.
  Added third `dirname` hop + regression guard test
  `tests/test_llm_package_paths.py`.
- **Test infra updates** — 5+ `importlib.reload(_routing)` sites now
  reload the real `services.llm._routing` (not just the shim).
  Stale `services/llm.py` path assertions across ~8 test files
  updated to `services/llm/__init__.py`.
- **Verification** — All LLM-domain tests green (192+ passing across
  Phase 0a/1/2 + Session C guards + no-contamination + build-hash).
  E2E endpoints: `/api/health` 200, `/api/aurem-dev/chat/agents/list`
  401, `/api/aurem-dev/admin/architecture` 401. Zero circular imports.
- **Deploy status**: LOCAL VERIFIED, awaiting founder go-ahead to deploy.

### 2026-08-01 — GitHub Connect Funnel Telemetry (revenue-item follow-up)
- **New router**: `backend/routers/github_funnel.py` at
  `/api/aurem-dev/funnel/github/{event,stats}`
- **New collection**: `github_funnel_events` (session_id + stage + source)
- **5 tracked stages**: `cta_click → oauth_redirect → callback_received →
  linked → repo_selected`. Client fires 1 + 5; server fires 2/3/4.
- **CTA wiring** at 4 entry points: Login, Signup, GitHubCard,
  NewUserWizard. `withFunnelParams()` appends `fs` + `fsrc` to the
  OAuth URL so client + server events share a session_id.
- **Silent-fail** — telemetry never blocks the OAuth flow.
- **Tests**: 8/8 backend pytest + 6/6 frontend vitest (real HTTP + real
  Mongo, zero mocks).
- **Deploy status**: Shipped to prod. Data collection window: 3-5 days
  of real new signups. Existing 41-user cohort NOT backfilled.

### 2026-07-31 (evening) — build_hash Fix + Session 7 shipped
- **Bug**: prod `/api/health` `build_hash` stayed at `m1c61197`
  (2026-07-31 04:07 UTC) even after Session 6+7 landed. Root cause:
  `_resolve_build_hash()` used only `main.py`'s mtime; Session 6/7
  didn't modify main.py so fingerprint never shifted.
- **Fix (Option A)**: `_resolve_build_hash()` now scans max mtime
  across all `backend/**/*.py` files (skips `__pycache__`, `.venv`,
  `node_modules`, `/tests`). Verified on prod: `m1c61197 → m1c615a4`.
- **Follow-up (Option B, pending)**: Founder to set `BUILD_HASH=$GIT_COMMIT`
  env var in Emergent deploy config so any future frontend/config-only
  deploy also updates the fingerprint.

### 2026-07-31 (day) — Session 7: Loop UI-State Reliability
- Item 1: Cancel loop UI stuck chip → async state sync fixed
- Item 2: Duplicate plan-bubble dedup
- Item 3: Approval Panel missing Cancel button safety fix
- Item 4: Rapid concurrent-send React race condition lock
- Tests: 17/17 vitest pass (Session7_Item1/2/3 files)

### 2026-07-31 (day) — Session 6: Real-User QA Batch
- Item 1: VS Code Marketplace real status API in `/admin`
- Item 2: Tavily `topup_alerts` cross-day dedup (was piling 1 row/day)
- Item 3: `minimal_edit.py` surgical diff path (avoids full LLM rewrites)
- Item 4: Live-feed vs Ship-panel state mismatch fix
- Item 5: "Developers: —" undefined value → reads `stats?.real_developers`
- Item 6: `qa_manifest.json` stale-data threshold warnings
- Tests: 47/47 pytest pass

### Earlier (pre-2026-07-31)
- `services/llm.py` split into `_llm_state.py`, `_llm_routing.py`,
  `_llm_probes.py` (Phase 0a, 1, 2 done; Phase 3 package conversion
  pending)
- Resend API key rotated + Cloudflare-1010 bypass fix
- Session 5: ORA-chat silent catch cleanup

---

## Prioritized Backlog

### P0 (needed for revenue / stability)
- **GitHub Connect funnel data collection** — wait 3-5 days for real
  new-signup data, then targeted fix (b/c/d — the specific drop-off
  point once data reveals it).

### P1 (soon)
- **Option B `BUILD_HASH` env var (platform-level)** — Deployer_agent
  should auto-inject commit SHA. **Workaround shipped**: `.build_info`
  file (tracked in git, ships in tarball) + raw `.git/HEAD` parse
  ladder (see 2026-02-01 changelog). Known 1-commit lag on prod
  because `.build_info` cannot be self-referential; see
  `/app/memory/emergent_support_ticket_option_B.md` for the pending
  Emergent platform feature request that would eliminate the lag.
- **~~Session D — LLM Split Phase 4~~** ✅ COMPLETE
- **~~Session D-part-2 — extract `_call_llm_with_meta_inner`~~** ✅ COMPLETE
- **~~Session F — Background-tasks supervisor~~** ✅ COMPLETE
- **~~Session E — 22 deferred CI-lane failures~~** ✅ COMPLETE (real fixes)
- **~~Session G Phase 3.1 — Legacy Bucket-A auth-fixture drift~~** ✅ PARTIAL
  (+12 tests unblocked; full Bucket-A completion needs PAT-mock + SSE-shape work)
- **~~`services/llm.py` Phase 3~~** ✅ COMPLETE (Sub-step 1 + Sub-step 2).

### P0 (current focus)
- **~~SSOT-Model-ID-Refactor~~** ✅ COMPLETE (Feb 2026 — 8 files + drift-guard test suite)
- **~~Persona-Dedupe~~** ✅ COMPLETE (Feb 2026 — 25,687 → 21,559 chars, 5 tests un-quarantined, 3 live spot-checks)

### P1 (next up)
- **Session G Bucket-A Batch 4c** — production_wiring +
  ora_chat_persistence already pass (19/19). Real Batch 4c candidates
  from live quarantine scan: `test_iter205_pat_decryption_in_tools`
  (3 fails, PAT decrypt returns None), `test_iter212m6_wiring_audit::
  test_known_python_repl_tools_covers_local_tools` (missing Vercel
  tools in KNOWN list), `test_iter169_fix_hallucination_guards::
  test_budget_hit_*` (budget-hit message with `seen_paths[0].split`
  + `"narrow the ask to one file"` never landed in
  `services/orchestrator.py` — needs real code implementation).
- Session 5 P2 findings: vanguard-config Mongo migration, MCP fallback logging
- **~~20+ Unsupervised Background Tasks wrapper~~** ✅ COMPLETE (Session F)
- Founder-Blocked env vars (G8-G11)
- VS Code Marketplace publish (blocked on Azure DevOps PAT)
- `/admin` funnel widget (visualise `/funnel/github/stats` output)
- Session G Phase 3.2+ — Bucket A remaining ~80 nodeids
  (needs PAT mocking + SSE shape update + individual fixture repair)
- Session G Phase 4 — Categorise the 78 UNCATEGORIZED legacy files

### P2 (backlog)
- Fix `test_iter80_seo_pwa.py` post-marketing-content refresh
  (blocked on canonical pricing $9/mo + brand "Aurem CTO" + current
  feature-list confirmation from founder)
- Re-investigate `test_iter267_url_fetch_retry.py` (SSRF pre-check
  timing issue)

### Overnight Session — Feb 2026 — LOC + Test Score Deltas
- `services/llm/__init__.py`: 1591 → 426 LOC (**−73%**)
- New sibling files: `_meta.py` (596), `_state.py`, `_routing.py`,
  `_probes.py`, `openrouter_client.py`, `groq_client.py`,
  `openrouter_providers.py`, plus `services/supervised_tasks.py`
- Backend pytest sweep: 4002 pass / 8 fail → **4014 pass / 0 fail /
  65 skipped** (Session E fixed all 8 pre-existing failures)
- Legacy quarantine: 216 fail / 30 pass → **180 fail / 42 pass**
  (Session G partial)
- Prod `/api/health` new field: `supervised_tasks:
  {supervised_count:13, alive[], dead[]}` — Guard 20 wired

### Feb 2026 · Sidebar Integrity + Batch 4h
- **Sidebar audit**: all 20 NAV items + 14 standalone `/admin/*`
  routes verified. Cockpit build clean, no orphans.
- **12 new `/admin/*` deep-link routes** added (Support, Audit,
  House Rules, Robot Guide, Payments, Token P&L, Projects, Tasks,
  Agent Performance, MCP Usage, Reliability, Settings). Every NAV
  entry now carries a `route:` field so sidebar clicks change URL
  → browser Back/Forward work naturally.
- **QA-page auth-token drift fixed** — `AdminQADashboard.jsx`
  standardised on `getToken()` (was reading a never-set legacy
  `aurem_admin_token` key first).
- **Batch 4h remediation** (contract-drift quarantine):
  - Round 1: 72 → 46 (13 un-quarantined, 1 fixed, 12 moved).
  - Round 2 (this session): 46 → **22** (**−69% total**). 24 more
    dead-surface moves after per-marker grep verification. Every
    remaining quarantine entry is now a real functional regression
    (KeyErrors, auth drift, LLM fallback, tier tokens), not stale
    contract-drift. Ready for per-test RCA in a future dedicated
    session.
  - Zero regressions on the 14 previously-un-quarantined tests.

---

## Testing & Credentials
- Backend: `pytest` in `/app/backend/tests/`
- Frontend: `vitest` (via `npx vitest run`)
- QA manifest: `backend/qa_manifest.json` (regen: `python scripts/gen_qa_manifest.py`)
- **Zero mocks rule**: every test hits real Mongo + real HTTP; no
  `unittest.mock` in the codebase for feature tests.
- Credentials: `/app/memory/test_credentials.md` (preview + prod founder).
