/**
 * lib/sentry.js — Iter 388-p1 · Frontend Sentry wiring (Item #20).
 *
 * ACTIVATION: Requires `REACT_APP_SENTRY_DSN` in `frontend/.env`.
 * Without a DSN this module is a NO-OP — it does not crash, does not
 * network, does not emit console noise.  Safe to import unconditionally.
 *
 * To activate:
 *   1. Create a free Sentry account at https://sentry.io/signup/
 *   2. Create a React project → copy the DSN
 *      (looks like: https://abc123@o12345.ingest.us.sentry.io/6789)
 *   3. Add to `frontend/.env`:
 *        REACT_APP_SENTRY_DSN=https://…
 *   4. Redeploy — Sentry initialises automatically on next boot.
 *
 * Why this exists (from FUTURE_BUILDS_LEDGER item #20):
 *   Before this wiring, prod white-screen bugs were INVISIBLE — a
 *   React render exception would blank the tab and the founder had
 *   no way to know until a user complained.  With Sentry live, every
 *   uncaught frontend error surfaces in the Sentry dashboard within
 *   seconds, tagged with the deploy commit SHA so we know exactly
 *   which build introduced the regression.
 *
 * Config choices:
 *   · tracesSampleRate: 0.1        — 10% perf traces (cheap on free tier)
 *   · replaysSessionSampleRate: 0  — session replay OFF by default (costly)
 *   · replaysOnErrorSampleRate: 1  — full replay ONLY on error sessions
 *   · release: build_hash from index.html <meta> → tags every event with
 *              the deploy that caused it (matches /api/health build_hash)
 *   · environment: 'production' | 'preview' — auto-detected from hostname
 */
import * as Sentry from "@sentry/react";

let _initialized = false;

/**
 * Initialise Sentry.  Idempotent: safe to call multiple times.
 * Returns true if Sentry is now active, false if DSN missing (no-op).
 */
export function initSentry() {
  if (_initialized) return true;

  const dsn = (process.env.REACT_APP_SENTRY_DSN || "").trim();
  if (!dsn) {
    // Intentional silent no-op — Sentry is opt-in via env.
    return false;
  }

  // Detect environment from hostname (avoids depending on NODE_ENV
  // which Vite fixes to "production" for every build).
  const host = (typeof window !== "undefined" && window.location?.hostname) || "";
  const environment =
    host === "auremcto.com" || host === "www.auremcto.com"
      ? "production"
      : host.includes("preview.emergentagent.com")
        ? "preview"
        : "dev";

  // Pull the build hash from the <meta name="build-hash"> tag if
  // present so every Sentry event is tagged with the deploy that
  // triggered it (matches /api/health build_hash).  Fallback to
  // "unknown" so init never fails.
  let release = "unknown";
  try {
    const meta = document.querySelector('meta[name="build-hash"]');
    if (meta && meta.content) release = String(meta.content).trim().slice(0, 20);
  } catch { /* SSR / non-DOM env — ignore */ }

  Sentry.init({
    dsn,
    environment,
    release,
    // BrowserTracing + Replay integrations wire themselves up when
    // enabled below; keep the list explicit for grep-ability.
    integrations: [
      Sentry.browserTracingIntegration(),
      Sentry.replayIntegration({
        maskAllText: false,
        blockAllMedia: true,   // never upload user images/video to Sentry
      }),
    ],
    tracesSampleRate: 0.1,
    replaysSessionSampleRate: 0.0,
    replaysOnErrorSampleRate: 1.0,
    // Never phone home about localhost/dev noise.
    beforeSend(event) {
      if (environment === "dev") return null;
      // Drop the well-known "ResizeObserver loop limit exceeded" —
      // browser quirk, not a real error.  Every React app on the
      // internet hits this and it pollutes the dashboard.
      const msg = event?.message || event?.exception?.values?.[0]?.value || "";
      if (typeof msg === "string" && msg.includes("ResizeObserver loop")) {
        return null;
      }
      return event;
    },
  });

  _initialized = true;
  return true;
}

/** Sentry.ErrorBoundary re-export so callers don't need a second
 *  `import * as Sentry` line. */
export const SentryErrorBoundary = Sentry.ErrorBoundary;

/** Manually report an exception without throwing (e.g. from a
 *  catch block that intentionally recovers).  No-op if Sentry
 *  wasn't initialised. */
export function reportSentryException(err, context) {
  if (!_initialized) return;
  Sentry.captureException(err, context ? { extra: context } : undefined);
}
