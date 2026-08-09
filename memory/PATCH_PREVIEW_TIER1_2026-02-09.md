# Tier-1 Patch Preview · 2026-02-09
### 3 text-only hardcode fixes, staged for tonight's deploy bundle

**Status**: NOT applied yet. Awaiting founder approval on the exact wording
below. Once approved, I run `search_replace` on each file, re-run the
Track 3 pytest suite (regression only — no logic touched), take one
smoke screenshot, and stage clean into the tonight bundle.

**Regression risk**: near-zero. Three string replacements, no imports,
no new state, no new API calls, no schema changes. Not a component
render change — just literal text.

---

## H1 · `pages/BugHunt.jsx:542` — static "498 of 500 founder spots"

### Current (line 542)
```jsx
<div className="cta-foot">498 of 500 founder spots remaining at $9/month</div>
```

### Proposed
```jsx
<div className="cta-foot">$9/month · flat pricing · 10 free scans, no credit card</div>
```

**Rationale**: strips the false counter, keeps the *value props* that
were already right (flat pricing + free trial + no CC). Same visual
weight, same information density. No React state, no polling.

**Alternate if you'd rather wire it live**: replace with a
`{promoRemaining}/{promoTotal} founder spots · $9/month` polled from
`/api/aurem-dev/promo/first50/status`. Costs ~30 min more (add
useEffect + safe-guard for null state). Recommend the static
proposal above for tonight since the /bug-hunt page has zero other
live state currently — introducing one adds complexity beyond
"text-only".

---

## H2 · `pages/BugHunt.jsx:299` — JSON-LD "Used by 500+ developers"

### Current (line 299)
```jsx
"description": "ORA Bug Hunt detects 50+ security vulnerabilities in your codebase using static analysis. Finds secrets (15 types), vulnerable code patterns (20), exposed endpoints (10), and CVE-vulnerable dependencies (11). Used by 500+ developers. $9/month.",
```

### Proposed
```jsx
"description": "ORA Bug Hunt detects 50+ security vulnerabilities in your codebase using static analysis. Finds secrets (15 types), vulnerable code patterns (20), exposed endpoints (10), and CVE-vulnerable dependencies (11). Flat $9/month pricing, 10 free scans, no credit card.",
```

**Rationale**: removes the fabricated `Used by 500+ developers.`
sentence entirely. Replaces with truthful pricing + trial claims we
already stand behind on the visible page. Keeps the SEO description
substantive (character count within 10 of original — Google's
`<meta description>` sweet spot is 150-160 chars, both are ~250
which is fine for schema.org, not meta).

**Why not "Used by N+ developers" with the real N?** Any specific
count becomes stale the moment the number ticks. And baking a live
count into JSON-LD requires SSR (React SPA can't reliably render
JSON-LD server-side for Google to see the current number). Cleanest
long-term posture: no fabricated social-proof numbers in the schema
until we have a real >500 base worth advertising.

---

## H3 · `pages/Landing.jsx:667` — FAQ "founder pricing limited to the first 500 users"

### Current (line 666-667)
```jsx
{ q: "How much does ORA cost?",
  a: "ORA starts at $9/month flat, and there are 10 tasks free with no credit card. The $9 plan has no token metering and no per-seat pricing — the same monthly price whether ORA runs 5 tasks or 500. Founder pricing is limited to the first 500 users; check the pricing page for the current spots-remaining count." },
```

### Proposed
```jsx
{ q: "How much does ORA cost?",
  a: "ORA starts at $9/month flat, and there are 10 tasks free with no credit card. The $9 plan has no token metering and no per-seat pricing — the same monthly price whether ORA runs 5 tasks or 500 per month. The first 50 verified signups get 30 days of Pro tier free — after that everyone stays on $9/month flat unless they cancel." },
```

**Rationale**:
- Kills the "500 users" claim that directly contradicts the just-shipped First-50 promo counter on the same page.
- Names the actual current promo terms in the founder's own voice: "first 50 verified signups", "30 days of Pro", "auto-downgrade unless they cancel" — matches what Track 3 code actually does.
- Adds "per month" clarification on the "5 tasks or 500" line so a reader doesn't confuse *task quota* with the old "500 users" line the sentence above used to have.
- Zero JS/state change — pure array-of-objects text edit.

**Note**: after Track 3's first 50 spots claim out, the "first 50 verified signups get 30 days of Pro tier free" clause will read past-tense. Founder call whether to (a) leave it as evergreen copy that mildly overpromises for late arrivals, or (b) revise once claimed=50 to "$9/month flat forever, no metering." Recommend (b) via a follow-up ledger item; not tonight's scope.

---

## Not touched tonight (deliberately)

- **H4-H6** hardcoded `500` denominators in FounderOfferPill / ConnectRepoBanner / FounderOfferCard — those are inside *live-counter components* with their own logic branches; safer to parameterise them properly during tomorrow's Payments Accuracy deploy alongside the backend `TOTAL_SPOTS` env externalisation.
- **H7** onboarding email body "One of 500 founder spots" in `services/onboarding_email.py:130` — backend change, deserves its own regression pass with the nudge cron tests. Bundle it with tomorrow's deploy.
- **Payments $0 hardcodes + cost-rate table + field standardisation** — Tier-2 per your ruling, tomorrow's deploy.

---

## What happens after you approve

1. I apply the 3 `search_replace` edits (parallel, single message).
2. Re-run `pytest tests/test_promo_first50_verification.py tests/test_promo_first50_waitlist.py tests/test_welcome_email.py` — pure regression, expect 18/18 unchanged.
3. Lint pass on the 2 changed files.
4. One smoke screenshot of /bug-hunt and /#pricing FAQ to visually confirm the new copy renders as designed.
5. Report git diff. Ready for the bundle window.

Total time from your "approve" to "ready": ~5 minutes.
