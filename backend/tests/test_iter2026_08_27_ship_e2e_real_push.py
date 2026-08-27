"""P2 item 3 — permanent real ship-E2E proof (2026-08-27).

"Show the Outcome, Never the Engine" P2 item 3: pulls the P0a live-
proof attempt into a standing CI test instead of a one-off manual
script. Uses the REAL production code path — `pat_vault.
get_repo_token_or_error()` (GitHub App installation token) +
`git_identity.resolve_git_identity()` + `github_api_writer.
commit_files()` — against a real, disposable drill repo the founder
already established for exactly this purpose
(`polarisbuiltinc-wq/aurem-rollback-testbed`, owned by `test_admin_001`).

STATUS as of 2026-08-27 (see memory/investigation_show_outcome_not_
engine.md, Q1): every project in this Preview pod returns
`app_installation_missing` — there is NO GitHub App installation
configured in this environment at all. That is an environment/infra
gap, not a code gap. This test is BUILT and wired into CI now so it
requires ZERO further code work once the founder installs the GitHub
App on the drill repo — it will simply flip from "skipped (blocked)"
to a real passing push proof.

Do NOT fabricate credentials, use an unrelated token, or mark this
green without a real network commit — that would be exactly the kind
of overclaim this whole initiative exists to prevent.
"""
from __future__ import annotations

import asyncio
import os
import time

import pytest

DRILL_OWNER = os.environ.get("AUREM_DRILL_REPO_OWNER", "polarisbuiltinc-wq")
DRILL_REPO = os.environ.get("AUREM_DRILL_REPO_NAME", "aurem-rollback-testbed")
DRILL_USER_ID = os.environ.get("AUREM_DRILL_USER_ID", "test_admin_001")


def _get_real_db():
    """Best-effort real Mongo connection, mirroring other real-DB
    integration tests in this suite (no shared fixture exists)."""
    try:
        from motor.motor_asyncio import AsyncIOMotorClient
        mongo_url = os.environ.get("MONGO_URL")
        db_name = os.environ.get("DB_NAME")
        if not mongo_url or not db_name:
            return None
        client = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=5000)
        return client[db_name]
    except Exception:
        return None


async def _resolve_drill_project_and_token():
    """Returns (project_dict, token, block_reason). `block_reason` is
    None only when a real, usable installation token was resolved."""
    db = _get_real_db()
    if db is None:
        return None, None, "no_mongo_connection_in_this_environment"
    project = await db.cto_projects.find_one({
        "user_id": DRILL_USER_ID, "github_repo": DRILL_REPO,
    })
    if not project:
        return None, None, (
            f"drill repo project not found for user={DRILL_USER_ID} "
            f"repo={DRILL_OWNER}/{DRILL_REPO}"
        )
    from services.pat_vault import get_repo_token_or_error
    token, err_code, err_detail = await get_repo_token_or_error(project)
    if not token:
        return project, None, (err_code or "unknown_token_error")
    return project, token, None


def _block_reason_sync() -> str | None:
    try:
        _, token, reason = asyncio.run(_resolve_drill_project_and_token())
        return reason if not token else None
    except ModuleNotFoundError:
        # 2026-08-27 — testing-agent code review: collection-time
        # import of `services.*` only resolves when cwd=backend (the
        # CI convention this repo already uses everywhere else). Fail
        # soft to a clear skip instead of breaking collection if a
        # future run invokes pytest from a different cwd.
        return "backend_package_not_importable_from_this_cwd"


_BLOCK_REASON = _block_reason_sync()


@pytest.mark.skipif(
    _BLOCK_REASON is not None,
    reason=(
        f"BLOCKED — {_BLOCK_REASON}. Unblock: founder installs the "
        f"GitHub App on {DRILL_OWNER}/{DRILL_REPO} (Settings → "
        f"Applications → Install App → select the drill repo), then "
        f"re-run this test. No code change required once installed. "
        f"See memory/investigation_show_outcome_not_engine.md Q1."
    ),
)
class TestShipE2ERealPush:
    def test_real_commit_with_resolved_author_identity(self):
        """The permanent T3-in-CI proof: a REAL network commit to the
        drill repo, with the REAL resolved git identity (not the old
        hardcoded `AUREM <cto@auremcto.com>` bot identity), landing as
        one atomic commit via the same `commit_files()` every
        production ship uses."""
        async def _run():
            from services.git_identity import resolve_git_identity, build_commit_message
            from services.github_api_writer import commit_files

            db = _get_real_db()
            project, token, reason = await _resolve_drill_project_and_token()
            assert token, f"unexpectedly blocked mid-run: {reason}"

            name, email = await resolve_git_identity(db, DRILL_USER_ID)
            stamp = int(time.time())
            commit_message = build_commit_message(
                task_type="test",
                summary=f"P2 ship-E2E CI proof ({stamp})",
            )
            result = await commit_files(
                owner=DRILL_OWNER, repo=DRILL_REPO, branch="main",
                token=token,
                files={
                    f"aurem_p2_ship_e2e_proof_{stamp}.txt":
                        f"AUREM P2 ship-E2E CI proof — {stamp}\n"
                        f"author={name} <{email}>\n"
                },
                commit_message=commit_message,
                author_name=name, author_email=email,
            )
            return result, name, email

        result, name, email = asyncio.run(_run())
        assert result.get("sha") or result.get("full_sha"), (
            f"commit_files() returned no sha — real push did not land: {result}"
        )
        assert name != "AUREM", (
            "author identity fell back to the old generic bot name — "
            "resolve_git_identity() did not resolve a real developer identity"
        )
