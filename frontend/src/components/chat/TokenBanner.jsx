/**
 * components/chat/TokenBanner.jsx — Iter 212m-153 / Iter 364
 *
 * Yellow/red banner shown above the chat composer when the user is
 * close to (or past) their token budget.  Drives upgrade conversion.
 *
 * Props:
 *   usage  { used, effective_limit, remaining, pct_used, is_exhausted,
 *            is_blocked, monthly_task_cap, tasks_this_month }
 *
 *   is_blocked == true: red,    "🚫 Tokens exhausted — upgrade …"
 *                                 (server-side enforcement is live)
 *   pct_used >=  80%:   yellow, "⚠️ 80% tokens used — N remaining …"
 *   below 80%:          renders nothing (returns null)
 *
 * Iter 364 change — `is_blocked` (canonical server flag) wins over the
 * client-computed pct calc so the banner can never lie about "you're
 * blocked" while the server is happily accepting requests, and can
 * never say "you're fine" while the server is 402-ing every call.
 */
import React from "react";

export default function TokenBanner({ usage }) {
  if (!usage) return null;
  const pct = usage.pct_used || 0;
  // Server-authoritative block flag. Falls back to legacy is_exhausted
  // + pct >= 100% for older /usage/me responses that don't ship
  // is_blocked yet (safe to remove once the deploy is on Iter 364+).
  const blocked =
    usage.is_blocked === true
    || usage.is_exhausted === true
    || pct >= 100;
  if (!blocked && pct < 80) return null;
  const remaining = Math.max(0, usage.remaining || 0);
  const used  = usage.used || 0;
  const limit = usage.effective_limit || 0;

  const tone = blocked
    ? { bg: "rgba(255,77,77,0.10)",  border: "rgba(255,77,77,0.45)",  color: "#ff8585", icon: "🚫" }
    : { bg: "rgba(255,196,0,0.10)", border: "rgba(255,196,0,0.45)", color: "#ffcf5c", icon: "⚠️" };

  return (
    <div
      data-testid="token-banner"
      data-state={blocked ? "exhausted" : "warning"}
      style={{
        display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
        padding: "8px 12px",
        background: tone.bg,
        border: `1px solid ${tone.border}`,
        borderRadius: 8,
        fontSize: 12,
        color: tone.color,
        fontFamily: "'Jost', system-ui, sans-serif",
      }}
    >
      <span style={{ fontSize: 14 }}>{tone.icon}</span>
      <span style={{ flex: 1, minWidth: 0 }}>
        {blocked ? (
          <>
            <b>Tokens exhausted.</b> {used.toLocaleString()} / {limit.toLocaleString()} used.
            Upgrade your plan to continue.
          </>
        ) : (
          <>
            <b>{Math.round(pct)}% tokens used</b> · {remaining.toLocaleString()} remaining.
            Upgrade to keep going.
          </>
        )}
      </span>
      <a
        href="/pricing"
        data-testid="token-banner-upgrade"
        style={{
          padding: "5px 12px",
          background: tone.color,
          color: "#0a0a0e",
          fontWeight: 600,
          fontSize: 11,
          borderRadius: 6,
          textDecoration: "none",
          whiteSpace: "nowrap",
        }}
      >
        Upgrade →
      </a>
    </div>
  );
}
