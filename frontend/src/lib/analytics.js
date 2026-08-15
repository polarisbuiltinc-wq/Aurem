/**
 * analytics.js — Google Ads conversion tracking helpers.
 *
 * Iter 156 — Single source of truth for all gtag conversion events.
 * The base library is loaded from `index.html` so window.gtag exists
 * by the time React mounts. These wrappers degrade silently when gtag
 * is unavailable (ad-blocker, prerender, SSR, etc.) so callers never
 * need to guard their own call-sites.
 *
 *   trackSignup()        — fire after a successful account creation
 *   trackPurchase(value) — fire after a confirmed Stripe checkout
 *
 * REPLACE the two `*_LABEL` strings below with the actual labels
 * from Google Ads → Tools → Conversions → Create conversion → Website.
 * Each conversion action in the Google Ads console issues its own
 * label string of the form `abc123XYZ` that pairs with the account ID.
 */

// === Google Ads account ID ====================================
const GADS_ACCOUNT_ID = "AW-18239920865";

// === Per-conversion labels (replace placeholders) =============
// Until the founder pastes the real labels from the Google Ads
// console these resolve to the literal text "CONVERSION_LABEL",
// which means Google will silently ignore the event. That's the
// safest default — no garbage conversions logged against the wrong
// goal.
const SIGNUP_LABEL   = "CONVERSION_LABEL";
const PURCHASE_LABEL = "CONVERSION_LABEL";

// ----------------------------------------------------------------
// Safe wrapper. Returns true if the event was queued, false otherwise.
// We do NOT throw — analytics failures must never surface in product
// flows like signup / checkout polling.
// ----------------------------------------------------------------
function _fire(label, value, currency, extras = {}) {
  try {
    if (typeof window === "undefined" || typeof window.gtag !== "function") {
      return false;
    }
    window.gtag("event", "conversion", {
      send_to: `${GADS_ACCOUNT_ID}/${label}`,
      value,
      currency,
      ...extras,
    });
    return true;
  } catch (_) {
    return false;
  }
}

/**
 * Fire a "signup completed" conversion. Called once per successful
 * account creation (email/password or GitHub OAuth landing).
 *
 * Default value 9.0 CAD matches the standing instruction from the
 * founder — change here, not at call-sites.
 */
export function trackSignup() {
  return _fire(SIGNUP_LABEL, 9.0, "CAD");
}

/**
 * Fire a "purchase completed" conversion. Called once per confirmed
 * Stripe checkout (when the polling loop sees `payment_status === paid`).
 *
 * `value` and `currency` may be overridden so dynamic plan amounts
 * (Starter $9, Pro $19, Team $49 USD) can be reported accurately. The
 * default keeps the founder's original 9.0 CAD instruction for any
 * call-site that doesn't pass plan info.
 */
export function trackPurchase(value = 9.0, currency = "CAD", txnId = null) {
  const extras = txnId ? { transaction_id: txnId } : {};
  return _fire(PURCHASE_LABEL, value, currency, extras);
}

// ================================================================
// Meta Pixel conversion helpers — Iter 389
// ----------------------------------------------------------------
// The Pixel base script is injected in `CookieConsentBanner.jsx`
// which calls `fbq('init', ...)` + `fbq('track', 'PageView')` on
// load. These helpers fire ADDITIONAL standard events tied to
// concrete backend confirmations (not button-clicks). Every wrapper
// is a silent no-op when `window.fbq` is absent (ad-blocker, SSR,
// pre-init) so business flows never surface analytics failures.
//
//   metaCompleteRegistration() — after /auth/signup or OAuth `new=1`
//   metaLead(source)           — after /cto/projects/add success
//   metaPurchase(v, ccy, sid)  — after /payments/status = "paid"
//
// The `eventID` field (Meta's dedup key) mirrors the transaction /
// user context so a future CAPI (server-side) hop can dedupe against
// the browser event.
// ================================================================

function _fbq(eventName, params = {}, eventID = undefined) {
  try {
    if (typeof window === "undefined" || typeof window.fbq !== "function") {
      return false;
    }
    if (eventID) {
      window.fbq("track", eventName, params, { eventID });
    } else {
      window.fbq("track", eventName, params);
    }
    return true;
  } catch (_) {
    return false;
  }
}

/**
 * Fire Meta Pixel `CompleteRegistration` once per successful account
 * creation (email/password signup OR OAuth flow where backend flags
 * `new=1`). Callers must ensure this only fires when the backend has
 * actually minted a fresh account row.
 */
export function metaCompleteRegistration(method = "email") {
  return _fbq("CompleteRegistration", {
    content_name: method,
    status: true,
  });
}

/**
 * Fire Meta Pixel `Lead` on a meaningful signal that the user is a
 * genuine potential customer (currently: successful project add via
 * `/cto/projects/add`). `source` is a short string included for
 * Meta's `content_name` so we can slice by trigger later.
 */
export function metaLead(source = "project_added") {
  return _fbq("Lead", {
    content_name: source,
  });
}

/**
 * Fire Meta Pixel `Purchase` ONLY when the backend has confirmed a
 * paid Stripe checkout (`payment_status === "paid"`). Callers MUST
 * pass real `value` + `currency` — pass `null` for value to skip the
 * event entirely (guardrail: never send $0 / unknown-value purchases
 * to Meta because it pollutes the ad account's conversion data).
 *
 * `sid` (Stripe checkout session id) doubles as the Meta dedup key
 * so a future CAPI server event can dedupe against this browser one.
 */
export function metaPurchase(value, currency, sid = null) {
  if (value == null || Number.isNaN(Number(value)) || Number(value) <= 0) {
    return false;
  }
  if (!currency || typeof currency !== "string") {
    return false;
  }
  const params = { value: Number(value), currency };
  if (sid) params.content_ids = [sid];
  return _fbq("Purchase", params, sid || undefined);
}
