/**
 * frontend/src/pages/NotFound.jsx
 *
 * Iter 388o — Bug 10 fix.  Previously `<Route path="*">` redirected
 * silently to `/` — SEO-hostile (Google indexed the homepage under
 * every broken URL) and UX-hostile (a founder clicking a stale link
 * couldn't tell the link was broken).  This page renders a proper
 * 404 experience in the SPA and injects `noindex` for crawlers so
 * search engines drop the URL from their index instead of caching
 * whatever homepage content loaded.
 *
 * SPA caveat: React cannot set a real HTTP 404 status from client
 * code — that would need SSR or edge middleware.  What we CAN do:
 * emit `noindex,follow`, keep the URL untouched (no auto-redirect),
 * and give the user a clear way back.  If prod SEO ever demands a
 * true 404 status, the fix belongs in the Vercel/CDN layer, not
 * here.
 */
import React, { useEffect } from "react";
import { Link, useLocation } from "react-router-dom";

export default function NotFound() {
  const location = useLocation();

  useEffect(() => {
    const prevTitle = document.title;
    document.title = "404 · Page not found — AUREM";

    // The site's index.html ships a permissive `<meta name="robots"
    // content="index, follow, …">` tag.  If we only APPEND a new
    // `noindex` meta, most crawlers respect the most-restrictive of
    // the two — but a couple (older Bing, sitemap tools) read only
    // the FIRST match.  Safest: mutate the existing tag in place,
    // then restore it on unmount so navigating away from the 404
    // doesn't leave the rest of the SPA marked `noindex`.
    const meta = document.querySelector('meta[name="robots"]');
    const prevContent = meta ? meta.getAttribute("content") : null;
    let injected = null;
    if (meta) {
      meta.setAttribute("content", "noindex, follow");
    } else {
      injected = document.createElement("meta");
      injected.name = "robots";
      injected.content = "noindex, follow";
      document.head.appendChild(injected);
    }

    return () => {
      document.title = prevTitle;
      if (meta && prevContent !== null) {
        meta.setAttribute("content", prevContent);
      } else if (meta && prevContent === null) {
        meta.removeAttribute("content");
      }
      if (injected && injected.parentNode) {
        injected.parentNode.removeChild(injected);
      }
    };
  }, []);

  return (
    <main
      data-testid="not-found-page"
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "var(--bg, #0b0b12)",
        color: "var(--text, #eaeaea)",
        padding: "48px 24px",
        fontFamily: "'JetBrains Mono', monospace",
      }}
    >
      <div style={{ maxWidth: 520, width: "100%" }}>
        <div
          style={{
            fontSize: 72,
            fontWeight: 900,
            letterSpacing: "-0.03em",
            lineHeight: 1,
            marginBottom: 12,
            color: "var(--warning, #f5a524)",
          }}
        >
          404
        </div>
        <h1
          style={{
            fontSize: 24,
            fontWeight: 700,
            margin: "0 0 12px",
            letterSpacing: "-0.01em",
          }}
        >
          Page not found
        </h1>
        <p
          style={{
            fontSize: 14,
            opacity: 0.72,
            margin: "0 0 24px",
            lineHeight: 1.55,
          }}
        >
          The URL{" "}
          <code
            data-testid="not-found-path"
            style={{
              background: "rgba(255,255,255,0.06)",
              padding: "2px 6px",
              borderRadius: 4,
              fontSize: 13,
            }}
          >
            {location.pathname}
          </code>{" "}
          doesn&apos;t match any page on AUREM. Either the link is stale or
          the page moved. Nothing broken on your end.
        </p>
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
          <Link
            to="/"
            data-testid="not-found-home-link"
            style={{
              display: "inline-block",
              padding: "10px 18px",
              background: "var(--accent, #ff6b35)",
              color: "#fff",
              borderRadius: 6,
              textDecoration: "none",
              fontSize: 13,
              fontWeight: 700,
              letterSpacing: "0.02em",
            }}
          >
            Back to home
          </Link>
          <Link
            to="/dashboard"
            data-testid="not-found-dashboard-link"
            style={{
              display: "inline-block",
              padding: "10px 18px",
              background: "transparent",
              color: "var(--text, #eaeaea)",
              border: "1px solid rgba(255,255,255,0.15)",
              borderRadius: 6,
              textDecoration: "none",
              fontSize: 13,
              fontWeight: 700,
              letterSpacing: "0.02em",
            }}
          >
            Open dashboard
          </Link>
        </div>
      </div>
    </main>
  );
}
