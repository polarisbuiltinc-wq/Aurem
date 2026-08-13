/**
 * CompareHub.jsx — /compare : index of every /vs/* comparison page.
 * Content driven by src/data/competitors.js (same source as the pages
 * themselves and the build-time SEO snapshots).
 */
import React, { useEffect } from "react";
import { Link } from "react-router-dom";
import { ArrowRight } from "lucide-react";
import { COMPETITORS, COMPARE_HUB, LAST_VERIFIED } from "../data/competitors";

const S = {
  page:  { minHeight: "100vh", background: "var(--bg)", color: "var(--text)" },
  shell: { maxWidth: 980, margin: "0 auto", padding: "0 20px 88px" },
  card:  { background: "var(--panel)", border: "1px solid var(--border)",
           borderRadius: 14, padding: "22px 24px", display: "block",
           textDecoration: "none", color: "var(--text)" },
  dim:   { color: "var(--text-dim)", lineHeight: 1.6 },
};

function MetaTags() {
  useEffect(() => {
    document.title = COMPARE_HUB.title;
    const desc = document.head.querySelector('meta[name="description"]');
    const prevDesc = desc?.getAttribute("content");
    desc?.setAttribute("content", COMPARE_HUB.description);

    const ld = document.createElement("script");
    ld.type = "application/ld+json";
    ld.id = "ld-compare-hub";
    ld.text = JSON.stringify({
      "@context": "https://schema.org",
      "@type": "ItemList",
      name: "ORA comparisons",
      itemListElement: Object.values(COMPETITORS).map((c, i) => ({
        "@type": "ListItem",
        position: i + 1,
        name: `ORA vs ${c.name}`,
        url: c.canonical,
      })),
    });
    document.head.appendChild(ld);
    return () => {
      if (prevDesc != null) desc?.setAttribute("content", prevDesc);
      document.getElementById("ld-compare-hub")?.remove();
    };
  }, []);
  return null;
}

export default function CompareHub() {
  return (
    <div data-testid="compare-hub-root" style={S.page}>
      <MetaTags />
      <div style={S.shell}>
        <header style={{ padding: "72px 0 32px", textAlign: "center" }}>
          <h1 style={{ fontSize: 38, fontWeight: 700, margin: "0 0 12px" }}>
            How ORA compares
          </h1>
          <p style={{ ...S.dim, maxWidth: 620, margin: "0 auto" }}>
            Honest, verifiable comparisons — pricing, delivery mode, security
            gates, and where each tool genuinely wins. Data verified{" "}
            {LAST_VERIFIED}, updated quarterly.
          </p>
        </header>

        <div style={{ display: "grid", gap: 14,
                      gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))" }}>
          {Object.values(COMPETITORS).map((c) => (
            <Link key={c.slug} to={`/vs/${c.slug}`} style={S.card}
                  data-testid={`compare-card-${c.slug}`}>
              <h2 style={{ fontSize: 17, fontWeight: 600, margin: "0 0 8px",
                           display: "flex", alignItems: "center", gap: 8 }}>
                ORA vs {c.name} <ArrowRight size={15} aria-hidden />
              </h2>
              <p style={{ ...S.dim, fontSize: 13.5, margin: 0 }}>
                {c.intro.slice(0, 150)}…
              </p>
            </Link>
          ))}
        </div>

        <section style={{ textAlign: "center", padding: "56px 0 0" }}>
          <Link to="/signup" data-testid="compare-hub-cta" style={{
            display: "inline-flex", alignItems: "center", gap: 8,
            background: "var(--accent)", color: "#1a0e00", fontWeight: 600,
            borderRadius: 10, padding: "12px 22px", textDecoration: "none",
          }}>
            Start free — 10 tasks/month <ArrowRight size={18} aria-hidden />
          </Link>
        </section>
      </div>
    </div>
  );
}
