# Frontend Sentry — Activation Guide (Iter 388-p1, Item #20)

## Current state

- **Code is shipped and preview-verified.** `src/lib/sentry.js` is
  wired into `main.jsx` and `RouteErrorBoundary.jsx`. **Idle NO-OP
  until you paste a DSN.**
- **6/6 vitest pass** (`src/lib/__tests__/sentry.test.js`).
- **Landing page smoke test:** 0 console errors, page renders
  cleanly with Sentry inactive.

## Activation steps (2 minutes)

### 1. Create a Sentry account (free)

- Go to https://sentry.io/signup/
- Sign up with GitHub or email (free tier gives 5K errors/mo, plenty
  for a solo-founder app until it grows)

### 2. Create a "React" project

- Project name: `aurem-cto` (or anything you like)
- Platform: **React**
- Copy the DSN from the setup page. It looks like:
  ```
  https://abc123def456@o1234567.ingest.us.sentry.io/9876543
  ```

### 3. Add to `frontend/.env`

```bash
# Append this line to /app/frontend/.env
REACT_APP_SENTRY_DSN=https://abc123def456@o1234567.ingest.us.sentry.io/9876543
```

### 4. Redeploy

- Sentry initialises automatically on next backend/frontend boot.
- **Zero code changes needed** — the wiring is already live.

### 5. Verify (once live on prod)

- Open the browser DevTools console on https://auremcto.com
- Type: `throw new Error("sentry test — please ignore")`
- Within 5-30 seconds the error should appear in your Sentry
  dashboard tagged with the deploy commit hash + environment `production`.

## What's configured

| Setting | Value | Why |
|---|---|---|
| `tracesSampleRate` | 0.1 | 10% perf traces — cheap on free tier |
| `replaysSessionSampleRate` | 0.0 | Session replay OFF by default |
| `replaysOnErrorSampleRate` | 1.0 | Full replay ONLY on error sessions |
| `environment` | auto (production/preview/dev) | Detected from hostname |
| `release` | `<meta name="build-hash">` content | Tags every event with the deploy that caused it |
| `blockAllMedia` in replay | `true` | Never uploads user images/video to Sentry |
| `beforeSend` filter | drops "ResizeObserver loop" noise + dev events | Keeps dashboard signal-heavy |

## What's captured

- Every uncaught `throw` on any page (via Sentry's global handler)
- Every unhandled promise rejection
- Every React render error (via `RouteErrorBoundary`'s
  `componentDidCatch` — forwards to Sentry AND to the existing
  `/admin/errors/report` endpoint)
- Full stack trace + component stack + browser + OS + release SHA
- Session replay ONLY for error sessions (video-like reproduction
  of what the user was doing right before the crash)

## What's NOT captured

- Successful `fetch` / axios calls (only failures if you manually
  `reportSentryException(err)` in a catch block)
- Data from `<input>` / `<textarea>` (via `blockAllMedia` and default
  Sentry masking; can be tuned per-field with `data-sentry-mask` if
  you want to allow some fields through)
- Localhost `dev` env — filtered in `beforeSend` so you don't spam
  the dashboard with dev experiments

## To disable temporarily

Just remove or comment out the `REACT_APP_SENTRY_DSN` line in
`frontend/.env` and redeploy. The wiring gracefully no-ops.

## To roll to a paid tier later

- Sentry free tier: 5K events/mo — enough until Aurem has ~500-1K
  daily active users
- If we outgrow it, `tracesSampleRate` and `replaysOnErrorSampleRate`
  are the two knobs to turn down first
- Paid tier starts at $26/mo (Team plan, 50K events/mo)

---

**Next**: paste the DSN into `frontend/.env`, redeploy, and every
prod white-screen bug becomes visible within seconds instead of
invisible until a user complains.
