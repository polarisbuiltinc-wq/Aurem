FROM python:3.11-slim

WORKDIR /app

# Iter 212m-166 — add nodejs + eslint alongside ruff so the LoopEngine's
# Verify phase (`services/loop_verify.py`) can actually lint the files
# it generates before Ship.  When either binary is missing the loop
# gracefully soft-skips (rc=127) but the design intent is to LINT — a
# missing linter silently masks syntax errors on generated code.
#
# 2026-02-09 — added wget + gnupg (needed for the MongoDB apt-repo GPG
# key below) and mongodb-database-tools (provides `mongodump` +
# `mongorestore`, required by services/db_backup.py + db_restore.py).
# Preview pods have these bundled in Emergent's agent-env base image
# but the prod Dockerfile starts from python:3.11-slim which does NOT
# — omitting them was the root cause of the 2026-02-09 prod backup
# FileNotFoundError incident.
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl nodejs npm wget gnupg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# MongoDB Database Tools (mongodump/mongorestore). Installed from the
# official MongoDB apt repo so we get signed, versioned binaries
# instead of shipping a raw .deb. python:3.11-slim is Debian Bookworm.
RUN wget -qO /usr/share/keyrings/mongodb-server-7.0.gpg \
      https://pgp.mongodb.com/server-7.0.pub \
    && echo "deb [ signed-by=/usr/share/keyrings/mongodb-server-7.0.gpg ] http://repo.mongodb.org/apt/debian bookworm/mongodb-org/7.0 main" \
      > /etc/apt/sources.list.d/mongodb-org-7.0.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends mongodb-database-tools \
    && rm -rf /var/lib/apt/lists/* \
    && mongodump --version | head -1 \
    && mongorestore --version | head -1

# Verify runtimes (fails the build early if apt served a broken node/npm)
RUN node --version && npm --version

# Install ruff (Python linter for Loop Verify — .py files)
RUN pip install --no-cache-dir ruff \
    && ruff --version

# Install eslint (JS/TS linter for Loop Verify — .js/.jsx/.ts/.tsx files)
# Pinned to v8 because v9+ requires a flat-config file that Loop
# doesn't provide (Loop invokes eslint with --no-eslintrc / --no-config-lookup).
RUN npm install -g eslint@8 \
    && eslint --version

# Install Python deps (emergentintegrations needs Emergent's extra index)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    --extra-index-url https://d33sy5i8bnduwe.cloudfront.net/simple/

# 2026-08-30 KIT D — Option B (the cure, not the band-aid). `playwright`
# (the Python package) was already in requirements.txt, but its
# Chromium BINARY was never installed here — that's the exact root
# cause of prod's "chromium executable doesn't exist at
# /root/bin/chromium" error (services/deploy_verify.py's C1 fallback
# is the band-aid for until this lands; screenshots/full verify need
# this). `PLAYWRIGHT_BROWSERS_PATH` is set as a stable ENV (not a
# version-specific hardcoded binary path, which changes every
# Playwright release) so both this install step AND the app at
# runtime resolve the SAME browser location regardless of which user
# runs it. `--with-deps` pulls Chromium's own apt dependencies
# (libnss3, libatk-bridge2.0-0, libgbm1, etc.) — no manual apt list to
# maintain, always matches the pinned playwright==1.61.0 version.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN python -m playwright install --with-deps chromium \
    && chmod -R o+rX /opt/ms-playwright

# Copy application code
COPY . .

# Iter 212m-227 — CIS Docker Benchmark 4.1: run as non-root.
# Creates an unprivileged system user, chowns /app, and drops
# to it before starting uvicorn. Any RCE inside the container
# now runs as UID 1001, not root.
RUN groupadd --system --gid 1001 aurem \
    && useradd --system --uid 1001 --gid aurem --home-dir /app --shell /bin/false aurem \
    && chown -R aurem:aurem /app
USER aurem

# Expose port
EXPOSE 8002

# Healthcheck
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8002/api/health || exit 1

# Run application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002"]