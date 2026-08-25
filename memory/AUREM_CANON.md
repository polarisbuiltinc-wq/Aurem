# AUREM_CANON.md — North Star & Constitution

*Created 2026-08 (Production Hardening session). The founder referenced
this file's content in a task brief, but the exact source text was not
attached to that message — this is a synthesized scaffold built from
every explicit product/trust decision made across this session and its
predecessors (Steps 1-3, onboarding aha, Google OAuth fix, the full
system map, and this hardening pass). Treat C1-C9 below as a first
draft for the founder to correct, not a final legal document. The
status table is intentionally short-lived — update it at every
close-out, per the instruction that created this file.*

## NORTH STAR

AUREM connects to a user's GitHub repo, writes and ships code changes
on their behalf, and is **honest about what it did and didn't do** at
every step — the trust the product sells is as real as the code it
ships.

## THE CONSTITUTION (C1–C9)

**C1 — Honesty over impressiveness.** Never claim more than what was
actually verified. A claim about the system is CONFIRMED (read the
code / ran the command), LIKELY (strong indirect evidence), or
UNCERTAIN (say so) — never presented as fact when it's a guess.

**C2 — Blocked ≠ Failed.** A safety gate stopping a task (test-file
lock, self-heal exhaustion, ship-pending-approval) is a distinct,
calmer outcome from a crash. It must never render, sound, or feel like
a failure to the user.

**C3 — Every error has a name and a message.** Every error the system
can produce is classified into `core/errors.py`'s `ErrorCode`
taxonomy, carries a `ref_id`, and has a plain-language catalog entry in
`i18n/errors_en.json`. No code is ever allowed to have a failure mode
with no matching message — that gap is closed by a standing test, not
a one-time fix (Production Hardening Fix 1, 2026-08).

**C4 — Silent degradation is forbidden.** If a safety or verification
layer is disabled (by config, by admin choice, or by missing
credentials), the system must say so loudly — a health check, a
founder alert, a visible status — never just quietly do less work
(Production Hardening Fix 2, 2026-08).

**C5 — No feature ships without a real, working test.** Enforced by
the coverage ratchet (`scripts/ci_check_coverage_ratchet.py`): no
overall coverage drop, no under-floor touched file, no untested new
lines in a diff. Real tests only — no mocks on shipped paths, no
stubs, no orphaned code.

**C6 — A new regression can never hide in old red.** The suite is
allowed to carry known, tracked, pre-existing failures (the
`@pytest.mark.legacy` quarantine) without being blocked by them — but
the TOTAL red count is locked (`tests/baseline_counts.txt`,
Production Hardening Fix 3, 2026-08) so a brand-new failure can't
blend into the noise and go unnoticed.

**C7 — The founder's settings are the founder's.** No automated
process silently flips an admin-controlled setting back on/off to
"fix" something. The system can only make a setting's current state
visible and loud — the decision to change it stays a human click.

**C8 — Shipping means it reached GitHub.** "Committed" and "pushed"
are different, both-tracked outcomes. A commit that exists by SHA but
never reached the branch (`PushFailedError`) is reported as exactly
that — never silently collapsed into "done" or "nothing happened."

**C9 — Read the actual code before claiming what it does.** Every
architectural claim about AUREM (this file, PRD.md, any founder-facing
report) is expected to cite file:line or a captured command output,
not memory or assumption.

## HONEST STATUS TABLE

*To be updated at each close-out. Snapshot as of this hardening
session (2026-08):*

| Constitution item | Status | Evidence |
|---|---|---|
| C1 Honesty discipline | **PRACTICED** | Every report this session used CONFIRMED/LIKELY/UNCERTAIN labeling |
| C2 Blocked ≠ Failed | **BUILT** | Step 1 (ship/commit robustness), `LoopState.PAUSED_FOR_USER` |
| C3 Error taxonomy + catalog | **BUILT, closed today** | `core/errors.py` (14 codes) + `i18n/errors_en.json` (14/14 entries as of Fix 1) |
| C4 Loud-not-silent safety | **BUILT, closed today** | `services/health_checks.py::_check_vanguard_e2b_sandbox` (Fix 2) |
| C5 Ratchet/floor/diff-coverage | **BUILT** | `scripts/ci_check_coverage_ratchet.py` |
| C6 Regression baseline lock | **BUILT, closed today** | `scripts/ci_check_regression_baseline.py` + `tests/baseline_counts.txt` (Fix 3) |
| C7 No auto-admin-override | **PRACTICED** | Fix 2's check never calls `save_config` (test-enforced) |
| C8 Commit vs push truth | **BUILT** | `PushFailedError`, `ErrorCode.PUSH_FAILED` |
| C9 Evidence-based reporting | **PRACTICED** | This session's full system map (8-section audit) |

## SCOPE NOTE

This file does not replace `memory/PRD.md` (feature history/backlog)
or `memory/CHANGELOG.md` (dated change log). It exists as the stable
values/rules reference the other two are held accountable to.
