/**
 * pages/ContactPage.jsx — real, static contact info (P1-#11 trust page).
 * Content lives in src/data/companyInfo.mjs so this page and the
 * build-time SEO snapshot (scripts/seo-prerender.mjs) can never drift.
 */
import React, { useEffect } from "react";
import { Link } from "react-router-dom";
import { CONTACT } from "../data/companyInfo";

export default function ContactPage() {
  useEffect(() => {
    const prevTitle = document.title;
    document.title = CONTACT.title;
    return () => { document.title = prevTitle; };
  }, []);

  return (
    <div data-testid="contact-page" style={{
      maxWidth: 820, margin: "0 auto", padding: "40px 24px 80px",
      color: "var(--text, #e8e3d3)",
      fontFamily: "Inter, system-ui, -apple-system, sans-serif",
      lineHeight: 1.65,
    }}>
      <Link to="/" data-testid="contact-back-home" style={{
        fontSize: 12, color: "var(--text-dim)", textDecoration: "none",
      }}>← Back to AUREM</Link>

      <h1 data-testid="contact-title" style={{
        fontSize: 32, fontWeight: 500, letterSpacing: "-0.02em",
        margin: "16px 0 24px",
      }}>{CONTACT.heading}</h1>

      {CONTACT.paragraphs.map((p, i) => (
        <p key={i} data-testid={`contact-paragraph-${i}`} style={{
          fontSize: 14, color: "var(--text-dim, #a39d8a)", margin: "0 0 16px",
        }}>{p}</p>
      ))}

      <div style={{ display: "flex", gap: 16, marginTop: 8 }}>
        <Link to="/support" data-testid="contact-support-link" style={{
          color: "var(--accent, #ff8a2a)", fontSize: 13,
        }}>Open a support ticket →</Link>
      </div>

      <footer style={{
        marginTop: 60, paddingTop: 20,
        borderTop: "1px solid var(--border, rgba(255,200,120,0.16))",
        fontSize: 11, color: "var(--text-faint)",
      }}>
        <Link to="/about" style={{ color: "inherit", marginRight: 16 }}>About</Link>
        <Link to="/privacy" style={{ color: "inherit", marginRight: 16 }}>Privacy</Link>
        <Link to="/terms" style={{ color: "inherit" }}>Terms</Link>
      </footer>
    </div>
  );
}
