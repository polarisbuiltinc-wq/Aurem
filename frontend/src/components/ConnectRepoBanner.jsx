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

const COLLAPSE_KEY = "aurem_connect_banner_collapsed";

export default function ConnectRepoBanner({ onConnect }) {
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
            {typeof remaining === "number"
              ? `${remaining} of 500 founder spots remaining`
              : "Loading founder spots…"}
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexShrink: 0 }}>
          <button
            type="button"
            data-testid="connect-repo-banner-cta"
            onClick={onConnect}
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
            title={collapsed ? "Show how to generate a PAT" : "Hide details"}
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

      {/* 2026-02-11 · Phase 4b — banner walkthrough now points at the
          wizard as the single source of truth for the connect UX.
          The wizard itself is App-first (one-click install) with a
          PAT disclosure fallback for private/legacy repos. */}
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
          Click <strong>Connect repo →</strong> above. The wizard offers
          two ways to connect:{" "}
          <strong>GitHub App</strong> (recommended — one click, no token
          to manage) or a{" "}
          <strong>Personal Access Token</strong> (for private / legacy
          repos). You&apos;ll pick whichever fits.
        </div>
      )}
    </div>
  );
}


function StepCard({ n, title, body }) {
  return (
    <div
      data-testid={`connect-repo-banner-step-${n}`}
      style={{
        background: "rgba(0,0,0,0.18)",
        border: "1px solid rgba(255,255,255,0.06)",
        borderRadius: 8,
        padding: "10px 12px",
        display: "flex",
        flexDirection: "column",
        gap: 4,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          fontSize: 11,
          color: "var(--text-dim, #aaa)",
          fontFamily: "'JetBrains Mono', monospace",
          letterSpacing: "0.06em",
        }}
      >
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            width: 18, height: 18,
            borderRadius: "50%",
            background: "rgba(234,179,8,0.20)",
            border: "1px solid rgba(234,179,8,0.50)",
            color: "#facc15",
            fontWeight: 700,
            fontSize: 10,
          }}
        >
          {n}
        </span>
        STEP {n}
      </div>
      <div
        style={{
          fontSize: 13,
          fontWeight: 600,
          color: "var(--text, #fff)",
          marginTop: 2,
        }}
      >
        {title}
      </div>
      <div
        style={{
          fontSize: 12,
          lineHeight: 1.5,
          color: "var(--text-dim, #bbb)",
        }}
      >
        {body}
      </div>
    </div>
  );
}

function counterColor(remaining) {
  if (typeof remaining !== "number") return "var(--text-dim, #aaa)";
  if (remaining <= 10) return "#ef4444";
  if (remaining <= 50) return "#f97316";
  return "#22c55e";
}

const codeStyle = {
  background: "rgba(255,255,255,0.08)",
  padding: "1px 5px",
  borderRadius: 3,
  fontFamily: "'JetBrains Mono', monospace",
  fontSize: 11,
};
