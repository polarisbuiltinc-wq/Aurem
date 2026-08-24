# DELETE GATE — mandatory process for ANY file deletion (Iter 331)

**Why**: `tool_executor.py` was deleted 3× as "approved dead code" while being
LAZY-imported (`import` inside a function body inside `local_tools.py`) — the
delete only crashed at RUNTIME on the first tool call, breaking every chat
tool-call on production. Normal grep / IDE "find usages" missed it every time.
`VisualFixtures.jsx` / `LoopLiveFeedDemo.jsx` were likewise "approved" while
being live Playwright QA fixtures. This gate makes that bug class impossible.

## Layer 1 — Dependency-scan script (the tool)

```
./scripts/check-safe-to-delete.sh path/to/file.py
```

Catches what got missed 3×:
- Section 2: lazy/dynamic imports (`importlib.import_module`, in-function `import`)
- Section 3: string-keyed references (routing dicts like `TOOLS = {"tool_executor": …}`,
  route strings hit by Playwright specs)

## Layer 2 — Hard process gate (the actual fix)

A file enters the "approved for deletion" list ONLY with the script's FULL
output pasted. No output block → request auto-rejected, regardless of
confidence.

### Delete approval template (mandatory, no exceptions)

```
File: `path/to/file.py`
Script output:
[paste full ./scripts/check-safe-to-delete.sh output here]

Verdict: ✅ Safe / ❌ Not safe
Approved by: [name]
Date: [date]
```

### 2026-08-26 — automated push-flow verdict

This repo pushes straight to `main` (no PR flow), so the manual template
above was easy to forget in an auto-committed push. The `delete-gate` CI
job (`.github/workflows/ci.yml`) now closes that gap itself: if a push
deletes source files and this same push did NOT already update this doc,
the job runs `check-safe-to-delete.sh` on every deleted file and:
- if **every** file comes back "✅ ZERO references found" — it appends
  an auto-generated verdict block below (bot commit, `[delete-gate-bot]`)
  and the gate passes. The audit trail is never skipped.
- if **any** file still has real references — the gate **fails for
  real**, same as before. This only automates the safe case; a risky
  deletion still needs a human/agent to look at it and either restore
  the file or manually record a verdict.

## Layer 3 — Quarantine instead of hard-delete

Even with a ✅ verdict, never hard-delete immediately:

1. **Soft delete first** — either add at the top of the file:
   ```python
   import warnings
   warnings.warn(
       "<file> is scheduled for deletion on <date>. "
       "If you see this warning in logs, this file is still in use — DO NOT DELETE.",
       DeprecationWarning,
   )
   ```
   …or move the file to `_deprecated/<file>` (import path preserved via a stub
   if needed). Ship it. Monitor prod logs 1-2 weeks for
   `ModuleNotFoundError` / `DeprecationWarning`.
2. **Hard delete** — only after ZERO related errors in the quarantine window.

## Current verdict record (2026-07-28, script-verified)

| File | Verdict | Evidence |
|---|---|---|
| `backend/services/tool_executor.py` | ❌ NOT SAFE — restored | lazy-imported in `local_tools.py` `invoke_local_tool()`; every chat tool-call crashed |
| `backend/services/tools_bridge.py` | ❌ NOT SAFE | imported by `services/orchestrator.py:20`; orchestrator live in 8 routers; also `test_iter138` |
| `frontend/src/pages/VisualFixtures.jsx` | ❌ NOT SAFE | route `/dev/visual` used by Playwright `state_fixtures.spec.js` + `interaction_latency.spec.js`; App.jsx route |
| `frontend/src/pages/LoopLiveFeedDemo.jsx` | ❌ NOT SAFE | route `/dev/loop-live-feed` used by `a11y_journeys.spec.js` + `public_routes.spec.js`; App.jsx route |

**The founder-approved delete list #14 is therefore fully REJECTED.**
A new list requires fresh script runs + this template.

## Protected data files (never delete without scan) — Iter 334

- `.emergent/qa-history/regression_library.json` — append-only Auto-QA
  regression library. String-referenced (not imported) by
  `services/qa_matrix.py::_load_regression_library` at every
  auto-qa-agent run; deleting it silently blanks the "Regressions
  checked against" section of every future QA report. Exactly the
  class of reference `scripts/check-safe-to-delete.sh` exists to catch.
