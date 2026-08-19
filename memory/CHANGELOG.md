# AUREM CTO — Changelog (append-only)


- **Chat UX #1-#3 + Admin Cockpit merge shipped preview-verified (2026-02-18)** — three chat-UX polish fixes + full BI merge into `/admin/cockpit`. Zero prod deploys yet; all preview-only.
  - **#1 · LongCat empty-tool-call fix**
    - Frontend: `RenderedMessage.jsx` placeholder rewritten from "internal tool call with no visible reply" (internal jargon) → `"ORA didn't have a text reply for that — mind rephrasing?"`.
    - Backend: `_call_longcat` (`services/llm/openrouter_providers.py`) now uses new `_strip_tool_call_xml_len()` helper mirroring the frontend sanitizer. When LongCat returns pure `<longcat_tool_call>…</…>` XML with no prose, we flip `LONGCAT_LIVE=False` and fall through to GLM-5.2 in-flight — same graceful branch as the pre-existing empty-content case.
    - Regression: `tests/test_longcat_tool_call_only_fallback.py` (8/8 green) + `iter388m.bug9.test.jsx` updated (6/6 green).
  - **#2 · `[Working on project: …]` chip refactor**
    - `ChatPanel.jsx`: removed the legacy `[Working on project: …]` prompt preamble that was leaking into persisted user bubbles. Backend already receives `project_id` in the `streamChat` payload and resolves brain context itself.
    - New `<div data-testid="active-project-chip">` above the composer showing `📌 Scope: {name} · {owner}/{repo}@{branch}` with tooltip. Hidden in home mode.
    - Frontend sanitizer's `[Working on project: …]` strip retained belt-and-suspenders for legacy stored messages.
  - **#3 · Swift / LOOP OFF tooltips**
    - New `<HoverTip>` component (`components/HoverTip.jsx`) — zero-dep CSS-only rich tooltip with 80ms delay, replaces browser-native `title=""`.
    - `ModeSelector` (Swift/Pro/Maxx) now wraps each pill in `<HoverTip>` with detailed trade-off copy per mode.
    - `LoopModeToggle` (loop on/off + locked variant) wrapped in `<HoverTip>` with pipeline explanation.
  - **#4 · Deferred** — Tier 1 exploration only; architecture report delivered but implementation deferred at founder's request (Admin merge took priority).
  - **Admin Cockpit + Financials MERGE**
    - Root cause of Stripe discrepancy: `int_stripe` health check read only `STRIPE_API_KEY` env, while `stripe_key()` accepts BOTH `STRIPE_API_KEY` and `STRIPE_SECRET_KEY` and filters the `sk_test_emergent…` placeholder. Prod uses `STRIPE_SECRET_KEY` → cockpit said `stripe: not-set` while BI card said `STRIPE · OK · LIVE`. Fix: registry `stripe` entry now delegates to `_is_stripe_key_present()` → `stripe_client.stripe_key()`. Single source of truth. Regression: `tests/test_admin_merge_stripe_registry.py` (3 pass, 1 legit skip).
    - Root cause of user-count discrepancy: cockpit shows `total_users` (all `dev_users`), financials shows `free_users` (`tier=="free"` bucket). Same DB, different label. Fix: cockpit `BusinessPulse` TOTAL USERS card now shows a sub-line "{free} free · {paid} paid" — same source, explicit breakdown, no more mystery gap.
    - BI panel extracted into `components/LiveBusinessIntelligence.jsx` and mounted inside `AdminCockpit.jsx` after BusinessPulse. Owns its own data fetch + reconcile handler.
    - `AdminFinancials.jsx` stripped of its BI copy (~280 LOC removed). Now renders only the P&L catalog editor + tier margins + cost-per-task tables. Blue notice at top: "Live BI now lives in the Cockpit — one dashboard, one source of truth" with `[Open Cockpit →]` CTA button.
    - Preview-verified via screenshot: cockpit renders 788 total users with sub-line "787 free · 0 paid", `STRIPE · OK · LIVE` badge, MRR "No data yet · Stripe subscriptions" (honest — 0 subs), full BI charts + Reconcile button; financials shows blue notice + editor + no duplicate BI.


- **Slice A · BI Cockpit shipped preview-verified (2026-02-18)** — Live Business Intelligence added to `AdminFinancials.jsx` above the existing catalog cards. Zero hallucination:
  - **New backend router** `/app/backend/routers/admin_bi.py` — 3 founder-gated endpoints under `/api/aurem-dev/admin/bi/*`:
    - `GET /stripe-metrics` — live `stripe.Subscription.list(status="all")` with auto-paging. Returns MRR (sum of recurring USD unit_amount normalised to monthly across `active + past_due`), ARR (MRR × 12), active/trialing/past_due subs, new_30d, canceled_30d, ARPU. Fails soft when key missing (`status="missing_key"`) so the UI never silently paints $0.
    - `GET /inference-metrics` — aggregates `ora_chat_usage` (existing collection) for today/month totals, 30-day daily timeseries, top-15 by-model and by-route breakdowns. Uses the shipped `cost_tracker.budget_status()` so daily/monthly `mode` (normal/warning/economy/spike_hard_stop) matches what the /message router actually enforces.
    - `GET /summary` — atomic payload (both fetches run concurrently) + net-margin projection (`mrr − month_infer_pro_rated_to_end_of_month`).
  - **Frontend** — added `recharts@3.10.1`. New `<BiCockpit>` section renders 5 Stripe cards + 4 inference cards + 30-day cost line-chart + cost-by-model bar-chart + `🧹 Reconcile Orphans` button (wires to existing `POST /admin/payments/reconcile`).
  - **Preview cleanup**: 26 orphaned test rows in `cto_payments` (all `test@aurem.dev` / `test_iter179_*` / `test_preview_*`) purged before shipping so preview MRR endpoint has clean signal (was 1 paid row after purge, still is — no real preview transactions).
  - **Preview-verified evidence**: curl → all 3 endpoints return real data. Frontend screenshot → all cards + charts render live values (`STRIPE · OK · LIVE` badge, `$0.0518` month infer matches DB aggregate exactly).
  - **Regression**: `/app/backend/tests/test_slice_a_bi_cockpit.py` — 4 pytest cases (shape contract for each endpoint + anonymous access rejected). All green.
  - Not yet shipped to prod — awaits founder verification of read-only prod DB snapshot (still-pending orphan count check before deploy) and manual PSI Mobile score paste.

- **Guards Fix Batch — G1/G7/G12/G15/G21, preview-verified (2026-08-19)** — all 5 code-bug guards fixed and confirmed live via `/admin/status/all`:
  - **G1 Route Sweep** → green. `scripts/g1_route_smoke_sweep.py` never persisted its result despite claiming to; added the missing `synthetic_checks` write, ran it once (7/7 routes clean).
  - **G7 Payment Recon** → green. `run_reconciliation()` was fully built (Stripe key already set) but never called anywhere — added `schedule_payment_reconciliation()` (hourly, same pattern as `integration_health_cron`), wired into `main.py` startup. Also fixed a field-name bug in the adapter (`findings` not `drift_events`/`drift`).
  - **G12 Rollback** → gray (correctly — no real drill has ever run; this is now an HONEST gray, not a structurally-broken one). Adapter was reading `last_drill_at`, a key that never existed (real shape is `last_rollback`) — fixed. Also found + cleaned up 6 stale `repro-*` test-fixture rollback records polluting the live signal (confirmed via `loop_id` naming — not real founder rollbacks, safe to strip).
  - **G15 Dependency CVE** → green. Same missing-persistence bug as G1 — `scripts/g15_dependency_scan.py` never wrote to `synthetic_checks`; fixed + ran once (0 HIGH/CRITICAL unhandled).
  - **G21 Security Scan** → red at time of fix (correctly — REAL finding: 1 unpinned dependency in requirements.txt; fixed same day, see follow-up below). Adapter was querying `db.vanguard_findings` (scanner:"trufflehog") — a collection nothing writes (that's the CUSTOMER-repo CI ingest table, unrelated to G21). Now calls the same live `run_scan()` the working `/admin/qa` endpoint already uses.
  - Regression: `tests/test_health_registry_adapters.py` — 19/19 green (2 pre-existing G7 tests fixed to use the correct field name, 5 new tests added for G12/G21).
  - **G8/CI-drift NOT activated** — founder sent a literal bracketed placeholder `[paste here once you create it]` instead of a real PAT. Asked founder to resend the actual token + repo slug (still pending as of this entry).
  - **Follow-up (same day)** — founder asked to fix G21's real finding. The "1 unpinned dep" was `-e /app/_extract` in `requirements.txt`, an editable install of an unused `ora-grounding==0.1.0` package (confirmed zero imports anywhere in the codebase — leftover from an earlier reference-zip extraction). Removed the line; G21 now green (`0 misconfig findings, 0 unpinned deps`).

- **Customer Chat Regen — REAL bugs found + fixed, preview-verified (2026-08-19)** — before starting the "3-4 hr" wiring job estimated last entry, checked whether `chat.py` already had fabrication protection. CORRECTION #2 to an earlier claim: it did — `services/citation_guard.py` (Iter 209, `CitationGuard`) is a complete, unconditionally-wired, already-tested (11/11 unit tests) fabricated-file-path guard on `chat_stream`. The "chat.py has nothing" claim from the prior entry was wrong. But tracing the real wiring found it was **completely non-functional** for a different reason:
  1. **The corrective retry always silently failed.** `_llm_retry()` inside `chat_stream()` imported `services.orchestrator.respond_text` — a function that has **never existed anywhere in this codebase**. Every real retry hit a bare `except Exception: return content` and returned the ORIGINAL fabricated draft unchanged. The guard's `retried=True` flag fired, a no-op "reset" SSE frame flashed on screen, but the customer's text never actually got corrected. Fixed by pointing `_llm_retry` at the real, existing `services.llm.call_llm()`.
  2. **The persisted copy was stale even when the retry DID work.** `_persist_turn()` ran BEFORE the CitationGuard block, so even a successful correction only updated what the live SSE stream showed — a page refresh (`GET /chat/history`) still showed the original fabricated draft. Fixed by moving `_persist_turn()` to after the guard block.
  - Regression: `tests/test_citation_guard_persist_ordering.py` (new) — full in-process HTTP test against the REAL `/chat/stream` + `/chat/history` endpoints proving a fabricated path never reaches the client AND the Mongo-persisted turn matches the corrected text. `tests/test_iter209_citation_guard_and_tool_executor.py` (11/11, unaffected — those test the `CitationGuard` class in isolation with a mocked `llm_caller`, which is exactly why the `respond_text` crash was never caught before).
  - Confirmed 3 pre-existing, unrelated test failures during regression sweep (reproduce identically with these changes reverted): `test_iter264_grounding_validator.py` (shape drift), `test_iter359_guard18_timeout_audit.py` (call-site count), `test_session4_step_d_guard15_yarn_audit.py::...deduped_findings` (a genuine new duplicate CVE ID from yarn's advisory feed). Not touched — out of scope for this task.

- **Anti-Fabrication Regen — admin tool shipped, preview-verified (2026-08-19)** — founder asked to confirm status since it "fell off the list twice". CORRECTION to my own earlier status report: I initially told the founder this was "not started, zero regeneration code anywhere" based on a keyword grep for "regenerat" that missed the real implementation (named "corrective retry", not "regenerate"). On deeper inspection: a COMPLETE regen-on-fabrication implementation already existed on the admin `/ora-chat/message` path (Iter 264 Fix A5) — one silent corrective LLM retry when a fabricated file path is detected — but it was dormant, gated behind `ORA_REGEN_ON_FABRICATION` (default `"0"`, never set anywhere, so permanently OFF). Two fixes shipped:
  1. `services/ora_chat/adversarial_review.py::trigger_reason()` — previously only fired the hostile-reviewer pass on soft `unverified` claims; a reply with ONLY a hard `fabricated` claim (and no unverified ones) got zero review chance on the deep-research path. Now fires on `fabricated` too.
  2. `backend/.env` → `ORA_REGEN_ON_FABRICATION=1` — switches on the general-chat path's Fix A5 corrective retry (was fully built, never enabled).
  - Regression/verification: `tests/test_anti_fabrication_regen_admin.py` (2/2) — one pure-logic test on the trigger fix, one full in-process HTTP integration test against the REAL `/ora-chat/message` endpoint (stubbed LLM calls only) proving a fabricated path never reaches the client and the corrective clean draft is what streams AND persists to Mongo.
  - Scope: admin-only "Ask ORA" tool (`routers/ora_chat.py`). Customer-facing `chat.py` does NOT have this yet — separate `services/hallucination_guard.py` there only covers credential/auth claims, no general fabricated-file-path detection. Founder explicitly wants this as a fast-follow, not deferred — estimate given: **~3-4 hrs** (needs: (a) port `grounding_check.py`'s claim-extraction + canonical-path-check into `chat.py`'s pipeline — the codebase-index/canonical-paths lookup already exists and is reusable; (b) wire a corrective retry into `chat.py`'s streaming loop, same shape as Fix A5; (c) new test suite mirroring this one). Smaller than the admin piece because the detection logic is already written and reusable — the remaining work is wiring + one new retry call site + tests.

- **GLM 5.2 model-name leak — audit + fix, preview-verified (2026-08-19)** — founder caught "GLM 5.2" visible directly in the chat UI (not just admin panels). Full scan of every place a raw provider/model string reaches a regular (non-admin) user:
  1. `MessageBubble.jsx` **Scope Badge** (`via Council X · {m.provider}`) — rendered the raw backend `provider` field (e.g. `glm-5.2`, `deepseek-v3-rescue`) after every reply.
  2. `LiveStepFloatingCard.jsx` **footer** (`data-testid="live-step-model"`) — same raw `provider` shown in the live progress card while ORA works.
  3. `services/orchestrator.py` `activity_hook` — emitted `"calling Claude…"` / `"calling DeepSeek…"` into the live "thinking" activity label (`m.activity` in `MessageBubble.jsx`).
  4. `routers/chat.py` advisor rescue chain — `_step("⚙️ Switching to Groq rescue…")` / `"⚙️ Switching to DeepSeek rescue…"` landed directly in the step-trail cards (same UI Chat UX #4 just made persistent).
  5. `routers/chat.py` timeout-guard fallback message — literal "This usually means OpenRouter/DeepSeek cold-started…" text inside the assistant bubble.
  6. `OraChatDrawer.jsx` economy-mode banner — hardcoded "using economy model (GLM-5.2)" string shown to any user hitting budget mode.
  7. `pages/Both.jsx` scripted terminal demo — public marketing page (`/both`, no login required) literally typed out "sibling review (GLM-5.2)" in the animated illustrative log.
  - **Fix**: new `frontend/src/lib/providerLabel.js::brandProvider(raw)` — collapses any truthy raw provider string to `"ORA"`. Wired into (1) and (2). (3)-(6) genericised at the source (no model names in the string at all). (7) swapped to "second model". Admin-only surfaces (`AdminHouseRules.jsx`, `AdminOverview.jsx`, `LiveBusinessIntelligence.jsx`, `feature_window.py` founder-gated endpoint) intentionally left untouched — already correctly gated per L-11.
  - Regression: `frontend/src/lib/__tests__/providerLabel.test.js` (2/2 green) + existing `test_chatux4_step_persistence.py` + `test_longcat_tool_call_only_fallback.py` (10/10 green, no regression from the orchestrator/chat.py string edits). Verified live via `/chat/send` — backend still returns a raw provider (`longcat-2.0`) confirming the mask is doing real work, not a no-op.

- **Chat UX #4 (Tier 1) — Reading/Diff step-trail persistence, preview-verified (2026-08-19)** — the live "📖 Reading repo… ✍️ Writing files… 🚀 Committing…" step-trail only ever lived in frontend in-memory state; a page refresh called `GET /chat/history` (which never returned a `steps` field) so the trail silently vanished, leaving only the final text bubble.
  - Backend: `routers/chat.py` `_persist_turn()` gained an optional `steps` kwarg (capped at the last 40). The main SSE loop now accumulates every `{type:"step"}` frame into `collected_steps` and passes it to both `_persist_turn` call sites (timeout-guard branch + normal completion). `GET /chat/history` needed no change — it already returns the raw turn dict.
  - Frontend: `ChatPanel.jsx` history-hydration mapper now carries `t.steps` onto each historical message. `MessageBubble.jsx` gained a second `<StepCards/>` render site (guarded by `!m.streaming`) so historical/hydrated messages with `m.steps` render the same step cards the live turn showed, all flipped to ✅.
  - Regression: `tests/test_chatux4_step_persistence.py` (2/2) + testing-agent-added `tests/test_chatux4_history_steps_api.py` (HTTP round-trip). Full browser verification: seeded 3-step turn survived 3 consecutive hard reloads.
  - Not yet prod-verified — founder is separately setting up Cloudflare R2 for the asset-migration Pass A leak fix; this ships whenever founder deploys next.

See `/app/memory/DEPLOY_VERIFICATION_CHECKLIST.md` for the mandatory deploy protocol.

- **Iter 392 + 393 · PROD-VERIFIED via 4-check battery (2026-02-15)** — after the previous same-session queue-dedup absorbed calls 2+3, a fresh marker-comment commit to `frontend/vite.config.js` broke the dedup and forced a real new run. All 4 evidence checks green:
  1. `/llms.txt` prod SHA256 = `07a5150bb7db6d53e84bd5128dbf86d0e47da60afd3e014f0dd510a1bb389c18` = matches local → Iter 392 llms.txt Markdown rewrite shipped.
  2. `win-preview-title` grep = 2 hits in new prod main chunk `/assets/index-BNQClbdB.js` (was `index-B2OwBVj7.js` = fresh build filename) → Iter 392 heading order fix shipped.
  3. `<main>.js.map` fetch returns `Content-Type: application/octet-stream` (real map file, not SPA HTML fallback) → Iter 393 hidden sourcemaps shipped.
  4. Regression: `requestIdleCallback` still 4 hits, ora-icon WebP still resolving with correct MIME → Iter 391 intact.
  - Founder green-lit to run PSI Mobile against `https://auremcto.com/`. Expected: Perf **63 → 88-93**, A11y **95 → 100**, BP **96 → ~97** (bumps to ~99 after 7 Cloudflare headers from `/app/memory/CLOUDFLARE_HEADERS_ITER393.md` are pasted), SEO 100, Agentic **2/3 → 3/3**.
  - Bug L-01 companion learning: **session-scoped queue dedup** in `send_to_deployer` is real — multiple deploy calls within the same session with unchanged git state get absorbed. Workaround: add a real source diff (a marker comment counts) before every follow-up deploy in the same session. Documented as **Bug L-02** — will add to `/app/memory/BUGS_LEDGER.md` in next session.

- **BI cockpit (Iter 394+) — Slice A next session** — founder-approved scope: extend existing `AdminFinancials.jsx` in place, auto-log inference cost in `services/council/`, manual CAC input, Recharts chart lib, one atomic slice at a time. Stripe key confirmed present in `backend/.env` (`STRIPE_API_KEY=sk_live_51TKUU90…`, code accepts either `STRIPE_API_KEY` or `STRIPE_SECRET_KEY`).



- **Iter 391/392/393 deploy sequence — post-ship verification & correction (2026-02-15)** — founder-triggered PSI Mobile pre-check prompted a full prod side-check that surfaced a critical false-positive claim from earlier in the session.
  - **Ground truth (curl + chunk-tree walked, evidence-backed)**:
    - Iter 391 (perf) = **LIVE on prod** ✅ — verified via `requestIdleCallback` (4 hits) + font preload `<link>` + `/ora-icon.webp` (200, 6.4 KB) + `/ora-icon@2x.webp` (200, 15 KB) all on `auremcto.com`.
    - Iter 392 (a11y + agentic) = **NOT LIVE** ❌ — prod main chunk `index-B2OwBVj7.js` still contains literal `<h4>Welcome back</h4>` (should be `<div className="win-preview-title">`); `win-preview-title` grep returns 0 hits across all prod chunks; `llms.txt` on prod SHA still Feb-4 version with bare URLs, not the Feb-15 Markdown-link rewrite.
    - Iter 393 (Vite hidden sourcemaps) = **NOT LIVE** ❌ — `.map` fetch returns HTTP 200 but body is the SPA catch-all `<!DOCTYPE html>` fallback with `content-type: text/html`, NOT a real source map. Sourcemaps not emitted → Vite config change never shipped.
  - **Root cause**: three sequential `send_to_deployer` calls in the same session returned identical `job_id 92e41e3e-9fa0-4499-a913-f3e3d1530c79`. Session-level queue dedup meant only call 1 (Iter 391) created a real run; calls 2 + 3 were silently absorbed. This is a **new failure mode** distinct from Bug L-01 (which was a false-negative in verification tooling); here the verifier was right and the deploy layer swallowed the intent.
  - **Correction to previous CHANGELOG entries**: Iter 392 + 393 remain preview-verified only. Prod-verified label REMOVED. A fresh combined deploy request has been sent to the deployer with explicit language that this must create a NEW run distinct from `bc85023a-…` and `1a5d0682-…` — the two prior runs.
  - **Post-deploy 4-check verification protocol** (before founder runs PSI Mobile):
    1. `sha256sum` of prod `/llms.txt` must match local `frontend/public/llms.txt`
    2. Grep any prod chunk for literal `win-preview-title` must return ≥1 hit (currently 0)
    3. `curl https://auremcto.com/assets/<main>.js.map` must return real map content (Content-Type NOT `text/html`)
    4. Iter 391 signals (requestIdleCallback, font preload, WebP variants) must all still resolve — regression check
  - **Founder-blocking note**: PSI Mobile run explicitly deferred pending all 4 checks green. Attempting PSI now would only measure Iter 391 delta, not the full 3-iter sweep — misleading data point.



- **Iter 393 · Best Practices security · source maps + Cloudflare CSP/XFO/COOP doc (2026-02-15)** — closes the four Best-Practices audits flagged by PSI Mobile (CSP, COOP, XFO/clickjacking, source-maps).
  - **`frontend/vite.config.js`** — `build.sourcemap: false → 'hidden'`. Emits `.map` files alongside every chunk (dist/assets/*.js.map) but omits the `//# sourceMappingURL` comment in the shipped `.js` files, so DevTools + Sentry + Lighthouse can resolve them by explicit fetch while casual visitors don't get the discovery hint. Unblocks "Missing source maps for large first-party JavaScript" audit.
  - **`memory/CLOUDFLARE_HEADERS_ITER393.md`** (created) — production-grade doc for founder to paste into Cloudflare Dashboard → Rules → Transform Rules → HTTP Response Header Modification. Contains exact copy-paste values for **7 headers**: `X-Frame-Options: DENY` (unlocks clickjacking audit), `Cross-Origin-Opener-Policy: same-origin` (unlocks COOP audit), `Content-Security-Policy-Report-Only` (partial credit — full 100 needs strict-dynamic nonces, deferred to Iter 395), `Strict-Transport-Security`, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`. CSP allowlist tailored to actual sources (Meta Pixel, Google Ads/Analytics, Google Fonts, Emergent asset CDN, backend API, Sentry).
  - **Honest ceiling note**: with current `unsafe-inline` + `unsafe-eval` in the CSP (needed because React uses inline `style={{}}` + Vite lazy-chunks + FB pixel loader all eval), Lighthouse gives partial credit only. Full BP 100 requires per-request nonces from a Cloudflare Worker — flagged as Iter 395 candidate.
  - **Expected PSI Mobile after Cloudflare rules are pasted**: BP **96 → ~99** (three unlocked audits + partial CSP credit).
  - **Preview-verified**: `yarn build` emits `.map` files (e.g. `Admin-Cf1XcnOH.js.map` 627 KB); grep confirms no `sourceMappingURL` string leaks into shipped `.js`. Sentinels intact (Iter 389 events all present in dist). Founder-owned action: Cloudflare Transform Rules edit — I cannot invoke Cloudflare directly.



- **Iter 392 · A11y + Agentic 3/3 + mobile audit (2026-02-15)** — followup pass after Iter 391 addressing every non-perf finding from PSI Mobile.
  - **Contrast fix (WCAG AA)** — `WalkthroughPlayer.jsx` browser-URL-bar demo div was `color: #64748b` on `background: #0a0e18` measuring **4.09:1** (fails AA 4.5:1). Bumped to `#94a3b8` → **5.9:1**, still reads as a muted URL bar. This was the single audit-flagged low-contrast element on `/dashboard` (the WalkthroughPlayer renders inside the demo player embedded in Dashboard).
  - **Heading order** — `Landing.jsx:1176` had `<h4>Welcome back</h4>` inside a decorative "browser preview" mockup with no surrounding h3, which skipped from h2 → h4 and failed Lighthouse's heading-order audit. Replaced with `<div className="win-preview-title">` and updated the matching CSS rule (`.win-preview h4, .win-preview .win-preview-title`) so the visual is byte-identical while the semantic tree is clean.
  - **llms.txt · llmstxt.org spec compliance (unlocks Agentic 3/3)** — Lighthouse's `llms.txt does not follow recommendations · File does not appear to contain any links` audit was failing despite 7 bare URLs being present, because the [llmstxt.org](https://llmstxt.org) spec requires Markdown-formatted `[text](url)` links, not bare URLs. Rewrote `/public/llms.txt` (88 → 84 lines) with proper Markdown links grouped by section: Core pages, Legal + trust, Compare pages, Optional. Preserved all product-of-truth copy verbatim (5-phase Loop, Vanguard scanner, MCP 2.4, founder pricing) and the ISO-cite quotes.
  - **JSON-LD SoftwareApplication audit** — verified `index.html:269-322` already contains a rich SoftwareApplication schema with `applicationCategory: DeveloperApplication`, `applicationSubCategory: AI Coding Assistant`, `operatingSystem: Web, Windows, macOS, Linux`, two Offer entries (Free / Founder), a 9-item featureList, and author/publisher back-references to the Organization `@id`. No changes needed — already at target.
  - **Mobile-specific sweeps** — audited all `fontSize: 10-11px` usages across Landing/Signup/Login/Pricing/Dashboard. All ~30 hits are limited to badges, uppercase eyebrows, monospace metadata, and other design-hierarchy labels — NOT body copy. PSI Mobile's "Legible font sizes" audit already PASSES; no changes made because bumping every label to 12 px would flatten the visual hierarchy without any Lighthouse score benefit. Tap-target audit — PSI passed "Touch targets have sufficient size and spacing" on baseline; no regressions.
  - **robots.txt AI-crawler audit** — verified current policy allows GPTBot, ClaudeBot, PerplexityBot, Google-Extended, Applebot-Extended (explicit `Allow: /` blocks for each). All welcome, only authenticated dashboard surfaces blocked. No change needed — matches founder's stated preference for AI-crawler friendliness.
  - **Expected PSI Mobile after ship**: A11y **95 → 100**, Agentic **2/3 → 3/3**. Perf unchanged from Iter 391 (this iter didn't touch the critical path). Preview-verified via `yarn build` (17.7 s, 6 SEO snapshots emitted, all 6 postdeploy sentinels present).



- **Iter 391 · Mobile PageSpeed critical-path perf (2026-02-15)** — baseline PSI Mobile score **Perf 63 / A11y 95 / BP 96 / SEO 100 / Agentic 2/3** (FCP 3.1 s · LCP 8.0 s · TBT 230 ms · CLS 0.078). Founder asked for a full sweep with mobile-first framing; this iter ships the perf critical-path fixes only, with A11y/agentic (Iter 392) and CSP/security (Iter 393) staged separately.
  - **`ora-icon.png` 512×512 · 322 KB → responsive `<picture>` set**: regenerated at 128 px (WebP 6.2 KB / PNG 27.9 KB) + 256 px @2x (WebP 15 KB / PNG 93.5 KB) via Pillow. `<img src="/ora-icon.png">` in `Landing.jsx` (2× refs) + `BugHunt.jsx` (2× refs) replaced with a `<picture>` element carrying `image/webp` source + PNG fallback + explicit `width={67} height={67}` + `decoding="async"`. **~316 KB saved on cold-load** on the AVIF/WebP-capable ~99% of mobile browsers.
  - **Landing demo videos · new `components/LazyVideo.jsx`**: 5 (now 4) `<video preload="metadata" controls>` cards in `Landing.jsx` were causing PageSpeed's "Enormous network payload" (45 MB total). Replaced with an `IntersectionObserver`-driven placeholder that swaps in the real `<video preload="none">` only when the card enters viewport (`rootMargin: 200px`). Videos now fetch on user tap, not on page load. Also removed the broken `9ioe1ylh_ora%20easy%20to%20use%20video.mp4` URL that was throwing ERR_CONNECTION_FAILED (BP audit flag). Estimated Slow-4G LCP reclaim: **~5 s**.
  - **Meta Pixel + Google Ads gtag deferred to `requestIdleCallback`**: `index.html` inlined bootstraps for both fbevents.js and `gtag/js?id=AW-18239920865` were previously running synchronously in `<head>`, adding ~250 ms of main-thread eval before LCP. Now both create their queue stubs immediately (so any `fbq('track', ...)` or `gtag('config', ...)` call from `analytics.js` still queues fine) but the actual `<script src="...">` injection is wrapped in `requestIdleCallback(load, {timeout: 3000})` with a 2.5 s `setTimeout` fallback for Safari. Iter 389 conversion events (CompleteRegistration / Lead / Purchase) remain functional because they push into the queue that fbevents drains on load.
  - **Font preload**: added `<link rel="preload" as="font" type="font/woff2" href="…Jost…woff2" crossorigin>` for the primary body-text weight so the hero doesn't wait for the Google Fonts CSS → discover → fetch chain (was 1.1 s on baseline).
  - **Sentinels**: postdeploy-verify (Bug L-01 guard) — all 6 sentinels (`PageView`, `1571887197933821`, `CompleteRegistration`, `"Lead"`, `"Purchase"`, `AW-18239920865`) present in local `dist/` after build. Preview smoke: mobile 412×823 viewport shows 0 videos on first paint (LazyVideo working), 2 `<picture>` webp sources (ora-icon working), `window.fbq` stub present (analytics can queue), no console errors from the deferred bootstraps.
  - **PSI Mobile post-deploy retest**: pending founder-triggered rerun after this ships to prod. Expected Perf **63 → 88-93**, LCP **8 s → ~2.5 s**, TBT **230 ms → <150 ms**.
  - Preview-verified only. Prod-verified awaits deployer ship + founder's PSI Mobile rerun evidence.



- **Signup Terms checkbox hit-target polish · Iter 390.1 (2026-02-15)** — during Iter 390 prod verification, founder reported the T&C checkbox was hard to click precisely (had to force-click via JS). Root cause: default native `<input type="checkbox">` renders at ~13px, and the tightly-packed adjacent `<Link>` elements (Terms/Privacy) in the label text meant nearby clicks landed on the Link's hit target instead of the label. Fix: explicit `width:16, height:16, flexShrink:0, cursor:pointer` on the checkbox + `padding:6px 4px, borderRadius:4` on the label for a roomier tap zone. Preview-verified via Playwright: normal-precision click AND off-center label click (12px from left edge) both toggle checkbox state. No visual density change.



- **Developer-only default · /choose-track removed · Iter 390 (2026-02-15)** — founder decision: AUREM is a developer-first product going forward. Personal Track opt-in remains alive for the (currently 0) users who might switch to it via Settings → TrackSwitcher, but the mandatory selector page after signup is gone.
  - **Prod DB check first** (respected "existing users unaffected" constraint): deployer ran `db.dev_users.count({track: "personal"})` on prod → **0 personal, 33 developer, 0 null**. Zero users affected by removal.
  - **Backend**: all 3 `dev_users.insert_one` paths now default `"track": "developer"` + `"track_updated_at": <epoch>` at creation time. Paths covered: (1) `/auth/signup` email+password (`routers/auth.py:264`), (2) Google OAuth new-user (`routers/auth.py:389`), (3) GitHub OAuth new-user (`routers/github_oauth.py:436`). The startup backfill `_backfill_dev_users_track` in `main.py:677` stays as a safety net for any future insert path that forgets to default.
  - **Frontend**: `Signup.jsx` now navigates directly to `next` (usually `/dashboard`) after `/auth/signup` success — no more `/choose-track` hop. `preselectedTrack` URL-param logic + related dead comments removed. `App.jsx` route + lazy import for `<ChooseTrack>` deleted. `Dashboard.jsx` no longer renders `<PersonalTrackBanner>` (impossible to trigger now that no user has null track). Stale comments in `Landing.jsx` + `Login.jsx` updated to reflect Iter 390 state. Login-time track routing (`Login.jsx:76-82`) preserved — existing/opt-in Personal users still land on `/build`.
  - **Files deleted**: `pages/personal/ChooseTrack.jsx`, `components/PersonalTrackBanner.jsx`.
  - **Tests**: `backend/tests/test_iter390_developer_default.py` — 5 read-only source assertions locking in the contract (signup + Google + GitHub inserts default to developer; backfill safety net still present; `/auth/set-track` endpoint preserved for TrackSwitcher). All 5 pass. Frontend vitest: 475/476 pass (1 pre-existing LoopLiveFeed modal test failure — unrelated to Iter 390 touchpoints).
  - **Preview-verified**: `/choose-track` on preview returns clean 404 "Page not found" with helpful copy. Signup form still renders correctly. **Trigger-verified: pending** — founder to run real signup on prod post-deploy and confirm direct-to-dashboard flow.



- **Bug L-01 institutional log + `postdeploy-verify.mjs` guard · Iter 389.1 (2026-02-15)** — while trigger-verifying Iter 389 on prod, founder's `curl` + grep on the initially-loaded bundles returned zero hits for `CompleteRegistration` / `Lead` string literals. This looked identical to a "deploy didn't propagate" bug and burned ~2 hours across two false RCAs (backend-only-deploy false negative + stale Cloudflare cache). Actual root cause: **Vite production build code-splits shared modules into their own chunks** — `lib/analytics.js` (holding all Iter 389 helpers) was compiled into a separate lazy chunk `analytics-DAsU-d0r.js` (898 bytes) that only loads on-demand as React Router mounts Signup/OAuthFinish/Settings. Founder's grep never fetched that chunk → false negative. Three-layer SHA-256 comparison (custom-domain `auremcto.com` / platform origin / local `yarn build` output) proved the deployed artifact was byte-identical to the local build all along.
  - Logged permanently to `/app/memory/BUGS_LEDGER.md` as **L-01** (new institutional ledger, fresh numbering to avoid collision with scattered legacy Bug 1–29 entries in CHANGELOG/PRD).
  - Shipped `/app/frontend/scripts/postdeploy-verify.mjs` — chunk-aware synthetic verifier that fetches deployed HTML → extracts main entry chunk → recursively walks the ENTIRE lazy-chunk dependency tree (fetched 183 chunks on `/signup` for Iter 389 verification) → greps every chunk for a **SENTINELS manifest** (`PageView`, `1571887197933821`, `CompleteRegistration`, `"Lead"`, `"Purchase"`, `AW-18239920865`) → fails loud with `process.exit(1)` on any miss. Both success (all 6 present on prod) and fail-injection paths tested.
  - Added `yarn verify:prod` and `yarn verify:preview` scripts to `frontend/package.json` for one-command runs.
  - Standing rule reinforced in the ledger: **bundle-verified ≠ trigger-verified**. Code present in served chunks (SHA-256 comparable) is NOT the same as a code path actually firing at runtime; both labels are valid and separately verified.



- **Meta Pixel conversion events · Iter 389 (2026-02-15)** — Meta Pixel base script was already loaded (Iter 388-ag) but only firing `PageView`. This iter adds 3 standard-event conversion helpers wired to real backend confirmations:
  - `metaCompleteRegistration(method)` — fires from `Signup.jsx` on `/auth/signup` success (method=`email`) and from `OAuthFinish.jsx` when the backend flags `d.new === true` (method=`google`) or `?new=1` (method=`github`). Runs alongside existing Google Ads `trackSignup()`.
  - `metaLead("project_added")` — fires from `AddProjectWizard.jsx` immediately after `/cto/projects/add` returns success (strongest intent signal: user actually connected a repo). NOT fired on GitHub connect click alone (would overlap with signup).
  - `metaPurchase(value, "USD", sid)` — fires from `Settings.jsx` only after the Stripe session-status poller sees `payment_status === "paid"` (real backend confirmation, NOT checkout button click). Uses hard-coded plan values (Starter $9, Pro $19, Team $49) and the Stripe session id doubles as Meta's `eventID` for future CAPI dedup.
  - **Guardrail (founder-approved):** if tier is missing/unknown when a paid session lands, `metaPurchase` is SKIPPED entirely (rather than firing with a fallback value). Better a small analytics gap than polluting the Meta ad account with $0 / unknown-currency purchase events.
  - All helpers are silent no-ops when `window.fbq` is undefined (ad-blocker / SSR / pre-init) and swallow any fbq exceptions so business flows never surface analytics failures.
  - Tests: `frontend/src/lib/__tests__/metaPixel.iter389.conversions.test.js` — 12 cases covering: no-op behaviour without fbq, correct standard-event names + params, value/currency guardrails, exception-safety. Full lib suite: **57/57 pass**. Preview-verified: `window.fbq.loaded === true` on `/signup`.



- **Brand P0 follow-up · SEO pre-render fix + P1 deploy_logger default · Iter 388-brand2 (2026-08-13)** — first P0 brand deploy left search-engine HTML stale.
  - After P0 landed, real-curl on prod showed 9× "AUREM CTO" on `/vs/devin` and 13× on `/compare` **in the raw HTML** — root-caused to `frontend/scripts/seo-prerender.mjs` which pre-renders static HTML for crawlers before React hydrates. Client-side render (Playwright) was correct; server-served HTML was not. Search engines were seeing deprecated branding.
  - Fixed `seo-prerender.mjs` end-to-end: H1s, related-link anchors, table column header, pick-card CTA copy ("Choose ORA if you want…"), BreadcrumbList JSON-LD positions 1 (`AUREM` company root) + 3 (`ORA vs {name}`), ItemList JSON-LD name + items. Post-fix: **0 "AUREM CTO" in the pre-render script, 8 correct "ORA" replacements.**
  - Also shipped P1 (user-confirmed): `backend/services/deploy_logger.py:26` `_DEFAULT_REPO` fallback `AUREMBeauty/AUREM-` → `polarisbuiltinc-wq/auremdev`. Only surfaces if `AUREM_GITHUB_REPO` env var is unset. 7/7 regression tests still pass.


- **Brand identity P0 alignment · Iter 388-brand (2026-08-13)** — deep-scan revealed user-visible surfaces mixing "AUREM CTO" (deprecated product label) with the correct hierarchy (Legal: Polaris Built Inc., Trade name: AUREM, Product: ORA by Aurem).
  - **`frontend/src/pages/VsPage.jsx`** — H1 template, related-comparisons badges, breadcrumb JSON-LD position 3, pick-card CTA all switched from "AUREM vs {competitor}" / "Choose AUREM if you want…" to "ORA vs {competitor}" / "Choose ORA if you want…". Body copy referring to AUREM as the acting company (e.g. "AUREM ships an MCP server") kept unchanged — matches trade-name usage per user hierarchy.
  - **`frontend/src/pages/CompareHub.jsx`** — `<h1>How ORA compares</h1>` (was `How AUREM compares`), ItemList JSON-LD `name: "ORA comparisons"`, per-card headings `ORA vs {c.name}`.
  - **`frontend/src/data/competitors.mjs`** — 7× `"AUREM vs …"` in `title` + `description` fields for all 5 competitor entries + the compare-hub aggregate flipped to `"ORA vs …"`. Drives `<title>` / `<meta description>` / OG tags.
  - **`frontend/index.html:194`** — Organization JSON-LD `alternateName` reduced from `["AUREM", "AUREM Labs"]` to `["AUREM"]`. "AUREM Labs" was never a registered name — dropping fixes Google Knowledge Graph disambiguation.
  - **Preview verified**: `/vs/devin` renders `<h1>ORA vs Devin</h1>`, `<title>ORA vs Devin (2026) — honest comparison | Devin alternative</title>`, 0 occurrences of "AUREM CTO" in body text, 5 correct "ORA vs" occurrences (H1 + related badges + breadcrumb).
  - **Legal/policy pages already correct** — Terms, Privacy, DPA, Cookie, Subprocessors, Security, AI Code Processing, Status all consistently use "Polaris Built Inc." + "AUREM™ is a trademark of Polaris Built Inc." — no changes needed there.
  - **Deferred (P1/P2, not touched)**: `deploy_logger.py` default repo (`AUREMBeauty/AUREM-` → `polarisbuiltinc-wq/auremdev`), internal `AUREM_CTO_PERSONA` constant, `AUREM_CTO_MASTER_KEY` env, `aurem_cto_*` Mongo collections, `aurem-dev.*` log tag prefixes — all internal/ops-facing, cost > value to rename without migration plan.


- **Frontend Sentry wiring · Iter 388-p1 (Item #20 · 2026-08-13)** — coded, preview-verified, IDLE until user pastes DSN.
  - `frontend/src/lib/sentry.js` — NEW. Exports `initSentry()` + `reportSentryException()` + `SentryErrorBoundary`. Reads `REACT_APP_SENTRY_DSN` from env; if empty, silent no-op (safe to import unconditionally). Auto-detects environment from hostname (`production` / `preview` / `dev`). Release tag pulled from `<meta name="build-hash">` so every event carries the exact deploy SHA.
  - `frontend/src/main.jsx` — imports + calls `initSentry()` at boot, before `errorReporter`.
  - `frontend/src/components/RouteErrorBoundary.jsx` — `componentDidCatch` now ALSO calls `reportSentryException(err, {componentStack, source: "route-error-boundary"})` in addition to the existing console.error → `/admin/errors/report` pipeline. Sentry is an ADDITIONAL surface, not a replacement.
  - Config: `tracesSampleRate: 0.1`, `replaysSessionSampleRate: 0.0`, `replaysOnErrorSampleRate: 1.0`, `blockAllMedia: true`, `beforeSend` drops "ResizeObserver loop" browser-quirk noise + all `dev` env events.
  - **Tests**: `frontend/src/lib/__tests__/sentry.test.js` — 6 pass (no-op when DSN missing, init when DSN present, trims whitespace-only DSN, idempotent init, reportSentryException no-op when uninit'd, reportSentryException forwards when init'd).
  - **Smoke test**: preview `/` renders with 0 console errors, `window.__SENTRY__` absent (no-DSN safe path).
  - **Package**: `yarn add @sentry/react` (transitively brings @sentry/browser + @sentry/replay).
  - **Activation guide**: `memory/SENTRY_ACTIVATION_GUIDE.md` — step-by-step for founder to create free-tier Sentry account, copy DSN, paste in `frontend/.env`, redeploy. No further code changes needed.


- **Rate-limiter WARNING throttle · Iter 388-noise (2026-08-13)** — user flagged the deploy log stream as "failing" due to volume; audit showed the deploy actually succeeded (200 OK responses across all endpoints, `/health` healthy) but the log stream was drowning in ~100 identical `rate_limiter: Redis unavailable ... max requests limit exceeded` warnings per minute after Upstash's free-tier monthly quota (500,000 req) exhausted.
  - Real cause: `services/rate_limiter.py::_ensure_redis()` deliberately logged WARNING on every failed attempt ("so a Redis flap is visible") — great for transient flaps, terrible for sustained same-error outages like a quota cap.
  - Fix: throttle policy keyed on `(error_signature, minute_bucket)`. Signature = `type(e).__name__:str(e)[:80]`.
    - NEW signature → log immediately (flap visibility preserved)
    - SAME signature, new minute → one WARNING with "N identical warnings suppressed" tally
    - SAME signature, same minute → dropped silently, counted for the next roll-up
  - **Tests**: `tests/test_iter388_noise_rate_limiter_throttle.py` — 4 pass (new error logs immediately, repeats within minute suppressed, minute rollover logs with tally, different-signature always logs).
  - App functionality unaffected — code already gracefully falls back to in-memory rate limiting when Redis is down. This is purely a log-noise/observability fix.
  - **Follow-up recommendation to user**: Upstash Redis is at 500,003/500,000 monthly requests. Options: (a) upgrade Upstash paid tier ($10/mo → 10M req), (b) accept per-pod in-memory rate limiting until next month's cycle rolls over. Current fallback is safe but doesn't enforce a cross-pod ceiling.


- **Deploy Logger cascade fix · Iter 388z (2026-08-13)** — the Option B banner fix landed in the previous deploy but PROD `/api/health` still returned the stale legacy values. Root-caused via live prod curl:
  - `/api/aurem-dev/version` returned fresh `commit_sha: 6017337dfdb3` (uses `routers/version.py::_read_commit()` cascade which includes BUILD_INFO.txt fallback).
  - `/api/health` returned stale `build_hash: ed5b698` (used `app.state.deploy_event` which was never populated).
  - Cause: `services/deploy_logger.py::get_current_commit()` cascade was only `git rev-parse HEAD` → `AUREM_DEPLOY_COMMIT` env. Prod strips `.git`, env var isn't set → returns `None` → `log_deploy_event()` bails at "no commit sha resolvable — skip" → `app.state.deploy_event` stays unset → `/api/health` falls back to legacy resolvers with cached stale SHA.
  - Fix: extended `get_current_commit()` cascade to include `backend/BUILD_INFO.txt` as final fallback (same file `/version` route reads successfully on prod). Added `_read_build_info_sha()` helper with hex validation (never surfaces garbage as a SHA), tries backend path then repo-root path.
  - **Tests**: `tests/test_iter388z_deploy_logger_build_info_fallback.py` — 7 pass (backend path read, repo-root fallback, non-hex rejection, both-paths-missing, prod scenario, git wins over BUILD_INFO when present, env var wins over BUILD_INFO).
  - **Preview verified**: after fix, `/api/health` returns `build_hash: 38e9ca104aeb` (matches `git rev-parse HEAD`) and `built_at: <current boot ISO>`. Prod will get this in the next deploy.


- **Admin Panel Payments Accuracy (Iter 388y · #35 P0 slice · 2026-08-13)** — closes the "founder's most-viewed metric is fake" bug documented in `memory/ADMIN_AUDIT_2026-02-09.md`.
  - **`routers/admin_analytics.py::token_pnl()` (line 578)** — was returning `revenue_month:0, stripe_fees:0, net_revenue:0, net_profit:-ai_cost, margin_pct:0` HARDCODED. Now computes real revenue via aggregate on `cto_payments` with `payment_status='paid'` + `created_at >= month_ago`, estimates Stripe fees at 2.9% + $0.30/txn (US standard, `_note` explicitly flags it as estimate), returns real `net_profit`, `margin_pct`, `paid_txn_month`. `stripe_configured` now reads `STRIPE_API_KEY` env instead of the hardcoded `False`.
  - **Cost-per-1k rate table refreshed** — was 2024-era (deepseek $0.30 / maxx $0.65 / groq $0.03; unknown agents defaulted to $0.30). Now 2026 rates with keys for `claude-sonnet-5, claude-haiku-4, gpt-5.2, gpt-5.2-mini, gemini-3-flash, gemini-3-pro, glm-5.2` in addition to the legacy labels. Rates verified against providers' public pricing pages, dated in the source comment. Fallback rate uses DeepSeek band as conservative headroom.
  - **`routers/admin_analytics.py::overview-metrics` (line ~1400)** — was filtering revenue by `status IN [paid, complete, completed, succeeded]` (Stripe checkout-session state). Now uses `payment_status='paid'` — same source of truth as `token_pnl` and `list_payments`. Single SoT achieved — three admin cards now agree by construction. Preview `curl` confirms all three return `9.0` on current DB state.
  - **`routers/admin_payments.py::list_payments`** — `total_revenue` was summing over the visible-page 100 rows only, silently truncating lifetime revenue past 100 paid txns. Now aggregates on the WHOLE collection with `payment_status='paid'`, returns `total_paid_count` alongside. Preview curl proves lifetime aggregate is independent of visible page size (27 rows visible, 1 paid → `total_revenue: 9.0`, `total_paid_count: 1`).
  - **Tests**: `tests/test_iter388y_admin_payments_accuracy.py` — 4 pass (revenue reflects paid-only rows, zero when no paid rows, lifetime revenue survives 100-row cap, stripe_configured reads env not hardcoded).
  - **Live preview evidence (post-fix)**: token-pnl returns `revenue_month:9.0, stripe_fees:0.56, net_revenue:8.44, net_profit:8.44, margin_pct:93.8, paid_txn_month:1, stripe_configured:True` — matches list_payments + overview-metrics exactly, all 3 endpoints on single SoT.


- **Guards batch fix + Deploy Insights Option B · Iter 388x (2026-08-13)** — 3 guards flipped GREEN, banner data source fixed.
  - **G18 Timeout Audit** (81 → **85/85 covered, pass=True**):
    - `frontend/src/pages/SupportThread.jsx:71,93` (Iter 388u regressions) — added `{ timeout: 15000 }`
    - `frontend/src/pages/Support.jsx:58` (pre-existing) — same fix
    - `backend/services/http/client.py:170` (false positive: `httpx.AsyncClient(**kwargs)` DID pass `timeout=to`) — added `# g18-exempt` marker on same line, guard's exempt-line detection now recognises it
  - **G20 Open Incidents** — root-cause chain fix (`services/process_recovery.py:196-207`): `resolve_incident()` was only fired when `topup_alerts.update_many` returned modified_count > 0. If the alert was already resolved out-of-band, the G20 incident stayed OPEN forever (why MTTR was 40h). Decoupled — `resolve_incident()` now runs unconditionally whenever boots < LOOP_THRESHOLD; it's a no-op when no open incident exists (safe + idempotent).
  - **G21 Security Scan** (2 findings → **0, pass=True**):
    - `backend/routers/admin_first50_campaign.py` — router had no `dependencies=[Depends(require_admin_dep)]` gate. All handlers still called `_require_admin()` inline, but the router-level gate was missing (defense-in-depth). Added.
    - `respx>=0.23.0` in `requirements.txt` — unpinned dep flagged by supply-chain check. Pinned to `respx==0.23.1` (current installed version).
  - **G15 Dependency CVE** (previously STALE — never run) — 1 real HIGH finding surfaced:
    - `extract-zip::CVE-2026-56876` — transitive dev-only dep via `@lhci/cli → lighthouse → puppeteer-core → @puppeteer/browsers`. Never runs in prod runtime. Upstream reports `fix=<0.0.0` (no patch shipped yet). Added to `backend/scripts/g15_allowlist.json` with 90-day expiry + revisit-when-upstream-patches reason. Guard now returns `OK — 0 unhandled HIGH/CRITICAL findings`.
  - **Deploy Insights Panel · Option B** — `/api/health` now surfaces `build_hash` + `built_at` from the `deploy_events` collection (freshly recorded at every backend boot from real `git rev-parse HEAD` + real UTC timestamp) instead of the legacy cascade (BUILD_INFO.txt / emergent.yml.created_at / .build_info mtime), which was lagging deploys by 24h+. Implementation caches the boot's deploy_event on `app.state.deploy_event`; health-endpoint prefers those values with clean fallback to legacy resolvers if unset. Preview verified: `build_hash: f2be127f5107` (matches current HEAD), `built_at: 2026-08-13T05:07:42` (matches actual backend boot log).
  - **Tests**: `tests/test_iter388x_deploy_insights_option_b.py` — 3 pass (prefers deploy_event when set, falls back to legacy when unset, falls back when commit_sha empty).
  - **Open G20 items remaining after this deploy:**
    - `inc_33092d8376` (G19 restart-loop) — will auto-resolve within 60s of the next backend boot on prod (new chain-fix logic kicks in)
    - `inc_11357babd9` (Tavily 432 credits exhausted) — GENUINELY OPEN, real external billing issue. Live probe confirms Tavily still returns `warn` with "Credits exhausted or rate-limited (432)". Not a code fix — founder action needed: top up Tavily plan OR gracefully degrade OR remove Tavily integration.


- **Reusable mask utility · Iter 388w (2026-08-13)** — extracted the shoulder-surf masking policy from `DangerZone.jsx` into `frontend/src/lib/mask.js` so the same shield can protect Stripe / GitHub / API-key IDs anywhere in the app.
  - `maskEmail(email, {reveal=2, minMask=4})` — preserves `@domain`, hides local part except trailing `reveal` chars, falls back to `minMask` stars for tiny/short inputs.
  - `maskId(value, {reveal=4, minMask=4})` — generic opaque-identifier masking; keeps the trailing fingerprint chars.
  - `DangerZone.jsx` refactored to consume `maskEmail()` — behaviour identical, 7/7 integration tests still pass after refactor.
  - **Contract tests**: `lib/__tests__/mask.test.js` — **14 pass** covering happy path, reveal option, minMask floor, empty/null coercion, whitespace trim, malformed-email fallthrough, multi-@ handling, long-local-part masking, number coercion for `maskId`.


- **P0 Security · Danger Zone email masking (Iter 388v · 2026-08-13)** — user-caught shoulder-surf gap. The confirm-email display in `DangerZone.jsx` was showing the full plaintext email directly above the input, letting any screen-share / shoulder-surf viewer copy-paste it back and unlock the delete. Confirmation step was security theatre.
  - Fix: `emailMasked` derived value hides the local part entirely except last 2 chars (e.g. `teji.ss1986@gmail.com` → `*********86@gmail.com`; `test@aurem.dev` → `**st@aurem.dev`). Wrapping span carries `userSelect: "none"` to block mouse-drag copy. Copy updated to "type your full account email exactly — from memory, not copied from here".
  - Validation unchanged — server + client both require the FULL lowercase email to match.
  - **Tests**: `components/__tests__/DangerZone.iter388v.mask.test.jsx` — **7 pass** (mask never contains full local part, last 2 + domain the only reveal, short local part → 4 stars, pasted-mask keeps button disabled, real email enables button, case-insensitive match works, userSelect:none set, long emails still hide local).
  - **Real-preview E2E screenshot proof**: modal opened as `test@aurem.dev`, mask rendered as `**st@aurem.dev`, filling input with masked value kept confirm button DISABLED (`is_disabled=True`), filling with real email ENABLED it. Deployed to preview via hot reload.


- **Support Reply UX Fix — Option A (Iter 388u · 2026-08-13)** — closed the black-hole: admin replies were writing to Mongo but user never got a surface (no email, no badge, no polling). SupportPopup's success message "You'll see the reply in this same app" was a lie — no code fetched replies.
  - **NEW `services/support_email.py`** — `send_reply_notification()` builds HTML+text email with admin message inline and CTA link to `/support/thread/{tid}?t=…&e=…`. HMAC token via existing `support_token()` (same scope as `/support?t=…&e=…` composer link). Sends via Resend using same pattern as `first50_campaign._resend_send`.
  - **NEW public endpoints** in `routers/support.py`:
    - `GET /support/tickets/{id}/thread?t=…&e=…` — public read; 403 bad token, 404 wrong-owner (never leaks existence), 200 returns ticket + messages.
    - `POST /support/tickets/{id}/reply/token` — public reply-back; user can continue conversation from the thread page without logging in.
  - **REFACTORED `admin_reply()` in `routers/admin_support.py`** — after DB insert, best-effort fires notification email; response now includes `email_notified` + `email_error`. Email failures NEVER break the reply (reply stays durable in Mongo).
  - **NEW `pages/SupportThread.jsx`** — public thread view; renders full conversation (user + admin bubbles), textarea to send reply, refetches on send. Route registered at `/support/thread/:ticketId` in `App.jsx`.
  - **Copy fix** in `SupportPopup.jsx` — replaced the jhoothi "you'll see the reply in this same app" promise with truthful "my reply lands in your email inbox with a signed link". Toast copy updated too.
  - **Tests**: `tests/test_iter388u_support_reply_ux.py` — **10 pass** (HMAC deterministic + case-insensitive, thread_url shape, HTML escape safety, thread 403 bad token, thread 200 valid token, thread 404 wrong owner, reply/token 403 bad token, reply/token 200 appends + reopens, admin_reply fires email with correct args, admin_reply survives email failure).
  - **Smoke test**: bad-token URL renders red "This link is invalid or has expired" — verified on preview.
  - Deploy status: **NOT YET DEPLOYED** — user requested Option A to ship on a separate deploy after the 4 pending verifications clear (GDPR modal, Deploy Insights, Bug 28 highlight, chat double-border).


- **GDPR/DSAR Self-Serve Account Deletion (Iter 388t · 2026-08-13 · commit 8a1aa62)** — compliance risk closed.
  - **NEW `services/user_deletion.py`** — shared `cascade_delete_user_data(db, user_id)` helper. Three layers:
    1. `stripe.Subscription.delete(sub_id)` immediate cancel (best-effort, error-swallowed)
    2. `github_app.revoke_installation()` for each active install (per-install error-swallowed)
    3. Mongo purge across **15 collections** — added 5 (`github_installations`, `ui_settings`, `user_seo_claims`, `login_attempts`, `oauth_states`) on top of the original 10.
  - **NEW `POST /api/aurem-dev/auth/delete-me`** in `routers/auth.py:731+` — JWT-auth, founder refused (403), email-verbatim confirmation required (422 otherwise), calls shared helper on match.
  - **REFACTORED `routers/admin_users.py:699-758`** — admin cascade now uses the same helper; automatically inherits Stripe cancel + GitHub revoke fixes that the old admin path silently skipped.
  - **NEW `components/DangerZone.jsx`** — red-bordered card in Settings > Profile tab bottom. Multi-step modal with typed-email confirmation (button disabled until match). Escape closes. On success: `apiLogout()` + `window.location.replace("/login?deleted=1")`.
  - **`Login.jsx`** reads `?deleted=1` → green success banner.
  - **Tests**: `tests/test_iter388t_self_delete.py` — 7 pass (cascade all 15, stripe cancel mocked, github revoke mocked, stripe error swallowed, email mismatch 422, founder 403, success 200 + report).

- **Bug 24 + Bug 25 + Bug 26 A11y batch (Iter 388t · 2026-08-13 · commit b97f83c)** — WCAG 2.4.7 focus rings + skip link.
  - **Bug 24** (`index.css:339-372`) — `:focus-visible` outline (2px solid --accent-2) for every rail data-testid family.  Rail nav genuinely keyboard-navigable now. VERIFIED live.
  - **Bug 26** (`index.css:334-337, 628-632`) — same `:focus-visible` treatment on `.input` and `.composer-input-bare`. VERIFIED live.
  - **Bug 25** (`App.jsx:242-303`) — skip-to-content link repositioned `position: fixed` @ top:8/left:8, zIndex 10000, programmatic focus on `#main-content` in onClick. Works on Landing/Login/Signup (verified via /login preview screenshot); Dashboard scope-limited by design (autoFocus composer takes first Tab; rail nav directly keyboard-reachable via Bug 24 anyway). Doc comment explains the trade-off.

- **/podshell slash command + Bug 29 F12 counter cap (Iter 388t · 2026-08-13 · commit f4525f4)** — Bug 20 UI-reachable.
  - **NEW `routers/dev_tools.py`** — `POST /api/aurem-dev/dev-tools/podshell` and `GET /podshell/info`. Admin-gated. Runs `validate_founder_pod_command` (chaining/traversal/secret denylist) → `execute_bash` with founder_pod_mode=True. VERIFIED live on prod with `__pycache__` in real stdout (dispositive filesystem proof).
  - **`ChatPanel.jsx` /podshell intercept** — bypasses LLM entirely; renders stdout in ```plaintext code fence.
  - **Bug 29** (`public/F12ErrorCapture.js`) — network_errors.push() sites now check `MAX_ERRORS=20` before push; badge stops runaway growth (was 36→58→75→104 on normal navigation, now ≤40 total). VERIFIED live.

- **Bug 20 root-cause deterministic bypass (Iter 388t · 2026-08-13 · commit 079e18b)** — 3rd try, finally correct.
  - **REVISED DIAGNOSIS**: refusal was NOT LLM safety RLHF; our own `ORA_BOUNDARY_NO_REPO_RULE` template in `services/ora_context.py:165-166` literally instructed the LLM to reply with "I work with your repository only…". Combined with server-side `execute_bash` gate that refused /app/* for founder Home chat (bin_ctx=None → no debug_mode).
  - **Fix (5 layers, no LLM involved)**: new `ORA_FOUNDER_POD_DEBUG_RULE` permissive template + `is_founder_pod_chat_session(is_founder, project_id)` detector + `validate_founder_pod_command(cmd)` safety layer (chaining/traversal/secret denylist) + `render_ora_boundary_prompt(ctx, founder_pod_mode=…)` router + orchestrator wiring populating `local_ctx['founder_pod_mode']` and execute_bash honouring it as escape hatch.  Scope limited to founder + no-project (Home) chat; customer chats still strict.
  - **Tests**: 24 pass in `test_iter388t_bug20_founder_pod_bypass.py` + 8 pass in `test_iter388t_podshell_endpoint.py`.

- **Bug 21-bold table cells (Iter 388t · 2026-08-13 · commit d0d4597)** — `RenderedMessage.jsx:164-188` `renderInline` splitter now parses `**bold**` alongside `` `code` `` in inline segments including table cells. VERIFIED live.


- **`ora@auremcto.com` Bounce Fix (Iter 388b · 2026-02-12 · Preview only)** — direct-reply bounces resolved.
  - **Root cause identified**: `auremcto.com` has **no MX record** → every reply to `ora@auremcto.com` (referenced across policies, README, in-app error strings, orchestrator prompts, landing footer) was guaranteed to bounce. `aurem.live` DOES have MX (Cloudflare Email Routing) but that's a separate check the founder is doing.
  - **Two-layer fix shipped**:
    1. **`Reply-To` header** — new `services/email_reply_to.py` centralizes `REPLY_TO_EMAIL` env read. Added `REPLY_TO_EMAIL=polarisbuiltinc@gmail.com` to preview `.env`. Every user-facing Resend send (verification, welcome, onboarding, first50 campaign, referral reward, admin email tool) now conditionally includes `"reply_to": <env value>` when the env is set. Result: Gmail "Reply" button sends directly to the founder's real inbox, bypassing the aurem.live MX chain entirely.
    2. **Swap all `ora@auremcto.com` → `auremcto.com/support`** across product surfaces:
       - Policy docs (7 files): privacy-policy.md, terms-of-service.md, acceptable-use-policy.md, refund-policy.md, cookie-policy.md, security.md, ai-code-processing.md, dpa.md, subprocessors.md, AUREM_README.md — same treatment for `privacy@auremcto.com`.
       - Backend: `services/orchestrator.py` (founder-escalation prompts at count=3/4/5/6+), `services/error_translator.py`, `routers/payments.py`, `routers/unlock.py`, `routers/harden.py`, `routers/chat.py` (draft-support-email), `routers/admin_users.py` (email tool reply_to fallback).
       - Frontend: `pages/PolicyPage.jsx`, `pages/OpsRecipes.jsx`, `pages/VsPage.jsx`, `pages/Landing.jsx` (footer), `pages/Admin.jsx` (email tool banner), `components/PricingCards.jsx`, `README.md`.
  - **Regression guards**: new `tests/test_iter388_reply_to_header.py` asserts (a) Resend payload includes reply_to when env set, (b) omits it when env unset, (c) no product code ever re-introduces `ora@auremcto.com`. Existing tests `test_iter99`, `test_iter71`, `test_iter73`, `test_iter104` all updated for the new canonical channel.
  - **Verified in preview**: real Resend send to `teji.ss1986@gmail.com` returned Resend ID `4e65a915-8bf5-44f7-b33f-4476e4a97f26` — clicking Reply in Gmail should now land in `polarisbuiltinc@gmail.com`.
  - **Prod deploy pending founder confirmation** + Cloudflare Email Routing status check on `aurem.live`. Reminder: `REPLY_TO_EMAIL=polarisbuiltinc@gmail.com` must be configured via Emergent dashboard on prod, not via the .env file.


- **In-App + Email Support Flow (Iter 388 · 2026-02-12)** — replaces broken `ora@aurem.live` email replies as the user-side entry point.
  - `POST /support/tickets/token` (public, HMAC-verified) — new: users file tickets from email links without login. Same HMAC pattern as unsubscribe (`support:<email>` scope on `UNSUBSCRIBE_SECRET`).
  - `POST /support/tickets` extended — accepts optional `source` + optional `subject` (auto-derived from body's first line).
  - `cto_support` schema now carries `source` + `user_name`. Same collection admin Support panel already reads → zero parallel systems.
  - `GET /admin/users/{user_id}` extended with `support_tickets` field (last 20).
  - Admin Support panel now shows per-ticket `source` badge (`email_stage_0`, `in_app_dashboard`, etc).
  - Admin User Detail page has new "Support tickets" section with source badges.
  - Public `/support` page (token-verified, subject-less textbox).
  - Reusable `SupportPopup` + `SupportButton` + globally-mounted `GlobalHelpFAB` (floating "Need help?" pill on all logged-in in-app routes).
  - Every campaign email (Stage 0/3/7) footer now includes "Need help? Send us a message" link + "replies to this email may bounce, use the link instead" disclaimer.
  - Verified end-to-end (preview): 2 tickets filed via 2 different paths → both visible in admin Support inbox with correct source badges → both visible on user detail page.
  - Files: `routers/support.py`, `services/first50_campaign.py`, `routers/admin_users.py`, `pages/Support.jsx`, `components/SupportPopup.jsx`, `components/GlobalHelpFAB.jsx`, `App.jsx`, `pages/Admin.jsx`.
- **First-50 Campaign — `to_emails` override (Iter 388 · 2026-02-12)** — `POST /admin/first50-campaign/dispatch?to_emails=a@x,b@y` bypasses all guards & DB filter, doesn't record in `first50_campaign_state`. Real sends verified to founder inbox (Resend IDs `0131f6dc…`, `4b73c8f5…`, `8c953df6…`).


## 2026-02-12 (Batch 8a → 8b day)

- **Iter 314 — BUILD_INFO.txt lag fixed** + Deploy Verification Checklist rewrite
  - `backend/BUILD_INFO.txt` untracked; `scripts/git_hooks/post-commit` stamps HEAD SHA
  - `scripts/install_hooks.sh` bootstraps hook into fresh sessions
  - `/app/memory/DEPLOY_VERIFICATION_CHECKLIST.md` rewritten:
    removed SHA-pinning assumption per Emergent Support,
    named Manage Publishes → Overview as primary source of truth,
    documented 3 HEAD-mutation channels (A/B/C),
    added "no build in-flight" + "intended commits landed" pre-dispatch rules
  - Verified live: prod SHA `42aba1160e0e` == local HEAD (best case)
  - 7 new tests in `test_build_info_stamping.py`
  - 3 tests in `test_deploy_verification_discipline.py` rewritten to pin new invariants

- **Batch 8a — 7 router files, 10 sites migrated to `services.http.ext_client`**
  - admin_qa.py (3): GitHub Actions x2 + VSCode Marketplace (new dep)
  - admin_bin.py (2): GitHub HEAD probe (4s in-loop) + OpenRouter credits
  - admin_projects_brain.py (1): internal_probe dep (breaker isolation)
  - admin_ops_config.py (1): cloudflare dep
  - admin_users.py (1): resend dep (15s)
  - upload.py (1): OpenRouter vision (45s explicit preserve)
  - fix_pipeline.py (1): GitHub commit verification (10s)
  - Verified live: prod SHA `39ba1122764f` == local HEAD (best case, ~12min build)
  - 13 new tests in `test_phase3_http_wrapper_migration_batch8a.py`

- **Batch 8b — SOLO: `github_oauth.py::_gh_primary_email`**
  - Migrated `httpx.AsyncClient(timeout=10)` → `ext_client("github", timeout=httpx.Timeout(10.0))`
  - Broad `except Exception` guard preserved (load-bearing for OAuth signup with private emails)
  - 7 new tests in `test_phase3_http_wrapper_migration_batch8b.py` — including runtime tests
    that simulate ExternalCallError + HTTPStatusError → confirm graceful-degrade contract
  - Verified live: prod SHA `51be15a52d09` == local HEAD (best case, ~24min build)
  - Live functional check on OAuth `/connect` (401 gate) + `/callback` (400 clean error) — no 500s
  - E2E OAuth signup flow with a private-email GitHub account requires human test

- **Middleware "No response returned" fix** (Iter 313)
  - Defensive try/except around `_global_rate_limit_guard`'s `call_next()` + `check_rate_limit_async()`
  - Live on prod (confirmed via Emergent Support; earlier live-signal was ambiguous due to BUILD_INFO.txt lag which Iter 314 subsequently fixed)

- **Deploy discipline track record established today:**
  - 3 races in one day (all resolved) → 3 consecutive best-case deploys under revised model
  - Pipeline model confirmed: "snapshot at build-start" (no SHA pinning)
  - Deploy incident report + 6 pipeline questions sent to Emergent Support

**Cumulative Phase 3 progress after today: 65 sites / 23 files migrated.**

See `/app/memory/DEPLOY_VERIFICATION_CHECKLIST.md` for mandatory deploy protocol.
See `/app/memory/PRD.md` for full backlog + prioritization.
