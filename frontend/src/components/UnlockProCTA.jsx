/**
 * UnlockProCTA.jsx — 2026-09 · D2 founder decision.
 *
 * Renders a real, working "Unlock Pro →" button on ORA's upgrade-offer
 * / fix-quota-exceeded chat bubbles. Root cause of the reported bug:
 * the backend already creates a real Stripe checkout session on
 * confirm, but only ever pasted the checkout_url as plain text in the
 * reply — nothing on the frontend ever opened it. This button opens
 * checkout_url in a NEW TAB (user stays in the chat); if no
 * checkout_url exists yet (the offer/quota-exceeded bubble, before
 * the user has confirmed anything) it creates one on click via the
 * same /payments/checkout endpoint ModeSelector.jsx already uses.
 */
import React, { useState } from "react";
import { CreditCard } from "lucide-react";
import { api } from "../lib/api";

const UPGRADE_PROVIDERS = new Set([
  "edit-tier-upgrade-offer",
  "fix-quota-exceeded",
]);

export function shouldShowUnlockProCTA(m) {
  if (!m || m.role !== "assistant") return false;
  if (UPGRADE_PROVIDERS.has(m.provider)) return true;
  return !!m.meta?.checkout_url;
}

export default function UnlockProCTA({ m, idx }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  async function handleClick() {
    if (m.meta?.checkout_url) {
      window.open(m.meta.checkout_url, "_blank", "noopener,noreferrer");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const r = await api.post("/payments/checkout", { plan: "pro" });
      const url = r.data?.checkout_url || r.data?.url;
      if (url) {
        window.open(url, "_blank", "noopener,noreferrer");
      } else {
        setError("Couldn't start checkout — try again.");
      }
    } catch (e) {
      setError(e?.response?.data?.detail || "Couldn't start checkout — try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ marginTop: 10, paddingLeft: 4 }}>
      <button
        type="button"
        data-testid={`unlock-pro-cta-${idx}`}
        onClick={handleClick}
        disabled={loading}
        style={{
          display: "inline-flex", alignItems: "center", gap: 6,
          padding: "8px 16px", borderRadius: 999,
          border: "1px solid var(--accent, #FF6608)",
          background: "var(--accent, #FF6608)",
          color: "#0A0A0A", fontWeight: 600, fontSize: 13,
          cursor: loading ? "wait" : "pointer",
        }}
      >
        <CreditCard size={14} strokeWidth={2.5} />
        {loading ? "Starting checkout…" : "Unlock Pro →"}
      </button>
      {error && (
        <div data-testid={`unlock-pro-cta-error-${idx}`} style={{
          marginTop: 6, fontSize: 12, color: "var(--danger, #fca5a5)",
        }}>
          {error}
        </div>
      )}
    </div>
  );
}
