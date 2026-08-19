/**
 * ShipWallOptInCard.jsx — 2026-08-19
 * SEC-003 fix companion: the public Ship Wall is now opt-IN, so users
 * need a way to actually turn it on. Settings → Profile tab.
 */
import React, { useState } from "react";
import { Trophy } from "lucide-react";
import { api } from "../lib/api";

export default function ShipWallOptInCard({ me, onChange }) {
  const [busy, setBusy] = useState(false);
  const optedIn = !!me?.wall_opt_in;

  async function toggle() {
    setBusy(true);
    try {
      await api.post(optedIn ? "/wall/opt-out" : "/wall/opt-in");
      onChange?.(!optedIn);
    } catch { /* silent — non-critical toggle */ }
    finally { setBusy(false); }
  }

  return (
    <section className="card" data-testid="settings-ship-wall">
      <h3 style={{ fontSize: 14, color: "var(--text)", margin: 0, marginBottom: 8, display: "flex", alignItems: "center", gap: 8 }}>
        <Trophy size={14} /> Public Ship Wall
      </h3>
      <p style={{ fontSize: 12, color: "var(--text-dim)", margin: 0, marginBottom: 14, lineHeight: 1.5 }}>
        Show your shipped tasks (repo name + AI summary) on the public{" "}
        <code>/wall</code> showcase. Off by default — nothing is shared
        until you turn this on.
      </p>
      <button
        type="button"
        data-testid="ship-wall-opt-toggle"
        onClick={toggle}
        disabled={busy}
        className={optedIn ? "btn-secondary" : "btn-primary"}
        style={{ justifyContent: "center", width: "fit-content" }}
      >
        {busy ? "Saving…" : optedIn ? "Public — click to hide" : "Show my ships on the wall"}
      </button>
    </section>
  );
}
