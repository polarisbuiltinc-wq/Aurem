/**
 * VsPage.jsx — generic /vs/:slug comparison page.
 *
 * Iter 358 — the VsDevin.jsx shell, made data-driven. ALL content lives
 * in src/data/competitors.js (single source: this page, /compare hub,
 * AND the build-time SEO snapshots read the same objects, so the
 * on-page FAQ and the FAQPage JSON-LD can never drift apart).
 */
import React, { useEffect } from "react";
import { Link, Navigate, useParams } from "react-router-dom";
import {
  ArrowRight, Shield, Brain, Zap, GitCommitHorizontal, Smartphone,
  DollarSign,
} from "lucide-react";
import { COMPETITORS, LAST_VERIFIED } from "../data/competitors";

/* ── tiny presentational helpers (unchanged from VsDevin) ─────────── */
const S = {
  page:   { minHeight: "100vh", background: "var(--bg)", color: "var(--text)",
            fontFamily: "inherit" },
  shell:  { maxWidth: 1080, margin: "0 auto", padding: "0 20px" },
  h2:     { fontSize: 26, fontWeight: 600, margin: "64px 0 16px" },
  dim:    { color: "var(--text-dim)", lineHeight: 1.65 },
  card:   { background: "var(--panel)", border: "1px solid var(--border)",
            borderRadius: 14, padding: "22px 24px" },
  badge:  { display: "inline-flex", alignItems: "center", gap: 6,
            fontSize: 12, color: "var(--text-dim)",
            border: "1px solid var(--border)", borderRadius: 999,
            padding: "4px 12px" },
  cta:    { display: "inline-flex", alignItems: "center", gap: 8,
            background: "var(--accent)", color: "#1a0e00", fontWeight: 600,
            borderRadius: 10, padding: "12px 22px", textDecoration: "none" },
  ghost:  { display: "inline-flex", alignItems: "center", gap: 8,
            border: "1px solid var(--border-strong)", color: "var(--text)",
            borderRadius: 10, padding: "12px 22px", textDecoration: "none" },
};

const DIFFERENTIATORS = [
  [Shield, "Vanguard pre-commit scan",
   "25+ patterns checked on every change: AWS / GitHub / Stripe / " +
   "OpenAI keys, DB connection strings, private keys, plus eval, " +
   "shell=True, SQL string formatting and unsafe innerHTML. " +
   "Critical hits block the commit."],
  [Zap, "Maxx two-model review",
   "A second model (Claude Sonnet) reviews critical changes — wrong " +
   "imports, logic errors and security gaps get caught before commit."],
  [GitCommitHorizontal, "Two delivery modes",
   "Direct commit via the GitHub REST API when speed matters, " +
   "or a Pull Request when your team wants eyes on the diff."],
  [Brain, "Project Brain",
   "Per-repo permanent memory: your stack, your conventions, " +
   "your past decisions. Say \"we don't use Redux\" once."],
  [Smartphone, "Ships from anywhere",
   "Full web UI that works on mobile, a VS Code extension, and an " +
   "MCP server for Cursor / Claude Desktop. No desktop IDE required."],
];

function MetaTags({ c }) {
  useEffect(() => {
    document.title = c.title;

    const ensure = (selector, create) => {
      let el = document.head.querySelector(selector);
      if (!el) { el = create(); document.head.appendChild(el); }
      return el;
    };

    const desc = ensure('meta[name="description"]', () => {
      const m = document.createElement("meta");
      m.setAttribute("name", "description");
      return m;
    });
    const prevDesc = desc.getAttribute("content");
    desc.setAttribute("content", c.description);

    const canon = ensure('link[rel="canonical"]', () => {
      const l = document.createElement("link");
      l.setAttribute("rel", "canonical");
      return l;
    });
    const prevCanon = canon.getAttribute("href");
    canon.setAttribute("href", c.canonical);

    const ldId = `ld-vs-${c.slug}`;
    const ld = document.createElement("script");
    ld.type = "application/ld+json";
    ld.id = ldId;
    ld.text = JSON.stringify({
      "@context": "https://schema.org",
      "@graph": [
        {
          "@type": "FAQPage",
          mainEntity: c.faq.map(({ q, a }) => ({
            "@type": "Question",
            name: q,
            acceptedAnswer: { "@type": "Answer", text: a },
          })),
        },
        {
          "@type": "BreadcrumbList",
          itemListElement: [
            { "@type": "ListItem", position: 1, name: "AUREM CTO",
              item: "https://auremcto.com/" },
            { "@type": "ListItem", position: 2, name: "Compare",
              item: "https://auremcto.com/compare" },
            { "@type": "ListItem", position: 3,
              name: `AUREM CTO vs ${c.name}`, item: c.canonical },
          ],
        },
      ],
    });
    document.head.appendChild(ld);

    return () => {
      if (prevDesc != null) desc.setAttribute("content", prevDesc);
      if (prevCanon != null) canon.setAttribute("href", prevCanon);
      document.getElementById(ldId)?.remove();
    };
  }, [c]);
  return null;
}

export default function VsPage({ forcedSlug }) {
  const params = useParams();
  const slug = forcedSlug || params.slug;
  const c = COMPETITORS[slug];
  if (!c) return <Navigate to="/compare" replace />;

  return (
    <div data-testid={`vs-${c.slug}-root`} style={S.page}>
      <MetaTags c={c} />

      <div style={S.shell}>
        {/* 1 ── Hero */}
        <header style={{ padding: "72px 0 8px", textAlign: "center" }}>
          <span style={S.badge} data-testid={`vs-${c.slug}-verified`}>
            Honest comparison · data verified {LAST_VERIFIED} · no benchmarks we can&apos;t reproduce
          </span>
          <h1 style={{ fontSize: 40, fontWeight: 700, margin: "20px 0 14px",
                       lineHeight: 1.15 }}>
            AUREM CTO vs {c.name}
          </h1>
          <p style={{ ...S.dim, maxWidth: 640, margin: "0 auto", fontSize: 17 }}>
            {c.intro}
          </p>
        </header>

        {/* 2 ── Verdict cards */}
        <section style={{ display: "grid", gap: 16, marginTop: 40,
                          gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))" }}>
          <div style={{ ...S.card, borderColor: "var(--border-strong)" }}
               data-testid={`vs-${c.slug}-pick-aurem`}>
            <h2 style={{ fontSize: 18, fontWeight: 600, margin: "0 0 10px" }}>
              Choose AUREM CTO if you want…
            </h2>
            <ul style={{ ...S.dim, margin: 0, paddingLeft: 18, lineHeight: 1.9 }}>
              {c.pickAurem.map((li) => <li key={li}>{li}</li>)}
            </ul>
          </div>
          <div style={S.card} data-testid={`vs-${c.slug}-pick-them`}>
            <h2 style={{ fontSize: 18, fontWeight: 600, margin: "0 0 10px" }}>
              Choose {c.name} if you want…
            </h2>
            <ul style={{ ...S.dim, margin: 0, paddingLeft: 18, lineHeight: 1.9 }}>
              {c.pickThem.map((li) => <li key={li}>{li}</li>)}
            </ul>
          </div>
        </section>

        {/* 3 ── Pricing reality */}
        <section>
          <h2 style={S.h2}>
            <DollarSign size={22} style={{ verticalAlign: -3, marginRight: 8,
                                           color: "var(--accent)" }} aria-hidden />
            The pricing difference, in real numbers
          </h2>
          {c.pricingProse.map((p) => (
            <p key={p.slice(0, 40)} style={{ ...S.dim, maxWidth: 760 }}>{p}</p>
          ))}
          <p style={{ ...S.dim, fontSize: 13 }}>{c.pricingFootnote}</p>
        </section>

        {/* 4 ── Feature matrix */}
        <section>
          <h2 style={S.h2}>Feature by feature</h2>
          <div style={{ overflowX: "auto", border: "1px solid var(--border)",
                        borderRadius: 14 }}>
            <table data-testid={`vs-${c.slug}-table`}
                   style={{ width: "100%", borderCollapse: "collapse",
                            fontSize: 14, minWidth: 640 }}>
              <thead>
                <tr style={{ background: "var(--panel-2)", textAlign: "left" }}>
                  <th style={{ padding: "12px 16px", fontWeight: 600 }}> </th>
                  <th style={{ padding: "12px 16px", fontWeight: 600,
                               color: "var(--accent-2)" }}>AUREM CTO</th>
                  <th style={{ padding: "12px 16px", fontWeight: 600 }}>{c.name}</th>
                </tr>
              </thead>
              <tbody>
                {c.rows.map(([label, aurem, them]) => (
                  <tr key={label}
                      style={{ borderTop: "1px solid var(--border)" }}>
                    <td style={{ padding: "12px 16px", color: "var(--text-dim)",
                                 whiteSpace: "nowrap" }}>{label}</td>
                    <td style={{ padding: "12px 16px" }}>{aurem}</td>
                    <td style={{ padding: "12px 16px",
                                 color: "var(--text-dim)" }}>{them}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p style={{ ...S.dim, fontSize: 13, marginTop: 10 }}>
            Sources: AUREM CTO production codebase and docs; {c.name} public
            pricing and documentation, last verified {LAST_VERIFIED}. Spotted
            something stale? Email ora@auremcto.com and we&apos;ll fix it.
          </p>
        </section>

        {/* 5 ── What makes AUREM different, concretely */}
        <section>
          <h2 style={S.h2}>What &ldquo;safer by default&rdquo; means here</h2>
          <div style={{ display: "grid", gap: 16,
                        gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))" }}>
            {DIFFERENTIATORS.map(([Icon, title, body]) => (
              <div key={title} style={S.card}>
                <Icon size={20} style={{ color: "var(--accent)" }} aria-hidden />
                <h3 style={{ fontSize: 15, fontWeight: 600, margin: "10px 0 6px" }}>
                  {title}
                </h3>
                <p style={{ ...S.dim, fontSize: 13.5, margin: 0 }}>{body}</p>
              </div>
            ))}
          </div>
        </section>

        {/* 6 ── FAQ (mirrors JSON-LD 1:1 — same array) */}
        <section>
          <h2 style={S.h2}>Frequently asked</h2>
          <div style={{ display: "grid", gap: 12 }}>
            {c.faq.map(({ q, a }) => (
              <details key={q} style={{ ...S.card, padding: "16px 20px" }}>
                <summary style={{ cursor: "pointer", fontWeight: 600,
                                  fontSize: 15 }}>{q}</summary>
                <p style={{ ...S.dim, fontSize: 14, margin: "10px 0 0" }}>{a}</p>
              </details>
            ))}
          </div>
        </section>

        {/* 7 ── Other comparisons */}
        <section>
          <h2 style={S.h2}>More comparisons</h2>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            {Object.values(COMPETITORS)
              .filter((o) => o.slug !== c.slug)
              .map((o) => (
                <Link key={o.slug} to={`/vs/${o.slug}`} style={S.badge}
                      data-testid={`vs-link-${o.slug}`}>
                  AUREM CTO vs {o.name}
                </Link>
              ))}
            <Link to="/compare" style={S.badge} data-testid="vs-link-compare-hub">
              All comparisons
            </Link>
          </div>
        </section>

        {/* 8 ── CTA */}
        <section style={{ textAlign: "center", padding: "72px 0 88px" }}>
          <h2 style={{ fontSize: 28, fontWeight: 700, margin: "0 0 12px" }}>
            Try the comparison yourself
          </h2>
          <p style={{ ...S.dim, maxWidth: 520, margin: "0 auto 26px" }}>
            10 free tasks every month. No credit card. Connect a repo and ORA
            ships its first commit in about two minutes.
          </p>
          <div style={{ display: "flex", gap: 14, justifyContent: "center",
                        flexWrap: "wrap" }}>
            <Link to="/signup" style={S.cta} data-testid={`vs-${c.slug}-cta`}>
              Start free <ArrowRight size={18} aria-hidden />
            </Link>
            <Link to="/wall" style={S.ghost}>
              See real ships on the Wall
            </Link>
          </div>
        </section>
      </div>
    </div>
  );
}
