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
 */
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { COMPETITORS, COMPARE_HUB, LAST_VERIFIED } from "../src/data/competitors.js";

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
    .map((o) => `<a href="/vs/${o.slug}">AUREM CTO vs ${esc(o.name)}</a>`)
    .join(" · ");
  return `<main>
<h1>AUREM CTO vs ${esc(c.name)}</h1>
<p>Honest comparison · data verified ${esc(LAST_VERIFIED)}.</p>
<p>${esc(c.intro)}</p>
${pick("Choose AUREM CTO if you want…", c.pickAurem)}
${pick(`Choose ${c.name} if you want…`, c.pickThem)}
<h2>The pricing difference, in real numbers</h2>
${c.pricingProse.map((p) => `<p>${esc(p)}</p>`).join("")}
<p><small>${esc(c.pricingFootnote)}</small></p>
<h2>Feature by feature</h2>
<table><thead><tr><th></th><th>AUREM CTO</th><th>${esc(c.name)}</th></tr></thead>
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
    `<li><a href="/vs/${c.slug}">AUREM CTO vs ${esc(c.name)}</a> — ${esc(c.intro.slice(0, 150))}…</li>`)
    .join("");
  return `<main>
<h1>How AUREM CTO compares</h1>
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
          { "@type": "ListItem", position: 1, name: "AUREM CTO",
            item: "https://auremcto.com/" },
          { "@type": "ListItem", position: 2, name: "Compare",
            item: "https://auremcto.com/compare" },
          { "@type": "ListItem", position: 3,
            name: `AUREM CTO vs ${c.name}`, item: c.canonical } ] },
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
    name: "AUREM CTO comparisons",
    itemListElement: Object.values(COMPETITORS).map((c, i) => ({
      "@type": "ListItem", position: i + 1,
      name: `AUREM CTO vs ${c.name}`, url: c.canonical })),
  });
  html = injectRoot(html, compareBody());
  mkdirSync(join(DIST, "compare"), { recursive: true });
  writeFileSync(join(DIST, "compare", "index.html"), html);
  written += 1;
  console.log("[seo-prerender] wrote /compare/index.html");
}

console.log(`[seo-prerender] done — ${written} snapshots.`);
