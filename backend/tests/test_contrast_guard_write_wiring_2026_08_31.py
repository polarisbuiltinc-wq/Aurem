"""
tests/test_contrast_guard_write_wiring_2026_08_31.py

Item 2 (2026-08-31) — end-to-end: write_repo_file must nudge an
unreadable text/background color pair in a .css write BEFORE the
commit happens, and the COMMITTED content must carry the nudged
color, not the original. Mirrors the existing syntax-gate wiring
test's mocking pattern (tests/test_iter212m152_prompt_mode_gaps.py).
"""
import pytest

import services.local_tools as lt


@pytest.mark.asyncio
async def test_write_repo_file_nudges_unreadable_css_before_commit(monkeypatch):
    def _stub_repo_ctx(ctx):
        return {
            "ok": True,
            "owner": "test-owner", "repo": "test-repo",
            "branch": "main", "token": "FAKE_TOKEN",
            "is_founder": False, "bin_id": "u1", "pid": "p1",
        }
    monkeypatch.setattr(lt, "_repo_ctx_from", _stub_repo_ctx)

    committed = {}

    async def _stub_commit(**kwargs):
        committed["files"] = kwargs.get("files")
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
    res = await lt.write_repo_file(
        ctx={"user_id": "u1", "project_id": "p1"},
        args={"path": "theme.css", "content": unreadable_css},
    )

    assert res["ok"] is True
    assert res["contrast_adjustments"], "expected a contrast adjustment to be reported"
    committed_content = committed["files"]["theme.css"]
    assert "#cccccc" not in committed_content, "unreadable color must never reach the commit"

    from services.contrast_guard import contrast_ratio, WCAG_AA_NORMAL_TEXT
    import re
    nudged_hex = re.search(r"--text-color:\s*(#[0-9a-fA-F]{6})", committed_content).group(1)
    assert contrast_ratio(nudged_hex, "#ffffff") >= WCAG_AA_NORMAL_TEXT


@pytest.mark.asyncio
async def test_write_repo_file_noop_for_non_css_paths(monkeypatch):
    def _stub_repo_ctx(ctx):
        return {
            "ok": True,
            "owner": "test-owner", "repo": "test-repo",
            "branch": "main", "token": "FAKE_TOKEN",
            "is_founder": False, "bin_id": "u1", "pid": "p1",
        }
    monkeypatch.setattr(lt, "_repo_ctx_from", _stub_repo_ctx)
    async def _stub_commit(**kwargs):
        return {"sha": "abc", "html_url": "x", "path": "x"}
    monkeypatch.setattr(
        "services.github_api_writer.commit_files", _stub_commit, raising=False,
    )
    monkeypatch.setattr(
        "services.vanguard_scanner.scan_file_blocks", lambda blocks: [], raising=False,
    )
    monkeypatch.setattr(
        "services.vanguard_scanner.has_critical", lambda findings: False, raising=False,
    )
    res = await lt.write_repo_file(
        ctx={"user_id": "u1", "project_id": "p1"},
        args={"path": "about.md", "content": "# About\n\n--text-color: #cccccc;\n"},
    )
    assert res.get("contrast_adjustments") is None
