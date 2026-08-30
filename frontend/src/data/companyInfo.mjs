/**
 * companyInfo.mjs — single source of truth for /about and /contact.
 * Imported by BOTH the React pages (AboutPage.jsx, ContactPage.jsx)
 * AND scripts/seo-prerender.mjs, so the crawler-facing static HTML
 * can never drift from what a real visitor sees (same pattern as
 * competitors.mjs). Pure data — no JSX, no React imports.
 *
 * Honesty policy: every fact here traces to llms-full.txt / the
 * privacy policy's own Contact section. No invented phone number or
 * street address — AUREM doesn't publish one, so we say that plainly
 * instead of fabricating one.
 */

export const ABOUT = {
  title: "About AUREM — the company behind ORA",
  description:
    "AUREM (Polaris Built Inc.) builds ORA, an autonomous AI software " +
    "engineer that reads your GitHub repo, writes code, and commits " +
    "directly on your approval. Founded 2024 by Tejinder Sandhu.",
  canonical: "https://auremcto.com/about",
  heading: "About AUREM",
  paragraphs: [
    "AUREM is the company behind ORA — an autonomous AI software " +
      "engineer. ORA connects to a developer's GitHub repository, " +
      "reads the codebase, plans a change, writes the code, runs a " +
      "pre-commit security scan (Vanguard), and commits the result " +
      "directly to a branch, only after the developer's manual " +
      "approval click.",
    "AUREM is the trading name of Polaris Built Inc., a company " +
      "incorporated in Canada. AUREM\u2122 is a trademark of Polaris " +
      "Built Inc. \u2014 Canadian Intellectual Property Office " +
      "Application No. 2492318, filed August 5, 2026. The application " +
      "is currently pending examination and is not yet registered.",
    "AUREM was founded in 2024 by Tejinder Sandhu, who remains the " +
      "company's sole founder and CTO.",
    "ORA runs a verified five-phase Loop on every task \u2014 Plan, " +
      "Execute, Verify, Scan, Ship \u2014 with a hard self-heal cap so " +
      "no task can retry forever. Pricing is flat, not per-token: a " +
      "free tier (10 tasks/month, no card) and a $9/month Starter " +
      "tier for developers who want more.",
  ],
  links: [
    { label: "GitHub", href: "https://github.com/TJSNDHU/Aurem" },
    { label: "X", href: "https://x.com/aurem_live" },
    { label: "LinkedIn", href: "https://www.linkedin.com/in/tejinder-sandhu" },
  ],
};

export const CONTACT = {
  title: "Contact AUREM",
  description:
    "How to reach AUREM (Polaris Built Inc.): support tickets, " +
    "security disclosures, and privacy/legal requests, each routed " +
    "to the right place.",
  canonical: "https://auremcto.com/contact",
  heading: "Contact",
  paragraphs: [
    "AUREM doesn't currently list a public phone number or mailing " +
      "address. The fastest way to reach us is through one of the " +
      "channels below, matched to what you need.",
    "General support: open a ticket at auremcto.com/support from " +
      "your dashboard. Most replies land within one business day.",
    "Security disclosures: email security@auremcto.com directly. We " +
      "honour responsible disclosure and will credit researchers on " +
      "our Hall of Fame with consent.",
    "Privacy and legal requests (GDPR / PIPEDA / DPDP, data deletion, " +
      "Data Processing Agreement requests): contact our Privacy " +
      "Officer through auremcto.com/support, and put \"Privacy " +
      "Officer\" in the subject line. GDPR requests get a response " +
      "within 30 days; CCPA requests within 45 days.",
    "AUREM is the trading name of Polaris Built Inc., a company " +
      "incorporated in Canada.",
  ],
};
