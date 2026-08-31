"""
tests/test_palette_nudge_chat_visibility_2026_09_01.py

NEXT ROUND Item 2 — when the contrast guard nudges a palette inside
write_repo_file, that nudge must be VISIBLE to the owner in chat (an
inline before/after swatch pair + a plain-English note), not just in
the logs. Verifies:
  t_palette_nudge_shows_inline_before_after — write_repo_file's ctx
    carries a `palette_nudges` entry with before/after hex + a note.
  t_palette_note_no_jargon — the note never uses WCAG/luminance/token
    words; a non-technical owner should be able to read it plainly.
"""
import pytest

import services.local_tools as lt
from services.contrast_guard import describe_nudge


@pytest.mark.asyncio
async def test_t_palette_nudge_shows_inline_before_after(monkeypatch):
    def _stub_repo_ctx(ctx):
        return {
            "ok": True,
            "owner": "test-owner", "repo": "test-repo",
            "branch": "main", "token": "FAKE_TOKEN",
            "is_founder": False, "bin_id": "u1", "pid": "p1",
        }
    monkeypatch.setattr(lt, "_repo_ctx_from", _stub_repo_ctx)

    async def _stub_commit(**kwargs):
        return {"sha": "abc123", "html_url": "https://x", "path": "theme.css"}
    monkeypatch.setattr(
        "services.github_api_writer.commit_files", _stub_commit, raising=False,
    )
    monkeypatch.setattr(
        "services.vanguard_scanner.scan_file_blocks", lambda blocks: [], raising=False,
    )
    monkeypatch.setattr(
        "services.vanguard_scanner.has_critical", lambda findings: False, raising=False,
    )

    unreadable_css = ":root { --text-color: #cccccc; --background: #ffffff; }"
    ctx = {"user_id": "u1", "project_id": "p1"}
    res = await lt.write_repo_file(
        ctx=ctx, args={"path": "theme.css", "content": unreadable_css},
    )
    assert res["ok"] is True

    nudges = ctx.get("palette_nudges")
    assert nudges, "expected a palette_nudges entry for the owner to see in chat"
    entry = nudges[0]
    assert entry["before_hex"].lower() == "#cccccc"
    assert entry["after_hex"] != entry["before_hex"]
    assert entry["before_ratio"] < 4.5 <= entry["after_ratio"]
    assert entry["note"] and isinstance(entry["note"], str)


@pytest.mark.asyncio
async def test_t_palette_note_no_jargon(monkeypatch):
    def _stub_repo_ctx(ctx):
        return {
            "ok": True,
            "owner": "test-owner", "repo": "test-repo",
            "branch": "main", "token": "FAKE_TOKEN",
            "is_founder": False, "bin_id": "u1", "pid": "p1",
        }
    monkeypatch.setattr(lt, "_repo_ctx_from", _stub_repo_ctx)

    async def _stub_commit(**kwargs):
        return {"sha": "abc123", "html_url": "https://x", "path": "theme.css"}
    monkeypatch.setattr(
        "services.github_api_writer.commit_files", _stub_commit, raising=False,
    )
    monkeypatch.setattr(
        "services.vanguard_scanner.scan_file_blocks", lambda blocks: [], raising=False,
    )
    monkeypatch.setattr(
        "services.vanguard_scanner.has_critical", lambda findings: False, raising=False,
    )

    unreadable_css = ":root { --text-color: #b0b0b0; --background: #fafafa; }"
    ctx = {"user_id": "u1", "project_id": "p1"}
    await lt.write_repo_file(
        ctx=ctx, args={"path": "theme.css", "content": unreadable_css},
    )
    note = ctx["palette_nudges"][0]["note"].lower()
    for banned in ("wcag", "luminance", "token", "--text-color", "--background"):
        assert banned not in note, f"note leaked jargon word: {banned!r}"


def test_describe_nudge_is_deterministic():
    adj = {"original_fg": "#cccccc", "nudged_fg": "#5c5c5c",
           "before_ratio": 1.6, "after_ratio": 4.6}
    a = describe_nudge(adj)
    b = describe_nudge(adj)
    assert a == b
    assert "wcag" not in a.lower() and "luminance" not in a.lower() and "token" not in a.lower()
