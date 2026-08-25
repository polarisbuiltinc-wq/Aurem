/**
 * cookieConsentStorage.js — localStorage consent persistence + tracker
 * gating helpers for CookieConsentBanner.jsx.
 *
 * Extracted from components/CookieConsentBanner.jsx (2026-08-27,
 * mechanical split — no behaviour change) to keep that file under the
 * platform's file-size guard.
 */
export const STORAGE_KEY = "aurem_consent";
export const VERSION = 1;

export const DEFAULT_CATS = {
  necessary:  true,   // always on, non-toggleable
  functional: true,
  analytics:  false,
  marketing:  false,
};

export function loadConsent() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (parsed?.v !== VERSION) return null;
    return parsed;
  } catch (_) {
    return null;
  }
}

export function saveConsent(choice, cats) {
  const record = {
    v: VERSION,
    ts: new Date().toISOString(),
    choice,
    cats,
  };
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(record));
  } catch (_) { /* private mode etc. — silent */ }
  return record;
}

/**
 * Disable / enable the browser trackers we ship in index.html.
 * We can't fully unload scripts already loaded, but we can:
 *   • Stub `fbq` so future calls no-op
 *   • Set gtag consent mode (`analytics_storage`, `ad_storage`) to denied
 *   • Dynamically load Meta Pixel if consent has just been granted and
 *     the loader in index.html skipped it (consent gate).
 */
export function applyConsentToTrackers(cats) {
  try {
    if (!cats.marketing) {
      if (typeof window !== "undefined") {
        // Wipe queue + stub. Note: if the pixel script has already
        // loaded and sent PageView, we can only prevent FUTURE events.
        window.fbq = function noop() {};
        window.fbq.q = [];
      }
    } else if (typeof window !== "undefined" && (!window.fbq || !window.fbq.loaded)) {
      // Marketing just granted AND pixel never loaded (index.html gate
      // skipped it). Load it now.
      loadMetaPixel();
    }
    if (typeof window !== "undefined" && typeof window.gtag === "function") {
      window.gtag("consent", "update", {
        ad_storage:         cats.marketing  ? "granted" : "denied",
        analytics_storage:  cats.analytics  ? "granted" : "denied",
        ad_user_data:       cats.marketing  ? "granted" : "denied",
        ad_personalization: cats.marketing  ? "granted" : "denied",
      });
    }
  } catch (_) { /* never break page for consent-plumbing */ }
}

export function loadMetaPixel() {
  if (typeof window === "undefined") return;
  if (window.fbq && window.fbq.loaded) return;
  !(function (f, b, e, v, n, t, s) {
    if (f.fbq && f.fbq.loaded) return;
    n = f.fbq = function () { n.callMethod ? n.callMethod.apply(n, arguments) : n.queue.push(arguments); };
    if (!f._fbq) f._fbq = n; n.push = n; n.loaded = !0; n.version = "2.0";
    n.queue = n.queue || []; t = b.createElement(e); t.async = !0;
    t.src = v; s = b.getElementsByTagName(e)[0]; s.parentNode.insertBefore(t, s);
  })(window, document, "script", "https://connect.facebook.net/en_US/fbevents.js");
  try {
    window.fbq("init", "1571887197933821");
    window.fbq("track", "PageView");
  } catch (_e) {
    // Best-effort: pixel network blocked or fbq stub replaced.
  }
}

export function isGpcOn() {
  try {
    return Boolean(navigator?.globalPrivacyControl);
  } catch (_) { return false; }
}
