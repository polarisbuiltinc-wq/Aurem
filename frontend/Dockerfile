FROM node:20-alpine AS build

WORKDIR /app

COPY package.json yarn.lock ./
RUN yarn install --frozen-lockfile

COPY . .
RUN yarn build

FROM nginx:alpine

RUN printf 'server {\n\
  listen 3001;\n\
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
  location / {\n\
    try_files $uri $uri/ /index.html;\n\
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