To: support@emergent.sh
From: Tejinder Sandhu <teji.ss1986@gmail.com>
Subject: Feature request: auto-inject BUILD_HASH env var (or pre-build hook) at deploy time

---

Hi Emergent Support team,

I'm Tejinder Sandhu, founder of Aurem CTO (https://auremcto.com), running on Emergent.

Job ID: 73df9f0d-7149-4a95-89d4-c9972e2b0c6d
Deployed app: https://auremcto.com

Filing this as a platform feature request. My workaround is already shipped so this is NOT urgent, but I want it on the roadmap because it affects every backend app on Emergent.

## Problem

My FastAPI backend exposes a `build_hash` field via `/api/health` — a 7-char short git SHA that lets me confirm which commit is actually live on prod. Critical for incident triage ("did my fix deploy?").

On my Emergent prod pod, this doesn't work out-of-the-box because the runtime container has NEITHER:

- The `git` binary — so `subprocess.check_output(["git", "rev-parse", "--short", "HEAD"])` silently fails
- The `.git/` directory itself — so even raw file-parsing of `.git/HEAD` silently fails

Result: `/api/health` falls through to an mtime-based fallback that does NOT reliably change between deploys. I hit this exact regression twice in the last week and lost time trying to figure out whether my fixes had actually deployed.

## What I've already shipped (Option A workaround)

I've committed a `backend/.build_info` file to git. My backend now reads it as a priority-2 fallback (between the env var check and the `git` binary attempt). The file ships in the deploy tarball since removing it from `.gitignore`, so prod pods can now resolve a real SHA.

**Verified working — current prod `/api/health` returns:**
```json
{
  "build_hash": "01fe66d",
  ...
}
```
(where `01fe66d` is a genuine commit SHA in my git log, not the `m<hex>` mtime fallback anymore.)

**Inherent limitation of Option A**: `.build_info` cannot be self-referential — a file cannot contain the SHA of the commit that includes it. So the SHA shown in prod is always the PARENT of the actual deploy-commit's SHA. Users have to run `git log <shown_sha>..HEAD` locally to reconcile. Annoying but functional.

## Feature request (Option B — cleanest long-term)

Please add ONE of the following to Emergent's deploy pipeline:

1. **Auto-inject a `BUILD_HASH` environment variable** at deploy time, containing the git commit SHA of the code being deployed.
   ✅ My backend already reads `os.environ.get("BUILD_HASH")` as priority-1. Zero code changes on my side. Eliminates the 1-commit lag entirely.

2. **OR: Support a pre-build hook** (e.g., a `deploy-hooks/pre-build.sh` file in the repo that runs before the tarball is packaged).
   ✅ My script `backend/scripts/write_build_info.py` already exists — a pipeline hook would let me populate `.build_info` with the *actual* deploy SHA at tarball-creation time.

3. **OR: Include `.git/HEAD` + `.git/refs/heads/*` + `.git/packed-refs`** in the deploy tarball.
   ✅ My backend already has a priority-4 raw parser ready to consume these — no other changes needed on the platform side.

## Why this matters (broader use case)

Any Emergent user running a backend app that needs deploy-identity for:
- **Incident triage** — "which commit is live right now?"
- **Version telemetry** — linking user-reported bugs to specific revisions
- **Multi-environment sanity checks** — "is prod really running commit X, or did the deploy silently no-op?"

Currently every backend Emergent user with this need has to invent their own workaround. Any of the three options above would make it a solved problem platform-wide.

## Priority

Not urgent — Option A unblocks day-to-day work. But this is a real platform gap that would benefit every backend app on Emergent. Happy to jump on a quick call if the engineering team wants to discuss implementation trade-offs.

Thanks for building a solid platform. Looking forward to hearing what makes sense from your side.

Best,
Tejinder Sandhu
Founder, Aurem CTO
teji.ss1986@gmail.com
https://auremcto.com

---

Files already in the repo, ready to be leveraged by Option 2 (pre-build hook):
- `backend/scripts/write_build_info.py` — writes current SHA to `backend/.build_info` using either the `git` binary OR raw `.git/HEAD` parsing
- `backend/main.py::_resolve_build_hash()` — 5-tier resolution ladder documented inline
