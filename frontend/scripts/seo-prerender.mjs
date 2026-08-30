/**
 * seo-prerender.mjs — build-time SEO/GEO/AEO snapshots (Iter 358).
 *
 * WHY: the app is a client-rendered SPA — non-JS crawlers (and several
 * AEO/answer-engine bots) fetching /vs/* or /compare see the generic
 * landing <head> and an empty #root. This script runs AFTER `vite
 * build` and writes real static HTML for those routes into dist/:
 *
 *   dist/vs/<slug>/index.html   (5 competitor pages)
 *   dist/compare/index.html
 *
 * Each snapshot = that build's index.html with:
 *   - per-page <title>, meta description, canonical, og:* swapped
 *   - FAQPage/BreadcrumbList (or ItemList) JSON-LD injected
 *   - full semantic content rendered INSIDE <div id="root"> — React
 *     replaces it on mount, so browsers still get the live SPA while
 *     crawlers get real content (classic react-snap approach).
 *
 * Content comes from src/data/competitors.js — the SAME objects the
 * React pages render, so snapshots can never drift from the live UI.
 *
 * 2026-08-30 · AUREM public-site "agentic readiness" fixes (P0 #1):
 * the is-agentic.com scan found the homepage itself (and /about,
 * /contact, /privacy) serve ~60 chars of raw HTML to any crawler that
 * doesn't execute JS (AUREM's own research: 69% of AI crawlers can't
 * run JS). Same react-snap pattern extended to those four routes —
 * dist/index.html (the literal root file AND the SPA fallback shell
 * for every unmatched route) plus dist/about/, dist/contact/,
 * dist/privacy/. Content for about/contact comes from
 * src/data/companyInfo.mjs (same drift-proof pattern); privacy is the
 * real public/policies/privacy-policy.md, rendered through the same
 * `marked` parser PolicyPage.jsx uses client-side, so the static
 * snapshot and the live page always show identical text.
 */
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { marked } from "marked";
import { COMPETITORS, COMPARE_HUB, LAST_VERIFIED } from "../src/data/competitors.mjs";
import { ABOUT, CONTACT } from "../src/data/companyInfo.mjs";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const DIST = join(ROOT, "dist");

const esc = (s) =>
  String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");

function swapHead(html, { title, description, canonical }) {
  let out = html
    .replace(/<title>[\s\S]*?<\/title>/, `<title>${esc(title)}</title>`)
    .replace(/(<meta\s+name="description"\s+content=")[^"]*(")/,
      `$1${esc(description)}$2`)
    .replace(/(<meta\s+property="og:title"\s+content=")[^"]*(")/,
      `$1${esc(title)}$2`)
    .replace(/(<meta\s+property="og:description"\s+content=")[^"]*(")/,
      `$1${esc(description)}$2`)
    .replace(/(<meta\s+property="og:url"\s+content=")[^"]*(")/,
      `$1${esc(canonical)}$2`)
    .replace(/(<meta\s+name="twitter:title"\s+content=")[^"]*(")/,
      `$1${esc(title)}$2`)
    .replace(/(<meta\s+name="twitter:description"\s+content=")[^"]*(")/,
      `$1${esc(description)}$2`);
  if (/<link\s+rel="canonical"/.test(out)) {
    out = out.replace(/(<link\s+rel="canonical"\s+href=")[^"]*(")/,
      `$1${esc(canonical)}$2`);
  } else {
    out = out.replace("</head>",
      `  <link rel="canonical" href="${esc(canonical)}" />\n</head>`);
  }
  return out;
}

function injectLd(html, ld) {
  return html.replace("</head>",
    `  <script type="application/ld+json">${JSON.stringify(ld)}</script>\n</head>`);
}

function injectRoot(html, inner) {
  const re = /(<div id="root">)([\s\S]*?)(<\/div>)/;
  if (re.test(html)) return html.replace(re, `$1${inner}$3`);
  return html.replace('<div id="root"></div>', `<div id="root">${inner}</div>`);
}

function vsBody(c) {
  const rows = c.rows.map(([l, a, t]) =>
    `<tr><td>${esc(l)}</td><td>${esc(a)}</td><td>${esc(t)}</td></tr>`).join("");
  const faq = c.faq.map(({ q, a }) =>
    `<details><summary>${esc(q)}</summary><p>${esc(a)}</p></details>`).join("");
  const pick = (h, items) =>
    `<h2>${esc(h)}</h2><ul>${items.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>`;
  const others = Object.values(COMPETITORS)
    .filter((o) => o.slug !== c.slug)
    .map((o) => `<a href="/vs/${o.slug}">ORA vs ${esc(o.name)}</a>`)
    .join(" · ");
  return `<main>
<h1>ORA vs ${esc(c.name)}</h1>
<p>Honest comparison · data verified ${esc(LAST_VERIFIED)}.</p>
<p>${esc(c.intro)}</p>
${pick("Choose ORA if you want…", c.pickAurem)}
${pick(`Choose ${c.name} if you want…`, c.pickThem)}
<h2>The pricing difference, in real numbers</h2>
${c.pricingProse.map((p) => `<p>${esc(p)}</p>`).join("")}
<p><small>${esc(c.pricingFootnote)}</small></p>
<h2>Feature by feature</h2>
<table><thead><tr><th></th><th>ORA</th><th>${esc(c.name)}</th></tr></thead>
<tbody>${rows}</tbody></table>
<h2>Frequently asked</h2>
${faq}
<h2>More comparisons</h2>
<p>${others} · <a href="/compare">All comparisons</a></p>
<p><a href="/signup">Start free — 10 tasks/month, no credit card</a></p>
</main>`;
}

function compareBody() {
  const cards = Object.values(COMPETITORS).map((c) =>
    `<li><a href="/vs/${c.slug}">ORA vs ${esc(c.name)}</a> — ${esc(c.intro.slice(0, 150))}…</li>`)
    .join("");
  return `<main>
<h1>How ORA compares</h1>
<p>${esc(COMPARE_HUB.description)}</p>
<ul>${cards}</ul>
<p><a href="/signup">Start free — 10 tasks/month, no credit card</a></p>
</main>`;
}

function ldForVs(c) {
  return {
    "@context": "https://schema.org",
    "@graph": [
      { "@type": "FAQPage",
        mainEntity: c.faq.map(({ q, a }) => ({
          "@type": "Question", name: q,
          acceptedAnswer: { "@type": "Answer", text: a } })) },
      { "@type": "BreadcrumbList",
        itemListElement: [
          { "@type": "ListItem", position: 1, name: "AUREM",
            item: "https://auremcto.com/" },
          { "@type": "ListItem", position: 2, name: "Compare",
            item: "https://auremcto.com/compare" },
          { "@type": "ListItem", position: 3,
            name: `ORA vs ${c.name}`, item: c.canonical } ] },
    ],
  };
}

const base = readFileSync(join(DIST, "index.html"), "utf8");
let written = 0;

for (const c of Object.values(COMPETITORS)) {
  let html = swapHead(base, c);
  html = injectLd(html, ldForVs(c));
  html = injectRoot(html, vsBody(c));
  const dir = join(DIST, "vs", c.slug);
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "index.html"), html);
  written += 1;
  console.log(`[seo-prerender] wrote /vs/${c.slug}/index.html`);
}

{
  let html = swapHead(base, COMPARE_HUB);
  html = injectLd(html, {
    "@context": "https://schema.org",
    "@type": "ItemList",
    name: "ORA comparisons",
    itemListElement: Object.values(COMPETITORS).map((c, i) => ({
      "@type": "ListItem", position: i + 1,
      name: `ORA vs ${c.name}`, url: c.canonical })),
  });
  html = injectRoot(html, compareBody());
  mkdirSync(join(DIST, "compare"), { recursive: true });
  writeFileSync(join(DIST, "compare", "index.html"), html);
  written += 1;
  console.log("[seo-prerender] wrote /compare/index.html");
}

// ── 2026-08-30 · P0 #1 — homepage / about / contact / privacy ───────
function homeBody() {
  return `<main>
<h1>ORA by AUREM \u2014 the AI engineer that actually commits</h1>
<p>ORA is an autonomous AI software engineer. It connects to your GitHub repository, plans a change, writes the code, runs a pre-commit security scan (Vanguard), and commits the result directly to your branch \u2014 only after your manual approval.</p>
<h2>How it works</h2>
<ul>
<li><strong>Plan</strong> \u2014 ORA shows what it will do; you approve first.</li>
<li><strong>Execute</strong> \u2014 files are written one at a time.</li>
<li><strong>Verify</strong> \u2014 lint/type checks run after every file, with a hard self-heal cap of 2 retries.</li>
<li><strong>Scan</strong> \u2014 Vanguard's pre-commit security patterns run automatically.</li>
<li><strong>Ship</strong> \u2014 nothing commits until you click Ship.</li>
</ul>
<h2>Pricing</h2>
<p>Free: 10 tasks/month, no credit card. Starter: $9/month flat \u2014 no per-token billing.</p>
<p><a href="/about">About AUREM</a> \u00b7 <a href="/compare">How ORA compares</a> \u00b7 <a href="/pricing">Pricing</a> \u00b7 <a href="/signup">Start free</a></p>
</main>`;
}

function aboutBody() {
  const paras = ABOUT.paragraphs.map((p) => `<p>${esc(p)}</p>`).join("");
  const links = ABOUT.links
    .map((l) => `<a href="${esc(l.href)}">${esc(l.label)}</a>`)
    .join(" \u00b7 ");
  return `<main>
<h1>${esc(ABOUT.heading)}</h1>
${paras}
<h2>Links</h2>
<p>${links}</p>
<p><a href="/contact">Contact</a> \u00b7 <a href="/privacy">Privacy</a> \u00b7 <a href="/terms">Terms</a></p>
</main>`;
}

function contactBody() {
  const paras = CONTACT.paragraphs.map((p) => `<p>${esc(p)}</p>`).join("");
  return `<main>
<h1>${esc(CONTACT.heading)}</h1>
${paras}
<p><a href="/support">Open a support ticket</a></p>
<p><a href="/about">About</a> \u00b7 <a href="/privacy">Privacy</a> \u00b7 <a href="/terms">Terms</a></p>
</main>`;
}

function privacyBody() {
  const md = readFileSync(
    join(ROOT, "public", "policies", "privacy-policy.md"), "utf8",
  );
  return `<main>${marked.parse(md)}</main>`;
}

{
  let html = swapHead(base, {
    title: "ORA by AUREM \u2014 the AI engineer that actually commits",
    description:
      "ORA connects to your GitHub repo, writes code, runs a security " +
      "scan, and commits on your approval. Free tier: 10 tasks/month, " +
      "no card. Starter: $9/month flat.",
    canonical: "https://auremcto.com/",
  });
  html = injectRoot(html, homeBody());
  writeFileSync(join(DIST, "index.html"), html);
  written += 1;
  console.log("[seo-prerender] wrote / (root index.html, homepage content)");
}

{
  let html = swapHead(base, ABOUT);
  html = injectRoot(html, aboutBody());
  mkdirSync(join(DIST, "about"), { recursive: true });
  writeFileSync(join(DIST, "about", "index.html"), html);
  written += 1;
  console.log("[seo-prerender] wrote /about/index.html");
}

{
  let html = swapHead(base, CONTACT);
  html = injectRoot(html, contactBody());
  mkdirSync(join(DIST, "contact"), { recursive: true });
  writeFileSync(join(DIST, "contact", "index.html"), html);
  written += 1;
  console.log("[seo-prerender] wrote /contact/index.html");
}

{
  let html = swapHead(base, {
    title: "Privacy Policy — AUREM",
    description: "AUREM's privacy policy: GDPR/PIPEDA/DPDP disclosures, cookie policy, data-retention windows, subprocessor list.",
    canonical: "https://auremcto.com/privacy",
  });
  html = injectRoot(html, privacyBody());
  mkdirSync(join(DIST, "privacy"), { recursive: true });
  writeFileSync(join(DIST, "privacy", "index.html"), html);
  written += 1;
  console.log("[seo-prerender] wrote /privacy/index.html");
}

console.log(`[seo-prerender] done — ${written} snapshots.`);
