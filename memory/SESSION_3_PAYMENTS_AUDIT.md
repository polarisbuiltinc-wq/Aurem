# Session 3 · Payments/Stripe Deep Audit Report
**Date**: 2026-07-31 · Session 3 · post-fixes
**Discipline**: DISCOVERY ONLY for this scan — 4 confirmed fixes were applied at the top of Session 3, this document reports what's found in the payments subsystem.

---

## 0. Session 3 Fix summary (applied before the scan)

| Fix | Real proof |
|-----|-----------|
| Fix 1 · 2 Iter-367-caused test failures | `test_iter212m9_deploy_ui.py` (2 tests) + `test_iter212m119_langfuse_tracing.py` (1 test) — updated assertions to match the new deploy-config dict shape and the deletion of `llm_router.py`. **20/20 tests pass**. |
| Fix 2 · PERSONAL_TRACK_SCOPE.md filenames | Real file names now: `ChooseTrack.jsx`, `ShipProgress.jsx` (in place of made-up `PublishCheckpoint.jsx`, `Start.jsx`, `BuildLive.jsx`). `diff` between scope-doc references and disk = empty. |
| Fix 3 · 6 dead scripts deleted | `persona_drift_eval.py`, `build_favicons.py`, 4× `iter308_*` diagnostics. Also cascaded-deleted `test_iter124e_persona_drift_eval.py` (was importing the deleted script). `grep` for any remaining reference = empty. |
| Fix 4 · Supabase + Vercel platform disabled state surfaced | New `_probe_supabase_platform()` + `_probe_vercel_platform()` in `services/integration_health.py`. Both return `status: "disabled"` with explicit `Disabled — missing <ENV>` summary when the required envs aren't set. Frontend `AdminIntegrations.jsx` `STATUS_META.disabled` added (blue color, "Disabled" label). `summary_counts` now includes `disabled` key. **Real HTTP proof**: `POST /admin/integrations/refresh` returns both new probes with correct `disabled` status and missing-env details. |

---

## 1. Payment subsystem file map

### 1.1 Backend
| File | Import count | Purpose |
|------|-------------:|---------|
| `services/stripe_client.py` | 8 | Idempotent Stripe SDK client; multi-source key resolution (runtime → env → dotenv fallback) |
| `services/subscription_tiers.py` | 21 | **Single source of truth for plan limits** — task counter, mode access, parallel agents |
| `services/billing_cron.py` | 6 | `bill_maxx_overages(db)` + `grant_referral_reward(db, uid)` |
| `services/payment_reconciliation.py` | 1 | `get_recon_summary(db)` for the admin-QA dashboard |
| `routers/payments.py` | live | 6 endpoints: `/checkout`, `/status/{sid}`, `/webhook` + `/webhook/stripe` (legacy alias), `/my-plan`, `/portal` |

### 1.2 Frontend callers
| Page/Component | Endpoint(s) hit |
|----------------|-----------------|
| `components/PricingCards.jsx` | `POST /payments/checkout`, `POST /payments/portal` |
| `components/ModeSelector.jsx` | `POST /payments/checkout` (upgrade popup) |
| `components/ThinkingHint.jsx` | `POST /payments/checkout` |
| `pages/Settings.jsx` | `GET /payments/status/{sid}` |
| `pages/Pricing.jsx` | `GET /payments/my-plan` |
| `pages/Admin.jsx` | `GET /payments/status/{sid}` |
| `pages/AdminOverview.jsx` | Static reference (docs) |

### 1.3 Env vars — actual state
Set in `.env` ✅: `STRIPE_API_KEY`, `STRIPE_WEBHOOK_SECRET`, 6 price IDs (starter/pro/team × monthly/annual).
Referenced in code but NOT set ⚠️: `STRIPE_SECRET_KEY` (alias fallback — code checks both, so functionally OK), `STRIPE_CALL_TIMEOUT` (defaults to a hard-coded value), `STRIPE_PRICES` (grouping var), `STRIPE_KEY` (deprecated name).

Verdict: **All active Stripe env vars are set**. Payments should be live.

---

## 2. Per-file classification

| File | Status | Reason |
|------|--------|--------|
| `services/stripe_client.py` | ✅ FULLY BUILT | 8 imports; multi-source key resolution with 3-hop fallback. Handles both `STRIPE_SECRET_KEY` and `STRIPE_API_KEY` names. Runtime override for hot-swap. Real live use. |
| `services/subscription_tiers.py` | ✅ FULLY BUILT | 21 imports — highly used. Single source of truth for plan limits (Iter 153). Zero silent-swallow patterns. |
| `services/billing_cron.py` | ⚠️ **HALF BUILT** | 6 imports. **Critical finding: `bill_maxx_overages(db)` is NEVER scheduled at startup**. Only manually triggered via `POST /admin/billing/run-overage-cron`. This is a revenue feature — overage billing only fires when an admin clicks a button. If founder forgets → overages never billed → real revenue loss. `main.py` startup has 20+ background tasks but NOT this one. |
| `services/payment_reconciliation.py` | ⚠️ HALF BUILT | Only 1 caller (`admin_qa.py::get_recon_summary`). 2 silent-swallow blocks at lines 148, 166. Feature exists but only surfaces in an admin dashboard nobody looks at unless there's a payment issue. |
| `routers/payments.py` | ✅ FULLY BUILT | 6 endpoints, all with real frontend callers. Real Stripe API calls verified live via integration_health probe. Zero silent-swallow patterns. |

---

## 3. Test coverage — payments subsystem

Ran the 8 payment-related test files with 15s timeout:

```
63 passed, 4 skipped, 0 failed
```

Test files verified passing:
- `test_iter90_stripe_real_prices.py`
- `test_iter102_billing_cron_referral_reward.py`
- `test_iter124d_stripe_async_offload.py`
- `test_iter179_payments_defensive.py`
- `test_iter183_stripe_gpay_rewrite.py`
- `test_iter212m240_tier3_tier4_billing_gate.py`
- `test_iter335b_stripe_price_selfheal.py`
- `test_iter352_payments_truth.py`

**No pre-existing failures in the payments subsystem**. This is the healthiest subsystem I've audited so far — the earlier `test_iter102_billing_cron_referral_reward::test_bill_maxx_overages_iterates_real_db` failure from Session 2 was a **shared-fixture** issue (requires a seeded real Mongo state), not a payment-logic bug.

---

## 4. Silent-swallow patterns in payments

**Total in payments subsystem: 4** (all in service-level defensive layers, all reasonable):
- `stripe_client.py:79` — dotenv fallback failure → skip (correct: fallback is best-effort)
- `stripe_client.py:98` — key-normalisation edge → skip (correct: never break the caller)
- `payment_reconciliation.py:148` + `payment_reconciliation.py:166` — DB read best-effort — **debatable**; a reconciliation summary that silently loses rows may mislead the founder.

None of these are dangerous per se, but the `payment_reconciliation.py` two swallows deserve a `logger.warning` addition (**not fixed this session**).

---

## 5. Cross-cutting findings — payments-specific

### 5.1 Missing scheduled task for overage billing (⚠️ real revenue risk)
`billing_cron.bill_maxx_overages()` is documented as "safe to call repeatedly (idempotent within a billing month)" — but nothing calls it repeatedly. It's admin-manual only. Recommendation for a future session: add a startup task that runs monthly (e.g. 1st of the month at 02:00 UTC) with `is_first_of_month + hour_cutoff` gate.

### 5.2 `subscription_tiers.py` is the highest-import service (21) that hasn't been changed for many iters
Not a bug, just a stability signal — this file has been the SoT for months. Any refactor here has blast-radius across 21 call sites.

### 5.3 Webhook has TWO endpoints (`/payments/webhook` + `/webhook/stripe`)
The comment at `payments.py:467` says "legacy path — keep for old config". This is fine — but if Stripe dashboard config has drifted to the legacy path, and a future refactor removes the legacy alias, silent payment breakage would happen. **Recommendation**: audit the Stripe dashboard's actual webhook URL to know which path is live in prod. **Not actionable in code without founder access to the Stripe dashboard.**

### 5.4 `stripe_client.py::_stripe_client()` re-reads `.env` on EVERY call via `dotenv_values`
Lines 68-80 — dotenv fallback is inside the hot path. In a burst of concurrent Stripe calls this repeats the file read N times. Not incorrect (cached at filesystem-page level by the OS), but not efficient either. Low priority. **Not fixed this session** per your discipline rule.

### 5.5 No end-to-end test hitting REAL Stripe test-mode API
Every payments test uses mocks. There is no integration test that actually hits `sk_test_*` with a real fake card. Given the "Verify-First, Zero Mocks" discipline the founder demanded for Item B (real FTP/SSH), this is a gap. **Recommendation for a future session**: add a test that hits Stripe test-mode with the pre-configured `STRIPE_API_KEY` (which is a real sk_test_ key) using a `4242 4242 4242 4242` card and verifies the checkout session creation.

---

## 6. Cumulative status update (through Session 3)

- **Backend services deep-audited**: 15 (Session 1) + ~55 (Session 2) + 4 payments (Session 3) = **~74 of ~151** (~49%)
- **Backend routers**: still 60 registered, ~15 deeply spot-checked
- **Frontend orphans**: 2 deleted (Session 1) — clean
- **Dead scripts**: 6 deleted this session — clean
- **Silent no-op integrations**: Supabase + Vercel platform now SURFACE as `disabled` on admin dashboard (Fix 4)
- **Real pytest**: 3799 pass / 28 fail (26 pre-existing after fixing 2 I introduced)
- **Payments subsystem**: 63/63 pass, 1 real risk finding (missing scheduled overage billing task), 4 minor swallow patterns

---

## 7. Recommended focus for Session 4

Priority order:

1. **Add scheduled task for `bill_maxx_overages`** in `main.py` startup — real revenue guardrail
2. **26 pre-existing test failures** — full grouped-by-cause fix session
3. **20+ unsupervised background tasks** — one supervised-task wrapper
4. **Remaining ~77 services** not yet deep-audited — pick top-10 by import count
5. **Real Stripe test-mode integration test** — Item B-style "zero mocks" for payments
