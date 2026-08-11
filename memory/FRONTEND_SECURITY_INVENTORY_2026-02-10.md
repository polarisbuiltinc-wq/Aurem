# Frontend Security & Auth Restrictions Inventory
**Date:** 10 Feb 2026 · **Scope:** `/app/frontend/**` + parity check against `/app/backend/**`  
**Convention:** Every frontend control is annotated with **Backend Parity** — is the same check enforced server-side too, or is it frontend-only (bypassable)?

Legend  
· ✅ **Backend Parity:** Same check exists server-side; frontend is just UX.  
· ⚠️ **Frontend-only:** No server-side enforcement — bypassable by anyone who edits the JS or hits the API directly.  
· ➖ **N/A:** Rule has no server-side counterpart by design (e.g. localStorage cleanup on logout).

---

## 1 · Auth Guards on Routes

### 1.1 Router-level guards — **NONE**
`frontend/src/App.jsx:203-329` mounts every route (Dashboard, Admin, Settings, Analytics, Projects, Bug Hunt, AdminFinancials, AdminApiKeys, …) directly inside `<Routes>` with **no `<PrivateRoute>` or `<AdminRoute>` wrapper**. There is no `element={<RequireAuth>...</RequireAuth>}` pattern anywhere in the router.

- **Backend Parity:** ✅ Every protected backend endpoint is gated via `current_dev()` / `_require_admin()` — the frontend router being open is not a security gap, only a UX gap (an unauth'd user sees a broken/empty page before the per-page guard fires).

### 1.2 Per-page auth gate — `<Shell requireAuth>`
The single reusable gate lives at **`frontend/src/components/Shell.jsx:93,349-350`**:
```jsx
export default function Shell({ children, requireAuth, chromeless }) {
  ...
  useEffect(() => {
    if (requireAuth && !token) navigate("/login", { replace: true });
  }, [requireAuth, token, navigate]);
```
Callers that opt in: `Dashboard.jsx:69`, `Projects.jsx:28`, `Deploy.jsx:148`, `Domain.jsx:52`, `Analytics.jsx:54`, `Tokens.jsx:25`, `Wrapped.jsx:14`, `Automations.jsx:76`.

- **Backend Parity:** ✅ The redirect is UX only. All data-fetching hits `/api/...` which requires `Authorization: Bearer <jwt>` via `current_dev()` (`cto_services/auth.py:15-74`).

### 1.3 Pages that DO NOT use `<Shell requireAuth>` but still require login
- **`Settings.jsx:52`** — hand-rolled: `if (!getUser()) navigate("/login", { replace: true });`
- **`Admin.jsx:3101,3141`** — reads `localStorage.getItem("aurem_token")` and lets the backend 401 kick the user out via response interceptor.
- **`Integrations.jsx`, `BugHunt.jsx`, `CodebaseHealth.jsx`** — no explicit guard; they only render meaningful data after `/api/...` calls succeed with a valid token.

- **Backend Parity:** ✅ Same as above — server-side JWT gate is the source of truth.

### 1.4 Public routes (intentional, no gate)
`/`, `/login`, `/signup`, `/verify`, `/why-ora`, `/demo`, `/pricing`, `/privacy`, `/terms`, `/acceptable-use`, `/cookie-policy`, `/refund-policy`, `/ai-code-processing`, `/subprocessors`, `/dpa`, `/security`, `/status`, `/bug-hunt`, `/wall`, `/oauth-finish`, `/vs/:slug`, `/compare`, `/wrapped` (public), `/dev/*` (fixtures).

### 1.5 Role-based (admin / founder) gate
**`frontend/src/lib/api.js:158-162`** — single source of truth:
```js
export function isAdminOrFounder(u) {
  const me = u !== undefined ? u : getUser();
  if (!me) return false;
  return !!(me.is_admin || me.is_founder || me.tier === "founder");
}
```
Used by `Shell.jsx` sidebar links, `Admin.jsx` tab visibility, `BugHunt.jsx`, `CodebaseHealth.jsx`, `Vanguard*.jsx` for CONDITIONAL RENDERING of admin UI.

- **Backend Parity:** ✅ **This is the most important parity guarantee.** Server-side enforced by:
  - `cto_services/auth.py:77-117` — `require_admin()` re-fetches the live `dev_users` row so a stale JWT with `is_admin=false` can't be spoofed by frontend flag flipping.
  - `routers/admin.py:46` — `dependencies=[Depends(require_admin_dep)]` gates the ENTIRE router.
  - `routers/admin.py:50-70` — every admin handler ALSO calls `await _require_admin(authorization)` inline (defense-in-depth).
  - `routers/mfa.py`, `routers/admin_bin.py`, `routers/admin_health.py`, `routers/admin_qa.py`, `routers/admin_vanguard.py`, `routers/admin_public.py` — all use the same gate.

---

## 2 · Token Storage & Handling

### 2.1 Storage medium — `localStorage` (⚠️ XSS-exposed)
`frontend/src/lib/api.js:127-134`:
```js
export function setToken(t) {
  if (t) localStorage.setItem("aurem_token", t);
  else localStorage.removeItem("aurem_token");
}
export function getToken() { return localStorage.getItem("aurem_token"); }
```
Also stored: `aurem_user` (JSON blob with `user_id, email, tier, tokens_remaining`) in the same `localStorage`.

- **Backend Parity:** ⚠️ **Storage medium mismatch.** The JWT is NOT an `HttpOnly` cookie — it's readable/writable by any JS running in the origin. The backend never sets a `Set-Cookie` for auth (see: `grep -r "Set-Cookie" backend/` returns zero auth results). This is a **conscious trade-off** because the API is called from multiple origins (preview pod, prod domain, VS Code extension) and Bearer tokens work everywhere.
- **Mitigation:** CSP + DOMPurify + short JWT TTL (7 days) + refresh-on-`/auth/me` + `/auth/logout` server-side revocation (see §11).

### 2.2 Automatic Bearer attach — Axios request interceptor
`frontend/src/lib/api.js:19-26`:
```js
api.interceptors.request.use((config) => {
  const token = localStorage.getItem("aurem_token");
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});
```
Every `/api/aurem-dev/*` call auto-carries the JWT. No handler needs to remember it.

- **Backend Parity:** ✅ Required by every gated endpoint via `current_dev()`.

### 2.3 Automatic JWT rotation — Axios response interceptor
`frontend/src/lib/api.js:34-42` — any response body carrying `{ token: "..." }` is treated as an authoritative re-issue and silently swapped into `localStorage`. Enables the "sliding 7-day session" pattern where active users never see a logout.

- **Backend Parity:** ✅ Server re-signs on `/auth/me` (see `routers/auth.py:546`) and every JWT carries fresh `iat` / `jti` (`cto_services/auth.py:150-158`).

### 2.4 `logout()` — server-side revoke + local wipe
`frontend/src/lib/api.js:164-188`:
```js
export function logout() {
  ...
  fetch(`${API_BASE}/auth/logout`, { method: "POST", ... keepalive: true });
  setToken(null);
  setUser(null);
  window.location.href = "/login";
}
```
Fire-and-forget POST to `/auth/logout`, then clears both storage keys, then hard nav. Never awaits the network call (won't hang if backend is slow).

- **Backend Parity:** ✅ `routers/auth.py:652-688` — `revoke_jti()` writes the token's `jti` into `revoked_tokens` collection; `current_dev()` blocks any future call with that jti (`cto_services/auth.py:41-51`).

### 2.5 `revoke-all-sessions` — global session kill
`routers/auth.py:691-724` — flips `dev_users.session_barrier_at` so every JWT `iat` older than that becomes invalid. Frontend does not surface a UI button for this today (only the admin console / manual call).

- **Backend Parity:** ✅ Enforced in `current_dev()` `is_iat_before_barrier` check.

### 2.6 `aurem_ref` (referral code) storage
`frontend/src/App.jsx:164-185` — captures `?ref=<uid>`, length-caps at 100 chars, stashes in `localStorage`, and POSTs to `/api/aurem-dev/referrals/track`. Cleared after successful attribution in `Signup.jsx:77-81`.

- **Backend Parity:** ✅ `/referrals/track` and `/referrals/attribute` validate the ref code server-side.

---

## 3 · Session / JWT Expiry Handling

### 3.1 TTL
- **Access JWT:** 7 days (`cto_services/auth.py:157`).
- **MFA-pending JWT:** 5 minutes (`cto_services/auth.py:173`) — only usable to complete 2FA.
- **Sliding window:** every `/auth/me` re-mints, so active users never expire.

### 3.2 Expired-token UX
`api.js:43-63` — global response error interceptor. **It does NOT auto-redirect on 401.** Pages that hit a 401 must handle it themselves; in practice, `Shell.jsx:349` catches the empty-token state on next mount and the user is bounced to `/login`.

- ⚠️ **Gap:** A 401 mid-session (e.g. token revoked server-side but still in `localStorage`) does NOT immediately redirect. The user sees "failed" toasts until they navigate.

- **Backend Parity:** ✅ Backend returns 401 correctly; the gap is frontend UX.

### 3.3 `useAutoClearConsole` — console hygiene on route change
`frontend/src/lib/useAutoClearConsole.js` (mounted in `App.jsx:139-140`) clears `console` on every route change so leaked debug prints don't accumulate in a shared browser.

- **Backend Parity:** ➖ N/A.

---

## 4 · CORS & Cross-Origin Configuration

### 4.1 Frontend — request origin
The frontend simply calls `${REACT_APP_BACKEND_URL}/api/aurem-dev/...`. No `credentials: "include"` cookie mode is used (the app is Bearer-token only) so browsers do NOT auto-send cookies. See `api.js:16` and `logout` `fetch(..., keepalive: true)`.

### 4.2 Backend — allow-list
`backend/main.py:1378-1412`:
```py
_ALLOWED_ORIGINS = env "ALLOWED_ORIGINS", default:
  "https://auremcto.com,https://www.auremcto.com,
   http://localhost:3000,http://localhost:5173"

app.add_middleware(CORSMiddleware,
  allow_origins=_ALLOWED_ORIGINS,
  allow_origin_regex=r"^https://.*\.(preview\.emergentagent\.com|
                       emergent\.host|deploy\.emergentcf\.cloud)$",
  allow_credentials=True,   # ← for cookie-based OAuth callback flows
  allow_methods=["GET","POST","PUT","DELETE","OPTIONS","PATCH"],
  allow_headers=["Authorization","Content-Type","X-Requested-With"],
)
```
Wildcard `*` was intentionally removed (iter security lockdown). Preview pods and the prod domain are the only permitted origins.

- **Backend Parity:** ✅ Backend is the enforcer — frontend cannot bypass by editing origins.

---

## 5 · CSRF Protection

### 5.1 Overall posture
**Bearer-token model → CSRF-immune by design.** The JWT is stored in `localStorage` and injected via an `Authorization` header, not via cookies. A cross-origin request would not have the token, so no CSRF token / SameSite cookie / double-submit pattern is needed.

Search results — **NO CSRF middleware or CSRF token generation found** in either codebase:
```
grep -rn "CSRF\|csrf\|SameSite\|samesite" backend/main.py backend/routers/*.py frontend/src/lib/api.js
→ (empty)
```

### 5.2 Exceptions — OAuth callback state (short-lived)
- `routers/google_oauth.py`, `routers/github_oauth.py`, `routers/github_app.py` — OAuth callbacks use the standard **`state` param** for CSRF protection (signed random nonce validated on callback).
- **Backend Parity:** ✅ Server-side state validation is the enforcement.

### 5.3 Webhook secret verification (out-of-band CSRF equivalent)
- Stripe: `routers/payments.py:489` `/payments/webhook` verifies `stripe-signature` HMAC.
- GitHub App: `routers/github_app.py` `/webhook` verifies `X-Hub-Signature-256` HMAC against `admin_settings.github_app_config.webhook_secret`.
- Resend: TODO (Item #34 — not yet wired).

---

## 6 · Rate Limiting on Auth & Sensitive Endpoints

### 6.1 Frontend
**No client-side rate-limit.** No debounce on Login/Signup submit beyond the standard `setBusy(true)` disable-while-in-flight pattern (`Login.jsx:46-47`, `Signup.jsx:54-55`).

### 6.2 Backend — all enforced server-side
| Endpoint | Rule | Env var | File |
|---|---|---|---|
| `POST /auth/login` | 10/min per IP (burst) + 5 fails / 15 min lockout | `LOGIN_RATE_PER_MIN=10`, `LOGIN_FAIL_LIMIT=5`, `LOGIN_LOCKOUT_MIN=15` | `routers/auth.py:36-100` |
| `POST /auth/signup` | 3/24h per IP + honeypot + `form_age_ms` timing + disposable-email block | `SIGNUP_RATE_LIMIT_PER_IP=3` **(P1 bug: currently 999 in prod)** | `services/signup_guards.py:5-160` |
| `POST /chat/stream` | 30/min per IP; founders/unlimited bypass | (hard-coded 30) | `routers/chat.py:1143-1173` |
| `codebase_health` scans | 10 scans/hr/user/category (sliding window, DB-persisted) | (hard-coded 10) | `routers/codebase_health.py:671-688` |
| **Global safety net** | Per-IP `GLOBAL_RL_PER_MIN` catch-all across ALL endpoints, Redis-shared across pods | `GLOBAL_RL_PER_MIN` | `main.py:1893-1972` |

- **Backend Parity:** ⚠️ **Frontend-only:** none. **Backend-only:** all. This is the correct posture — frontend rate-limits are UX polish, backend is the enforcer.

### 6.3 Rate-limiter health probe
`GET /api/aurem-dev/health/rate-limiter` (`main.py:2430`) exposes whether Redis or in-memory backend is active. Founder-only.

---

## 7 · Input Validation & Sanitisation

### 7.1 Frontend form validation
- **`Signup.jsx:44-53`** — `agreed` (ToS checkbox) required, `password === password_confirm` required. Length/format checks are **NOT** done client-side; server rejects short passwords via `SignupBody` Pydantic model.
- **`Login.jsx`** — no client-side validation beyond `<input required>`.
- **`Verify.jsx`** — reads `?token=` from URL, POSTs to `/api/aurem-dev/auth/verify`. No frontend format check.
- **`Settings.jsx`** — Stripe `session_id` from URL is passed straight through to `/api/aurem-dev/payments/status/{sid}` (server validates).
- **`ConnectRepoBanner.jsx`** — GitHub PAT is passed straight through to backend which does the validation.
- **`ChatPanel.jsx`** — prompt text is not validated client-side; server enforces token quota + moderation.

- **Backend Parity:** ✅ **Every** payload lands on a Pydantic `BaseModel` (see `SignupBody`, `LoginBody`, `TwoFAVerifyBody` in `routers/auth.py`). Client-side is UX-only for these. **⚠️ Any frontend field-length or regex check is bypassable** — always assume server is the enforcer.

### 7.2 URL param sanitisation
- **Referral code** — `App.jsx:168`: `if (ref && ref.length > 0 && ref.length < 100)` before write.
- **Login/Signup `?next=`** — `Login.jsx:23`, `Signup.jsx:23`: `rawNext.startsWith("/") && !rawNext.startsWith("//")` → prevents open-redirect via `next=//evil.com`. ✅ Good.

- **Backend Parity:** ➖ N/A — redirect happens client-side.

### 7.3 XSS-relevant sinks (see §9 for full sanitisation strategy)
- `RobotGuide.jsx:74` — `dangerouslySetInnerHTML` (**DOMPurify sanitised**, `RobotGuide.jsx:31-37`).
- `MermaidBlock.jsx:189` — `dangerouslySetInnerHTML` for SVG (**DOMPurify SVG profile**, line 191).
- `PolicyPage.jsx:127` — `dangerouslySetInnerHTML` for markdown-rendered policy files (**DOMPurify HTML profile**, `PolicyPage.jsx:50-52`).
- `Projects.jsx:1529` — `dangerouslySetInnerHTML` (**DOMPurify sanitised** on same line).
- `RenderedMessage.jsx:35,211` — custom `sanitizeForDisplay()` for LLM output.
- `PreviewPanel.jsx:87-117`, `OraPreviewPanel.jsx:136` — `new Function(...)` code execution in a **sandboxed iframe** (marked with `// vanguard: ignore — sandboxed iframe`).
- `Both.jsx:948-973`, `LoopLiveFeedDemo.jsx:133` — demo/fixtures with static strings only.

---

## 8 · Emergent-Managed Google OAuth Handshake

### 8.1 Session-id flow
`frontend/src/pages/OAuthFinish.jsx` reads `session_id` from URL fragment, POSTs to `/api/aurem-dev/auth/google` server-side which exchanges it for the verified Google profile.

- **Backend Parity:** ✅ **Session id is never trusted client-side** — server does the Google verification, then mints its own AUREM JWT (see `routers/auth.py:305-` for the pattern).

### 8.2 GitHub OAuth
`routers/github_oauth.py` — standard OAuth 2.0 code-exchange with `state` param CSRF guard. Frontend just triggers a popup / redirect and waits for the postMessage handshake (`NewUserWizard.jsx`, `AddProjectWizard.jsx`).

---

## 9 · XSS Protections

### 9.1 Content Security Policy (CSP)
`backend/main.py:1712-1721`:
```
default-src 'self';
script-src 'self' 'unsafe-inline' https://auremcto.com;
style-src  'self' 'unsafe-inline';
img-src    'self' data: https:;
connect-src 'self' https://auremcto.com wss://auremcto.com
            https://openrouter.ai https://api.github.com;
frame-src  'self' blob:;
font-src   'self' data:;
```

- **⚠️ Gap:** `'unsafe-inline'` is kept for `script-src` and `style-src` because of lucide-react + inline React styles. Noted in the code comment as "will tighten with nonces in a later iteration".
- **Backend Parity:** ✅ Sent by backend on every response (both `_security_headers` middleware `main.py:1701-1721` and `_apply_security_headers` route-cache helper `main.py:1752-1758`).

### 9.2 DOMPurify usage
- `frontend/src/components/RobotGuide.jsx:23,31-37` — allow-list: `strong, em, b, i, u, span, br, small, code` + `class, style` attrs.
- `frontend/src/components/MermaidBlock.jsx:22,191` — SVG profile.
- `frontend/src/pages/PolicyPage.jsx:15,50-52` — HTML profile (for markdown output).
- `frontend/src/pages/Projects.jsx:22,1529` — HTML profile.

### 9.3 Custom sanitiser
- `frontend/src/components/RenderedMessage.jsx:35-` — `sanitizeForDisplay()` — LLM-output-specific escaping.
- `frontend/src/components/RobotGuide.jsx:109` — `escapeHtml()` re-exported. Used by `NewUserWizard.jsx`, `Projects.jsx`, `Login.jsx`, `Signup.jsx` before interpolating user data into RobotGuide messages.

### 9.4 Security-header stack (defense-in-depth beyond CSP)
`backend/main.py:1701-1706`:
| Header | Value |
|---|---|
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` (clickjacking) |
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `X-XSS-Protection` | `1; mode=block` |
| `Permissions-Policy` | `geolocation=(), microphone=(), camera=()` |

---

## 10 · Cancellation-Flow Authorisation (specifically requested)

The app has **three** distinct cancellation surfaces. All are backed by JWT auth.

### 10.1 Subscription (Stripe) — cancel via Billing Portal
- **Frontend:** `frontend/src/components/PricingCards.jsx:213` → `api.post("/payments/portal")` → redirects to Stripe-hosted portal where the user cancels/updates the sub.
- **Backend:** `routers/payments.py:704-745` `POST /payments/portal`:
  ```py
  async def billing_portal(http_request, authorization):
    _require_stripe()
    user = await current_dev(authorization)      # ← 401 if no JWT
    row  = await db.dev_users.find_one({"user_id": user["user_id"]}, ...)
    sub_id = (row or {}).get("stripe_sub_id")
    if not sub_id: raise HTTPException(400, "No active subscription")
  ```
- **Who can cancel:** ONLY the account owner (JWT `user_id` → `dev_users.stripe_sub_id`). No cross-user vector — the portal URL is scoped to the customer's Stripe `customer_id`.
- **Backend Parity:** ✅ Fully server-enforced.

### 10.2 Founder-offer claim — cancel a preview
- **Frontend:** `frontend/src/components/FounderOfferCard.jsx` → `POST /founder-offer/cancel` with `{claim_id}`.
- **Backend:** `routers/founder_offer.py:321-352`:
  ```py
  @router.post("/cancel")
  async def cancel_offer(body, authorization):
    me = await current_dev(authorization)                     # ← 401 gate
    user_id = me["user_id"]
    res = await db.user_seo_claims.find_one_and_update(
      {"claim_id": body.claim_id, "user_id": user_id,         # ← ownership check
       "fix_status": "preview"},                              # ← state gate
      {"$set": {"fix_status": "cancelled", ...}},
    )
    if not res: return {"success": False, "reason": "not_cancellable"}
  ```
- **Who can cancel:** ONLY the claim owner (`user_id` in the filter). No admin bypass. State-locked to `fix_status == "preview"` — post-preview, cancel is a soft-no.
- **Backend Parity:** ✅ Fully server-enforced. Enum-proof (returns `success: false` instead of leaking existence of other users' claim IDs).

### 10.3 GitHub App installation — disconnect
- **Frontend:** `frontend/src/components/GitHubCard.jsx` (via disconnect button) → `DELETE /github-app/installations/{installation_id}`.
- **Backend:** `routers/github_app.py:757-802`:
  ```py
  @router.delete("/installations/{installation_id}")
  async def disconnect_installation(installation_id, authorization):
    user = await current_dev(authorization)
    row  = await db.github_installations.find_one({
      "installation_id": int(installation_id),
      "user_id":         user["user_id"],                      # ← ownership check
    })
    if not row: raise HTTPException(404, "installation_not_found")
    # (explicitly 404, not 403, so installation_id can't be enumerated
    #  across accounts)
  ```
- **Who can disconnect:** ONLY the owner. Cross-user attempts get **404** (deliberate — comment on line 765-767 explains the enum-proof design). Cascades to `cto_projects.installation_active=false` via `_cascade_project_active()`.
- **Backend Parity:** ✅ Fully server-enforced.

### 10.4 Loop cancel & chat-session delete
- `POST /loop/{loop_id}/cancel` (`routers/loop.py:923`) — verifies `current_dev()` + ownership of the loop.
- `DELETE /chat/sessions/{session_id}` (`routers/chat.py:3214`) — verifies `current_dev()` + ownership.

### 10.5 Full account deletion
- **Frontend:** **NOT EXPOSED IN THE UI.** No "delete my account" button exists in `Settings.jsx` today. Documented gap — likely part of the P1 backlog / privacy-compliance work.
- **Backend:** `DELETE /admin/users/{user_id}` (`routers/admin.py:4086-`) — **admin-only** (`_require_admin` gate), refuses to delete founder accounts, refuses to delete the caller's own account. **There is no `DELETE /auth/me` self-deletion endpoint.**
- ⚠️ **Gap flagged for GDPR/DSAR** — user cannot self-serve delete; must email support. Not exploitable, but a compliance and UX gap.

---

## 11 · JWT Revocation (server-side kill switch)

### 11.1 Two orthogonal revocation checks in `current_dev()`
`cto_services/auth.py:41-51`:
1. **Per-token (`jti`)** — token is in `revoked_tokens` collection → 401. Fired by `/auth/logout` and admin nuke.
2. **Per-user barrier (`iat < session_barrier_at`)** — any token issued before the barrier is invalid. Fired by `/auth/revoke-all-sessions`.

### 11.2 `revoked_tokens` collection
Written by `services/token_revocation.py::revoke_jti()`. TTL matches original token expiry so entries auto-clean up.

- **Backend Parity:** ➖ Purely backend; frontend just calls the logout endpoint.

---

## 12 · Multi-Factor Authentication (2FA / TOTP)

### 12.1 Coverage
- **Founders/admins only** — `routers/mfa.py:51` — the router prefix is `/admin/2fa`.
- **Not offered to regular users** yet.

### 12.2 Flow
- `POST /admin/2fa/enroll-start` → returns TOTP secret + QR + 10 backup codes.
- `POST /admin/2fa/enroll-verify` → confirms the first 6-digit code and flips `mfa_enabled=true`.
- Login: `Login.jsx:53-56` handles the `{ mfa_required: true, mfa_token }` response by switching the form into a code-entry step.
- **Server:** `POST /auth/login/2fa-verify` (`routers/auth.py:497`) consumes the 5-min `mfa_pending` token and mints the real session JWT.

- **Backend Parity:** ✅ MFA is fully server-enforced; frontend just renders the challenge.

### 12.3 Backup codes
- Hashed on enrollment (`services/mfa.py`).
- Marked one-shot after `consume_backup_code()`.

---

## 13 · Trust Level & Progressive Restriction

`frontend/src/components/TrustLevelCard.jsx` surfaces the user's `trust_level` (from `dev_users`). Backend uses this to gate high-risk operations (e.g. auto-merge PRs, batch fixes) — not a login gate, but an authorization tier.

- **Backend Parity:** ✅ Trust level is a server-side field read on every gated call.

---

## 14 · Cookie Consent

`frontend/src/components/CookieConsentBanner.jsx` (mounted in `App.jsx:333`) — one-time banner. Purely a **UI consent record** (writes `aurem_cookie_consent` to `localStorage`), no cookies are set/blocked based on it because the app doesn't set tracking cookies to begin with (Bearer-token auth, analytics via Meta Pixel loaded at `index.html`).

- **Backend Parity:** ➖ N/A.

---

## 15 · Email Verification Gate

- **Signup** (`routers/auth.py:262`): `email_verified: bool(is_founder)` — non-founders start unverified.
- **Verify endpoint** (`routers/promo_first50.py:127-`): `GET /auth/verify?token=...` — single-use atomic consume via `findOneAndUpdate`, never echoes the token, 302-redirects to `/verify` frontend page.
- **Frontend gate:** `Verify.jsx` handles the redirect landing and shows success/error UI.
- **Current enforcement:** **Verification is NOT hard-blocking for feature access today.** Unverified users can log in and use the dashboard. Verification unlocks the First-50 promo claim and (per `PRD.md`) the Referral program.

- **Backend Parity:** ✅ Server-side atomic consume + `email_verified` field is the enforcement point.

---

## 16 · Input-Field Auto-Complete & Password Hygiene

- **`Login.jsx`, `Signup.jsx`** — password inputs use `type="password"` (browser-native masking). No custom "show password" toggle observed on the login form.
- **No password strength meter** on signup (server enforces min-length only).
- **`Signup.jsx:50-52`** — requires `password === password_confirm` before submit.

- **Backend Parity:** ⚠️ **Server-side:** `bcrypt.hashpw()` with a fresh salt (`routers/auth.py:224`). **Client-side password strength is not enforced** — user can pick "abc123". Consider adding a strength meter (P2).

---

## 17 · Error Message Sanitisation

`frontend/src/lib/cleanErr.js` — shared sanitiser used by founder-facing admin pages so raw Mongo/Stripe errors never leak stack traces or IDs to the UI. Called before rendering any `catch (e)` message.

- **Backend Parity:** ✅ Backend uses `HTTPException(status, "user-safe message")` throughout — internal stack traces are logged, not returned.

---

## 18 · Analytics & Third-Party Tracking

- **Meta Pixel** — loaded via `index.html`; re-fires on SPA route change via `MetaPixelRouteTracker` (`App.jsx:147-157`).
- **Google Analytics** — none observed.
- **Sentry** — `SENTRY_ACTIVE` flag in `backend/main.py:1724`; frontend Sentry DSN is TODO (Item #20 in the P2 backlog).

- **Backend Parity:** ➖ N/A.

---

# Summary — Gap Register (frontend-only checks / observed weaknesses)

| # | Item | Severity | Recommendation |
|---|---|---|---|
| 1 | JWT in `localStorage` (XSS-exposed) | Medium | Accepted trade-off; mitigated by CSP + DOMPurify + short TTL + revocation. Not fixable without giving up multi-origin Bearer flow. |
| 2 | No 401 auto-redirect in Axios response interceptor | Low | UX polish — add global 401 handler that calls `logout()`. |
| 3 | CSP `'unsafe-inline'` on script/style | Medium | Migrate to nonces (already flagged in code comment). |
| 4 | No frontend password-strength meter | Low | Add zxcvbn-style meter on `Signup.jsx`. |
| 5 | No self-serve account deletion in `Settings.jsx` | Low (compliance) | Add `POST /auth/delete-me` + confirmation modal (privacy/DSAR compliance). |
| 6 | Router-level routes are all open — auth is per-page | Low | Purely UX (backend gates everything). Could add a `<PrivateRoute>` wrapper for cleaner code. |
| 7 | `SIGNUP_RATE_LIMIT_PER_IP` currently `999` in prod (P1 known bug) | High | Reset to `3` — this is on the current backlog. |
| 8 | No frontend Sentry DSN → client-side errors invisible | Low | P2 backlog (Item #20). |

# Chunk C answer (verification requested)
`/api/aurem-dev/promo/first50/status` endpoint **EXISTS** at `backend/routers/promo_first50.py:91-111`. Returns:
```json
{ "claimed": N, "total": 50, "remaining": max(0, total - claimed), "is_active": bool }
```
So `ConnectRepoBanner.jsx`'s hardcoded `"500 spots"` (actually the intended value is 50 per `PROMO_TOTAL_SPOTS` env default) can be wired to `.total` (or `.remaining` for the "X spots left" copy) without any backend change.

---
**End of inventory.** No code changes performed as requested.
