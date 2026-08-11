/**
 * passwordStrength.js — 2026-02-11 · Gap Register item #40
 *
 * Tiny custom strength calculator for Signup.jsx. Uses length + char
 * diversity + a common-pattern block-list. Not as strong as zxcvbn's
 * weighted dictionary attack, but zero-dependency and adds ~1.5KB to
 * the signup bundle vs zxcvbn-ts full core+dict at ~730KB.
 *
 * Score scale (0-4):
 *   0 — very weak (empty or in the block-list)
 *   1 — weak     (too short OR single character class)
 *   2 — fair     (8+ chars, 2+ classes)   ← minimum to submit
 *   3 — good     (12+ chars, 3+ classes)
 *   4 — strong   (16+ chars, 4 classes, no repeats)
 */

// Passwords that are always considered `score: 0` regardless of length.
// Sourced from the SecLists top-100 leaked-passwords list. Deliberately
// short — the goal is to catch the "abc123 / password1 / letmein"
// class of low-effort submissions, not run a full HIBP check
// (which would require a network round-trip we don't want on every
// keystroke).
const COMMON_PASSWORDS = new Set([
  "password", "password1", "password123", "passw0rd", "p@ssword",
  "123456", "1234567", "12345678", "123456789", "1234567890",
  "qwerty", "qwerty123", "qwertyuiop", "abc123", "iloveyou",
  "admin", "administrator", "welcome", "monkey", "letmein",
  "dragon", "sunshine", "princess", "football", "master",
  "hello", "freedom", "whatever", "qazwsx", "trustno1",
  "654321", "jordan23", "harley", "ranger", "hunter",
  "buster", "thomas", "robert", "soccer", "batman",
  "test", "test123", "guest", "aurem", "auremcto",
  "changeme", "default", "root", "toor", "pass",
]);

/**
 * Return an object describing the password's strength.
 * Safe to call every keystroke — O(n) on password length.
 */
export function scorePassword(pw) {
  const password = String(pw ?? "");
  if (!password) {
    return { score: 0, label: "", color: "transparent", ok: false };
  }
  const lower = password.toLowerCase();
  if (COMMON_PASSWORDS.has(lower)) {
    return {
      score: 0, label: "Very weak — commonly used",
      color: "#ef4444", ok: false,
    };
  }
  // If the password is fully alphanumeric AND its lowercase form is in
  // the common list with a couple of digits appended (e.g. "password12"
  // → matches "password"), also flag.
  const trimmed = lower.replace(/[\d!@#$%^&*_.-]+$/, "");
  if (trimmed !== lower && trimmed.length >= 4
      && COMMON_PASSWORDS.has(trimmed)) {
    return {
      score: 0, label: "Very weak — variant of a common password",
      color: "#ef4444", ok: false,
    };
  }
  const classes =
    (/[a-z]/.test(password) ? 1 : 0)
    + (/[A-Z]/.test(password) ? 1 : 0)
    + (/[0-9]/.test(password) ? 1 : 0)
    + (/[^A-Za-z0-9]/.test(password) ? 1 : 0);
  const len = password.length;

  // Detect trivial repeats like "aaaaaa" or "111111"
  const isRepeat = /^(.)\1{5,}$/.test(password);
  if (isRepeat) {
    return {
      score: 0, label: "Very weak — repeated character",
      color: "#ef4444", ok: false,
    };
  }
  // Detect trivial sequences like "12345678" / "abcdef"
  const isTrivialSeq =
    /0123456789|1234567890|abcdefgh|qwertyuiop/.test(lower);
  if (isTrivialSeq && len < 12) {
    return {
      score: 1, label: "Weak — obvious keyboard/number sequence",
      color: "#f97316", ok: false,
    };
  }

  // Score ladder
  if (len < 8 || classes < 2) {
    return {
      score: 1, label: len < 8 ? "Weak — too short" : "Weak — mix in more character types",
      color: "#f97316", ok: false,
    };
  }
  if (len >= 16 && classes >= 4) {
    return { score: 4, label: "Strong", color: "#10b981", ok: true };
  }
  if (len >= 12 && classes >= 3) {
    return { score: 3, label: "Good", color: "#22c55e", ok: true };
  }
  return { score: 2, label: "Fair", color: "#eab308", ok: true };
}

/**
 * Minimum score allowed to submit. Kept as a const so tests and the
 * UI stay in sync.
 */
export const MIN_ACCEPTABLE_SCORE = 2;
