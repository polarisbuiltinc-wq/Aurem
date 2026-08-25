#!/usr/bin/env python3
"""One-time REAL push proof for T3 (Ship/Commit Robustness).

Not a mock, not respx — calls the actual `services.github_api_writer.
commit_files()` against a real GitHub repo over the network. Run this
yourself against a disposable repo you control; do not hand the token
to anyone else.

Usage:
    cd backend
    GH_PUSH_PROOF_OWNER=your-org \
    GH_PUSH_PROOF_REPO=your-disposable-repo \
    GH_PUSH_PROOF_BRANCH=main \
    GH_PUSH_PROOF_TOKEN=ghp_xxx_with_contents_write_scope \
    python scripts/one_time_real_push_proof.py

Prints the real commit SHA + html_url on success (T3 pass), or the
real PushFailedError (commit_sha + reason) if the ref-update/push is
rejected — either output is the honest proof this task needs.
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.github_api_writer import commit_files
from core.errors import PushFailedError


async def main() -> int:
    owner = os.environ["GH_PUSH_PROOF_OWNER"]
    repo = os.environ["GH_PUSH_PROOF_REPO"]
    branch = os.environ.get("GH_PUSH_PROOF_BRANCH", "main")
    token = os.environ["GH_PUSH_PROOF_TOKEN"]

    stamp = int(time.time())
    try:
        result = await commit_files(
            owner=owner, repo=repo, branch=branch, token=token,
            files={
                f"aurem_t3_push_proof_{stamp}.txt":
                    f"AUREM T3 real-push proof — {stamp}\n"
            },
            commit_message=f"AUREM T3 real-push proof ({stamp})",
            author_email="aurem-proof@example.com",
            author_name="AUREM T3 Proof",
        )
        print("REAL PUSH SUCCEEDED")
        print(f"  sha:      {result['sha']}")
        print(f"  full_sha: {result['full_sha']}")
        print(f"  html_url: {result['html_url']}")
        return 0
    except PushFailedError as e:
        print("REAL PUSH REJECTED (commit object created, branch ref-update failed)")
        print(f"  commit_sha: {e.commit_sha}")
        print(f"  reason:     {e.reason}")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
