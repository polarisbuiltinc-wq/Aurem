"""Verify the `.build_info` + `.git/HEAD` file-based BUILD_HASH workaround.

Rationale (Feb 2026 handoff — 'Option B / BUILD_HASH not set on Prod'):
    Emergent prod pods lack the `git` binary AND sometimes lack `.git/`.
    The `_resolve_build_hash()` ladder now has TWO new file-based steps
    between the env-var check and the mtime fallback:

      • Priority 2 — read `backend/.build_info` (build artifact)
      • Priority 4 — raw parse of `.git/HEAD` (no git binary)

    This test locks in each path so a future refactor cannot silently
    revert to mtime-based hashes on prod.

Zero-mocks rule: we manipulate real files and re-invoke the real
resolver by clearing its module-level cache.
"""
from __future__ import annotations

import os
import sys
import pathlib

import pytest


BACKEND_DIR = pathlib.Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
BUILD_INFO = BACKEND_DIR / ".build_info"


@pytest.fixture()
def _reset_cache(monkeypatch):
    """Ensure each test hits the resolver from a clean cache."""
    sys.path.insert(0, str(BACKEND_DIR))
    import main as main_mod

    original = main_mod._BUILD_HASH
    main_mod._BUILD_HASH = None
    original_env = {
        k: os.environ.pop(k, None)
        for k in ("BUILD_HASH", "GIT_COMMIT", "VERCEL_GIT_COMMIT_SHA")
    }

    yield main_mod

    main_mod._BUILD_HASH = original
    for k, v in original_env.items():
        if v is not None:
            os.environ[k] = v


def test_priority_2_build_info_file_wins_over_git(_reset_cache, tmp_path):
    """`backend/.build_info` should short-circuit git binary + fallback."""
    main_mod = _reset_cache
    BUILD_INFO.write_text("deadbee\n", encoding="utf-8")
    try:
        result = main_mod._resolve_build_hash()
        assert result == "deadbee", (
            f"Expected .build_info override 'deadbee', got {result!r}"
        )
    finally:
        BUILD_INFO.unlink(missing_ok=True)


def test_priority_4_raw_git_head_read(_reset_cache):
    """`_read_git_head_file` should return a 7-char short SHA."""
    main_mod = _reset_cache
    sha = main_mod._read_git_head_file(str(REPO_ROOT))
    assert sha is not None, "Raw .git/HEAD read failed — is .git/ present?"
    assert len(sha) == 7, f"Expected 7-char short SHA, got {sha!r}"
    assert all(c in "0123456789abcdef" for c in sha), (
        f"Non-hex chars in SHA: {sha!r}"
    )


def test_env_var_beats_build_info(_reset_cache):
    """Explicit BUILD_HASH env var wins over .build_info (priority 1)."""
    main_mod = _reset_cache
    BUILD_INFO.write_text("fromfile1234\n", encoding="utf-8")
    os.environ["BUILD_HASH"] = "envwins1234"
    try:
        result = main_mod._resolve_build_hash()
        assert result == "envwins", (
            f"Env var should beat .build_info; got {result!r}"
        )
    finally:
        BUILD_INFO.unlink(missing_ok=True)
        os.environ.pop("BUILD_HASH", None)


def test_writer_script_produces_valid_file(_reset_cache):
    """`backend/scripts/write_build_info.py` writes a well-formed SHA."""
    import subprocess

    script = BACKEND_DIR / "scripts" / "write_build_info.py"
    assert script.exists(), "Pre-deploy helper script missing"
    BUILD_INFO.unlink(missing_ok=True)
    try:
        out = subprocess.check_output(
            [sys.executable, str(script)], stderr=subprocess.STDOUT, timeout=10
        )
        assert BUILD_INFO.exists(), f"Script did not write .build_info: {out!r}"
        content = BUILD_INFO.read_text().strip()
        assert len(content) == 7 and all(c in "0123456789abcdef" for c in content), (
            f"Malformed .build_info content: {content!r}"
        )
    finally:
        BUILD_INFO.unlink(missing_ok=True)
