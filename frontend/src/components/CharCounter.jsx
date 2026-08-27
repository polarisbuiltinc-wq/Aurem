/**
 * CharCounter.jsx — Live character counter for chat composers.
 *
 * Shows `N / MAX` on the right edge of the composer. Turns amber at
 * 80% of the cap and red at 100%. When over cap, exposes the same
 * "message too long — shorten or split" copy the backend 422 handler
 * uses so the two error paths feel consistent.
 *
 * Also exports `formatTooLongError(err, max)` — a helper that turns
 * FastAPI's raw Pydantic 422 into a plain-English message.
 */
import React from "react";

export function CharCounter({ value = "", max, style = {} }) {
  const n = (value || "").length;
  const pct = max > 0 ? n / max : 0;
  const color = pct >= 1 ? "#ef4444"       // red — over limit
              : pct >= 0.8 ? "#eab308"     // amber — warning
              : "#8b8b8b";                  // muted default
  return (
    <span
      data-testid="chat-char-counter"
      title={pct >= 1 ? "Too long — shorten or split into multiple messages"
                       : `${n} / ${max} characters`}
      style={{
        fontSize: "var(--chip-font-md)",
        fontFamily: "'JetBrains Mono', monospace",
        color,
        fontWeight: pct >= 0.8 ? 600 : 400,
        whiteSpace: "nowrap",
        ...style,
      }}>
      {n.toLocaleString()} / {max.toLocaleString()}
    </span>
  );
}

/** Convert a FastAPI 422 or generic axios error into a founder-friendly
 *  "Message too long" string. Falls back to null if it doesn't look
 *  like a length-related error — caller can then keep its old error
 *  toast. */
export function formatTooLongError(err, max) {
  const d = err?.response?.data;
  if (!d) return null;
  // Pydantic v2 shape: {detail: [{type: "string_too_long", ctx: {max_length}}]}
  const arr = Array.isArray(d.detail) ? d.detail : null;
  if (arr) {
    const overflow = arr.find(x => x?.type === "string_too_long"
                                  || /too\s*long/i.test(x?.msg || ""));
    if (overflow) {
      const cap = overflow.ctx?.max_length || max;
      const got = (overflow.input || "").length;
      return `Message too long: ${got.toLocaleString()} chars / ` +
             `${cap.toLocaleString()} max. Shorten it, or split into ` +
             `multiple messages.`;
    }
  }
  // Fallback: plain-string detail like "String should have at most N characters"
  if (typeof d.detail === "string" && /at most.*character/i.test(d.detail)) {
    return `Message too long — shorten it or split into multiple messages.`;
  }
  return null;
}

export default CharCounter;
