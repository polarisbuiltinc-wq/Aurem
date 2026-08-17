# Cloudflare Response Headers — Iter 393 (Best Practices security)

**Where to add**: Cloudflare Dashboard → your `auremcto.com` zone →
Rules → **Transform Rules → HTTP Response Header Modification** →
Create rule.

**Rule name**: `Iter 393 · security headers`

**Rule condition**: `Hostname` equals `auremcto.com` (or `starts with`
`auremcto.com`; add a second matching rule for `www.auremcto.com`
if you also serve requests through the apex-redirect zone).

**Actions**: click **"Set static"** and add each header below one at
a time. All values are ready to paste.

---

## Priority 1 — headers that unlock Lighthouse Best Practices audits

### 1. `X-Frame-Options` · unlocks "Mitigate clickjacking"

```
X-Frame-Options
```
value:
```
DENY
```

Rationale: prevents any site from embedding auremcto.com in an
`<iframe>` — kills clickjacking vector. `DENY` is stricter than
`SAMEORIGIN` and correct for us since we never embed our own pages
inside our own frames.

### 2. `Cross-Origin-Opener-Policy` · unlocks "Ensure proper origin isolation with COOP"

```
Cross-Origin-Opener-Policy
```
value:
```
same-origin
```

Rationale: isolates the top-level window from any pop-up opener so
credentialed cross-origin attacks (SharedArrayBuffer, Spectre) are
mitigated. `same-origin` is the strict Chrome-recommended value.

### 3. `Content-Security-Policy-Report-Only` · unlocks "Ensure CSP is effective against XSS attacks" (partial credit)

```
Content-Security-Policy-Report-Only
```
value (single-line, paste exactly — no leading spaces):
```
default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' https://connect.facebook.net https://www.googletagmanager.com https://www.google-analytics.com https://pagead2.googlesyndication.com https://googleads.g.doubleclick.net; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com data:; img-src 'self' data: blob: https:; media-src 'self' https://customer-assets.emergentagent.com; connect-src 'self' https://auremcto.com https://launch-pad-237.emergent.host https://connect.facebook.net https://www.facebook.com https://graph.facebook.com https://www.google-analytics.com https://pagead2.googlesyndication.com https://googleads.g.doubleclick.net; frame-src https://www.facebook.com https://td.doubleclick.net; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'; upgrade-insecure-requests
```

**Important**: this is `-Report-Only` (browsers only WARN, don't
enforce). Run in report-only mode for at least 1 week, check
Cloudflare or your own reporting endpoint for violations, then
flip the header name to `Content-Security-Policy` (drop the
`-Report-Only` suffix) to enforce.

**Full-100 caveat (honest read)**: Lighthouse's CSP audit gives
partial credit for `unsafe-inline` + `unsafe-eval`. To hit **full
100** on Best Practices, we'd need strict-dynamic with per-request
nonces — which requires the origin (Cloudflare Worker or backend)
to inject a fresh nonce into `<script>` tags on every response.
That's a larger migration (Iter 395 candidate) and NOT part of
Iter 393. Expected BP delta after this iter: **96 → ~99** (source
maps + XFO + COOP unlock 3 audits; CSP gets partial credit).

---

## Priority 2 — hardening headers Lighthouse doesn't check but production security expects

### 4. `Strict-Transport-Security` (HSTS)

```
Strict-Transport-Security
```
value:
```
max-age=63072000; includeSubDomains; preload
```

Rationale: forces HTTPS for 2 years. `preload` is safe because we
never serve HTTP on this domain. Once live for ~2 weeks, you can
submit `auremcto.com` to https://hstspreload.org — that bakes it
into Chrome / Firefox / Safari so users never touch HTTP even on
first visit.

### 5. `X-Content-Type-Options`

```
X-Content-Type-Options
```
value:
```
nosniff
```

### 6. `Referrer-Policy` (may already exist)

```
Referrer-Policy
```
value:
```
strict-origin-when-cross-origin
```

### 7. `Permissions-Policy`

```
Permissions-Policy
```
value:
```
camera=(), microphone=(), geolocation=(), interest-cohort=(), browsing-topics=()
```

Rationale: opts us out of FLoC / Topics API, denies geolocation +
camera + mic access to any embedded content. Cheap defensive
default; no functional impact since we don't use any of those APIs.

---

## Priority 3 — deferred (require app-side migration)

- **Trusted Types** (`require-trusted-types-for 'script'`) — needs
  audit of every `innerHTML =` / `dangerouslySetInnerHTML` call in
  the React codebase. Deferred to Iter 395.
- **Strict CSP with nonces** — needs a Cloudflare Worker that
  intercepts every HTML response and injects a per-request nonce
  into `<script>` tags. Also deferred to Iter 395.

Both would move BP from ~99 → 100.

---

## Verification steps (post-Cloudflare-save)

Run each in a fresh incognito tab (no cache):

```bash
# 1. Confirm each header is being served.
curl -I https://auremcto.com/ | grep -iE 'x-frame|opener|content-security|hsts|content-type-options|referrer|permissions'

# 2. Re-run PSI Mobile.
open "https://pagespeed.web.dev/analysis/https-auremcto-com/?form_factor=mobile"

# 3. Watch DevTools console on prod for any `Content Security Policy` violations.
#    Report-only mode will log warnings — that's the input for tightening.
```

Expected PSI Mobile after all 6 headers are live:
- **Best Practices: 96 → ~99** (100 needs Iter 395 nonces + Trusted Types)
- No regression on Perf / A11y / SEO / Agentic.
