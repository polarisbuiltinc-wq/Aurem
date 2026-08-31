# DRAFT ONLY — do not send unless Option B (Dockerfile fix) fails after redeploy

To: support@emergent.sh
Subject: Production Playwright/Chromium missing after Dockerfile fix — need platform guidance

## Error seen in production
```
BrowserType.launch: Executable doesn't exist at /root/bin/chromium
```
Raised by our backend's server-side Playwright verification service
(`services/deploy_verify.py`) when it tries to launch headless Chromium
to screenshot/verify a deployed page.

## What we found
- `backend/requirements.txt` pins `playwright==1.61.0` (the Python package
  only — this does not include the browser binary).
- `backend/Dockerfile` (built from `python:3.11-slim`) never ran
  `playwright install chromium` — so no Chromium binary existed in the
  production image at all.
- Preview pods have a working Chromium at `/root/bin/chromium` (provisioned
  by Emergent's own preview agent-env base image), which is why this only
  ever showed up in production, not preview.

## What we tried (Option B)
Added to `backend/Dockerfile`, after the pip install step, before switching
to the non-root user:
```dockerfile
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN python -m playwright install --with-deps chromium \
    && chmod -R o+rX /opt/ms-playwright
```
`--with-deps` installs Chromium's own apt dependencies. We used a stable
`PLAYWRIGHT_BROWSERS_PATH` env (not a version-specific hardcoded binary
path) so both the build step and the app at runtime (running as a
non-root user) resolve the same browser location.

## Expected path after redeploy
`PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright` → Playwright resolves the
Chromium binary under that directory automatically; no
`PLAYWRIGHT_CHROME_EXECUTABLE_PATH` override should be needed or set in
production going forward.

## If this ticket is being sent, it means
After redeploying with the Dockerfile change above, the production
error is STILL present (or a new Chromium-related error appeared) —
i.e., the platform's base image or build pipeline is blocking Chromium's
own apt dependencies from installing (e.g., no root during build, a
read-only or non-Debian base, a network-restricted build sandbox, etc.).

## What we need from Emergent support
1. Confirm whether the production build environment permits `apt-get
   install` as root during the Docker build stage (Playwright's
   `--with-deps` needs this).
2. If not, what is the platform-recommended way to ship a headless
   Chromium binary in a production backend image on Emergent — e.g. a
   pre-baked base image with Chromium already installed, or a documented
   alternate install path.
3. Any known Emergent-specific `PLAYWRIGHT_BROWSERS_PATH` or cache
   directory convention we should be using instead of `/opt/ms-playwright`.

## Context
This backend already has a graceful fallback (browser-free HTTP check)
for when Chromium is missing, so the app is not broken — but full visual
verification (screenshots, console/runtime error checks, click
interactions) requires an actual Chromium binary in production.
