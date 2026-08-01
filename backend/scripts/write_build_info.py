#!/usr/bin/env python3
"""Write the current short git SHA to `backend/.build_info`.

Used by the deploy pipeline as a pre-build hook so that runtime pods
without a `git` binary (Emergent prod) still surface the correct
commit hash via `/api/health`.

Usage:
    python backend/scripts/write_build_info.py

Exits 0 on success, 1 if the SHA could not be resolved.
"""
from __future__ import annotations

import os
import subprocess
import sys


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.abspath(os.path.join(here, "..", ".."))
    backend_dir = os.path.abspath(os.path.join(here, ".."))
    target = os.path.join(backend_dir, ".build_info")

    sha: str | None = None
    # Prefer git binary — most accurate.
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            cwd=repo_root,
            timeout=5,
        )
        sha = out.decode().strip()[:7] or None
    except Exception:
        sha = None
    # Fallback — raw .git/HEAD read (no binary).
    if not sha:
        try:
            head_path = os.path.join(repo_root, ".git", "HEAD")
            with open(head_path, "r", encoding="utf-8") as fh:
                head = fh.read().strip()
            if head.startswith("ref:"):
                ref = head.split(":", 1)[1].strip()
                ref_path = os.path.join(repo_root, ".git", ref)
                if os.path.exists(ref_path):
                    with open(ref_path, "r", encoding="utf-8") as fh:
                        sha = fh.read().strip()[:7]
            else:
                sha = head[:7]
        except Exception:
            sha = None

    if not sha:
        sys.stderr.write("write_build_info: could not resolve git SHA\n")
        return 1

    with open(target, "w", encoding="utf-8") as fh:
        fh.write(sha + "\n")
    print(f"wrote {target} :: {sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
