# Test/Junk Data Investigation — 2026-08-20 (Report Only, Zero Deletions)

## 0. CRITICAL — Stripe "pro tier without payment" finding (resolved)

**Direct API check (fresh, right now) against the live Stripe key in `backend/.env`:**
- Account: `acct_1TKUU90Exg9gU93t`, business name **"aurem"**, email
  **polarisbuiltinc@gmail.com**, country CA, charges_enabled=true.
- **0 subscriptions ever. 0 charges ever. 1 payment_intent ever — $1.00 CAD, status `canceled`.**
- 75 customer records exist, but **zero of them ever completed a real payment.**

**If your Stripe Dashboard shows "Customers: empty"**, please check you're
logged into the account above (email/business name/account ID) AND that
the Test/Live toggle (top-right) is on **Live** — that combination is the
most likely explanation for the mismatch, since the API (using the app's
own configured key) does return 75 customer objects.

**How does "pro tier without Stripe payment" happen at all — is it a bug?**
No bypass bug found. Traced every code path that can write `tier`:
- `/payments/webhook` — properly verifies `stripe.Webhook.construct_event()`
  signature before writing any tier (`routers/payments.py:499`). No bypass.
- Referral reward (`grant_referral_reward`) only **extends an existing real
  Stripe subscription's trial_end** — requires the referrer to already have
  a `stripe_sub_id`. Does not grant tier to anyone new.
- **The one real non-Stripe path: `routers/promo_first50.py`** — an
  intentional, documented "first 50 verified signups get a free 30-day Pro
  trial" growth promo. Capped at 50 spots, auto-downgrades after 30 days via
  an hourly cron, and explicitly excludes real paying subscribers from that
  downgrade (`stripe_subscription_active` check). This is almost certainly
  what "Michael L. Lawson — Pro" is, if seen in production.
- No admin endpoint found that lets anyone manually set `tier` to a paid
  value outside these paths.

**Scope limit — I could not find "Michael L. Lawson" anywhere in preview's
database.** Preview's `dev_users` currently has **zero** users on
pro/team/starter tier, and `cto_payments` (internal ledger) has only 2 rows,
both tied to a literal `test_admin_001` / `test@aurem.dev` fixture account.
This strongly suggests you're looking at **production's** admin panel,
which I have no direct access to. I cannot personally verify whether other
production users also carry a paid tier without a matching Stripe
subscription — I can only confirm the promo mechanism exists and is the
only non-Stripe path in the code.

**Is this a revenue-integrity bug?** Based on the code: no — it's a
bounded, time-boxed, intentionally-built acquisition promo, not free access
slipping through a broken paywall. If you don't recall authorizing this
promo's current scope/reach, that's a product decision for you, not a
code defect.

**Recommended next step (needs your input):** if you want a definitive
per-user answer for production, I'd need either temporary read access to
production's Mongo, or you can run one read-only query yourself:
`db.dev_users.find({tier: {$in: ["pro","team","starter"]}}, {email:1, tier:1, stripe_sub_id:1, promo_first50_claimed:1})`
— any row with `promo_first50_claimed: true` is the legitimate promo, not a bug.

---

## 1. Stripe — full customer inventory (75 customers, live mode)

- **0 subscriptions, 0 charges, ever.** Zero real revenue has occurred on
  this account to date.
- Of 75 customers, **51 match obvious test patterns** by domain:
  - `aurem-test.com` — 34 (names like "Stream Test Co", "Mr Rooter Plumbing
    of Mississauga" [x3 variants], "Mike's Auto Repair" — QA fixtures for
    checkout-flow testing)
  - `gmail.com` with test-style local parts — 14 (e.g.
    `ora.platform.test.1779315034@example.com`)
  - `example.com` — 3
- The remaining **24 "non-matching" customers are NOT confirmed real
  paying customers either** — on manual inspection:
  - **13 are your own founder/dogfood accounts**: `teji.ss1986@gmail.com` /
    `teji.ss1986+dogfood@gmail.com` / `admin@aurem.live`, named "AUREM
    Founder", "AUREM Admin", "TJ Sandhu (Dogfood)", "TEJINDER SANDHU" — all
    the same person testing checkout himself, repeatedly.
  - **~5 more under `admin@reroots.ca` / "Reroots" / "Tejinder Sandhu"** —
    same name, different business email (Reroots Aesthetics Inc.) — looks
    like a second self-owned/related account used for testing.
  - **1 `pawandeep19may1985@gmail.com` "Pawandeep Singh Sandhu"** — same
    surname, likely a family/team test account.
  - **2 more (`oraoly123@gmail.com`, `otati234@gmail.com`)** — sequential
    numeric suffixes, "ora"-prefixed — pattern-consistent with test
    signups my regex simply didn't catch.
  - **Net result: I found zero customers among all 75 that look like a
    genuine unrelated external paying customer.** Every single one traces
    to either an obvious QA domain, your own name/email, or a
    test-numbering pattern.
- Full raw data (all 75, with flags): `/app/test_reports/stripe_customers_full.jsonl`
- **Risk of removal**: Stripe customer objects with no subscriptions/charges
  are inert — deleting them has no revenue or billing-history impact. Low risk.
- **Stripe test mode**: still not checked — waiting on your test-mode key.

---

## 2. MongoDB — full 136-collection sweep

- Ran a pattern sweep (test/demo/dummy/fixture/qa/fake/probe/etc.) across
  every non-empty collection. Full raw output:
  `/app/test_reports/mongo_full_sweep.txt`
- **Important caveat discovered while doing this**: `backend/tests/conftest.py`
  confirms the pytest suite reads the **same `MONGO_URL`/`DB_NAME` as the live
  preview app** — there is no isolated test database. Every one of the
  thousands of backend tests run over this project's life has been writing
  into these same collections. That's *why* so many collections show high
  "test" pattern-match rates — it's cumulative test-run residue, not
  necessarily deliberate seed data. This also means **everything below is
  PREVIEW-only** — production has its own separate Atlas Mongo I cannot query.
- **`dev_users` — the clearest, most reportable finding**: all 860 rows
  break down as:
  - `aurem.dev`: 371, `x.io`: 233, `aurem.test`: 222, `example.com`: 34
  - **These four domains account for all 860 rows — 100%.** Preview's user
    table currently contains **zero rows that look like a real external
    signup.** This is expected for a preview/sandbox environment (real
    customers sign up against production, not preview) — flagging it as
    confirmation, not alarm.
- **`cto_payments`** (internal payment ledger): only **2 rows total**, both
  `user_id: "test_admin_001"`, `user_email: "test@aurem.dev"` — one fake
  "starter" purchase marked paid with subscription id `sub_test352` (not a
  real Stripe ID format), one "pro" purchase stuck at `pending` (this looks
  like the artifact from testing the "Manage billing" button fix earlier
  today). Both are unambiguous test fixtures.
- **`cto_projects`**: earlier sampling already found `demo-app`,
  `aurem-demo/frontend`, `aurem-demo/backend`, `"Iter 330 history test"` —
  consistent with dev/QA usage.
- Collection **names** containing test/demo/sandbox: only 3, all tiny —
  `smoke_test_runs` (2 docs), `smoke_test_kv` (0), `preview_sandboxes` (1
  doc) — these are legitimate CI/health-check infrastructure, not junk.
- **Caution on the high-percentage operational collections** (`loop_task_specs`,
  `ora_prompt_snapshots`, `loop_verification_log`, `audit_log`,
  `cto_vault_audit_log`, etc., showing 90-100% "test" pattern hits): these
  are **audit trails and operational logs**, not seed/junk data. The high
  match rate is because the word "test" appears in normal fields (e.g. a
  step literally named "run tests", or QA canary health-check rows). I do
  **not** recommend treating these as deletion candidates — they're either
  real audit history or harmless synthetic health-check traffic, and
  deleting audit logs specifically is generally the wrong move regardless.

---

## 3. Hardcoded credentials in code

- Scanned all of `backend/`, `frontend/`, `scripts/` for API-key/secret/
  password/token patterns (including Stripe/AWS/Google key shapes),
  excluding `.env`/`.env.example`.
- **Result: clean.** Every match found was inside `backend/tests/` — and
  every one is a deliberately fake fixture used to test the app's *own*
  secret-scanner/redactor features (e.g. `"sk_live_51ABCdefGHIjkl..."`,
  `"AKIAIOSFODNN7EXAMPLE"`, `token = 'abcdefghijklmnop1234'"`). None are
  real credentials. Frontend: zero matches at all.

---

## 4. One-off / debug scripts

- `backend/scripts/` has 28 files — all clearly named and purposeful
  (secret scanner, dependency audit, route smoke sweep, pricing-copy lint,
  password rotation, regression-pattern seeding, etc.). None look like
  throwaway debug junk.
- `server.py` at backend root is a legitimate 2-line supervisor shim
  (`from main import app`), not stray.
- One file I created for *this investigation* (`_investigation_mongo_sweep.py`)
  has already been deleted — it was mine, not part of the codebase.

---

## Bottom line

- **No revenue-integrity bug found** — the only non-Stripe path to "pro"
  tier is an intentional, capped, auto-expiring promo.
- **Zero real Stripe revenue exists to date** on the account the app is
  configured to use — 75 customer records, 0 charges, 0 subscriptions.
- **Every one of those 75 customers traces to test/QA/founder-dogfood
  activity** — none look like a genuine outside paying customer.
- Preview's Mongo is 100% synthetic data by design (no real customers ever
  reach preview); I cannot make the same claim about production without
  direct access.
- No hardcoded secrets, no suspicious one-off scripts.
- **Nothing has been deleted.** Awaiting your call on next steps.
