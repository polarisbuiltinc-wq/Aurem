/**
 * PreferredSourceButton.jsx — Visibility Kit Phase A (2026-08-28), §6.1.
 *
 * Google "Preferred Sources" (shipped 2026-05-27): a per-user "preferred"
 * badge in AI Mode / AI Overviews + Top Stories boost. NOT a ranking
 * signal — copy here never implies one.
 *
 * Idempotent widget script + always-visible deeplink fallback (survives
 * AdBlock/script failure, per spec). Shown once, at "moment of delight"
 * (right after a user's first completed scan — wired in LoopLiveFeed.jsx's
 * ShippedRow, since ship only happens after scan passes).
 */
import React, { useEffect } from "react";
import { ExternalLink } from "lucide-react";
import { trackPreferredSourceClicked, trackPreferredSourceDeeplinkFallback } from "../lib/analytics";

const SCRIPT_SRC = "https://news.google.com/swg/js/v1/publisher.js";
const SITE_NAME = "AUREM";
const SITE_DOMAIN = "auremcto.com";

function loadWidgetScriptOnce() {
  if (document.querySelector(`script[src="${SCRIPT_SRC}"]`)) return; // R6 — idempotent
  const s = document.createElement("script");
  s.src = SCRIPT_SRC;
  s.async = true;
  document.head.appendChild(s);
}

export function PreferredSourceButton({ dark = true }) {
  useEffect(() => {
    loadWidgetScriptOnce();
  }, []);

  return (
    <div
      data-testid="preferred-source-button"
      style={{
        display: "flex", flexDirection: "column", gap: 6,
        padding: "10px 14px", borderRadius: 10,
        background: dark ? "rgba(255,255,255,0.04)" : "rgba(0,0,0,0.03)",
        border: `1px solid ${dark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.08)"}`,
      }}
    >
      <span style={{ fontSize: 11, color: dark ? "#9aa0a8" : "#5b6470" }}>
        Get a "preferred" badge for {SITE_NAME} links in AI Mode &amp; AI Overviews
      </span>
      {/* Google's widget — renders nothing visible until the script loads;
          the deeplink below is the ALWAYS-visible fallback (R2/§6.1). */}
      <div
        data-testid="preferred-source-widget-slot"
        google-add-preferred-source-btn=""
        data-theme={dark ? "dark" : "light"}
        data-lang="en"
      />
      <a
        data-testid="preferred-source-deeplink"
        href={`https://www.google.com/preferences/source?q=${SITE_DOMAIN}`}
        target="_blank"
        rel="noopener noreferrer"
        onClick={() => {
          trackPreferredSourceClicked();
          trackPreferredSourceDeeplinkFallback();
        }}
        style={{
          color: dark ? "#e6ebf3" : "#111318",
          textDecoration: "none", fontSize: 12, fontWeight: 600,
          display: "inline-flex", alignItems: "center", gap: 4,
        }}
      >
        Prefer {SITE_NAME} in AI answers <ExternalLink size={11} strokeWidth={2.5} />
      </a>
    </div>
  );
}

export default PreferredSourceButton;
