/**
 * utils/chatTextUtils.js — Iter 212m-153
 *
 * Pure text-processing helpers used by ChatPanel.  Extracted into a
 * standalone module so each function is unit-testable in isolation
 * and ChatPanel's surface stays focused on streaming + UI state.
 *
 * Zero React imports here — these are deliberately tree-shakable.
 */
import { getUser } from "../lib/api";

// ── Founder / unlimited / admin synchronous check ────────────────────
// Used by ChatPanel's `useState(loadExecMode)` reducer to wipe a stale
// `localStorage.ora_execution_mode="loop"` value before it ever reaches
// the chat-stream body.  Mirrors the memoised `isLoopUnlocked` inside
// the component.
export function isLoopUnlockedSync() {
  try {
    const u = (typeof getUser === "function" && getUser()) || null;
    // 2026-08-21 — unlocked for all Pro/Team tier (founder decision,
    // after checking Admin QA Dashboard's Loop Beta panel: healthy
    // kill-switch, 0 stuck loops). Backend (services/loop_beta.py)
    // enforces this the same way + still has the kill-switch/stuck-
    // loop auto-trip safety net regardless of what the UI shows.
    return !!(u && (
      u.is_admin || u.is_unlimited || u.tier === "founder"
      || u.tier === "pro" || u.tier === "team"
    ));
  } catch {
    return false;
  }
}

// ── Natural-language Loop opt-in detector ────────────────────────────
// 2026-08-22 — founder bug report: typing "run this as a loop" in a
// normal chat message did NOT trigger the actual Loop pipeline (no
// PLAN/EXECUTE/VERIFY/SCAN/SHIP bar) — it silently fell through to a
// slow, single-shot chat request instead. Mirrors the backend's own
// `_LOOP_OPT_IN` regex (services/loop_intent.py) so both sides agree
// on what counts as "the user explicitly asked for Loop mode in
// plain English/Hinglish". Used by ChatPanel's send() to actually
// switch into Loop mode (if entitled) instead of ignoring the phrase.
export const LOOP_OPT_IN_RE =
  /\b(as a loop|loop mode|run (this |it )?as a loop|loop chala|loop mein|loop me\b)/i;

export function detectsLoopOptIn(text) {
  return LOOP_OPT_IN_RE.test(text || "");
}


// ── Code block extractor ─────────────────────────────────────────────
// Finds every ```lang\n...``` block in a markdown body.  Returns
// [{ lang, code }] with each lang lower-cased; falls back to "text"
// when no language tag is given.
export const CODE_BLOCK_RE = /```(\w+)?\n([\s\S]*?)```/g;

export function extractCodeBlocks(content) {
  if (!content) return [];
  const blocks = [];
  let m;
  CODE_BLOCK_RE.lastIndex = 0;
  while ((m = CODE_BLOCK_RE.exec(content)) !== null) {
    const lang = (m[1] || "text").toLowerCase();
    const code = m[2];
    if (code && code.trim()) blocks.push({ lang, code });
  }
  return blocks;
}


// ── Quick-reply suggestion extractor — Iter 132 ──────────────────────
// ORA frequently signs off with a CTA like:
//   "_3 of these can be auto-fixed. Say **\"fix the critical issues\"**
//   and I'll ship them via Mode C._"
// We surface that phrase as a one-click chip below the bubble.
//
// The regex captures phrases introduced by Say / Reply / Type / Respond
// followed by an optional markdown wrapper (`**`, `*`, or backtick) and
// a quoted literal.  Match window is 2-80 chars so we don't accidentally
// chip out a paragraph and we don't chip out a single-character noise.
export const SUGGESTION_RX = new RegExp(
  // intro verb
  "\\b(?:say|reply|respond(?:\\s+with)?|type)\\s+" +
  // optional opening md wrapper
  "(?:\\*\\*|\\*|`)?" +
  // opening quote
  "[\"'`]" +
  // capture: 2-80 chars
  "([^\"'`\\n]{2,80})" +
  // closing quote
  "[\"'`]" +
  // optional closing md wrapper
  "(?:\\*\\*|\\*|`)?",
  "gi",
);

export function extractSuggestions(content) {
  if (!content || typeof content !== "string") return [];
  const seen = new Set();
  const out = [];
  let m;
  // Reset lastIndex (global regex shared across calls).
  SUGGESTION_RX.lastIndex = 0;
  while ((m = SUGGESTION_RX.exec(content)) !== null) {
    const phrase = (m[1] || "").trim();
    if (!phrase) continue;
    const key = phrase.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(phrase);
    if (out.length >= 4) break;   // cap chips per bubble
  }
  return out;
}


// ── Cheap token estimator ────────────────────────────────────────────
// ~1.3 tokens per word.  Backend uses the same heuristic to deduct
// tokens, so the chat-side preview matches what gets charged.
export function estimateTokenCount(text) {
  if (!text) return 0;
  return Math.ceil(text.split(/\s+/).filter(Boolean).length * 1.3);
}
