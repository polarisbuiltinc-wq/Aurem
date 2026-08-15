# AUREM CTO — Changelog (append-only)

See `/app/memory/DEPLOY_VERIFICATION_CHECKLIST.md` for the mandatory deploy protocol.

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
