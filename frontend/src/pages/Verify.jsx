/**
 * pages/Verify.jsx — Track 3 (item #31)
 *
 * Landing page after clicking the email-verification link.
 *
 * The backend endpoint `GET /api/aurem-dev/auth/verify?token=…` does
 * the actual work — marks the user verified, atomically claims one of
 * the 50 First-50 promo spots, upgrades tier to Pro with a 30-day
 * expiry — then 302-redirects to `/verify?status=ok&claimed=1` here.
 *
 * This page just renders the result the URL query string reports.
 * No API call, no token in the URL — the token was consumed and never
 * echoed to the browser.
 */
import React, { useEffect, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { api } from "../lib/api";

const PAL = {
  bg:        "#0b0b0b",
  card:      "#141414",
  accent:    "#eab308",
  text:      "#e8e8e8",
  muted:     "#a0a0a0",
  errBg:     "rgba(239, 68, 68, 0.08)",
  errBorder: "rgba(239, 68, 68, 0.32)",
  okBg:      "rgba(234, 179, 8, 0.08)",
  okBorder:  "rgba(234, 179, 8, 0.28)",
};

const REASON_COPY = {
  invalid_token:     "That link is not recognised. It may already have been used.",
  expired_token:     "That verification link has expired. Please request a fresh one from your dashboard.",
  missing_token:     "The verification link is missing its token.",
  db_unavailable:    "Our database is temporarily unreachable. Please try again in a minute.",
  user_not_found:    "We could not find the account this link belongs to.",
  already_verified:  "You've already verified this email. Nothing more to do.",
  already_claimed:   "You've already claimed your founder spot.",
  promo_full:        "The First-50 promo is fully claimed — your email is verified but the promo spots are gone.",
};

export default function Verify() {
  const loc = useLocation();
  const navigate = useNavigate();
  const [promo, setPromo] = useState(null);

  const params  = new URLSearchParams(loc.search);
  const status  = params.get("status") || "ok";
  const claimed = params.get("claimed") === "1";
  const reason  = params.get("reason") || "";
  const isOk    = status === "ok";

  useEffect(() => {
    // Non-blocking counter fetch for the celebratory copy.
    api.get("/promo/first50/status")
      .then((r) => setPromo(r.data))
      .catch(() => {});
  }, []);

  const humanReason = REASON_COPY[reason] || "";

  return (
    <div
      data-testid="verify-page"
      style={{
        minHeight: "100vh",
        background: PAL.bg,
        color: PAL.text,
        display: "flex", alignItems: "center", justifyContent: "center",
        padding: "48px 20px",
        fontFamily: "'Helvetica Neue', Arial, sans-serif",
      }}
    >
      <div
        style={{
          maxWidth: 560, width: "100%",
          background: PAL.card,
          border: `1px solid ${isOk ? PAL.okBorder : PAL.errBorder}`,
          borderRadius: 14,
          padding: "36px 32px",
          boxShadow: "0 12px 48px rgba(0,0,0,0.4)",
        }}
      >
        <div
          data-testid="verify-status-badge"
          style={{
            display: "inline-block",
            padding: "4px 12px",
            borderRadius: 999,
            background: isOk ? PAL.okBg : PAL.errBg,
            color: isOk ? PAL.accent : "#f87171",
            fontSize: 12, fontWeight: 600,
            letterSpacing: 0.4,
            textTransform: "uppercase",
            marginBottom: 20,
          }}
        >
          {isOk ? "✓ verified" : "✗ verification failed"}
        </div>

        {isOk && claimed && (
          <>
            <h1
              data-testid="verify-claimed-title"
              style={{
                fontSize: 26, lineHeight: 1.25, margin: "0 0 12px",
                fontWeight: 700,
              }}
            >
              You&apos;re in. Founder spot secured.
            </h1>
            <p style={{ color: PAL.muted, lineHeight: 1.65, margin: "0 0 20px" }}>
              Your email is verified and one of the 50 First-50 promo
              spots is now yours. Pro tier is active for 30 days —
              flat pricing after that unless you cancel.
            </p>
          </>
        )}

        {isOk && !claimed && reason === "promo_full" && (
          <>
            <h1 style={{ fontSize: 26, margin: "0 0 12px", fontWeight: 700 }}>
              Email verified.
            </h1>
            <p style={{ color: PAL.muted, lineHeight: 1.65, margin: "0 0 20px" }}>
              {humanReason}
            </p>
          </>
        )}

        {isOk && !claimed && reason === "already_verified" && (
          <>
            <h1 style={{ fontSize: 26, margin: "0 0 12px", fontWeight: 700 }}>
              Already verified.
            </h1>
            <p style={{ color: PAL.muted, lineHeight: 1.65, margin: "0 0 20px" }}>
              {humanReason}
            </p>
          </>
        )}

        {isOk && !claimed && reason === "already_claimed" && (
          <>
            <h1 style={{ fontSize: 26, margin: "0 0 12px", fontWeight: 700 }}>
              Founder spot already yours.
            </h1>
            <p style={{ color: PAL.muted, lineHeight: 1.65, margin: "0 0 20px" }}>
              {humanReason}
            </p>
          </>
        )}

        {isOk && !claimed && !reason && (
          <>
            <h1 style={{ fontSize: 26, margin: "0 0 12px", fontWeight: 700 }}>
              Email verified.
            </h1>
            <p style={{ color: PAL.muted, lineHeight: 1.65, margin: "0 0 20px" }}>
              You&apos;re all set to sign in and start shipping.
            </p>
          </>
        )}

        {!isOk && (
          <>
            <h1 style={{ fontSize: 26, margin: "0 0 12px", fontWeight: 700 }}>
              We couldn&apos;t verify that link.
            </h1>
            <p
              data-testid="verify-error-reason"
              style={{ color: PAL.muted, lineHeight: 1.65, margin: "0 0 20px" }}
            >
              {humanReason || "The link may be invalid or expired. Sign in and request a fresh one."}
            </p>
          </>
        )}

        {isOk && promo && promo.is_active && (
          <div
            data-testid="verify-promo-counter"
            style={{
              margin: "0 0 24px",
              padding: "10px 14px",
              background: "rgba(255,255,255,0.02)",
              border: `1px solid ${PAL.okBorder}`,
              borderRadius: 8,
              color: PAL.muted, fontSize: 13,
            }}
          >
            <b style={{ color: PAL.accent }}>{promo.remaining}</b> of {promo.total} founder spots remaining.
          </div>
        )}

        <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
          <button
            data-testid="verify-goto-dashboard"
            onClick={() => navigate("/dashboard")}
            style={{
              background: PAL.accent, color: PAL.bg,
              border: "none", borderRadius: 8,
              padding: "10px 20px", fontWeight: 600, fontSize: 14,
              cursor: "pointer",
            }}
          >
            Go to dashboard →
          </button>
          <Link
            to="/"
            data-testid="verify-goto-home"
            style={{
              padding: "10px 20px",
              borderRadius: 8,
              border: `1px solid ${PAL.okBorder}`,
              color: PAL.text, textDecoration: "none",
              fontWeight: 500, fontSize: 14,
            }}
          >
            Back to home
          </Link>
        </div>
      </div>
    </div>
  );
}
