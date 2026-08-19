# AUREM CTO — Founder Status Report
**Generated: 2026-08-19 23:57 UTC · Checked against live code/config/data, not memory of past chat.**

Scope note that applies to this entire report: I (the coding agent) have **no access to production** (`auremcto.com`) — no production logs, no production Mongo, no production OpenRouter/Stripe/Upstash dashboards. Every claim below is labeled as one of:
- **LIVE-CONFIRMED** — you personally verified it against production and told me (highest tier)
- **PREVIEW-VERIFIED** — I tested it myself, but only in this preview environment
- **UNVERIFIED** — I have no way to check this from here; stated explicitly rather than guessed

---

## === 1. SECURITY STATUS ===

| ID | Finding | Status |
|---|---|---|
| SEC-001 | Leaked real password + Mongo URI (in git history / old docs) | **Password rotated — LIVE-CONFIRMED** (you personally used the self-service Forgot Password flow in production and confirmed the old leaked password is dead). Working tree redacted. **Git history scrub — still OPEN.** A checkpoint commit (`8e31b74 "before secret-history scrub"`) exists but I found no evidence a `git-filter-repo` rewrite ever ran — commit history continues normally after it. You're waiting on Emergent Support's confirmation before running anything destructive here. **The historical exposure in git history is unresolved**, even though the operational risk (the password itself) is defused. |
| SEC-002 | Provider/model leak — raw model names shown to users instead of "ORA" | **Fixed — PREVIEW-VERIFIED only.** 10 backend + 2 frontend tests passed at the time. Not independently re-checked against production this session. |
| SEC-003 | Ship Wall defaulted to public (opt-out) without explicit consent | **Fixed — PREVIEW-VERIFIED** (curl + screenshot). Changed to opt-in. Production status not independently re-confirmed. |
| SEC-004 | Non-uniform 404s leak resource existence/ownership (enumeration) | **Fixed at source level — limited verification.** No dedicated two-account exploit re-run against these exact routes after the fix. |
| SEC-005 | **CRITICAL** — OS command injection via ORA's post-edit build hook (shell string built from a file path) | **Fixed — LIVE-CONFIRMED in production** (you verified this today via `/api/health` after redeploy). This was the most serious finding in the whole audit — full env-var access (customer GitHub PATs, vault key, Stripe key, Mongo creds) was theoretically reachable. |
| SEC-006 | No boundary between user instructions and untrusted repo/tool content (prompt injection delivery path for SEC-005) | **Fixed — LIVE-CONFIRMED in production**, shipped together with SEC-005 (fixing the lock without the trigger path wasn't enough). |
| SEC-007 | Chat-path AI-generated code only gets a regex secret-scan, not the fuller review Loop mode gets | **Still OPEN — not fixed.** MEDIUM severity, but blast radius is self-inflicted (a user's own repo), not cross-tenant. |
| SEC-008 | `litellm` installed from `customer-assets.emergentagent.com`, not PyPI | **Still OPEN — not fixed.** LOW severity — hash-pinned (`#sha256=`), so tampering would be *detected*, but provenance still depends on an Emergent-hosted asset host. |

**New concerns found this session (not vulnerabilities, but worth knowing):**
- Preview's `STRIPE_API_KEY` is a **live** Stripe key, not test mode — flagged and fix in progress, waiting on your test key.
- The R2 credentials in `.env` are correctly scoped to only one bucket (this is *good* security practice, discovered as a blocker for the asset-migration task, not a finding).

**Is the live production site currently safe for real customers to use?**
**Yes, for the critical cross-tenant risk** — SEC-005/006 (the command-injection path that could have exposed every customer's GitHub token and the vault key) is fixed and you've personally confirmed it's live. **But two caveats you should know about**: the git history exposure (SEC-001) is still technically unresolved pending Support's guidance, and chat-path code gets lighter security scanning than Loop mode (SEC-007, self-inflicted-only risk). Neither of those is the "someone else can read your data" class of risk that SEC-005 was.

---

## === 2. FINANCIAL/COST STATUS ===

- **Current monthly OpenRouter spend**: **UNVERIFIED from here.** You mentioned ~$25.83/month from OpenRouter's own dashboard in an earlier conversation — I have no OpenRouter billing access to re-check that live. Preview's own tracked cost (all preview testing activity, this month): **$0.058** combined — this is preview-only test traffic and is NOT representative of production's real scale.
- **Is cost-tracking accurate for customer chat now?** Real gap fixed **today**, in preview, **not yet deployed**: `/chat/send` and `/chat/stream` now log a cost row for every turn (previously: zero rows, ever). It's a char-count **estimate** (~4 chars/token), not exact provider-reported token usage — stated honestly, not exact billing-grade accounting. **Until you redeploy, production still has the old 0%-coverage code.**
- **Per-user cost cap**: **Not built.** Only a flat task-*count* cap exists (10/50/etc. per month by tier), no $ ceiling. You're deciding the per-tier $ numbers before this starts, per your own instruction.
- **Upstash Redis**: **UNVERIFIED from here.** Preview's `REDIS_URL` is a local placeholder (`127.0.0.1:6379`, always fails, harmless fallback) — it is NOT connected to your real Upstash instance, so I cannot see current plan/usage/quota. You said you're handling the Upstash quota reset/upgrade directly — I have no way to confirm current state.
- **Other recurring costs (R2, Cloudflare, etc.)**: I have no billing-dashboard access to any of these — only what the code/API tells me. R2: one bucket (`aurem-mongo-backups`) holds 13 daily backup archives, few MB each — cost is likely trivial but I can't quote a real number. Cloudflare/Stripe: no billing visibility from here.

---

## === 3. PRODUCT RELIABILITY ===

**Guards (from `memory/GUARDS_CHARTER.md`, cross-checked against this session's own findings):**

| Status | Guards |
|---|---|
| 🟢 Green (shipped + tested) | G2, G17, G18, G19, G20, G21, **G22** (idle-spend guard — shipped 2026-08-19, PREVIEW-VERIFIED only, production status of this specific guard is unverified) |
| 🟡 Partial | G8 (GitHub App dispatch — built preview, needs your `GITHUB_ACTIONS_TOKEN`/`GITHUB_REPO` + deploy), G16 (router-level auth gate done, sub-items remaining) |
| 🔴 Not started | G1, G3, G4, G5, G6, G7, **G9 (external uptime monitor — you're setting this up yourself right now)**, G10, **G11 (DB backup automated but no recurring restore drill)**, **G12 (rollback — no real drill ever run)**, G13, G14, G15 |

So: **7 green, 2 partial, 13 not started** out of the tracked set (excluding G22 which isn't in the charter file yet — I'm counting it as green based on this session's own build+test).

- **Database backups**: **Automated, confirmed real** — I listed the actual R2 bucket: 12 consecutive daily backups, 03:00 UTC, Aug 8 through Aug 19, zero gaps. **Caveat**: this confirms the backup cron works reliably for *this preview pod's own Mongo*. Whether the same cron is successfully backing up **production's dedicated Atlas Mongo** is **UNVERIFIED** — I have no visibility into production's own backup runs. Restore: the capability exists in code and was E2E tested once in preview (121/122 collection parity) — no recurring drill, no production restore ever performed.
- **Uptime, last 30 days**: **UNVERIFIED — there is no monitoring tool in place to even have this data** (that's exactly G9, which you're setting up now). I can't tell you if there's been downtime because nothing outside our own infra has been watching.
- **Known bugs/broken features currently LIVE (not yet fixed in production)** — this is important, please read carefully: several fixes shipped **today** exist **only in preview**, meaning production currently still has the *old, broken* behavior until you redeploy:
  - "Manage billing" button — still disabled/inert in production today (fix is preview-only).
  - Anonymous/logged-out support form — still non-functional in production today (fix is preview-only).
  - Customer chat cost tracking — still 0% coverage in production today (fix is preview-only).
  - Round-2 health-probe hardening — cosmetic-only, round-1 (already live) already prevents the actual crash.
  - Emergent branding (4 landing-page videos + 2 logos still on `customer-assets.emergentagent.com`, `humans.txt` still says "Emergent") — live in **both** preview and production, unchanged, blocked on you generating new R2 credentials.

---

## === 4. CUSTOMER-FACING GAPS ===

- **Support channel — fully working for all users?** **In preview, yes** (both logged-in and logged-out paths tested end-to-end today). **In production, no — not yet redeployed.** Today production still has the original bug: the footer "Support" link leads to a permanently-disabled form for anyone without a magic-link token.
- **Billing/cancel flow — fully working?** **In preview, yes** (verified against real Stripe, button now reachable). **In production, no — not yet redeployed**, same disabled-button bug still live.
- **Onboarding**: 3-step guided wizard (connect repo → first task → live tape) on true first login — this is an older, already-shipped feature, not part of today's unshipped changes, so it's very likely already live in production, but I have not independently re-verified it against production this session.
- **Customer-reported issues sitting unaddressed**: **None that came to me as a direct customer complaint.** Every gap in this report was found via internal audit/testing initiated by you, not by a real user reporting a problem. I have no visibility into any support inbox/ticket queue outside what's in `cto_support` (and I haven't been asked to triage that this session).

---

## === 5. BRAND/PLATFORM EXPOSURE ===

- **R2 asset migration (videos + logo)**: **Still pending.** Blocked on R2 credentials — we went through three options together (new scoped token, broader token, shared-bucket-with-prefix) and rejected shared-bucket after I flagged a real risk (public access to that bucket would also expose your Mongo backups). Currently waiting on you to generate the new `aurem-public-assets` bucket + scoped token.
- **`humans.txt` "Emergent" removal**: **Still pending** — checked directly, the line is still there. Note: this one doesn't actually need R2 credentials at all — it's a one-line text edit I can do independently the moment you say go, it just got batched with the R2 work by conversation flow, not by technical dependency.
- **`llms-full.txt` trim**: **Appears already done** — zero "Emergent" mentions found. I can't independently confirm who/when did this or what it looked like before, but the current state is clean.
- **Other third-party exposure still visible to customers**: Google sign-in redirects through `auth.emergentagent.com` — this is **inherent to using Emergent-managed Google Auth**, not a bug to fix; removing it would mean switching to a different auth provider entirely (a real architectural decision, not a quick fix, and not something you've asked for). `litellm`'s install source (SEC-008 above) is visible in dependency/deploy logs, not in the customer-facing UI.

---

## === 6. TECHNICAL DEBT ===

**Largest code-quality risks:**
- `frontend/src/components/ChatPanel.jsx` — **5,134 lines**, the single largest risk, flagged before, unchanged, no refactor requested or done.
- `backend/routers/chat.py` — **3,775+ lines** (the file most of today's cost-tracking work touched).
- `backend/services/orchestrator.py` — **2,666 lines**.
- `backend/main.py` — **2,915 lines** (today's deploy-fix work touched this).

**Tests currently failing (ran the FULL suite just now, not a sample):**
- **Backend: 4,805 passed / 196 failed / 71 skipped / 12 errors** (5,083 collected total, 942s full run).
- **Frontend: 477 passed / 3 failed** (480 collected, Meta-Pixel HTML-string assertions, unrelated to anything touched today).
- **Honest scope limitation**: I did NOT individually triage all 196 backend failures — that volume is beyond what I could responsibly verify one-by-one in this session. What I DID verify: (a) every file I personally changed today (chat.py, admin_bi.py, main.py, support.py, PricingCards.jsx, customer_cost_tracker.py) was regression-tested clean via git-stash-baseline comparison — zero failures traced back to today's work; (b) a sample of ~15 of the pre-existing failures were confirmed via the same stash-baseline method to be pre-existing (stale file-path references, e.g. `services/llm.py` vs the current `services/llm/` package) and unrelated to any of my changes; (c) I found and cleared one test-suite-induced false failure live during this report (a login rate-limit lockout from heavy testing today caused 3 BI Cockpit tests to fail spuriously — confirmed passing once cleared). **I cannot tell you today whether the other ~190 failures are all similarly benign or include real, older, un-triaged bugs** — that would need a dedicated pass, not something to claim from memory.

**Parked/deferred items still genuinely outstanding:**
- SEC-001 git history scrub (awaiting Emergent Support)
- SEC-007, SEC-008 (both open, see Section 1)
- R2 asset migration + Stripe test key (both waiting on you)
- Per-user $ cost cap (waiting on your tier numbers)
- DB restore drill automation (approved, not yet built)
- "Remember Me", "Need Help" smart-hide, Diff View upgrade (all explicitly parked, none started)
- CI wiring gap — G4/G15/G18 were assumed CI-wired in an earlier session but only G21 actually was; you parked this as non-urgent
- GitHub Actions drift guard (G8) — needs your token, not started
- One real (non-fixture) Tavily rate-limit/credit incident — your call, non-blocking

---

## === 7. WHAT'S ACTUALLY LEFT TO DO ===

**MUST happen next (business-critical):**
1. **Redeploy** — today shipped 4 real fixes (cost-tracking, Manage-billing button, anonymous support form, health-probe hardening) that only exist in preview. Production is currently running the *old, broken* versions of the billing and support flows.
2. **R2 credentials** — you're generating the new bucket/token; once pasted, I complete the branding-leak migration (also fixes your PageSpeed LCP concern from earlier).
3. **Stripe test key** — waiting on your test-mode secret key so preview stops using the live key.
4. **Per-tier $ cost cap numbers** — once you decide these, I build the actual cap (data now exists to build it on top of).

**SHOULD happen soon (important, not urgent):**
5. `humans.txt` "Emergent" line removal — trivial, unblocked, just say go.
6. SEC-001 git history scrub — waiting on Emergent Support's guidance, not something to do blind.
7. External uptime monitor (G9) — you're already doing this yourself.
8. DB monthly restore drill — approved earlier, not yet built.
9. SEC-007 — bring chat-path code commits up to the same scanning rigor Loop mode gets.

**CAN wait (nice-to-have):**
10. SEC-008 — move `litellm` install off the Emergent asset host onto PyPI.
11. Remaining not-started guards (G1, G3-G7, G10, G13-G15) — mostly lower-severity/CI/spend-cap items.
12. "Remember Me", "Need Help" smart-hide, Diff View upgrade.
13. Unified visitor→signup analytics (currently split between external ad dashboards and the internal activation funnel).
14. A dedicated pass through the ~190 un-triaged pre-existing test failures, if you want real confidence there rather than my current honest "mostly looks pre-existing, not individually verified" answer.
