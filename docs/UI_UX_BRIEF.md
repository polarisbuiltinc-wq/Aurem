# UI_UX_BRIEF — actual design tokens & patterns (living doc)

Last updated: 2026-06-30 (Iter 358). Source: frontend/src/index.css +
shipped surfaces. Document what IS, not what we wish.

## Themes & tokens (index.css)
Dark (default):
- `--bg: #0A0A0A` · panels darker slate · `--border: #222222`
- `--accent / --accent-2: #FF6608` · `--accent-soft: rgba(255,102,8,.15)`
Light: `--bg: #F7F7F5`, same orange accent.
Admin/ops surfaces additionally use inline `rgb(7,8,13)` backgrounds,
`#ff8a2a` accent and `rgba(255,200,120,.1)` borders (TopBar, admin
pages, RouteErrorBoundary) — treat these as the "ops palette".

## Typography
- **Jost** — primary UI/body font (global).
- **Cinzel** — serif display accents (brand moments).
- **JetBrains Mono** — code, SHAs, terminal tapes, admin metrics.
(Google Fonts import at top of index.css.)

## Logo assets
- `public/og-image.png` — real ORA circuit-O logo, 1200×630 (used by
  og:image + sitemap image entries).
- `ds2-sidebar-logo` asset used in RailShell header.

## Canonical patterns
- **Navigation**: RailShell (56px icon rail + flyout panels) is THE nav
  pattern for any future authenticated page. Render pages inside
  `<RailShell railOnly>`; do not invent new sidebars.
- **Errors**: never blank-screen — RouteErrorBoundary wraps the route
  tree (retry card). API errors go through `lib/cleanErr.js` before
  display.
- **Testids**: every interactive/critical element gets kebab-case
  `data-testid` (e.g. `rail-icon-chat`, `github-sync-badge`).
- **Toasts**: `components/Toast` (admin) / sonner elsewhere.
- **Comparison/marketing pages**: content lives in
  `src/data/competitors.js`; pages render from data; JSON-LD built from
  the same objects (never hand-write duplicated copy).
