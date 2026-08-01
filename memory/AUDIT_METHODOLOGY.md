# Discovery-Audit Methodology (canonical checklist)

**Last update: 2026-08-01** — after 3 near-miss mis-classifications in
`services/` discovery track (Batch 1 + Batch 2).

Every discovery audit that reports a file as `DEAD CODE` MUST pass all
3 of the checks below. Individual signals are necessary but NOT
sufficient.

## The three signals

### 1. Zero LIVE callers
Grep `from services.X import` + `import services.X` across `routers/`,
`main.py`, `core/`, and all of `backend/services/` (excluding the file
itself). Live-callers list must be empty.

Necessary but not sufficient — a file can have zero absolute-import
live callers and still be alive via signal 2 or 3.

### 2. Zero RELATIVE-import callers
Grep `from .X import` (single-dot) and `from ..X import` (double-dot)
across `backend/services/*.py`. Intra-package imports do NOT match the
grep from signal 1.

**Learning from Batch 1 (2026-08-01):** I initially mis-classified
`services/agents.py` and `services/dev_skills.py` as dead code by
running only signal 1. Both files are called via relative imports —
`from .agents import CoordinatorAgent` and `from .dev_skills import ...`
— and are LIVE. Adding signal 2 catches this.

### 3. Purpose is NOT test-infrastructure / migration / one-shot util
Read the file's top-of-file docstring. If ANY of these self-descriptions
appear, the file is EXPECTED to have zero live callers and MUST NOT be
classified as dead code:

- "Test-infrastructure" / "used by tests/*" / "test-helper library"
- "One-shot migration" / "run-once script" / "backfill utility"
- "Scheduled cron entry-point" (called by a scheduler, not by imports)
- "Iter NNN behavioural evaluator" (a QA-track evaluator)

**Learning from Batch 2 (2026-08-01):** I initially mis-classified
`services/reasoning_evals.py` and `services/boilerplate_audit.py` as
dead code. Both are TEST-INFRASTRUCTURE modules purpose-built to be
consumed only by `tests/*.py` files. Zero live callers is by design,
not evidence of decay. Adding signal 3 catches this.

## Verdict rule

- Signal 1 True + Signal 2 True + Signal 3 True → **CONFIRMED DEAD**.
  Deletion is safe.
- Any signal False → NOT DEAD. Categorize as `FULLY BUILT`,
  `HALF BUILT`, `UNWIRED`, or `TEST-INFRASTRUCTURE` accordingly.

## Recording the audit

Every discovery audit report MUST cite which signals were checked per
file, or at minimum state that all 3 signals were applied at the
category level.

## Additional cross-cutting checks

Independent of the 3-signal dead-code checklist, always also check:

1. **Cross-product contamination markers** — run
   `tests/test_no_cross_contamination.py` mentally against any file
   with an unfamiliar domain vocabulary. Flag hits as HIGH PRIORITY.
2. **Silent no-op pattern** — file imports cleanly but every entry-
   point checks an env var / feature flag that is UNSET in prod
   `.env`, returning empty results. Flag as `HALF BUILT (silent no-op)`
   rather than `FULLY BUILT`. Established instances of this pattern:
   Supabase (Batch 1), Vercel platform (Batch 1), Vanguard-CI, QA-matrix,
   AUREM-Org (Batch 2).
3. **Duplicate implementations** — before flagging a file as
   deprecated by another, verify semantic scope difference (e.g.
   `rollback_manager.py` vs `loop_rollback.py` in Batch 2 — different
   scopes, not duplicates).
