/**
 * MaintenanceScreen.jsx — full-screen branded "System Maintenance"
 * experience shown by MaintenanceGate instead of a blank page / raw
 * network error. Two flavours:
 *   - manual: admin-scheduled, shows the founder's message + window.
 *   - auto:   detected via failed health pings — framed as a brief
 *             hiccup that's actively retrying, not a broken app.
 */
import React from "react";
import { Loader2, Wrench, Zap } from "lucide-react";

export default function MaintenanceScreen({ manual, message, window: windowText }) {
  return (
    <div
      data-testid="maintenance-screen"
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 99999,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background:
          "radial-gradient(circle at 30% 20%, rgba(255,138,42,0.10), transparent 55%), #0a0e1a",
        color: "#e5e7eb",
        fontFamily: "'JetBrains Mono', monospace",
        padding: 24,
        textAlign: "center",
      }}
    >
      <style>{`
        @keyframes maint-spin { to { transform: rotate(360deg); } }
        @keyframes maint-pulse { 0%,100% { opacity: 1; } 50% { opacity: .55; } }
      `}</style>
      <div style={{ maxWidth: 460 }}>
        <div
          style={{
            width: 64, height: 64, margin: "0 auto 22px",
            borderRadius: 16,
            background: "rgba(255,138,42,0.12)",
            border: "1px solid rgba(255,138,42,0.35)",
            display: "flex", alignItems: "center", justifyContent: "center",
            animation: "maint-pulse 2.4s ease-in-out infinite",
          }}
        >
          {manual
            ? <Wrench size={28} color="#ff8a2a" />
            : <Zap size={28} color="#ff8a2a" />}
        </div>

        <h1 data-testid="maintenance-title" style={{ fontSize: 22, fontWeight: 700, margin: "0 0 10px", letterSpacing: "0.01em" }}>
          {manual ? "Scheduled Maintenance" : "Brief Hiccup — Reconnecting"}
        </h1>

        <p data-testid="maintenance-message" style={{ fontSize: 13.5, lineHeight: 1.6, color: "#9ca3af", margin: "0 0 14px" }}>
          {manual
            ? (message || "We're deploying an update. This usually takes less than a minute.")
            : "AUREM is briefly unavailable — most likely a deploy in progress. We're retrying automatically; this page will resume on its own."}
        </p>

        {manual && windowText && (
          <div
            data-testid="maintenance-window"
            style={{
              fontSize: 11.5, color: "#ff8a2a",
              border: "1px solid rgba(255,138,42,0.3)",
              borderRadius: 8, padding: "8px 12px",
              marginBottom: 18, display: "inline-block",
            }}
          >
            {windowText}
          </div>
        )}

        <div
          data-testid="maintenance-retry-indicator"
          style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 8, marginTop: 6, color: "#6b7280", fontSize: 11.5 }}
        >
          <Loader2 size={13} style={{ animation: "maint-spin 1s linear infinite" }} />
          Checking automatically…
        </div>
      </div>
    </div>
  );
}
