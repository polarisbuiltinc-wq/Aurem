/**
 * components/ConnectRepoBanner.jsx — Persistent empty-state CTA.
 *
 * Renders above the chat panel when the user has no projects connected,
 * even after they dismiss the onboarding wizard.  Collapsible so it
 * doesn't dominate the screen on every reload; the collapsed state is
 * persisted to localStorage.
 *
 * Visibility contract:
 *   - Mount this only when the caller has confirmed projectCount === 0.
 *   - Polls /founder-offer/status every 60 s so the "X of 500" counter
 *     stays roughly fresh without hammering the unauthenticated route.
 *   - Hides itself when the founder offer is fully consumed (remaining
 *     === 0) — at that point there's no SEO reward to dangle, the
 *     existing wizard remains the entry point.
 *
 * The "Connect repo →" button fires `aurem:open-connect-repo` so the
 * parent (Dashboard) can show the wizard regardless of the dismiss
 * flag in localStorage.
 */
import React, { useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import { api } from "../lib/api";
import { trackFunnel } from "../lib/githubFunnel";

const COLLAPSE_KEY = "aurem_connect_banner_collapsed";

export default function ConnectRepoBanner({ onConnect }) {
  // 2026-08-27 · Journey Watch Phase 0 — this CTA was the #1 dark
  // click identified in the signup drop-off investigation: `onConnect`
  // only ever flipped local React state, so a click here was
  // indistinguishable from never clicking at all. Fire connect_repo_click
  // FIRST (fire-and-forget, never blocks the actual UI action).
  const handleConnectClick = useCallback(() => {
    trackFunnel("connect_repo_click", "banner");
    onConnect?.();
  }, [onConnect]);
  const [status, setStatus] = useState(null);
  const [collapsed, setCollapsed] = useState(() => {
    try { return localStorage.getItem(COLLAPSE_KEY) === "1"; }
    catch { return false; }
  });

  // ── Polling (60 s) ──────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    const refresh = () => {
      api.get("/founder-offer/status")
        .then((r) => { if (!cancelled) setStatus(r.data); })
        .catch(() => { /* best-effort */ });
    };
    refresh();
    const t = setInterval(refresh, 60_000);
    return () => { cancelled = true; clearInterval(t); };
  }, []);

  const toggleCollapsed = useCallback(() => {
    setCollapsed((c) => {
      const next = !c;
      try { localStorage.setItem(COLLAPSE_KEY, next ? "1" : "0"); }
      catch { /* ignore */ }
      return next;
    });
  }, []);

  // ── Visibility ──────────────────────────────────────────────────
  // Hide entirely once the offer sells out; the SEO incentive is gone
  // and the regular wizard is the only sensible empty-state UI.
  if (status && (status.remaining ?? 0) <= 0) return null;

  const remaining = status?.remaining;
  // Total spots come from the backend so we never hardcode the promo
  // ceiling in the UI.  Falls back to a neutral loading state until the
  // first /founder-offer/status response lands.
  const total = typeof status?.total === "number" ? status.total : null;

  return (
    <div
      data-testid="connect-repo-banner"
      style={{
        margin: "12px 18px 0",
        padding: collapsed ? "10px 16px" : "16px 18px",
        background:
          "linear-gradient(135deg, rgba(234,179,8,0.12) 0%, rgba(234,179,8,0.04) 100%)",
        border: "1px solid rgba(234,179,8,0.40)",
        borderRadius: 10,
        display: "flex", flexDirection: "column", gap: collapsed ? 0 : 12,
        boxShadow: "0 1px 3px rgba(0,0,0,0.05)",
        transition: "padding 180ms ease, gap 180ms ease",
        flexShrink: 0,
      }}
    >
      {/* Header — visible in both expanded + collapsed modes */}
      <div
        style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          gap: 12, flexWrap: "wrap",
        }}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
          <div
            data-testid="connect-repo-banner-headline"
            style={{
              fontWeight: 600, fontSize: 15,
              color: "var(--text-strong, var(--text, #fff))",
            }}
          >
            Connect a repo to unlock your free SEO fix
          </div>
          <div
            data-testid="connect-repo-banner-counter"
            style={{
              fontSize: 11,
              fontFamily: "'JetBrains Mono', monospace",
              letterSpacing: "0.04em",
              color: counterColor(remaining),
            }}
          >
            {typeof remaining === "number" && typeof total === "number"
              ? `${remaining} of ${total} founder spots remaining`
              : "Loading founder spots…"}
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexShrink: 0 }}>
          <button
            type="button"
            data-testid="connect-repo-banner-cta"
            onClick={handleConnectClick}
            className="btn-primary"
            style={{
              padding: "8px 16px",
              fontSize: 13, fontWeight: 600,
              whiteSpace: "nowrap",
            }}
          >
            Connect repo →
          </button>
          <button
            type="button"
            data-testid="connect-repo-banner-toggle"
            onClick={toggleCollapsed}
            title={collapsed ? "Show how the connect flow works" : "Hide details"}
            style={{
              padding: 6,
              background: "transparent",
              border: "1px solid var(--border, rgba(255,255,255,0.12))",
              borderRadius: 6,
              color: "var(--text-dim, #aaa)",
              cursor: "pointer",
              display: "inline-flex",
            }}
          >
            {collapsed ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
          </button>
        </div>
      </div>

      {/* 2026-02-12 · App-first flow — the wizard is the single source
          of truth for the connect UX. Copy focuses on the one-click
          GitHub App install; the wizard itself still offers a PAT
          fallback for private / legacy repos, so we don't advertise
          PAT setup here. */}
      {!collapsed && (
        <div
          data-testid="connect-repo-banner-steps"
          style={{
            paddingTop: 10,
            borderTop: "1px dashed rgba(234,179,8,0.30)",
            fontSize: 12.5,
            color: "var(--text-dim, #b8b8b8)",
            lineHeight: 1.6,
          }}
        >
          Click <strong>Connect repo →</strong> above to install the{" "}
          <strong>Aurem GitHub App</strong> — one click, no tokens to
          manage. Choose which repositories to grant access to, and
          Aurem will start indexing immediately.
        </div>
      )}
    </div>
  );
}


function counterColor(remaining) {
  if (typeof remaining !== "number") return "var(--text-dim, #aaa)";
  if (remaining <= 10) return "#ef4444";
  if (remaining <= 50) return "#f97316";
  return "#22c55e";
}
