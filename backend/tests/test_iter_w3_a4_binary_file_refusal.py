"""
tests/test_iter_w3_a4_binary_file_refusal.py — Part B · W3 · 2026-08

A4 — binary/legacy-encoding file corruption fix.

Bug (live-reproduced 2026-08, see memory/W3_LANGUAGE_SUPPORT_A4_
BINARY_FIX_2026_08.md): `services/github_api_writer.py::fetch_file()`
unconditionally did `base64.b64decode(...).decode("utf-8",
errors="replace")`. A real binary file (image, pdf, zip, .git pack)
silently decoded into U+FFFD replacement-character garbage — not
rejected, not flagged, not crashed. That corrupted text then flowed
into the LLM rewrite prompt and got committed back as the "edited"
file, permanently destroying the original bytes. `verify_files()`
can't catch it either — no linter maps binary extensions, so it
auto-skips with `ok: True`.

Fix: `fetch_file()` now detects binary content (NUL byte in the first
8 KiB — the standard heuristic; decode-failure alone is NOT used,
since legitimate Latin-1/Cp1252 TEXT also fails strict UTF-8) and
raises a typed `BinaryFileError`/`UnsupportedEncodingError`
(core/errors.py) instead of silently corrupting. The real Execute
path (`services/loop_engine.py::_gen_via_parliament`) and the
(currently unused-in-production) `services/loop_execute.py::
_generate_one_inner` both catch these specifically — BEFORE their
existing generic `except Exception: current = ""` — and skip that
ONE file with a clear, typed refusal message instead of treating it
as a blank new file for the LLM to fill in.
"""
from __future__ import annotations

import asyncio
import base64
import struct
import zlib

import httpx
import pytest


def _make_real_png() -> bytes:
    """A genuinely valid, minimal 1x1 PNG — real binary content with
    real NUL bytes, not a .txt renamed to .png."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data)))
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    idat = chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


_PNG_BYTES = _make_real_png()
_LATIN1_BYTES = "café resume - naive".encode("latin-1")  # not valid UTF-8
_PY_TEXT = "def hello():\n    return 'hi'\n"


def _mock_transport(paths_to_bytes: dict[str, bytes]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "GET" and "/contents/" in url:
            for p, raw in paths_to_bytes.items():
                if p in url:
                    return httpx.Response(200, json={
                        "encoding": "base64",
                        "content": base64.b64encode(raw).decode("ascii"),
                    })
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(404, json={"message": "not mocked"})
    return httpx.MockTransport(handler)


# ═══════════════════════════════════════════════════════════════════
# Unit-level proof directly on fetch_file() — the choke point itself
# ═══════════════════════════════════════════════════════════════════
def test_a4a_fetch_file_rejects_real_binary_png(monkeypatch):
    """T-A4a (unit level) — a real binary fixture through the real
    fetch_file() raises BinaryFileError, not a corrupted string."""
    from services import github_api_writer as gw
    from core.errors import BinaryFileError

    transport = _mock_transport({"logo.png": _PNG_BYTES})
    orig = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda *a, **kw: orig(*a, **{**kw, "transport": transport}))

    with pytest.raises(BinaryFileError) as ei:
        asyncio.run(gw.fetch_file("acme", "repo", "logo.png", "main", "tok"))
    assert ei.value.path == "logo.png"


def test_a4b_binary_content_never_written_back_byte_identical(monkeypatch):
    """T-A4b — the exact regression this whole fix exists for: prove
    the original bytes are NEVER corrupted. Since fetch_file() now
    raises instead of returning a string, there is no string for any
    caller to re-encode and commit — assert directly that no decoded
    value is ever produced (hash-before/after is meaningless once the
    read itself refuses; the invariant is simply: no content, no
    write)."""
    from services import github_api_writer as gw
    from core.errors import BinaryFileError
    import hashlib

    orig_hash = hashlib.sha256(_PNG_BYTES).hexdigest()
    transport = _mock_transport({"logo.png": _PNG_BYTES})
    orig = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda *a, **kw: orig(*a, **{**kw, "transport": transport}))

    decoded = None
    try:
        decoded = asyncio.run(
            gw.fetch_file("acme", "repo", "logo.png", "main", "tok"))
    except BinaryFileError:
        pass
    assert decoded is None, (
        "fetch_file must never return a decoded string for binary "
        "content — any non-None return here is exactly the corrupted "
        "write-back bug this fix closes."
    )
    # The original bytes on disk (simulated by our fixture) are
    # untouched — nothing downstream ever saw them to corrupt.
    assert hashlib.sha256(_PNG_BYTES).hexdigest() == orig_hash


def test_a4c_real_text_file_still_reads_fine(monkeypatch):
    """T-A4c — no over-blocking: a real .py file still decodes
    normally through the exact same real fetch_file() path."""
    from services import github_api_writer as gw

    transport = _mock_transport({"app.py": _PY_TEXT.encode("utf-8")})
    orig = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda *a, **kw: orig(*a, **{**kw, "transport": transport}))

    result = asyncio.run(
        gw.fetch_file("acme", "repo", "app.py", "main", "tok"))
    assert result == _PY_TEXT


def test_a4d_legacy_encoding_never_writes_u_fffd(monkeypatch):
    """T-A4d — a real Latin-1 fixture (fails strict UTF-8, has NO NUL
    byte, so it must NOT be misclassified as binary) raises
    UnsupportedEncodingError. The invariant: U+FFFD is never returned."""
    from services import github_api_writer as gw
    from core.errors import UnsupportedEncodingError

    assert b"\x00" not in _LATIN1_BYTES, "fixture must have no NUL byte"
    transport = _mock_transport({"legacy.txt": _LATIN1_BYTES})
    orig = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda *a, **kw: orig(*a, **{**kw, "transport": transport}))

    result = None
    with pytest.raises(UnsupportedEncodingError) as ei:
        result = asyncio.run(
            gw.fetch_file("acme", "repo", "legacy.txt", "main", "tok"))
    assert ei.value.path == "legacy.txt"
    assert result is None
    # The whole point: U+FFFD must never be the return value.
    assert result != _LATIN1_BYTES.decode("utf-8", errors="replace")


# ═══════════════════════════════════════════════════════════════════
# Engine-level proof — the REAL live Execute path (_gen_via_parliament)
# ═══════════════════════════════════════════════════════════════════
class _Coll:
    def __init__(self):
        self.rows: list[dict] = []

    async def insert_one(self, doc):
        self.rows.append(dict(doc))
        return type("R", (), {"inserted_id": "x"})()

    async def update_one(self, *a, **kw):
        return type("R", (), {"matched_count": 1, "modified_count": 1})()

    async def find_one(self, *a, **kw):
        return None

    async def find_one_and_update(self, *a, **kw):
        return None

    async def create_index(self, *a, **kw):
        return None

    async def replace_one(self, *a, **kw):
        return type("R", (), {"matched_count": 1, "modified_count": 1})()


class _StubDB:
    def __init__(self):
        self.loop_events   = _Coll()
        self.loop_sessions = _Coll()
        self.loop_run_log  = _Coll()
        self.cto_projects  = _Coll()
        self.dev_users     = _Coll()


class _BinCtx:
    repo_owner = "owner"
    repo_name  = "repo"
    branch     = "main"
    pat        = "ghp_test"


def test_a4_real_execute_path_skips_binary_generates_text(monkeypatch):
    """Engine-level proof through the REAL live path
    (`LoopEngine._do_execute` → `_gen_via_parliament` →
    `github_api_writer.fetch_file`), no mocked business logic — only
    the GitHub HTTP transport and the Parliament LLM call are faked.

    3 planned files: a binary PNG, a Latin-1 legacy-encoded text file,
    and a real .py file. Asserts:
      - the binary + legacy files are ABSENT from
        `engine.context["submitted_files"]` (never staged for commit)
      - each got a typed `file_not_editable` emit with the correct
        `error_code`
      - the real .py file generated normally (no over-blocking)
    """
    from services.loop_engine import LoopEngine, LoopState
    from services import file_selector as _fs
    from services import loop_task_specs as _lts
    from core import parliament as _pmod

    db = _StubDB()
    engine = LoopEngine(
        db=db, loop_id="loop-a4-1", user_id="u1", project_id="p1",
        user_message="translate the button label", bin_ctx=_BinCtx(),
    )
    engine.context["plan"] = {
        "title": "i18n button label",
        "files_to_change": ["assets/logo.png", "legacy_readme.txt",
                            "app.py"],
    }

    async def _fake_get(_db, _loop_id):
        return None
    monkeypatch.setattr(_lts, "get", _fake_get)

    async def _fake_sel(**_kw):
        return {"has_graph": False, "candidates": [], "skipped": []}
    monkeypatch.setattr(_fs, "select_relevant_files", _fake_sel)

    async def _fake_run(self, *, task, context):
        return {"status": "success",
                "output": "def hello():\n    return 'translated'\n"}
    monkeypatch.setattr(_pmod.Parliament, "run", _fake_run)

    raw_by_path = {
        "assets/logo.png":   _PNG_BYTES,
        "legacy_readme.txt": _LATIN1_BYTES,
        "app.py":            _PY_TEXT.encode("utf-8"),
    }
    transport = _mock_transport(raw_by_path)
    orig = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda *a, **kw: orig(*a, **{**kw, "transport": transport}))

    emitted: list[dict] = []
    orig_emit = engine._emit
    async def spy_emit(state, phase, **kwargs):
        emitted.append({"state": state, "phase": phase,
                        "message": kwargs.get("message"),
                        "data": dict(kwargs.get("data") or {})})
        return await orig_emit(state, phase, **kwargs)
    monkeypatch.setattr(engine, "_emit", spy_emit)

    asyncio.run(engine._do_execute())

    submitted = engine.context.get("submitted_files") or []
    submitted_paths = {f["path"] for f in submitted}

    # T-A4b (engine level) — never staged for commit.
    assert "assets/logo.png" not in submitted_paths, (
        f"binary file must never reach submitted_files (would be "
        f"committed by _do_ship): {submitted_paths}"
    )
    assert "legacy_readme.txt" not in submitted_paths, (
        f"legacy-encoding file must never reach submitted_files: "
        f"{submitted_paths}"
    )
    # T-A4c — real text file generated normally, no over-blocking.
    assert "app.py" in submitted_paths, (
        f"a real .py file must still generate through the same path: "
        f"{submitted_paths}"
    )

    refusal_events = [e for e in emitted
                     if e["data"].get("sub_step") == "file_not_editable"]
    refusals_by_path = {e["data"]["file"]: e for e in refusal_events}

    # T-A4a — typed refusal for the binary file.
    assert "assets/logo.png" in refusals_by_path, (
        f"expected a file_not_editable refusal for the binary file, "
        f"got: {[e['data'] for e in emitted]}"
    )
    assert (refusals_by_path["assets/logo.png"]["data"]["error_code"]
            == "FILE_BINARY_NOT_EDITABLE")
    assert "binary" in refusals_by_path["assets/logo.png"]["message"].lower()

    # T-A4d — typed refusal for the legacy-encoding file.
    assert "legacy_readme.txt" in refusals_by_path, (
        f"expected a file_not_editable refusal for the legacy-encoding "
        f"file, got: {[e['data'] for e in emitted]}"
    )
    assert (refusals_by_path["legacy_readme.txt"]["data"]["error_code"]
            == "FILE_ENCODING_UNSUPPORTED")

    # No crash, no raw traceback anywhere in any emitted message.
    for e in emitted:
        assert "Traceback" not in (e["message"] or "")
        assert "BinaryFileError" not in (e["message"] or "")
        assert "UnsupportedEncodingError" not in (e["message"] or "")
