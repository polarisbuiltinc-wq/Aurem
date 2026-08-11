#!/bin/bash
# scripts/install_hooks.sh
#
# Installs the tracked git hooks from scripts/git_hooks/ into the
# local .git/hooks/ directory. Idempotent — safe to re-run.
#
# This is needed because:
#   • .git/hooks/ is not tracked in the repo (git ignores it)
#   • Fresh sessions / new containers start without our custom hooks
#   • The post-commit hook stamps backend/BUILD_INFO.txt with the
#     current HEAD SHA (see scripts/git_hooks/post-commit for
#     rationale — the 2026-02-12 SHA ambiguity incident)
#
# Run from repo root: `bash scripts/install_hooks.sh`

set -e

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
if [ -z "$REPO_ROOT" ]; then
    echo "install_hooks.sh: not inside a git repo, aborting" >&2
    exit 1
fi

SRC_DIR="$REPO_ROOT/scripts/git_hooks"
DST_DIR="$REPO_ROOT/.git/hooks"

if [ ! -d "$SRC_DIR" ]; then
    echo "install_hooks.sh: source dir $SRC_DIR not found, aborting" >&2
    exit 1
fi

mkdir -p "$DST_DIR"

installed=0
for hook_file in "$SRC_DIR"/*; do
    [ -f "$hook_file" ] || continue
    hook_name="$(basename "$hook_file")"
    dst="$DST_DIR/$hook_name"
    cp "$hook_file" "$dst"
    chmod +x "$dst"
    installed=$((installed + 1))
    echo "installed: $hook_name"
done

echo "install_hooks.sh: $installed hook(s) installed to $DST_DIR"
