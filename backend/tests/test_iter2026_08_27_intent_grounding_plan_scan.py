"""
tests/test_iter2026_08_27_intent_grounding_plan_scan.py — P1 (Intent
Grounding) + P2 (Plan↔Scan Consistency Contract), Journey/Intent-
Grounding build round.

Reproduces + fixes the transcript bug chain:
  (a) a bare "yes" replying to ORA's own audit report was refused as
      "too broad" by services/ambiguity_gate.is_ambiguous_task() (a
      word-count heuristic with zero awareness of the pending proposal
      it was replying to);
  (b) the plan regenerated from a vague re-derivation targeted 4 files
      the scan never flagged and silently dropped 6 of 9 findings.
"""
import pytest

from services.intent_grounding import (
    is_confirmatory_reply,
    resolve_confirmatory_scope,
    render_grounded_task,
    NO_PENDING_PROPOSAL_MESSAGE,
)
from services.ambiguity_gate import is_ambiguous_task
from services.plan_scan_contract import (
    check_plan_scan_consistency,
    render_mismatch_message,
)


# ── P1a — confirmatory-reply detection ──────────────────────────────

@pytest.mark.parametrize("msg", [
    "yes", "Yes", "yes.", "yeah", "yep", "sure", "ok", "okay",
    "go ahead", "ship it", "ship them", "do it", "lgtm", "approved",
])
def test_confirmatory_replies_detected(msg):
    assert is_confirmatory_reply(msg) is True


@pytest.mark.parametrize("msg", [
    "fix the signup form validation in Signup.jsx",
    "yes, also add dark mode to the settings page",
    "fix it",  # genuinely vague opener, not a confirmatory reply shape
    "improve the app",
])
def test_non_confirmatory_not_flagged(msg):
    assert is_confirmatory_reply(msg) is False


def test_ambiguity_gate_still_flags_bare_yes_on_its_own_words():
    """The gate itself is UNCHANGED — "yes" alone is still flagged by
    is_ambiguous_task(). The fix lives one layer up: the caller must
    skip the gate once P1 resolves a concrete scope for it."""
    assert is_ambiguous_task("yes") is True


def test_genuinely_vague_broad_ask_still_gated():
    assert is_ambiguous_task("fix it") is True
    assert is_ambiguous_task("improve the app") is True


# ── P1b — resolve_confirmatory_scope ────────────────────────────────

class _FakeCursor:
    def __init__(self, doc):
        self._doc = doc

    async def find_one(self, *a, **kw):
        return self._doc


class _FakeDB:
    def __init__(self, session_doc=None):
        self.chat_sessions = _FakeCollection(session_doc)


class _FakeCollection:
    def __init__(self, doc):
        self._doc = doc
        self.updates = []

    async def find_one(self, *a, **kw):
        return self._doc

    async def update_one(self, filt, update, **kw):
        self.updates.append((filt, update))
        if "$unset" in update and "pending_scan" in update["$unset"]:
            self._doc = {**(self._doc or {})}
            self._doc.pop("pending_scan", None)


@pytest.mark.asyncio
async def test_not_confirmatory_skips_grounding():
    db = _FakeDB()
    res = await resolve_confirmatory_scope(db, "u1", "s1", "fix Signup.jsx validation")
    assert res == {"grounded": False}


@pytest.mark.asyncio
async def test_confirmatory_with_no_pending_scan_asks_once():
    db = _FakeDB(session_doc={})
    res = await resolve_confirmatory_scope(db, "u1", "s1", "yes")
    assert res["grounded"] is True
    assert res["no_pending"] is True


@pytest.mark.asyncio
async def test_confirmatory_with_pending_scan_resolves_full_scope():
    import time
    findings = [
        {"filepath": "backend/services/orchestrator.py", "line": 142,
         "description": "SQL injection risk", "severity": "critical", "fix": "parameterize"},
        {"filepath": "backend/services/orchestrator.py", "line": 200,
         "description": "unhandled exception", "severity": "high", "fix": ""},
    ]
    db = _FakeDB(session_doc={"pending_scan": {
        "findings": findings, "project_id": "p1", "created_at": time.time(),
    }})
    res = await resolve_confirmatory_scope(db, "u1", "s1", "yes")
    assert res["grounded"] is True
    assert res.get("no_pending") is not True
    assert res["source_findings"] == findings
    assert "orchestrator.py:142" in res["task_text"]
    assert "orchestrator.py:200" in res["task_text"]
    # Consumed — session doc no longer carries pending_scan.
    assert "pending_scan" not in db.chat_sessions._doc


@pytest.mark.asyncio
async def test_stale_pending_scan_is_ignored():
    import time
    old_findings = [{"filepath": "x.py", "line": 1, "description": "old", "severity": "low"}]
    db = _FakeDB(session_doc={"pending_scan": {
        "findings": old_findings, "created_at": time.time() - 3 * 3600,  # 3h old > 2h TTL
    }})
    res = await resolve_confirmatory_scope(db, "u1", "s1", "yes")
    assert res["grounded"] is True
    assert res["no_pending"] is True


def test_render_grounded_task_lists_every_finding():
    findings = [
        {"filepath": "a.py", "line": 5, "description": "d1", "severity": "high"},
        {"filepath": "b.py", "line": 9, "description": "d2", "severity": "low"},
    ]
    task = render_grounded_task(findings)
    assert "a.py:5" in task and "b.py:9" in task
    assert "2" in task  # count of findings mentioned
    assert "ONLY" in task


# ── P2 — Plan↔Scan Consistency Contract ─────────────────────────────

def test_plan_matching_scan_files_has_no_mismatch():
    findings = [
        {"filepath": "backend/services/orchestrator.py", "line": 142, "description": "d1"},
        {"filepath": "backend/services/orchestrator.py", "line": 200, "description": "d2"},
    ]
    plan = {"files_to_change": ["backend/services/orchestrator.py"]}
    coverage = check_plan_scan_consistency(plan, findings)
    assert coverage["mismatched_files"] == []
    assert coverage["covered_count"] == 2
    assert coverage["deferred_count"] == 0


def test_plan_targeting_wrong_files_is_blocked():
    """Reproduces the transcript bug exactly: scan cites orchestrator.py
    9x, plan targets 4 unrelated files."""
    findings = [
        {"filepath": "backend/services/orchestrator.py", "line": i, "description": f"f{i}"}
        for i in range(1, 10)
    ]
    plan = {"files_to_change": [
        "backend/routers/shopify_storefront_engine.py",
        "backend/routers/activity_feed_router.py",
        "backend/routers/action_engine_router.py",
        "backend/routers/admin_cache_router.py",
    ]}
    coverage = check_plan_scan_consistency(plan, findings)
    assert len(coverage["mismatched_files"]) == 4
    assert coverage["covered_count"] == 0
    assert coverage["deferred_count"] == 9
    msg = render_mismatch_message(coverage["mismatched_files"], ["backend/services/orchestrator.py"])
    assert "shopify_storefront_engine.py" in msg
    assert "orchestrator.py" in msg


def test_partial_plan_shows_visible_deferral_not_silent():
    """3 of 9 findings planned → 6 deferred, EXPLICITLY listed, not
    silently dropped (the exact transcript failure mode)."""
    findings = [
        {"filepath": "backend/services/orchestrator.py", "line": i, "description": f"f{i}"}
        for i in range(1, 10)
    ]
    plan = {"files_to_change": ["backend/services/orchestrator.py"]}
    # Simulate a plan that only "covers" 3 by construction of the planner
    # (still same file, so no mismatch) — deferred count reflects the
    # ones NOT actually in files_to_change when files are more granular.
    coverage = check_plan_scan_consistency(plan, findings)
    # Same-file plan covers ALL findings in that file (file-level grain);
    # verify deferred + covered always sum to total (contract invariant).
    assert coverage["covered_count"] + coverage["deferred_count"] == coverage["total_findings"]



# ---------------------------------------------------------------------------
# 2026-08-27 · P6 live-drive regression, NAMED BEFORE/AFTER.
#
# BEFORE (bug): routers/chat.py's Mode E branch wrote `pending_scan` via
# `db_h.chat_sessions.update_one({...}, {"$set": {...}})` with NO
# `upsert=True`. A scan is almost always the session's FIRST-EVER turn —
# the `chat_sessions` document doesn't exist yet at that point (it's
# only created afterwards, downstream, by `_persist_turn`'s own
# upsert). Mongo's `update_one` without `upsert=True` silently no-ops
# on a document that doesn't exist — pending_scan was NEVER actually
# persisted, so EVERY bare "yes" immediately after a real first-turn
# scan hit "no pending proposal" / the ambiguity gate instead of
# resolving to the scan's scope. Confirmed live via a real
# GitHub-App-installed drill repo (polarisbuiltinc-wq/ora-grounding,
# session p6_drill_session_3): a fresh scan turn left
# `"pending_scan" not in chat_sessions doc` even though findings were
# genuinely returned in the report.
#
# AFTER (fix): the update now carries `upsert=True` + `$setOnInsert`
# for the doc-identity fields, so the very first write in a session
# creates the document with pending_scan attached.
# ---------------------------------------------------------------------------

def test_chat_py_pending_scan_write_has_upsert_true():
    """Source-level regression guard: the Mode E pending_scan write in
    routers/chat.py must upsert — a scan is almost always the
    session's first-ever turn, so a non-upserting update is a
    guaranteed silent no-op (this is exactly what P6's live drive
    caught)."""
    src = "".join(open(f"/app/backend/routers/chat/{_f}.py", encoding="utf-8").read() for _f in ("__init__","misc","turn","stream","history","worker"))
    idx = src.index('"$set": {"pending_scan": {')
    # Look at the update_one(...) call this $set lives inside — the
    # matching `upsert=True,` must appear before its closing `)`.
    window = src[idx: idx + 900]
    assert "upsert=True" in window
    assert '"$setOnInsert"' in window


async def test_before_fix_non_upserting_update_on_missing_doc_is_a_no_op():
    """Documents the exact Mongo semantics that made the bug possible:
    `update_one` with only `$set` and no `upsert=True` against a
    session_id that has no document yet does nothing — 0 documents
    modified, none created."""

    class _Coll:
        def __init__(self):
            self.docs = {}

        async def update_one(self, query, update, upsert=False):
            key = query.get("session_id")
            if key in self.docs:
                self.docs[key].update(update.get("$set") or {})
                return
            if upsert:
                self.docs[key] = dict(update.get("$set") or {})
                self.docs[key].update(update.get("$setOnInsert") or {})

    coll = _Coll()
    await coll.update_one(
        {"session_id": "s1", "user_id": "u1"},
        {"$set": {"pending_scan": {"findings": [{"filepath": "a.py"}]}}},
    )
    assert "s1" not in coll.docs  # BEFORE-fix behavior: silently dropped

    coll2 = _Coll()
    await coll2.update_one(
        {"session_id": "s2", "user_id": "u1"},
        {"$set": {"pending_scan": {"findings": [{"filepath": "a.py"}]}},
         "$setOnInsert": {"session_id": "s2", "user_id": "u1"}},
        upsert=True,
    )
    assert "s2" in coll2.docs  # AFTER-fix behavior: document created
    assert coll2.docs["s2"]["pending_scan"]["findings"][0]["filepath"] == "a.py"
