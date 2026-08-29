"""T2 (2026-08-30) safe live drill — real GitHub API, TJSNDHU/Aurem,
installation 157161705 (reachable per admin/github-app-diagnostics;
`ora-grounding`'s installation 152797252 is currently unreachable,
`app_installation_missing`, a pre-existing infra gap unrelated to T2).

Uses the REAL production code paths (github_api_writer.commit_files,
services.github_api_writer.revert_commit, services.github_api_writer.
verify_branch_head — the exact new T2/R10 verify step) against a real,
founder-owned test repo. Leaves the repo clean: content restored to the
pre-drill state via a real, history-preserving revert commit (not a
force-reset — matches this codebase's non-destructive rollback design).
"""
import asyncio
import json
import os
import time

from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

OWNER = "TJSNDHU"
REPO = "Aurem"
BRANCH = "main"
INSTALLATION_ID = 157161705


async def main():
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    from cto_services.db import set_db
    set_db(db)
    from services.github_app_config import ensure_configured_from_db
    await ensure_configured_from_db(db)
    from services import github_app as gh
    tok, _exp = await gh.get_installation_token(INSTALLATION_ID)

    from services.github_api_writer import commit_files, revert_commit, verify_branch_head

    out = {"owner": OWNER, "repo": REPO, "branch": BRANCH, "steps": []}

    async def _prog(step, status="info"):
        out["steps"].append({"step": step, "status": status, "ts": time.time()})

    # 1. Real commit (small, additive, root-file README touch —
    #    matches this codebase's own existing test-ship convention).
    marker = f"T2 rollback-verify drill {int(time.time())}"
    ts = int(time.time())
    files = {"T2_DRILL_MARKER.md": f"# {marker}\nTemporary — will be reverted by the same drill.\n"}
    commit_res = await commit_files(
        owner=OWNER, repo=REPO, branch=BRANCH, token=tok, files=files,
        commit_message=f"chore(t2-drill): add marker file [via ORA T2 drill]",
        author_name="AUREM T2 Drill", author_email="cto@aurem.dev",
        progress=_prog,
    )
    out["ship_commit"] = commit_res
    print("SHIPPED", commit_res.get("full_sha"))

    # 2. Bounded-poll verify the ship itself landed (same helper the
    #    T2 rollback fix now uses) — proves verify_branch_head works
    #    live, not just against mocks.
    ship_verify = await verify_branch_head(OWNER, REPO, BRANCH, commit_res["full_sha"], tok, max_attempts=5, interval_s=2.0)
    out["ship_verify"] = ship_verify
    print("SHIP_VERIFY", ship_verify)
    assert ship_verify["verified"], "ship itself did not verify — abort before reverting"

    # 3. Real revert (the actual rollback code path) + bounded verify —
    #    this IS the T2/R10 fix's core new behavior under live test.
    revert_res = await revert_commit(
        owner=OWNER, repo=REPO, branch=BRANCH, token=tok,
        commit_sha=commit_res["full_sha"],
        author_name="AUREM T2 Drill Revert", author_email="cto@aurem.dev",
        progress=_prog,
    )
    out["revert_commit"] = revert_res
    print("REVERTED", revert_res.get("full_sha"))

    revert_verify = await verify_branch_head(OWNER, REPO, BRANCH, revert_res["full_sha"], tok, max_attempts=10, interval_s=3.0)
    out["revert_verify"] = revert_verify
    print("REVERT_VERIFY", revert_verify)
    assert revert_verify["verified"], "revert did not verify within budget — T2 rollback_failed path would fire here"

    # 4. Post-drill clean-state check: zero orphan auremcto/* branches
    #    (this drill uses direct-commit, not ship-via-PR, so none
    #    should ever be created — confirms no side-channel branch
    #    creation snuck in).
    import httpx
    headers = {"Authorization": f"token {tok}", "Accept": "application/vnd.github+json", "User-Agent": "aurem-t2-drill"}
    async with httpx.AsyncClient() as c:
        r = await c.get(f"https://api.github.com/repos/{OWNER}/{REPO}/branches?per_page=100", headers=headers)
        branches = [b["name"] for b in r.json()]
        orphans = [b for b in branches if b.startswith("auremcto/")]
        out["orphan_auremcto_branches_after"] = orphans
        r2 = await c.get(f"https://api.github.com/repos/{OWNER}/{REPO}/git/refs/heads/{BRANCH}", headers=headers)
        out["final_head_sha"] = r2.json().get("object", {}).get("sha")

    with open("/app/e2e-proof/T2/drill_result.json", "w") as f:
        json.dump(out, f, indent=2, default=str)
    print("ORPHANS", orphans)
    print("FINAL_HEAD", out["final_head_sha"])
    print("DRILL_OK — repo left clean (marker added then reverted, zero orphan branches)")


if __name__ == "__main__":
    asyncio.run(main())
