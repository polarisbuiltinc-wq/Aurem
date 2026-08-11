"""
BUILD_INFO.txt stamping fix — 2026-02-12.

The prior stamping mechanism wrote BUILD_INFO.txt at backend boot
time in routers/version.py's `_read_commit()`. This produced a lag:
the file was committed with whatever SHA was current at the LAST
backend boot, which for a session with multiple commits meant every
new commit inherited a stale value. That lag caused SHA ambiguity
on prod's /version endpoint on 2026-02-12.

The fix (Option 1, approved by founder):
  • backend/BUILD_INFO.txt is UNTRACKED (in .gitignore)
  • scripts/git_hooks/post-commit stamps it with the just-committed
    HEAD SHA after every commit
  • scripts/install_hooks.sh installs the hook into .git/hooks/
    for fresh sessions

These tests pin the invariants so a future refactor cannot
silently reintroduce the lag.
"""

import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_build_info_is_gitignored():
    """BUILD_INFO.txt must be untracked so it can hold fresh SHA
    without content-address self-reference issues."""
    gitignore = (REPO_ROOT / ".gitignore").read_text()
    assert "backend/BUILD_INFO.txt" in gitignore, (
        "backend/BUILD_INFO.txt must be listed in .gitignore. Without "
        "this, the post-commit hook's writes would create tracked "
        "modifications, and every commit would inherit a stale SHA "
        "(the same lag pattern the 2026-02-12 incident exposed)."
    )


def test_build_info_is_not_git_tracked():
    """Belt+braces — verify git itself considers it untracked."""
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "backend/BUILD_INFO.txt"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, (
        "backend/BUILD_INFO.txt is still tracked by git. Run "
        "`git rm --cached backend/BUILD_INFO.txt` to untrack it, "
        "then commit the removal alongside the .gitignore entry."
    )


def test_post_commit_hook_source_exists_and_is_executable():
    """The tracked hook source must exist so install_hooks.sh has
    something to copy into fresh sessions."""
    hook = REPO_ROOT / "scripts" / "git_hooks" / "post-commit"
    assert hook.exists(), (
        "scripts/git_hooks/post-commit missing. This is the source "
        "of truth for the BUILD_INFO.txt stamping hook."
    )
    assert os.access(hook, os.X_OK), (
        "scripts/git_hooks/post-commit is not executable. "
        "Run `chmod +x scripts/git_hooks/post-commit`."
    )


def test_install_hooks_script_exists_and_is_executable():
    """The installer must exist so new sessions can bootstrap the
    hook into .git/hooks/."""
    installer = REPO_ROOT / "scripts" / "install_hooks.sh"
    assert installer.exists(), (
        "scripts/install_hooks.sh missing. New sessions cannot "
        "bootstrap the post-commit hook without it."
    )
    assert os.access(installer, os.X_OK), (
        "scripts/install_hooks.sh is not executable. "
        "Run `chmod +x scripts/install_hooks.sh`."
    )


def test_post_commit_hook_writes_current_head_sha():
    """Sanity — the hook body must actually stamp HEAD's SHA."""
    hook = REPO_ROOT / "scripts" / "git_hooks" / "post-commit"
    src = hook.read_text()
    # Must read HEAD via rev-parse (not a cached value).
    assert "git rev-parse HEAD" in src
    # Must write to the canonical marker path.
    assert "backend/BUILD_INFO.txt" in src
    # Must guard against amend-loops even though this hook doesn't
    # amend today (defensive for future maintainers).
    assert "GIT_POST_COMMIT_HOOK_ACTIVE" in src


def test_post_commit_hook_is_installed_in_git_hooks_dir():
    """The hook must actually be installed in .git/hooks/ for this
    working session. install_hooks.sh handles this on fresh sessions."""
    installed = REPO_ROOT / ".git" / "hooks" / "post-commit"
    assert installed.exists(), (
        "post-commit hook not installed in .git/hooks/. "
        "Run `bash scripts/install_hooks.sh` from repo root."
    )
    assert os.access(installed, os.X_OK), (
        ".git/hooks/post-commit is not executable. Re-run "
        "`bash scripts/install_hooks.sh` to fix permissions."
    )


def test_version_reader_still_prefers_env_over_marker():
    """The read cascade in routers/version.py must still put explicit
    env vars ABOVE BUILD_INFO.txt so pipeline-injected SHA (if ever
    wired) takes precedence over the on-disk marker."""
    src = (REPO_ROOT / "backend" / "routers" / "version.py").read_text()
    # env cascade lines
    env_idx = src.find('AUREM_COMMIT_SHA')
    marker_idx = src.find('_BUILD_INFO_MARKER.exists()')
    assert env_idx > 0 and marker_idx > 0
    assert env_idx < marker_idx, (
        "AUREM_COMMIT_SHA env-var check must come BEFORE the "
        "BUILD_INFO.txt marker read in _read_commit()."
    )
