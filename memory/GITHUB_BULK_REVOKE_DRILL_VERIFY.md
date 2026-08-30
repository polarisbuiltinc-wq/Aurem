# GitHub bulk-revoke — drill-repo live verify (U1–U6)

Status as of 2026-08-30: **UNCERTAIN — not run.** Built + mock-tested
only. Real destructive use is gated OFF by the
`github_bulk_revoke_live_verified` feature flag (default `enabled: false`).

## Why the live test hasn't run

1. Preview's GitHub App credentials are stale — a live
   `GET /app/installations` call returns `401 Unauthorized` right now
   (same private-key mismatch flagged in an earlier round; founder
   declined to sync preview creds at the time).
2. Even with working credentials, this DB has exactly ONE installation:
   `152797252` on `polarisbuiltinc-wq` (the shared `ora-grounding` org),
   which dozens of OTHER test suites rely on staying connected. There
   is no spare/disposable installation to safely DELETE, and the agent
   cannot create one (GitHub's install flow requires a human clicking
   through GitHub's own UI).

Founder decision (2026-08-30): skip the live verify for now, ship
mock-tested, do the one real DELETE test later in production on a
disposable installation the founder creates when ready.

## The six checks (still open)

| # | Check | Status |
|---|---|---|
| U1 | Success status code (204?) | UNCERTAIN |
| U2 | All install tokens invalidated | UNCERTAIN |
| U3 | Branches/PRs/files intact, app access removed | UNCERTAIN |
| U4 | One-way (cannot App-restore; user must re-install) | UNCERTAIN |
| U5 | Error cases 404/401/403 + rate limit | UNCERTAIN |
| U6 | Can the drill install be restored afterward? | UNCERTAIN |

## Gate

`services/github_bulk_revoke.py::bulk_revoke` is fully built and unit
tested with a mocked GitHub client (`tests/test_bulk_github_revoke_2026_08_30.py`).
`routers/admin_bin.py::github_bulk_revoke` refuses to call it for real
(`403 live_verification_pending`) unless the
`github_bulk_revoke_live_verified` feature flag is `enabled: true`.
The admin UI's "Revoke" button is disabled with the same message
until that flag flips.

## To close this out (founder, later)

1. Create/obtain a genuinely disposable GitHub App installation
   (do NOT use `152797252` / `ora-grounding`).
2. Hit `DELETE /app/installations/{id}` once for real against it
   (via the App JWT, same as `services/github_app.py::revoke_installation_verbose`).
3. Record the observed U1–U6 results here as CONFIRMED/LIKELY, update
   this file.
4. Flip `github_bulk_revoke_live_verified` to `enabled: true` in
   `/admin/feature-flags`.
