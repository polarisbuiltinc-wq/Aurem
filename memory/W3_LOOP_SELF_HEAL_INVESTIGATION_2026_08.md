# W3 — Loop self-heal exhaustion investigation (report only, no behavior change)

## Finding: NOT a bug. Test is stale against a documented, founder-directed Feb 2026 change.

### 1. Exact terminal-state decision location
`backend/services/loop_engine.py`, inside `_do_verify()`:
- Line 2179-2189: in-line comment block, verbatim:
  > "Feb 2026 · Terminal hard-fail — founder-directed change. Previously
  > this fallback set state=PAUSED_FOR_USER... Founder's exact ask:
  > 'loop halts at exactly 2 heal attempts and surfaces a terminal state
  > (not a silent retry).' So: after MAX_SELF_HEALS heal rounds are
  > exhausted and files are STILL failing, we hard-fail the loop
  > (LoopState.FAILED) in a single terminal event and stop."
- Line 2205: `await self._fail("verify", ...)` is the call site.
- `_fail()` (line 3848) unconditionally sets `self.state = LoopState.FAILED`.

### 2. Is PAUSED_FOR_USER reachable, or dead?
**Reachable, not dead** — just intentionally not used on this path anymore.
Still live at `_do_scan()` line 2252: any Vanguard-detected critical security
finding on the diff sets `LoopState.PAUSED_FOR_USER` with `requires_user_action=True`.

### 3. exhausted-needs-human vs exhausted-hard-failure
No ambiguity in current code — deliberate, documented decision:
self-heal exhaustion = hard FAILED (not a pause-for-retry). It already has
dedicated frontend UX (`LoopFailureCard`, Iter 362, referenced at line
3878-3886 of `_fail()`) that surfaces `failed_files`, `errors`,
`max_self_heals` and still sets `requires_user_action=True` on the FAILED
event — so the user isn't left with a dead end, they just land on state
FAILED instead of PAUSED_FOR_USER.

### Reproduced test failure (live run, this session)
`tests/test_iter212m62_loop_verify.py::test_self_heal_exhausted_pauses_for_user`
→ `AssertionError: FAILED != PAUSED_FOR_USER`. The test's own docstring
("If self-heal can't fix it after MAX attempts, loop pauses") describes the
PRE-Feb-2026 behavior. It was never updated after the founder's directed
semantic change landed.

### The `_DB object is not subscriptable` warnings — red herring, confirmed unrelated
```
WARNING loop_engine: task spec freeze failed: TypeError("'_DB' object is not subscriptable")
WARNING loop_audit_log: log failed ... TypeError("'_DB' object is not subscriptable")
WARNING loop_safety: loop_failures insert failed: AttributeError("'_DB' object has no attribute 'loop_failures'")
WARNING loop_safety: loop_lock release failed: AttributeError(...)
WARNING loop_beta: log_execution failed: AttributeError(...)
```
These come from the test file's hand-rolled `_DB`/`_Coll` fixture (defined in
the same test file) missing a few collections (`loop_failures`, `loop_locks`,
`loop_execution_log`) and a subscript path used by peripheral fire-and-forget
logging calls inside `_fail()`. All are caught in `try/except` and merely
logged — they do NOT influence the FAILED vs PAUSED_FOR_USER branch. Confirmed
by the actual assertion output: `engine.state` is deterministically `FAILED`,
exactly as the Feb-2026 code intends.

## Proposal (NOT implemented — awaiting founder approval)
Two independent, low-risk options, can do either or both:

**(a) Fix the stale test** to assert current intended behavior:
- Rename to `test_self_heal_exhausted_hard_fails` (or similar).
- Assert `engine.state == eng.LoopState.FAILED`.
- Assert the FAILED event's `data` carries `failed_files`, `errors`,
  `max_self_heals` (already emitted by `_fail`, just not asserted).
- Zero production code change — test-only.

**(b) Add `LOOP_SELF_HEAL_EXHAUSTED` to `core/errors.py::ErrorCode`** and pass
it via `_fail("verify", ..., data={"error_code": ErrorCode.LOOP_SELF_HEAL_EXHAUSTED})`
so this specific terminal failure carries a stable machine-readable code
(distinct from generic `VERIFY_FAILED`) for the frontend `LoopFailureCard`
and any future error-metrics dashboard. Also test-safe / additive — no
existing behavior changes, purely adds a new enum member + one `data` key.

Not touching either without your explicit go-ahead, per your standing rule.
