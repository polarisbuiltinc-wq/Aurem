# Iter 367 · Item F — Personal Track Scope Proposal

**Status**: 🟡 SCOPE ONLY — Not built. Awaiting founder review + go-ahead.
**Created**: 2026-07-31
**Owner**: Founder (Aurem CTO)
**Do not** start implementation without an explicit "build Item F" approval.

---

## 1. What is Personal Track?

The **Personal Track** is Aurem CTO's product surface for **non-technical
users** — a single-project, single-page experience with zero jargon,
zero GitHub knowledge required, and safety rails that make it impossible
to break a live app.

The current Personal Track scaffold exists in code:
- `frontend/src/pages/personal/_shell.jsx`      — shared shell
- `frontend/src/pages/personal/BuildHome.jsx`   — entry / draft input
- `frontend/src/pages/personal/DraftReview.jsx` — plan preview
- `frontend/src/pages/personal/PreviewPanel.jsx` — live iframe
- `frontend/src/pages/personal/BuildSuccess.jsx` — post-ship summary
- `frontend/src/pages/personal/PublishCheckpoint.jsx` — before-live gate
- `frontend/src/pages/personal/Start.jsx`, `BuildLive.jsx`

The scaffold routes are mounted in `App.jsx` but the **loop wiring**
(`POST /loop/*`, streaming SSE, ship, rollback) is only wired for
the technical Track. The Personal Track pages currently:
- Render UI ✅
- Take user input ✅
- **Do not actually invoke the loop engine end-to-end** ⚠️
- **Do not enforce the safety rails described below** ⚠️

This scope proposal covers finishing that wiring PLUS adding the
distinctive safety + UX guarantees that make Personal Track different
from the technical Track.

---

## 2. Who is the target user?

**Persona: "Priya, the small-shop owner"**

- Runs a physical or digital business, e.g. bakery, salon, boutique.
- Has a WordPress or Shopify site OR wants a fresh one.
- Wants small changes: "add a Diwali special banner", "make the
  About Us page mobile-friendly", "add a WhatsApp button".
- Non-negative technical assumptions:
  - **Does NOT know what a commit is.**
  - **Does NOT know what "revert" means.**
  - Does not have a GitHub account.
  - Cannot read a diff.
  - Reads and writes in mixed English + local language.
- Positive assumptions:
  - Has a working browser + email.
  - Can screenshot / describe what they want in plain words.
  - Willing to pay ₹499–₹999 / month for something that "just works".

**Persona: "Rahul, the side-project maker"**

- Non-technical solopreneur / creator / coach.
- Has a landing page or portfolio he wants to iterate weekly.
- Doesn't want to learn git but wants ownership of his site.

**Explicitly out of scope for Personal Track**:
- Anyone running a codebase with more than ~30 files.
- Anyone who says "backend", "API", "database schema" unprompted.
- Anyone who wants to see or edit generated code.
  (These users belong on the technical Track.)

---

## 3. Non-negotiable safety rails

These are the guarantees that distinguish Personal Track from anything
else in the market. Every one of them is **founder-locked** — they
cannot be relaxed by the AI or by a support agent.

| # | Guarantee | How it's enforced |
|---|-----------|-------------------|
| S1 | User can NEVER accidentally deploy broken UI to their live site. | Every ship goes through **Publish Checkpoint** — the live iframe preview MUST render green on all critical URLs (Item E) before the "Publish to My Site" button is enabled. |
| S2 | User can ALWAYS undo the last change in one tap. | "Undo Last Change" button on `/personal/success` — wired to the real `POST /rollback/revert-last-ship` (Iter 367 · STEP 0). No git/commit language in the UI. |
| S3 | AI NEVER touches files outside a single-project scope. | Personal Track projects are auto-flagged `single_page_mode: true` in `cto_projects`. The loop planner refuses any change that touches more than N files (configurable, default 3). |
| S4 | AI NEVER pauses waiting for user input mid-loop. | Personal Track sessions have `interactive_prompts: false`. Any ambiguity → the loop halts and shows a single question at the end, never blocks the pipeline. |
| S5 | User NEVER sees a diff, PR, commit, or SHA. | Personal Track UI strips these fields from every API response via a shim in `routers/loop.py::_personal_track_view()`. |
| S6 | Risk-Routing Tier 3 (`PAUSE_FOR_FOUNDER`) is ALWAYS enforce mode. | Bypasses the 2-week shadow window (Item D). A high-risk edit halts the loop and shows: "This change needs a review — we'll email you when it's ready." |
| S7 | Every ship auto-runs the browser self-test (Item E). | Personal Track hard-requires `BROWSER_SELFTEST_BASE_URL` to be the user's live site. A failing smoke test blocks publish. |
| S8 | Payment is monthly, unable-to-refuse cancellation. | One-click cancel; on cancel, their site continues to work — Aurem only stops making changes. |

---

## 4. Product surface — the 5 screens

All under `/personal/*` in `App.jsx`. Each is a full-screen step.

### 4.1 `/personal/start` — Onboarding (3 fields, 30 seconds)
- Site URL (WordPress / Shopify / raw HTML — auto-detected)
- What you want to change (freeform, one paragraph)
- Your email

Backend: `POST /personal/onboard` — creates the `cto_projects` row
with `single_page_mode=true`, provisions storage, sends verification
email via Resend. No GitHub connect, no PAT — Personal Track uses
Aurem's org-level integration to write to the user's site via the
provisioned FTP/SFTP config (**Item B** is what unlocks this — that's
why Item B was P0 for the whole session).

### 4.2 `/personal/build` — Draft input
The chat surface, but LOCKED to the single project. No context switching,
no chat history from other projects, no `/switch` slash command.

Backend: `POST /loop/start` with `track=personal` — routes through
`services/loop_engine.py::PersonalTrackEngine` (subclass of the
main engine that pre-applies rails S3/S4 above).

### 4.3 `/personal/draft-review` — Plan preview
Shows the user WHAT will change in plain English + a screenshot preview
of the affected page rendered in an iframe. **Never** shows filenames,
line numbers, or code.

### 4.4 `/personal/publish-checkpoint` — Safety gate
- Green: "Your site is ready to update. Publish now?" (single button)
- Amber: "Preview looks slightly different — check the two views side-by-side"
- Red: "We spotted a problem — we're fixing it. Give us a minute."

Green comes from a passing Item E smoke test + Item D `AUTO_SHIP` or
`WARN_SHIP` tier. Red comes from `PAUSE_FOR_FOUNDER` or a failed smoke.

### 4.5 `/personal/success` — Post-ship
- Success animation.
- "Your change is live at [URL]"
- Single "Undo" button (calls STEP 0's real rollback).
- Weekly digest opt-in.

---

## 5. Pricing (non-binding proposal)

| Plan | Price/mo | Changes/mo | Reverts | Alerts |
|------|---------:|-----------:|:-------:|:------:|
| Starter | ₹499 / $9 | 3 changes | Unlimited | Email only |
| Grow    | ₹999 / $19 | 10 changes | Unlimited | Email + WhatsApp |
| Studio  | ₹2,499 / $49 | Unlimited | Unlimited | Email + WhatsApp + monthly Zoom |

Payment goes through the existing Stripe (already configured) with
a Razorpay fallback for India (this is a **separate integration
task** — not part of Item F build).

---

## 6. Implementation phasing (when built)

If founder green-lights this proposal, suggested phasing:

**Phase F.1 — Wiring only** (~1 session, 5-8h)
- Wire the 5 screens to real endpoints (currently they render but
  don't fire the loop).
- Add `track` field to `loop_sessions` and enforce single-project scope.
- Wire Item B's FTP/SFTP provisioning to Personal Track onboarding.

**Phase F.2 — Rails S3-S8** (~1 session, 4-6h)
- Add `PersonalTrackEngine` subclass enforcing S3, S4, S6.
- Wire response-shim S5.
- Wire Publish Checkpoint gate S1 to Item E's smoke result.

**Phase F.3 — Payment + onboarding polish** (~1 session, 4-6h)
- Stripe subscription tier binding.
- Weekly digest emails via Resend.
- Onboarding UX polish (loading states, error copy, mobile).

**Phase F.4 — Beta with 3 founder-picked users** (~2 weeks calendar)
- Real users on real sites.
- Instrument everything into `funnel_events`.
- Founder reviews every PAUSE_FOR_FOUNDER halt personally.

Do NOT attempt F.1-F.4 in one session — this is 3 sessions of code +
2 weeks of user calendar. Item F is a **product**, not a feature.

---

## 7. Open questions for founder (blockers for Phase F.1)

1. **What FTP/SFTP providers are we officially supporting?**
   The Item B code supports FTPS + SFTP but many low-end hosts still
   only offer plain FTP. Do we refuse those hosts, or accept them with
   a security warning?

2. **What's the max page count?**
   Priya's site probably has 5-20 pages. Rahul's landing has 1-3.
   Is `single_page_mode` really single-page or "single project up to
   30 pages"?

3. **Who owns the emails Aurem sends on behalf of the user?**
   If we auto-email their customers ("Diwali sale — 20% off"),
   are we on their mailing list rules? DPA implications.

4. **What's the ownership model of the generated site?**
   Does the user get the code exported if they cancel? A tarball?
   A "download my site" button?

5. **How does the AI narrate progress to a non-technical user?**
   Current narration says things like "verified 3 files" — that means
   nothing to Priya. We need a `personal_track_narrator.py` that
   translates every internal loop event to plain language.

---

## 8. Not this session — WHY

The user directive was explicit: **"Item F: PROPOSE SCOPE ONLY, do not build."**
This document is that proposal. All 5 screens exist as scaffolds; the
loop wiring, safety rails, and payment integration are the actual
Personal Track build and require:
- Multi-session commitment (≥3 sessions of code)
- 2-week beta calendar with real founder-picked users
- Explicit founder go-ahead on the open questions above

The Item F effort is comparable in size to Items A + B + C + D + E
combined. It is a Track, not a feature. Founder review + explicit
"build Item F" instruction required before ANY of the code in this
document is written.

---

**Do not delete this file.** It is the source of truth for the next
Personal Track discussion. Update it (don't recreate) when founder
answers the open questions.
