/**
 * Pricing.jsx — Public pricing route (/pricing).
 *
 * Iter 176 — fetches the authenticated user's real tier so the
 * "CURRENT" badge actually reflects what they're paying for. Anonymous
 * visitors (no token) get `currentTier=""` so no plan is mis-labelled
 * as their current. PricingCards.upgrade() itself handles redirect to
 * /login when an anon visitor clicks "Upgrade".
 */
import { useEffect, useState } from "react";
import Shell from "../components/Shell";
import PricingCards from "../components/PricingCards";
import { api, getToken } from "../lib/api";

export default function Pricing() {
  const [currentTier, setCurrentTier] = useState("");

  useEffect(() => {
    // Only call the authenticated endpoint when a token actually
    // exists — otherwise we just splash a 401 in the console for
    // nothing. Anon visitors see no "CURRENT" badge anywhere.
    if (!getToken()) return;
    let alive = true;
    api.get("/payments/my-plan")
      .then((r) => { if (alive) setCurrentTier(r.data?.tier || ""); })
      .catch(() => { /* logged-in user but plan lookup failed —
                        leave the badge off, never throw a banner */ });
    return () => { alive = false; };
  }, []);

  return (
    <Shell>
      <div
        data-testid="pricing-page"
        style={{ maxWidth: 900, margin: "0 auto", padding: "48px 24px" }}
      >
        <div style={{ textAlign: "center", marginBottom: 48 }}>
          <span className="eyebrow">pricing</span>
          <h1 className="serif" style={{ fontSize: 36, marginTop: 8 }}>
            Flat pricing. No token meters.
          </h1>
          <p style={{ color: "var(--text-dim)", marginTop: 8 }}>
            One flat monthly price. No token meters — pay for your plan's task allotment, not per-request.
          </p>
        </div>
        <PricingCards currentTier={currentTier} />
        <footer style={{
          marginTop: 48, paddingTop: 16, textAlign: "center",
          borderTop: "1px solid var(--border, rgba(255,200,120,0.16))",
          fontSize: 11, color: "var(--text-faint)",
        }}>
          <button
            type="button"
            data-testid="pricing-footer-cookie-prefs"
            onClick={() => {
              try { localStorage.removeItem("aurem_consent"); } catch (_e) { /* private mode */ }
              window.dispatchEvent(new CustomEvent("aurem:reopen-consent"));
            }}
            style={{
              background: "transparent", border: "none", padding: 0,
              cursor: "pointer", color: "inherit", font: "inherit",
              textDecoration: "underline",
            }}
          >
            Cookie preferences
          </button>
        </footer>
      </div>
    </Shell>
  );
}
