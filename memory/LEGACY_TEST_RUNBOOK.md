# Legacy Test Runbook — Session G Playbook
## Fast-Start Guide for the Next Fork Agent

_Written after Session G · Batch-3 (Feb 2026)._ This runbook captures
the recurring patterns discovered while unblocking 55+ legacy
quarantined tests across three overnight sessions. Read once before
starting any Bucket-A remediation batch — it will save you 30 min per
file.

---

## 1 · The Big Picture

Legacy tests are quarantined via `@pytest.mark.legacy` and DESELECTED
by default in `pytest.ini` (`addopts = -m "not flaky and not
llm_judge and not legacy"`). Run them explicitly with:

```bash
python3 -m pytest tests/test_X.py -m "legacy or not legacy" --tb=short -q --timeout=15
```

The `legacy_quarantine.txt` file lists ~200 failing nodeids across
~130 files. Files are placed in the quarantine when a test fails
without an owner + fix_by date on record.

## 2 · Recurring Failure Patterns (in priority order)

### Pattern A · Auth-Fixture Drift
**Symptom**: `assert 200 == 401` on the first fixture call. Downstream
tests error out with `KeyError: 'token'` or `409 email already
registered`.
**Root cause**: `SEEDED_PASS = "testpass123"` was the original
password for `test@aurem.dev`. Iter 212m-104 rotated to
`AuremTest2026!`. Test files that hard-code the old constant now 401.
**Fix**: `SEEDED_PASS = "AuremTest2026!"` (or the equivalent constant
name — `PASSWORD`, `ADMIN_PASSWORD`, or inline literal). See
`/app/memory/test_credentials.md` for the current preview
credentials.
**Impact**: Single-line fix unblocks 8–12 files, ~40 nodeids.

### Pattern B · Static-Source-Path Drift
**Symptom**: `AssertionError: assert 'X' in '/**...'`. The test does
`open("/app/backend/services/foo.py").read()` and asserts on a specific
line/pattern.
**Root cause**: The file was refactored (e.g. `services/llm/__init__.py`
was split into `_meta.py` + `openrouter_providers.py` in Session D-part-2)
or the invariant genuinely evolved (e.g. Iter 328 replaced a modal
with an inline row).
**Fix**: Retarget the assertion to the NEW file OR update the
assertion to the new invariant (never loosen — retain the semantic
contract). Prefer **substring / regex checks** over exact-line checks
so cosmetic reformatting doesn't break the test.
**Example**: `"Ask ORA to build, fix, or scan..."` → `"Ask ORA to
build, fix, or scan"` (drop trailing punctuation, keep the unification
invariant).

### Pattern C · Backend API Contract Evolution
**Symptom**: `AssertionError: 'X'.error == 'syntax_gate_blocked'`
where actual is `'No project selected. Please select a project...'`.
**Root cause**: The code path added a NEW guard IN FRONT OF the one
the test targets (typically Iter 212m-169 ORAContext isolation
adding `_repo_ctx_from(ctx)` before every tool's business logic).
**Fix**: Update the test's monkeypatch/fixture to also stub the new
front-guard. Example:
```python
# Old:
monkeypatch.setattr(lt, "_resolve_project", _stub_resolve)
# New: also stub the new BINContext gate:
monkeypatch.setattr(lt, "_repo_ctx_from", _stub_repo_ctx)
```

### Pattern D · Unit-Test-Only Fixtures Need New Fields
**Symptom**: `AttributeError: 'LoopEngine' object has no attribute
'_narration_ring'`. `AttributeError: 'NoneType' object has no
attribute 'loop_sessions'`.
**Root cause**: Production code added instance-level attributes
(e.g. `self._narration_ring = deque(...)` in `__init__`) or requires
a live DB handle for new features (Iter 212m-177 P0-1
`db.loop_sessions.find_one_and_update` claim). Tests that use
`LoopEngine.__new__(le.LoopEngine)` to bypass `__init__` miss the new
fields.
**Fix**: After the `__new__(...)` line, add the missing attributes
explicitly. For DB, use a tiny fake:
```python
class _FakeLoopSessions:
    async def find_one_and_update(self, q, u): return {"loop_id": "x", "context": {}}
    async def find_one(self, *a, **k): return {"context": {}, "state": "shipping"}
class _FakeDB:
    loop_sessions = _FakeLoopSessions()
eng.db = _FakeDB()
```

### Pattern E · Live-Server Test on a Slow/Flaky Preview
**Symptom**: `requests.ReadTimeout` at module-level `_ensure_seed_users()`
call, aborting pytest collection.
**Root cause**: Tests that hit a live preview backend at MODULE-LOAD
time only catch `requests.ConnectionError`, not `ReadTimeout` /
`RequestException`.
**Fix**: Broaden the catch:
```python
except (requests.ConnectionError, requests.Timeout):
    pytest.skip(..., allow_module_level=True)
```

### Pattern F · Manifest / Count Freshness
**Symptom**: `assert 3921 == 3938` on total test count.
**Root cause**: You added new test files; the pinned manifest is now
stale.
**Fix**: `python3 scripts/gen_qa_manifest.py` — regenerates the pinned
count.

### Pattern G · Real Production Bug Discovered
**Symptom**: A test that "should" pass reveals a genuine current bug
(e.g. Session E found a broken `_emit("state", {dict})` call in
`loop_engine.py` with wrong arg types; Session G Batch-3 found 6
f-string logger.warning calls violating Vanguard rules).
**Fix**: **FIX THE PRODUCTION BUG.** Do not shrug and skip. The test
was doing its job. Flag the finding prominently in the report.

### Pattern H · Genuine Product Evolution That Deserves Founder Review
**Symptom**: A test flags a REAL current concern (e.g. persona grew
+16% past its budget, chat latency risk resurfacing).
**Root cause**: Product legitimately evolved; the test's bound reflects
an old snapshot that may need re-evaluation.
**Fix**: **DO NOT SILENTLY RAISE THE BOUND.** Leave the test
failing (or add a follow-up TODO with a real fix_by date), and flag
it as a "genuine production finding" needing founder review in the
report.

---

## 3 · The Priority Matrix

Rank each file by:
1. **Critical services keyword hits** (grep for
   `orchestrator|chat|loop|github|advisor|ship` in filename + top-2000
   chars of file).
2. **Fewer nodeids** = quicker win.
3. **Business-user-facing behaviour** > **internal audit invariants**.

Example script for ranking (from Session G Batch-3):

```python
import re, pathlib
BACKEND = pathlib.Path("/app/backend")
lines = (BACKEND / "tests" / "legacy_quarantine.txt").read_text().splitlines()
files = sorted({l.split("::")[0] for l in lines if l.strip()})
CRITICAL = ["orchestrator", "chat", "loop", "github", "sse", "advisor", "ship"]
counts = {}
for l in lines:
    if l.strip():
        counts[l.split("::")[0]] = counts.get(l.split("::")[0], 0) + 1
def score(f):
    src = (BACKEND/f).read_text(errors="ignore") if (BACKEND/f).exists() else ""
    kw = sum(1 for k in CRITICAL if k in f.lower() or k in src.lower()[:2000])
    return (kw, -counts.get(f, 0))
for f in sorted(files, key=score, reverse=True)[:10]:
    print(counts.get(f, 0), f)
```

## 4 · Recurring Test-Only Env Variables

- **`AUREM_TEST_MODE=1`** — Preview-only env flag. Enables:
  - PAT-validation bypass in `POST /cto/projects/add` when the PAT
    starts with the sentinel prefix `github_pat_TEST_`.
  - Cron-death simulation endpoints (`/admin/dev/kill-supervised-task/`).
  - **NEVER set on production.**

## 5 · Cron-Death Simulation (UI verification)

For `/admin/architecture` supervised-tasks tile visual verification:

```bash
# Simulate a cron exception
curl -X POST https://preview/api/aurem-dev/admin/dev/kill-supervised-task/db_backup \
  -H "Authorization: Bearer $ADMIN_TOKEN"
# Simulate a silent completion
curl -X POST 'https://preview/api/aurem-dev/admin/dev/kill-supervised-task/db_backup?reason=silent_completion' \
  -H "Authorization: Bearer $ADMIN_TOKEN"
# Clear the postmortem
curl -X POST https://preview/api/aurem-dev/admin/dev/clear-supervised-postmortem/db_backup \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

## 6 · When to STOP a Batch

- If a single file needs >3 monkeypatch-chain updates to reach the
  new backend contract → the underlying feature has drifted more
  than the test can catch up on in one PR. Leave a comment explaining
  what would need to change, and move to the next file.
- If a test reveals a genuine production concern → STOP the routine
  work, fix the production bug (Pattern G) or flag it (Pattern H),
  and report prominently.
- At natural batch boundaries (5 files done, top-5 done, etc.) —
  DEPLOY + verify + report before starting the next batch. Never
  chain a 40-file batch under one deploy.

## 7 · Session-G Legacy Score History

| Session | Legacy fail | Legacy pass | Delta |
|---------|-------------|-------------|-------|
| Audit baseline | 216 | 30 | — |
| Batch 1 (auth-drift) | 180 | 42 | +12 |
| Batch 2 (Items 1-2) | 168 | 54 | +12 |
| Batch 3 (top-5) | 156 | 66 | +12 (+ 1 real prod bug fixed) |

Cumulative: **60 tests unblocked, 4 real prod bugs found + fixed,
1 legit prod concern surfaced for founder review** in three
overnight sessions.
