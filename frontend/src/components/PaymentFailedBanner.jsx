/**
 * PaymentFailedBanner.jsx — 2026-08-22
 *
 * Founder ask (billing gap fix): the LAST recurring charge on this
 * user's subscription failed (Stripe `invoice.payment_failed`
 * webhook — see routers/payments.py). Shows an unmissable in-app
 * prompt to update the card via the same Stripe Customer Portal used
 * by "Manage billing", rather than the user finding out only after
 * Stripe eventually cancels the subscription.
 */
import React, { useEffect, useState } from "react";
import { AlertTriangle, CreditCard, Loader2 } from "lucide-react";
import { api } from "../lib/api";

export default function PaymentFailedBanner() {
  const [paymentFailed, setPaymentFailed] = useState(false);
  const [tier, setTier] = useState("");
  const [opening, setOpening] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const check = async () => {
      try {
        const r = await api.get("/payments/my-plan");
        if (cancelled) return;
        setPaymentFailed(!!r.data?.payment_failed);
        setTier(r.data?.tier || "");
      } catch { /* leave last known state on a transient blip */ }
    };
    check();
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") check();
    }, 60000);
    return () => { cancelled = true; clearInterval(timer); };
  }, []);

  async function updateCard() {
    setOpening(true);
    try {
      const r = await api.post("/payments/portal");
      const url = r.data?.portal_url || r.data?.url;
      if (url) window.location.href = url;
    } catch {
      setOpening(false);
    }
  }

  if (!paymentFailed) return null;

  return (
    <div
      data-testid="payment-failed-banner"
      style={{
        display: "flex", alignItems: "center", gap: 10,
        margin: "0 0 10px", padding: "10px 14px",
        background: "rgba(239,68,68,0.10)",
        border: "1px solid rgba(239,68,68,0.4)",
        borderRadius: 8, fontSize: 13, color: "#fca5a5",
      }}
    >
      <AlertTriangle size={16} style={{ flexShrink: 0, color: "#ef4444" }} />
      <span style={{ flex: 1, lineHeight: 1.4 }}>
        <strong style={{ color: "#fff" }}>Your last payment failed</strong>{" "}
        — update your card to keep your{tier ? ` ${tier}` : ""} plan active.
        Stripe will keep retrying automatically in the meantime.
      </span>
      <button
        type="button"
        data-testid="payment-failed-update-card-btn"
        onClick={updateCard}
        disabled={opening}
        style={{
          display: "inline-flex", alignItems: "center", gap: 6,
          padding: "6px 12px", fontSize: 12, fontWeight: 600,
          background: opening ? "rgba(255,255,255,0.08)" : "var(--accent-2, #FF8A2A)",
          color: opening ? "#94a3b8" : "#fff",
          border: "none", borderRadius: 6,
          cursor: opening ? "default" : "pointer",
          whiteSpace: "nowrap",
        }}
      >
        {opening
          ? <><Loader2 size={12} className="animate-spin" /> Opening…</>
          : <><CreditCard size={12} /> Update card</>}
      </button>
    </div>
  );
}
