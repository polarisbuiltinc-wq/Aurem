/**
 * lib/sanitizeError.js — R3 P1-3 (2026 overnight round).
 *
 * Shared helper so a user-facing toast/chat bubble NEVER shows a raw
 * backend stack trace, Python traceback, exception class name, or
 * file path — only ever a short, human-readable sentence.
 *
 * Backend error shapes seen in this app's `e?.response?.data?.detail`:
 *   - a plain string (already human-written, e.g. HTTPException(400, "..."))
 *   - a dict `{error, message, ...}` (structured errors, e.g. scan_fix_quota)
 *   - a list of FastAPI 422 validation objects `[{msg, ...}, ...]`
 *   - (rare, a real bug) a raw exception string/stack that slipped past
 *     an endpoint's own error handling — this is exactly what must be
 *     caught and replaced with `fallback` instead of shown verbatim.
 */

// Any of these appearing in a "message" means it's not something a
// user should ever see — treat as unsafe and fall back.
const _UNSAFE_PATTERNS = [
  /traceback \(most recent call last\)/i,
  /file "\/[^"]+", line \d+/i,
  /^\s*at\s+\S+\s*\(/m,               // JS stack frame ("at Object.foo (...)")
  /\b[A-Za-z_][A-Za-z0-9_]*Error\b:/,  // "ValueError: ...", "TypeError: ..."
  /\b[A-Za-z_][A-Za-z0-9_]*Exception\b/,
  /\/app\/(backend|frontend)\//,
  /\bmongodb(\+srv)?:\/\//i,
  /\bsk-[a-zA-Z0-9]{10,}/,             // API-key-shaped tokens
];

function _looksUnsafe(s) {
  if (typeof s !== "string") return true;
  if (s.length > 400) return true;
  return _UNSAFE_PATTERNS.some((re) => re.test(s));
}

/** Extract the best human string out of a FastAPI-shaped `detail`. */
function _fromDetail(detail) {
  if (detail == null) return null;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const msgs = detail
      .map((d) => (d && typeof d.msg === "string" ? d.msg : null))
      .filter(Boolean);
    return msgs.length ? msgs.join(" ") : null;
  }
  if (typeof detail === "object") {
    if (typeof detail.message === "string") return detail.message;
    if (typeof detail.error === "string") return detail.error;
  }
  return null;
}

/**
 * `sanitizeErrorMessage(e, fallback)` — the ONE call every ship/rollback/
 * chat-error toast should route through. Never returns raw exception
 * text, a stack trace, or a file path — always either a genuine
 * short backend-authored message or `fallback`.
 */
export function sanitizeErrorMessage(e, fallback = "Something went wrong. Please try again.") {
  const detail = e?.response?.data?.detail;
  const candidate = _fromDetail(detail);
  if (candidate && !_looksUnsafe(candidate)) return candidate;
  // No structured backend `detail` (e.g. a network error, or a plain
  // JS Error thrown by the API client) — `e.message` is often still a
  // short, legitimate message (e.g. "Network Error", or a backend 4xx
  // reason surfaced as a thrown Error). Same safety filter applies.
  if (typeof e?.message === "string" && !_looksUnsafe(e.message)) return e.message;
  return fallback;
}

export default sanitizeErrorMessage;
