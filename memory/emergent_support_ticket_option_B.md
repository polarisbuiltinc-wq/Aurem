# Emergent Platform Feature Request — Auto-inject `BUILD_HASH` at Deploy Time

**To:** support@emergent.sh
**Subject:** Feature request: auto-inject `BUILD_HASH` env var (or pre-build hook) at deploy time
**From:** [Founder — teji.ss1986@gmail.com]
**App:** https://auremcto.com
**Job ID:** [attach current session job ID before sending]

---

Hi Emergent Support,

## Context
I'm running an Aurem CTO app (FastAPI + React + MongoDB) that exposes a `build_hash` field via `/api/health`. This field is critical for incident-response — it lets us confirm which commit is actually live on prod, especially during "did my fix deploy?" moments.

## Problem
The runtime prod pod on Emergent (`https://auremcto.com`) has neither:
- The `git` binary — so `subprocess.check_output(["git", "rev-parse", "--short", "HEAD"])` fails silently
- The `.git/` directory — so even raw file parsing of `.git/HEAD` fails silently

Result: `/api/health` falls through to an mtime-based fallback (`m<hex>`) that does NOT reliably change between deploys — masking whether new code is actually running.

## What I've Already Shipped (Option A Workaround)
A checked-in `backend/.build_info` file that carries the previous-commit SHA. This is now shipping to prod via the tarball. It works — but has an inherent **1-commit lag**: `.build_info` cannot be self-referential, so the SHA it contains is always the PARENT of the actual deploy-commit's SHA. Not ideal for real-time incident diagnosis.

## Feature Request (Option B, cleanest long-term)
Please add ONE of the following to Emergent's deploy pipeline:

1. **Auto-inject `BUILD_HASH` env var** at deploy time, containing the git commit SHA of the code being deployed.  
   ✅ My backend already reads `os.environ.get("BUILD_HASH")` as priority-1 — this would "just work" with zero code changes on my side, and would eliminate the off-by-one lag entirely.

2. **OR: Support a pre-build hook** (e.g., a `deploy-hooks/pre-build.sh` file in the repo that runs before the tarball is packaged).  
   ✅ My script `backend/scripts/write_build_info.py` already exists — a pipeline hook would let me populate `.build_info` with the actual deploy SHA at tarball-creation time.

3. **OR: Include `.git/HEAD` + `.git/refs/heads/*` + `.git/packed-refs`** in the deploy tarball. My backend's priority-4 raw parser is ready to consume these — no other changes needed on the platform side.

## Why This Matters (Broader Use Case)
Any Emergent user running a FastAPI/Node/etc. app that needs deploy-identity for:
- **Incident triage** — "which commit is live right now?"
- **Version telemetry** — linking user-reported bugs to specific revisions
- **Multi-environment sanity checks** — "is prod really running commit X, or did the deploy silently no-op?"

## Priority
Not urgent — my Option A workaround unblocks day-to-day work. But this is a real platform gap that affects every backend app deployed on Emergent, and Option A wouldn't be needed if the platform provided any of the three options above.

Happy to jump on a quick call if the engineering team wants to discuss implementation trade-offs.

Thanks,
[Founder name]
[Contact]

---

**Files that already exist in the repo, ready to be leveraged by Option 2 (pre-build hook):**
- `backend/scripts/write_build_info.py` — writes the current SHA to `backend/.build_info` using either the `git` binary OR raw `.git/HEAD` parsing.
- `backend/main.py::_resolve_build_hash()` — 5-tier resolution ladder documented inline.
