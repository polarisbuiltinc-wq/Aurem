# Disk Pressure — root cause & reclaim runbook (2026-08-24)

## Why "Save to GitHub" fails with ENOSPC
/app, /root, /var/log, /data/db all share ONE 9.8GB ext4 device
(/dev/nvme0n3). Git push needs temp objects + lock files in /app/.git —
0 bytes free = instant failure with "config.lock / cannot lock ref"
noise. The earlier fix (deleting /app/qa node_modules) cleaned a
symptom inside /app; the real growth drivers are OUTSIDE /app:

| Consumer | Size seen | Safe to reclaim? |
|---|---|---|
| /data/db (Mongo preview data) | 4.4G | Only with founder OK (real data) |
| /root/.npm (npm cache) | 1.9G | YES — cache only |
| /root/.venv (python env) | 883M | NO |
| /var/log/mongodb.out.log.[1-9] rotations | ~450M | YES — old rotations |
| /root/.emergent | 438M | NO (platform) |
| /app/frontend/dist + node_modules/.vite | ~100M | YES (build artifacts; vite build recreates) |

## Reclaim commands (run when df shows >90%)
rm -rf /root/.npm /var/log/mongodb.out.log.[0-9]* \
       /app/frontend/dist /app/frontend/node_modules/.vite /app/.ruff_cache
# then clear stale git locks left by a failed push:
find /app/.git -name "*.lock" -delete
find /app/.git/objects -name "tmp_obj_*" -delete

## Notes
- backend/coverage.json is TRACKED in git (6MB) — do not delete it in a
  push, the Delete Gate CI job blocks undocumented deletions.
- After any `vite build` verification, delete frontend/dist again.
- Mongo rotated logs regrow ~51MB per rotation; re-run reclaim periodically.

## 2026-08-24 recurrence — hit 100% again (git writes failing)
Same device, same drivers. Reclaimed via the runbook above PLUS these
additional safe items found this time (all cache/scratch, none are
source or tracked): `/usr/local/share/.cache/yarn/v6` (1.2G),
`/root/.cache/pip` + `/root/.cache/pip-audit` (50M), all
`backend/**/__pycache__` + `backend/.pytest_cache` (~23M),
`/app/vscode-extension/node_modules` (126M, rebuild with `yarn` if
the extension is worked on again), and ~1.4G of leftover
`/tmp/restore_restore_scratch_*.gz` + `/tmp/mongo_aurem_*.gz` rollback
scratch files (temp download copies, not the actual R2-backed
snapshots). Result: 100%→95%, 9.8G→498M free. Still tight — the
6.8G `/data/db` Mongo data is the real long-term driver and still
requires founder OK before touching. Re-run `df -h /app` before any
broad/full-suite pytest attempt.
