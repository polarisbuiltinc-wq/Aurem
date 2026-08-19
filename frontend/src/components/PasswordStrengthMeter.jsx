/**
 * PasswordStrengthMeter.jsx — 2026-08-19
 * Shared live strength indicator for Change/Reset password forms.
 * Client-side heuristic only — server-side length/policy rules are
 * the source of truth; this is a UX nudge, not validation.
 */
import React from "react";

export function scorePassword(pw) {
  if (!pw) return 0;
  let score = 0;
  if (pw.length >= 8) score++;
  if (pw.length >= 12) score++;
  if (/[a-z]/.test(pw) && /[A-Z]/.test(pw)) score++;
  if (/\d/.test(pw)) score++;
  if (/[^A-Za-z0-9]/.test(pw)) score++;
  return Math.min(score, 4);
}

const LEVELS = [
  { label: "Too weak", color: "var(--danger, #ff6b6b)" },
  { label: "Weak", color: "var(--danger, #ff6b6b)" },
  { label: "Fair", color: "var(--warn, #ffc560)" },
  { label: "Good", color: "var(--warn, #ffc560)" },
  { label: "Strong", color: "var(--ok, #6dd4a1)" },
];

export default function PasswordStrengthMeter({ password }) {
  if (!password) return null;
  const score = scorePassword(password);
  const level = LEVELS[score];
  return (
    <div data-testid="password-strength-meter" style={{ display: "grid", gap: 4, marginTop: -4 }}>
      <div style={{ display: "flex", gap: 4 }}>
        {[0, 1, 2, 3].map((i) => (
          <div
            key={i}
            data-testid={`password-strength-bar-${i}`}
            style={{
              height: 4, flex: 1, borderRadius: 2,
              background: i < score ? level.color : "var(--border)",
              transition: "background 160ms ease",
            }}
          />
        ))}
      </div>
      <span data-testid="password-strength-label" style={{ fontSize: 11, color: level.color }}>
        {level.label}
      </span>
    </div>
  );
}
