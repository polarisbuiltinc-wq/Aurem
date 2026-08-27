/**
 * TopBarStatusSlot.jsx — Iter 330 · Founder request: move F12 error
 * strip + ModePill from the composer's status bar into the TopBar
 * header, at a safe distance from the "New run" button.
 *
 * Design (loose coupling, no prop-drilling through Dashboard):
 *   • `useF12Errors` reads from the `window.__auremF12` singleton so
 *     mounting a second instance here does NOT double-count.
 *   • The mode indicator (detected + server) is delivered via the
 *     `aurem:composer-status` broadcast fired by ChatPanel whenever
 *     detectedMode or serverMode changes. That keeps ChatPanel as
 *     the single source of truth for mode detection; this slot is a
 *     pure subscriber.
 *   • Clicks on the F12 "SEND TO ORA" / "COPY" buttons dispatch
 *     `aurem:f12-send-to-ora` and `aurem:f12-copy` respectively;
 *     ChatPanel listens and executes (same behaviour it used to run
 *     inline). No functional change — just the DOM location moves.
 *
 * Rendered by Dashboard.jsx via TopBar's new `statusSlot` prop.
 */
import { useEffect, useState } from "react";
import { useF12Errors, F12Badge, ModePill } from "./ChatPanelF12";
import { getUser, isAdminOrFounder } from "../lib/api";

// 2026-08-27 · P5 (Journey/Intent-Grounding build round) — the F12
// error-count badge was already founder/admin-gated (2026-08-21), but
// that still means the founder's OWN production hostname shows it
// (e.g. during a customer screen-share). Reusing the same hostname
// convention lib/sentry.js already uses to gate it to dev/preview
// only, never the real production domain.
function _isDevEnv() {
  const host = (typeof window !== "undefined" && window.location?.hostname) || "";
  return host !== "auremcto.com" && host !== "www.auremcto.com";
}

export default function TopBarStatusSlot() {
  const f12 = useF12Errors();
  const isFounder = isAdminOrFounder(getUser());
  const showF12Badge = isFounder && _isDevEnv();
  const [detectedMode, setDetectedMode] = useState(null);
  const [serverMode,   setServerMode]   = useState(null);

  useEffect(() => {
    const onStatus = (e) => {
      const d = e?.detail || {};
      setDetectedMode(d.detectedMode || null);
      setServerMode(d.serverMode || null);
    };
    window.addEventListener("aurem:composer-status", onStatus);
    // Also request an initial snapshot in case ChatPanel already
    // mounted and last-broadcast state is set. ChatPanel's effect
    // fires on mount, but if this slot mounts LATER we miss it —
    // request a refresh.
    try {
      window.dispatchEvent(new CustomEvent("aurem:composer-status-request"));
    } catch { /* noop */ }
    return () => {
      window.removeEventListener("aurem:composer-status", onStatus);
    };
  }, []);

  const modeProp = detectedMode
    || (serverMode
        ? { mode: serverMode, color: "#6b7280", label: "Mode " + serverMode }
        : null);

  // 2026-08-21 — founder request: F12 chip is founder/admin-only.
  // Regular users never see it (background capture still runs for
  // everyone; only the UI trigger is gated).
  if (!modeProp && !(showF12Badge && f12.hasErrors)) return null;

  return (
    <div
      data-testid="topbar-status-slot"
      style={{
        display: "flex", alignItems: "center", gap: 8,
        // Small right-side spacer so the strip visually separates
        // from the "New run" button without a hard divider.
        marginRight: 4,
      }}
    >
      <ModePill mode={modeProp} />
      {showF12Badge && (
        <F12Badge
          errorCount={f12.errorCount}
          hasErrors={f12.hasErrors}
          onCopyPayload={() => {
            try {
              window.dispatchEvent(new CustomEvent("aurem:f12-copy"));
            } catch { /* noop */ }
          }}
          onSendToORA={() => {
            try {
              window.dispatchEvent(new CustomEvent("aurem:f12-send-to-ora"));
            } catch { /* noop */ }
          }}
        />
      )}
    </div>
  );
}
