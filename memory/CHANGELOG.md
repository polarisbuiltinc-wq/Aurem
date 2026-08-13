# AUREM CTO — Changelog (append-only)

See `/app/memory/DEPLOY_VERIFICATION_CHECKLIST.md` for the mandatory deploy protocol.

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
