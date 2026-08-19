/**
 * PasswordStrengthMeter.jsx — 2026-08-19 (consolidated from Signup.jsx's
 * original inline meter, 2026-08-19 later same-day pass)
 * Shared live strength indicator for Signup/Change/Reset password
 * forms. Scoring lives in `lib/passwordStrength.js` (length + char-class
 * diversity + common-password/sequence/repeat block-list) — single
 * source of truth, also used by Signup.jsx for submit-gate validation.
 */
import React from "react";
import { scorePassword } from "../lib/passwordStrength";

export default function PasswordStrengthMeter({ password }) {
  if (!password) return null;
  const s = scorePassword(password);
  return (
    <div data-testid="password-strength-meter" style={{ display: "grid", gap: 4, marginTop: 6 }}>
      <div style={{ display: "flex", gap: 3, height: 4, borderRadius: 2, overflow: "hidden" }}>
        {[0, 1, 2, 3].map((i) => (
          <div
            key={i}
            data-testid={`password-strength-bar-${i}`}
            style={{
              flex: 1,
              background: i < s.score ? s.color : "rgba(255,255,255,0.08)",
              transition: "background 120ms ease",
            }}
          />
        ))}
      </div>
      {s.label && (
        <span data-testid="password-strength-label" style={{ fontSize: 11, color: s.ok ? "var(--text-dim)" : s.color }}>
          {s.label}
        </span>
      )}
    </div>
  );
}
