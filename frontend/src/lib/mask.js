/**
 * lib/mask.js — Iter 388w · Shoulder-surf masking utilities.
 *
 * Extracted from the P0 Danger Zone fix (Iter 388v) so the same
 * pattern can protect any sensitive identifier displayed on screen
 * next to a user-input confirm step (Stripe subscription IDs, GitHub
 * installation IDs, API-key prefixes, etc.).
 *
 * Two functions, one policy each:
 *
 *   maskEmail(email, {reveal, minMask})
 *     Hide the local part, keep the domain verbatim (users recognise
 *     their inbox by domain; the local part is the secret).
 *
 *   maskId(value, {reveal, minMask})
 *     Hide the whole thing except the trailing `reveal` chars — for
 *     Stripe/GitHub/API-key IDs where the tail is the recognisable
 *     fingerprint and the head is the secret.
 *
 * Pair every use with `style={{ userSelect: "none" }}` on the wrapping
 * element so the mask can't be dragged-selected and copied.  Only the
 * user typing the FULL value from memory into a separate input should
 * satisfy the confirmation gate.
 */

// A star that doesn't render as a bullet in most fonts.
const STAR = "*";

/**
 * Mask the local part of an email address. Domain (`@example.com`)
 * is always preserved verbatim.
 *
 * Rule: hide the local part entirely except the trailing `reveal`
 * chars.  Malformed / too-short inputs fall back to a minimum of
 * `minMask` stars so we never leak the raw value.
 *
 * @param {string} email  Full email (case preserved on caller side)
 * @param {object} [opts]
 * @param {number} [opts.reveal=2]  Chars of the local part kept visible at the end
 * @param {number} [opts.minMask=4] Minimum star count for tiny local parts
 * @returns {string}      Masked email (e.g. "*********86@gmail.com")
 */
export function maskEmail(email, { reveal = 2, minMask = 4 } = {}) {
  const v = (email || "").trim();
  if (!v) return "";
  const at = v.lastIndexOf("@");
  if (at < 0) {
    // No `@` — treat the whole thing as an ID.
    return maskId(v, { reveal, minMask });
  }
  const local = v.slice(0, at);
  const domain = v.slice(at);
  if (local.length <= reveal) {
    // Too short to reveal any suffix without spilling the whole local.
    return STAR.repeat(minMask) + domain;
  }
  const tail = local.slice(-reveal);
  const mask = STAR.repeat(local.length - reveal);
  return mask + tail + domain;
}

/**
 * Mask an arbitrary opaque identifier — Stripe sub IDs, GitHub app
 * installation IDs, raw API keys, etc.  Keeps the trailing `reveal`
 * chars visible; the rest becomes stars.
 *
 * @param {string} value
 * @param {object} [opts]
 * @param {number} [opts.reveal=4]  Chars kept visible at the tail
 * @param {number} [opts.minMask=4] Minimum stars for short values
 * @returns {string}
 */
export function maskId(value, { reveal = 4, minMask = 4 } = {}) {
  const v = String(value ?? "").trim();
  if (!v) return "";
  if (v.length <= reveal) {
    return STAR.repeat(minMask);
  }
  const tail = v.slice(-reveal);
  const mask = STAR.repeat(Math.max(v.length - reveal, minMask));
  return mask + tail;
}
