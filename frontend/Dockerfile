FROM node:20-alpine AS build

WORKDIR /app

COPY package.json yarn.lock ./
RUN yarn install
COPY . .
RUN yarn build

FROM nginx:alpine

RUN printf 'server {\n\
  listen 3001;\n\
    location /api/ {\n\
        proxy_pass https://aurem.onrender.com;\n\
            proxy_set_header Host aurem.onrender.com;\n\
                proxy_set_header X-Real-IP $remote_addr;\n\
                    proxy_ssl_server_name on;\n\
                      }\n\
  root /usr/share/nginx/html;\n\
  index index.html;\n\
  # Iter 386 · Session 2 · Layer 7 — Deploy-lag mitigation.\n\
  # Hashed asset files (main.abc123.js, styles.def456.css, etc.) can\n\
  # be cached aggressively — their filename embeds the content hash,\n\
  # so a new build creates new filenames. index.html has NO hash in\n\
  # its own name — it is the SPA entry point that references the\n\
  # hashed bundles. If a CDN/browser caches index.html across a\n\
  # deploy, the client is stuck loading OLD bundle refs against a\n\
  # NEW backend contract — the exact FE-BE lag pattern we hit twice\n\
  # (Phase 2 + Phase 4). Force revalidation on index.html only.\n\
  location = /index.html {\n\
    add_header Cache-Control "no-cache, no-store, must-revalidate" always;\n\
    add_header Pragma "no-cache" always;\n\
    add_header Expires "0" always;\n\
    try_files $uri =404;\n\
  }\n\
  # 2026-08-30 · public-site fixes (P0 #2 real-404). is-agentic.com\n\
  # scan flagged: unknown paths returned 200 + the app shell (a soft\n\
  # 404 — crawlers/agents cannot tell a broken link from a real page).\n\
  # React Router already renders a NotFound component client-side for\n\
  # ANY unmatched path (see src/pages/NotFound.jsx), but the raw HTTP\n\
  # status was always 200 before React ever ran. Fix: only the KNOWN\n\
  # top-level route prefixes (mirrors every <Route path="..."> in\n\
  # src/App.jsx — keep this list in sync when routes are added/removed)\n\
  # fall through to the SPA shell; a genuinely unknown top-level path\n\
  # gets a real 404. Prerendered static dirs (about/, contact/,\n\
  # privacy/, vs/*, compare/) are matched by try_files BEFORE this\n\
  # regex even runs, so they keep serving their own real files.\n\
  location ~ ^/(both|why-ora|demo|login|signup|reset-password|verify|support|dashboard|ora|build|integrations|deploy|domain|settings|profile|tokens|analytics|projects|admin|tools|feature-window|codebase-health|health|bug-hunt|sidebar-preview|dashboard-preview-v2|privacy|terms|acceptable-use|cookie-policy|cookie-preferences|refund-policy|ai-code-processing|subprocessors|dpa|security|status|policies|wall|wrapped|automations|oauth-finish|magic-login|vs|pricing|compare|dev|about|contact)(/|$) {\n\
    try_files $uri $uri/ /index.html;\n\
  }\n\
  location / {\n\
    try_files $uri $uri/ =404;\n\
  }\n\
  error_page 404 /404.html;\n\
  location = /404.html {\n\
    internal;\n\
  }\n\
}\n' > /etc/nginx/conf.d/default.conf

COPY --from=build /app/dist /usr/share/nginx/html

# Iter 212m-227 — CIS Docker Benchmark 4.1 + 4.6.
# nginx:alpine's official image already ships an `nginx` user and
# a default HEALTHCHECK-free config. We add both: switch to the
# unprivileged nginx user (drops root) and add a lightweight
# healthcheck so orchestrators can detect a hung server.
RUN chown -R nginx:nginx /usr/share/nginx/html /var/cache/nginx /var/log/nginx \
    && touch /var/run/nginx.pid \
    && chown nginx:nginx /var/run/nginx.pid \
    && sed -i 's/listen 3001;/listen 3001;/' /etc/nginx/conf.d/default.conf

USER nginx

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q -O /dev/null http://localhost:3001/ || exit 1

EXPOSE 3001

CMD ["nginx", "-g", "daemon off;"]
