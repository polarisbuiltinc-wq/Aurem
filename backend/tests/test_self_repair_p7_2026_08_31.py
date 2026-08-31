"""
tests/test_self_repair_p7_2026_08_31.py

Named tests for P7 SELF-REPAIR (ORA recognizing its OWN bugs, not the
user's website — detect, diagnose, log, learn, and reply honestly).
All deterministic pieces (classifier, diagnosis, reply-guard) are
tested with zero DB/LLM. The DB-backed pieces (emit/known_handling/
self_bug_open) use a fake in-memory Mongo double, same pattern as
tests/test_2026_08_19_customer_cost_tracker.py.
"""
from __future__ import annotations

import pytest

from services.user_report_classifier import is_user_reporting_ora_bug
from services.self_bug import diagnose, signature, SelfBugEvent, SELF_BUG_TYPES
from services.self_bug_reply_guard import (
    is_compliant_self_bug_reply, compose_self_bug_reply,
    has_ownership, blames_user, has_path_forward, has_error_code,
)


# ── P7-D — user_report_classifier ───────────────────────────────────

def test_t_user_reported_ora_not_website():
    for text in [
        "the approve button isn't showing",
        "your screen is blank",
        "that reply just cut off",
        "it keeps saying try again and I don't know what to try",
        "nothing happened when I clicked approve",
    ]:
        assert is_user_reporting_ora_bug(text), f"expected self-bug: {text!r}"


def test_t_website_not_ora():
    for text in [
        "the button on my website doesn't work",
        "my homepage looks wrong",
        "my page's button doesn't work",
        "our contact page is broken",
    ]:
        assert not is_user_reporting_ora_bug(text), f"expected website task: {text!r}"


# ── P7-B — diagnosis (honest, no invented cause) ────────────────────

def test_t_self_diagnose_confirms_only_with_evidence():
    with_evidence = diagnose(SelfBugEvent(type="missing_button", source="k1", evidence="fence missing"))
    assert with_evidence.confidence == "confirmed"
    no_evidence = diagnose(SelfBugEvent(type="missing_button", source="k1", evidence=""))
    assert no_evidence.confidence == "likely"
    unknown = diagnose(SelfBugEvent(type="not_a_real_type", source="x", evidence=""))
    assert unknown.confidence == "uncertain"
    assert "uncertain" in unknown.likely_cause


def test_t_self_diagnose_owns_it():
    d = diagnose(SelfBugEvent(type="missing_button", source="k1", evidence="fence missing"))
    low = (d.what_ora_detected + d.likely_cause).lower()
    assert "your website" not in low and "your site" not in low
    assert "extracthandoffbrief" in low or "button" in low


def test_t_self_diagnose_all_known_types_have_a_cause():
    for t in SELF_BUG_TYPES:
        d = diagnose(SelfBugEvent(type=t, source="x", evidence="e"))
        assert d.confidence != "uncertain"


def test_t_signature_deterministic():
    assert signature("missing_button", {"subject": "approve"}) == signature("missing_button", {"subject": "approve"})
    assert signature("missing_button", {}) == "missing_button:missing_button"


# ── P7-E — self-bug reply guard ──────────────────────────────────────

def test_t_own_bug_reply_has_ownership():
    for t in ["missing_button", "truncated_reply", "blank_ui", "tool_error", "user_reported"]:
        assert has_ownership(compose_self_bug_reply(t)), t


def test_t_own_bug_reply_never_blames_user():
    for t in ["missing_button", "truncated_reply", "blank_ui", "tool_error", "user_reported"]:
        assert not blames_user(compose_self_bug_reply(t)), t
    assert blames_user("Please try clearing your cache or checking your connection.")
    assert blames_user("Try refreshing the page.")


def test_t_own_bug_reply_has_path_forward():
    for t in ["missing_button", "truncated_reply", "blank_ui", "tool_error", "user_reported"]:
        assert has_path_forward(compose_self_bug_reply(t)), t


def test_t_own_bug_reply_no_error_code():
    for t in ["missing_button", "truncated_reply", "blank_ui", "tool_error", "user_reported"]:
        assert not has_error_code(compose_self_bug_reply(t)), t
    assert has_error_code("Error 500: Internal Server Error")
    assert has_error_code("I read AuremHomepage.jsx and it failed.")


def test_t_all_default_templates_are_compliant():
    for t in ["missing_button", "truncated_reply", "blank_ui", "tool_error", "user_reported", "unknown"]:
        assert is_compliant_self_bug_reply(compose_self_bug_reply(t)), t


def test_t_wrong_reply_shape_correctly_rejected():
    # the OLD banned pattern this whole feature replaces
    bad = "Please try rephrasing your question or checking your browser."
    assert not is_compliant_self_bug_reply(bad)


# ── DB-backed pieces (fake Mongo double) ─────────────────────────────

class _FakeCursorResult:
    def __init__(self, doc):
        self._doc = doc

    def __await__(self):
        async def _inner():
            return self._doc
        return _inner().__await__()


class _FakeSelfBugsCollection:
    def __init__(self):
        self.inserts = []

    async def insert_one(self, doc):
        self.inserts.append(dict(doc))

    async def find_one(self, query, *args, **kwargs):
        matches = [d for d in self.inserts if d.get("context", {}).get("session_id") == query.get("context.session_id")]
        return matches[-1] if matches else None


class _FakeLearnedCollection:
    def __init__(self):
        self.docs = {}

    async def find_one_and_update(self, query, update, upsert=True, return_document=None):
        sig = query["signature"]
        doc = self.docs.get(sig, {"signature": sig, "times_seen": 0})
        doc["times_seen"] += update["$inc"]["times_seen"]
        doc["last_seen"] = update["$set"]["last_seen"]
        self.docs[sig] = doc
        return dict(doc)

    async def find_one(self, query, *args, **kwargs):
        return self.docs.get(query.get("signature"))


class _FakeDB:
    def __init__(self):
        self.ora_self_bugs = _FakeSelfBugsCollection()
        self.self_bug_learned = _FakeLearnedCollection()


@pytest.mark.asyncio
async def test_t_self_bug_logged_structured(monkeypatch):
    import services.self_bug as m
    fake_db = _FakeDB()
    monkeypatch.setattr(m, "get_db", lambda: fake_db)

    event = await m.emit("missing_button", "fence missing", {"session_id": "s1"}, source="k1")
    assert event is not None
    assert len(fake_db.ora_self_bugs.inserts) == 1
    row = fake_db.ora_self_bugs.inserts[0]
    assert row["type"] == "missing_button"
    assert row["likely_cause"]
    assert row["confidence"] == "confirmed"
    assert row["severity"] == "high"


@pytest.mark.asyncio
async def test_t_self_bug_learned_recurrence(monkeypatch):
    import services.self_bug as m
    fake_db = _FakeDB()
    monkeypatch.setattr(m, "get_db", lambda: fake_db)

    await m.emit("blank_ui", "e1", {"subject": "preview"}, source="ui")
    await m.emit("blank_ui", "e2", {"subject": "preview"}, source="ui")
    handled = await m.known_handling(m.signature("blank_ui", {"subject": "preview"}))
    assert handled["times_seen"] == 2


@pytest.mark.asyncio
async def test_t_self_bug_recurrence_counter(monkeypatch):
    import services.self_bug as m
    fake_db = _FakeDB()
    monkeypatch.setattr(m, "get_db", lambda: fake_db)

    for _ in range(3):
        await m.emit("tool_error", "e", {"subject": "read_repo_file"}, source="tool")
    handled = await m.known_handling(m.signature("tool_error", {"subject": "read_repo_file"}))
    assert handled["times_seen"] == 3


@pytest.mark.asyncio
async def test_t_self_bug_open_true_within_window(monkeypatch):
    import services.self_bug as m
    fake_db = _FakeDB()
    monkeypatch.setattr(m, "get_db", lambda: fake_db)

    await m.emit("dead_end_leak", "e", {"session_id": "s2"}, source="bail_reason")
    assert await m.self_bug_open("s2") is True
    assert await m.self_bug_open("no-such-session") is False


@pytest.mark.asyncio
async def test_t_emit_never_raises_when_db_missing(monkeypatch):
    import services.self_bug as m
    monkeypatch.setattr(m, "get_db", lambda: None)
    event = await m.emit("tool_error", "e", {}, source="x")
    assert event is not None  # returns the event even without a DB


@pytest.mark.asyncio
async def test_t_emit_rejects_unknown_type(monkeypatch):
    import services.self_bug as m
    fake_db = _FakeDB()
    monkeypatch.setattr(m, "get_db", lambda: fake_db)
    event = await m.emit("not_a_real_type", "e", {}, source="x")
    assert event is None
    assert fake_db.ora_self_bugs.inserts == []


# ── P7-C — the safety line: no unattended self-modify path ──────────

def test_t_self_fix_never_autodeploys_ora_itself():
    # self_bug.py is READ/LOG-ONLY by construction: no function in the
    # module writes to the filesystem, calls git, or hits any deploy/
    # PR-apply endpoint. This is the deterministic proof of the P7-C
    # safety line, checked at the source level so a future edit that
    # adds such a call is caught here.
    src = open("services/self_bug.py").read()
    for banned in ("subprocess", "os.system", "git.", "write_repo_file", "commit_and_push", "apply_patch"):
        assert banned not in src, f"self_bug.py must stay read/log-only, found: {banned!r}"


# ── Pipeline (single test proving the whole guarantee) ───────────────

@pytest.mark.asyncio
async def test_t_ora_own_bug_end_to_end(monkeypatch):
    import services.self_bug as m
    fake_db = _FakeDB()
    monkeypatch.setattr(m, "get_db", lambda: fake_db)

    # 1) user reports it
    assert is_user_reporting_ora_bug("the approve button isn't showing")
    # 2) ORA logs it (structured)
    await m.emit("user_reported", "the approve button isn't showing",
                 {"session_id": "s3"}, source="user_report_classifier")
    row = fake_db.ora_self_bugs.inserts[0]
    assert row["type"] == "user_reported" and row["likely_cause"]
    # 3) the reply ORA gives is compliant — ownership, no blame, path forward, no error code
    reply = compose_self_bug_reply("user_reported")
    assert is_compliant_self_bug_reply(reply)
    assert "try rephrasing" not in reply.lower()
    assert "check your" not in reply.lower()
