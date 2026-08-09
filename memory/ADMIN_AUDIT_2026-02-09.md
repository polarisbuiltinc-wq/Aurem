# Admin Panel Accuracy Audit — 2026-02-09 · Session 5

**Scope**: read-only diagnostic across all 16 sidebar sections in
`/admin`. Verify each section is backed by live DB queries vs
cached/hardcoded/stale data. **NO fixes / NO deploys** — findings only.

**Method**: for each section, traced the React component in
`/app/frontend/src/pages/Admin.jsx` (or its dedicated file) to the
API endpoint(s) it hits, then read the backend router to classify the
data source.

**Classification**:
- 🟢 **LIVE** — direct DB query on every request, no stale surface.
- 🟡 **CACHED** — DB-backed with a short in-process cache (drift risk
  bounded by TTL; multi-pod drift possible).
- 🔴 **HARDCODED** — the value is a literal in code that does NOT
  reflect reality.
- 🟠 **MIXED** — same section renders some LIVE and some
  HARDCODED/STALE values side by side (drift risk highest here).

---

## P0 SLICE — money + drift-risk (read fully)

### 15 · Payments & Revenue  🟠 MIXED  · **P0**

**Files**: `frontend/src/pages/Admin.jsx::PaymentsPage` (line 1134-1234).
**Endpoints hit**:
1. `GET /api/aurem-dev/admin/payments` — `admin.py:1259`
2. `GET /api/aurem-dev/admin/overview-metrics` — `admin.py:2865`
3. `GET /api/aurem-dev/admin/token-pnl` — `admin.py:1070`
4. `POST /api/aurem-dev/admin/payments/reconcile` — `admin.py:1277`

| UI card | Value source | Verdict |
|---|---|---|
| Revenue (30 d) | `overview-metrics.revenue_30d` — Mongo aggregate on `cto_payments.amount` where `status ∈ {paid, complete, completed, succeeded}` | 🟢 LIVE |
| **Revenue (mo)** | `token-pnl.revenue_month` — **HARDCODED `0`** at `admin.py:1117` (`"revenue_month": 0`) | 🔴 **FAKE — shows $0 regardless of actual monthly income** |
| **AI cost (mo)** | `token-pnl.ai_cost_month` — real Mongo aggregate on `cto_tasks` × per-agent rate | 🟡 CACHED-STALE — rates hardcoded `{"deepseek": 0.30, "maxx": 0.65, "groq": 0.03}` at `admin.py:1096` (missing GPT-4o, Claude Sonnet 4.5, Gemini 3, current 2026 pricing) |
| **Net profit** | `token-pnl.net_profit = -ai_cost_month` — **HARDCODED calculation** (revenue is 0 above, so net = negative cost only) | 🔴 **FAKE** |
| Lifetime revenue | `admin/payments.total_revenue` — sums `amount` where `payment_status=="paid"` over last 100 rows only | 🟠 CAPPED at 100 rows (see `admin.py:1265 limit(100)`) — silently truncated if lifetime crosses 100 transactions |
| Transactions | `admin/payments.count` — same 100-row cap | 🟠 CAPPED |
| Pending | Client-side filter on the same 100 rows | 🟠 CAPPED |
| Reconcile button | Real `stripe.checkout.Session.retrieve` per row | 🟢 LIVE |

**⚠️ FIELD-NAME DRIFT (P0)**: `admin/payments` uses `payment_status == "paid"` while `overview-metrics.revenue_30d` uses `status ∈ {paid, complete, completed, succeeded}`. **Different fields on the same collection.** Depending on which one the Stripe webhook actually populates, one of these numbers is off. Founder should verify via a real Mongo shell: `db.cto_payments.aggregate([{$group:{_id:{payment_status:"$payment_status",status:"$status"},n:{$sum:1}}}])` to see the field-population truth.

**ACTION**: 🔴 **P0** — `Revenue (mo)` + `Net profit` are literally $0 hardcodes today. Must fix before ad-driven signups convert. Cost-rate table must be refreshed for 2026 pricing.

---

### 3 · LLM Credits  🟡 CACHED  · **P0**

**Files**: `Admin.jsx:2548` renders inline via `LLMCreditsCard`. Endpoint:
`GET /api/aurem-dev/admin/llm-credits` — `admin_bin.py:310`.

| Value | Source | Verdict |
|---|---|---|
| OpenRouter balance USD | Live `GET https://openrouter.ai/api/v1/credits` with **60s in-process cache** (`admin_bin.py:275`) | 🟡 LIVE-CACHED-60s |
| LongCat live flag | `services.llm.LONGCAT_LIVE` — process attribute set at boot probe | 🟢 LIVE-BOOT |
| Circuit-breaker state | `services.llm_circuit_breaker.get_breaker_state()` | 🟢 LIVE |
| Linters missing | `services.app_state.get_state("loop_linters_missing")` — boot probe | 🟢 LIVE-BOOT |
| Full-scan health | `services.loop_full_scan.get_full_scan_health()` | 🟢 LIVE |
| Threshold | Mongo `settings/_id=llm_credit_alert` | 🟢 LIVE |

**No hardcodes.** Only real concern: 60s in-process cache means multi-pod deploys could show slightly-different balances per pod (bounded to 60s of skew). Non-issue at current single-pod scale.

**ACTION**: none.

---

### 16 · Token P&L  🟠 MIXED  · **P0**

Same `/admin/token-pnl` endpoint as feeds Payments page. Verdicts are identical:
- 🟢 LIVE: `month_by_agent`, `day_by_agent`, `ai_cost_month`, `ai_cost_today`, `tasks_done_month`, `tasks_done_today`, `chat_sessions_month` — real Mongo aggregations on `cto_tasks`.
- 🔴 HARDCODED: `revenue_month=0`, `stripe_fees=0`, `net_revenue=0`, `net_profit=-ai_cost_month`, `margin_pct=0`, `stripe_configured=False`.
- 🟡 STALE: cost-per-1k table `{"deepseek": 0.30, "maxx": 0.65, "groq": 0.03}` — 2024-era rates, no Claude/GPT-4o/Gemini entries, so any agent outside the 3 known keys defaults silently to `deepseek` rate.

**ACTION**: 🔴 **P0** — same fix as #15 above (they share the endpoint).

---

### 12 · Feature Flags  🟡 CACHED  · **P0**

**Endpoint**: `GET /api/aurem-dev/admin/feature-flags` — `admin.py:2283`.
Backing service: `services/feature_flags.py`.

| Value | Source | Verdict |
|---|---|---|
| Flag list + `enabled` state | Mongo `feature_flags` collection, cached 60s per process | 🟡 LIVE-CACHED-60s |
| Toggle button | `POST /feature-flags/{flag}/toggle` → real DB update + local `invalidate_cache()` | 🟢 LIVE + local-invalidation |

**⚠️ MULTI-POD DRIFT RISK (currently latent)**: `invalidate_cache()` only clears the CURRENT process's cache. If AUREM scales beyond 1 pod, a toggle on pod A leaves pod B serving the stale flag for up to 60s. Session 4 introduced Redis for rate-limiter; the same pattern would fix this — Redis pub/sub on flag-toggle events. Not urgent at current single-pod scale but flag it for the horizontal-scale contract (Layer 11 in the infra audit).

**ACTION**: 🟠 P1 — no current bug, add Redis-invalidation to the horizontal-scaling design doc (Layer 11 / item #29-adjacent).

---

## P1 SLICE — user-facing accuracy (spot-checked, deferred to next session for deep read)

### 1 · Cockpit — 🟢 LIVE (heavy)

Endpoints: `/admin/status/all`, `/version`, `/admin/dashboard`, `/admin/pulse`. All real DB / real subprocess health probes. No hardcodes spotted in the top-level pull; some sub-cards need per-endpoint verification next session.

### 2 · Overview — 🟢 LIVE (very heavy — 15+ endpoints in parallel)

`/usage/public/stats`, `/wall/stats`, `/admin/council/stats`, `/admin/mode-telemetry`, `/admin/db-health`, `/admin/overview-metrics`, `/admin/insights/user-patterns`, `/admin/insights/activation-funnel`, `/admin/alerts`, `/admin/council/health`, `/admin/github-sync`, `/admin/qa/guard17-breakers`, `/admin/qa/vscode-marketplace-status`, `/funnel/github/stats?days=7`. All appear DB-backed. Sub-card verification deferred — one card at a time next session.

### 4 · Parliament Live — deferred
### 5 · QA Health — deferred (separate `/admin/qa` route; already touched during Guard 17 work — presumed live)
### 6 · Architecture — deferred (dedicated `Architecture()` component in `Admin.jsx:1378`)
### 7 · BIN Tracker — deferred (`AdminBINTracker` component + `admin_bin.py` router)
### 8 · Users (Legacy) — 🟢 LIVE with known gap
Existing partial audit from Session 4: user list is live from `db.dev_users`; per-user detail page is missing an **Email Activity** card (that's item #34 in the ledger). No hardcoded totals observed.
### 9 · Support — deferred
### 10 · Suggestions — deferred (`AdminSuggestions` dedicated page)
### 11 · Audit Log — deferred

## P2 SLICE — internal-cosmetic (deferred)

### 13 · House Rules V2 — deferred
### 14 · Robot Guide — deferred

---

## 🎯 Founder-actionable summary

| Priority | Section | Finding | Fix effort |
|---|---|---|---|
| 🔴 **P0** | Payments & Revenue | "Revenue (mo)" + "Net profit" cards are literal `0` hardcodes at `admin.py:1117-1122` | ~1h — replace with real `cto_payments` aggregate for current month, fees from Stripe API |
| 🔴 **P0** | Payments & Revenue | Field-name drift: `payment_status` (list endpoint) vs `status` (overview) — one of the "Revenue" numbers is off | ~30 min — pick canonical field, migrate any legacy rows |
| 🟠 P1 | Payments & Revenue | Lifetime revenue / transactions / pending capped at last 100 rows | ~30 min — replace with Mongo `count_documents` + aggregate on full collection |
| 🔴 **P0** | Token P&L | Cost-per-1k table is 2024-era, missing GPT-4o / Claude Sonnet 4.5 / Gemini 3 pricing; unknown agents silently use `deepseek` rate | ~1h — move rates to `services/cost_rates.py`, keyed by `LLM_MODEL_PRICING` env or a dedicated Mongo `llm_cost_rates` collection so updates don't need a deploy |
| 🟠 P1 | Feature Flags | 60s per-process cache — future multi-pod toggle drift | ~2h — Redis pub/sub on toggle (Layer 11 horizontal-scale contract) |
| 🟢 | LLM Credits | No hardcodes | none |

**Total P0 fix effort**: ~2.5-3h. All safe to bundle with the Track 1 + Track 3 + Guard 18 deploy window if scoped tight — OR ship as a follow-up "Payments accuracy" deploy after the current bundle lands. Founder call.

---

**Read-only pass complete.** No code was modified. P1/P2 deferred sections logged as ledger item continuation work.

---

## Addendum — 2026-02-09 · Codebase-wide hardcoded-marketing-value grep

**Scope**: read-only sweep for the "498/500" class of drift — any
static counter, testimonial number, or marketing metric that could
outlive its truth. Frontend + backend.

### 🔴 P0 — direct contradictions with Track 3 or SEO-indexed false claims

| # | File · line | Hardcoded string | Why it matters |
|---|---|---|---|
| H1 | `pages/BugHunt.jsx:542` | `"498 of 500 founder spots remaining at $9/month"` | **Static text on a public marketing page**. Same exact drift as the Landing.jsx tag we just fixed — just moved to the /bug-hunt landing page. Ad campaigns pointing anywhere near this page carry the identical fraud/misrepresentation risk. |
| H2 | `pages/BugHunt.jsx:299` | `"Used by 500+ developers"` inside the `JSON_LD` schema.org description | **SEO-indexed marketing claim**. Google will scrape this into search snippets. If real dev count is <500 (Session 4 PRD says 30 prod / 74 preview) this is a factually false schema.org claim, indexable and citable. Higher FTC exposure than a visible-only claim because it's structured data. |
| H3 | `pages/Landing.jsx:667` | FAQ answer: `"Founder pricing is limited to the first 500 users; check the pricing page for the current spots-remaining count."` | **Direct contradiction with the just-shipped First-50 promo.** A visitor reading the FAQ sees "500", the promo card now says "50/50 left" (or "N/50" once claims start). Two authoritative-looking numbers on the same page conflict — worse than a single hardcode. |

### 🟠 P1 — hardcoded totals inside live-remaining components (drift the moment we change the cap)

| # | File · line | Pattern | Why it's fragile |
|---|---|---|---|
| H4 | `components/FounderOfferPill.jsx:41` | `` `{s.remaining} of 500 founder spots remaining` `` | Numerator polled live from `/founder-offer/status`, denominator hardcoded `500`. If backend `TOTAL_SPOTS` ever changes (currently `500` in `routers/founder_offer.py:41`), the two numbers drift silently. |
| H5 | `components/ConnectRepoBanner.jsx:106` | `` `${remaining} of 500 founder spots remaining` `` | Same pattern, same fragility. |
| H6 | `components/FounderOfferCard.jsx:114` | `"Sold out — all 500 spots have been claimed."` | Static error-message string. Fires only on backend `action==="sold_out"` response, so it IS live-triggered, but the number `500` is baked into the message. |
| H7 | `services/onboarding_email.py:130` | Email HTML body: `"→ One of 500 founder spots — yours"` | The connect-repo nudge email body. Every nudge sent past the 500 mark would send an outright false promise. Currently 1,485 rows in the collection so this has already fired at scale — worth checking if any went out post-cap. |

### 🟡 P2 — soft claims (marketing tone, low legal risk)

- `pages/CodebaseHealth.jsx:635` — `"Most codebases have 20-50 issues that developers don't know about"` — generic marketing claim, no counter drift.
- `pages/Integrations.jsx:305` — `"VS Code 1.100+ with MCP support required"` — factual version requirement, safe.

### 🟢 Backend — clean

Backend has no user-facing hardcoded marketing numbers. All `total`/`count` occurrences are either `$inc` counters, empty-state defaults, or MongoDB projections — legitimate. The `cost_per_1k` table in Token P&L is the only stale hardcode and is already logged in the main P0 audit.

---

## Founder-actionable priority stack (all findings, combined)

| Prio | Item | Effort | Where |
|---|---|---|---|
| 🔴 **P0** | Payments "Revenue (mo)" + "Net profit" = `0` hardcodes | ~1h | `admin.py:1117-1122` — main P0 slice |
| 🔴 **P0** | H1 · BugHunt "498 of 500 founder spots" static text | 5 min | `pages/BugHunt.jsx:542` — delete, or wire to `/promo/first50/status` |
| 🔴 **P0** | H2 · BugHunt JSON-LD "Used by 500+ developers" | 5 min | `pages/BugHunt.jsx:299` — replace with truthful count or remove the sentence |
| 🔴 **P0** | H3 · Landing FAQ contradicts the new promo counter | 10 min | `pages/Landing.jsx:667` — rewrite FAQ to match "First-50" promo |
| 🟠 P1 | Token P&L cost-per-1k table is 2024-era | ~1h | `admin.py:1096` — externalise rates |
| 🟠 P1 | Payments list capped at last 100 rows | ~30 min | `admin.py:1265` — count_documents on full collection |
| 🟠 P1 | H7 · onboarding email body promises "One of 500 founder spots" | ~15 min | `services/onboarding_email.py:130` — replace with First-50-aware copy |
| 🟠 P1 | H4-H6 · hardcoded "500" denominators in live-counter components | ~30 min combined | Wire denominator from backend response instead of literal |
| 🟠 P1 | Feature Flags 60s per-process cache — multi-pod drift latent | ~2h | Layer 11 horizontal-scaling doc |

**Combined P0 fix window (all four)**: ~1h 20min. Small enough to fit into the "Payments accuracy" follow-up deploy tomorrow WITHOUT stretching it. Recommend: bundle P0 items P0-1 through P0-4 into that single follow-up.

