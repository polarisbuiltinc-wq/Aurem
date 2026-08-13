"""
Iter 388z — services/deploy_logger.py `get_current_commit()` cascade fix.

Bug on prod: containers strip .git, so `git rev-parse HEAD` returned
None → `log_deploy_event()` bailed with "no commit sha resolvable —
skip".  That left `app.state.deploy_event` unset → /api/health kept
surfacing stale build_hash / built_at from an old deploy_events row.

Fix: extended the cascade to fall back to `backend/BUILD_INFO.txt`
(post-commit hook stamps it, deploy pipeline snapshots it) — matches
what `routers/version.py::_read_commit()` already does successfully on
prod.
"""
from __future__ import annotations

import os
import pytest
from pathlib import Path
from unittest.mock import patch


def test_read_build_info_sha_reads_backend_path(tmp_path, monkeypatch):
    """When BUILD_INFO.txt exists at backend/, _read_build_info_sha
    returns the stamped SHA."""
    from services import deploy_logger

    fake_path = tmp_path / "BUILD_INFO.txt"
    fake_path.write_text("abc123def456\n")

    monkeypatch.setattr(deploy_logger, "_BUILD_INFO_PATH_BACKEND", str(fake_path))
    monkeypatch.setattr(deploy_logger, "_BUILD_INFO_PATH_REPO",
                         str(tmp_path / "does-not-exist"))
    assert deploy_logger._read_build_info_sha() == "abc123def456"


def test_read_build_info_sha_falls_back_to_repo_path(tmp_path, monkeypatch):
    """When backend/BUILD_INFO.txt missing but a repo-root
    BUILD_INFO.txt exists, uses that."""
    from services import deploy_logger

    repo_path = tmp_path / "BUILD_INFO.txt"
    repo_path.write_text("deadbeef\n")

    monkeypatch.setattr(deploy_logger, "_BUILD_INFO_PATH_BACKEND",
                         str(tmp_path / "missing" / "BUILD_INFO.txt"))
    monkeypatch.setattr(deploy_logger, "_BUILD_INFO_PATH_REPO", str(repo_path))
    assert deploy_logger._read_build_info_sha() == "deadbeef"


def test_read_build_info_sha_rejects_non_hex(tmp_path, monkeypatch):
    """Guard against a corrupted file with non-hex content — never
    surface garbage as a commit SHA."""
    from services import deploy_logger

    fake_path = tmp_path / "BUILD_INFO.txt"
    fake_path.write_text("not-a-sha-line\n")

    monkeypatch.setattr(deploy_logger, "_BUILD_INFO_PATH_BACKEND", str(fake_path))
    monkeypatch.setattr(deploy_logger, "_BUILD_INFO_PATH_REPO",
                         str(tmp_path / "missing"))
    assert deploy_logger._read_build_info_sha() is None


def test_read_build_info_sha_returns_none_when_both_paths_missing(tmp_path, monkeypatch):
    from services import deploy_logger

    monkeypatch.setattr(deploy_logger, "_BUILD_INFO_PATH_BACKEND",
                         str(tmp_path / "a" / "BUILD_INFO.txt"))
    monkeypatch.setattr(deploy_logger, "_BUILD_INFO_PATH_REPO",
                         str(tmp_path / "b" / "BUILD_INFO.txt"))
    assert deploy_logger._read_build_info_sha() is None


def test_get_current_commit_uses_build_info_when_git_missing(tmp_path, monkeypatch):
    """PROD SCENARIO: `.git` stripped, no AUREM_DEPLOY_COMMIT env,
    but backend/BUILD_INFO.txt has the fresh SHA — cascade returns
    THAT sha, not None."""
    from services import deploy_logger

    fake_path = tmp_path / "BUILD_INFO.txt"
    fake_path.write_text("f00baa123\n")

    monkeypatch.setattr(deploy_logger, "_safe_run", lambda cmd: None)
    monkeypatch.delenv("AUREM_DEPLOY_COMMIT", raising=False)
    monkeypatch.setattr(deploy_logger, "_BUILD_INFO_PATH_BACKEND", str(fake_path))
    monkeypatch.setattr(deploy_logger, "_BUILD_INFO_PATH_REPO",
                         str(tmp_path / "missing"))
    info = deploy_logger.get_current_commit()
    assert info["commit_sha"] == "f00baa123"


def test_get_current_commit_prefers_git_over_build_info(tmp_path, monkeypatch):
    """DEV SCENARIO: git rev-parse works — its SHA wins over
    BUILD_INFO.txt (which may lag by one commit if the post-commit
    hook hasn't fired yet)."""
    from services import deploy_logger

    fake_path = tmp_path / "BUILD_INFO.txt"
    fake_path.write_text("stale123\n")

    def fake_run(cmd):
        if cmd[:2] == ["git", "rev-parse"]:
            return "freshdeadbeef"
        return None

    monkeypatch.setattr(deploy_logger, "_safe_run", fake_run)
    monkeypatch.delenv("AUREM_DEPLOY_COMMIT", raising=False)
    monkeypatch.setattr(deploy_logger, "_BUILD_INFO_PATH_BACKEND", str(fake_path))
    info = deploy_logger.get_current_commit()
    assert info["commit_sha"] == "freshdeadbeef"


def test_get_current_commit_prefers_env_var_over_build_info(tmp_path, monkeypatch):
    """CI/manual override via AUREM_DEPLOY_COMMIT still wins ahead of
    the BUILD_INFO.txt fallback (documented cascade order)."""
    from services import deploy_logger

    fake_path = tmp_path / "BUILD_INFO.txt"
    fake_path.write_text("filesha\n")

    monkeypatch.setattr(deploy_logger, "_safe_run", lambda cmd: None)
    monkeypatch.setenv("AUREM_DEPLOY_COMMIT", "envsha")
    monkeypatch.setattr(deploy_logger, "_BUILD_INFO_PATH_BACKEND", str(fake_path))
    info = deploy_logger.get_current_commit()
    assert info["commit_sha"] == "envsha"
