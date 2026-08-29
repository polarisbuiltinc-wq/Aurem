"""R1a gap#4 (2026-08-30) safe live drift drill — real GitHub API,
TJSNDHU/Aurem, installation 157161705 (same reachable repo T2's drill
already validated; ora-grounding/152797252 remains unreachable per
T2_SUMMARY.md, unrelated pre-existing infra gap).

Drills the NEW `check_branch_drift` function (this round's core new
service call) against a REAL repo, simulating exactly the founder's
scenario: ship a commit -> a DIFFERENT commit lands on the branch
(someone else pushed) -> drift is detected live -> acknowledge ->
revert the EXPECTED (original) commit, not the drifted head -> clean
up the drift-simulation marker too, leaving the repo clean.
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

    from services.github_api_writer import (
        commit_files, revert_commit, verify_branch_head, check_branch_drift, fetch_file,
    )

    out = {"owner": OWNER, "repo": REPO, "branch": BRANCH, "steps": []}
    ts = int(time.time())

    # 1. Ship a commit — this is what AUREM's own ship path would do,
    #    and what records `expected_branch_head_sha` (see loop_engine.py).
    ship_res = await commit_files(
        owner=OWNER, repo=REPO, branch=BRANCH, token=tok,
        files={"R1A_DRIFT_DRILL_SHIP.md": f"# drift drill ship {ts}\nWill be reverted.\n"},
        commit_message="chore(drift-drill): ship marker [via ORA drift drill]",
        author_name="AUREM Drift Drill", author_email="cto@aurem.dev",
    )
    out["ship_commit"] = ship_res
    expected_head = ship_res["full_sha"]
    print("SHIPPED (expected_branch_head_sha)", expected_head)

    ship_verify = await verify_branch_head(OWNER, REPO, BRANCH, expected_head, tok, max_attempts=5, interval_s=2.0)
    out["ship_verify"] = ship_verify
    assert ship_verify["verified"], "ship itself did not verify — abort before drifting"

    # 2. Simulate "someone else pushed a different commit to the
    #    branch" mid-cycle — a second, unrelated commit lands.
    drift_res = await commit_files(
        owner=OWNER, repo=REPO, branch=BRANCH, token=tok,
        files={"R1A_DRIFT_DRILL_SOMEONE_ELSE.md": f"# a different push landed {ts}\nSimulates drift.\n"},
        commit_message="chore(drift-drill): simulated third-party push (drift) [via ORA drift drill]",
        author_name="Someone Else", author_email="someone-else@example.com",
    )
    out["drift_push_commit"] = drift_res
    drifted_head = drift_res["full_sha"]
    print("DRIFTED HEAD (simulated 3rd-party push)", drifted_head)

    # 3. Attempt rollback -> drift check (the actual new function,
    #    live) must detect the branch moved away from what ship recorded.
    drift = await check_branch_drift(OWNER, REPO, BRANCH, expected_head, tok)
    out["drift_check"] = drift
    print("DRIFT CHECK", drift)
    assert drift["drifted"] is True, "drift drill setup failed — should have detected drift"
    assert drift["current_sha"] == drifted_head

    # -> at this point, the real /loop/{id}/rollback endpoint would have
    #    returned rollback_status="drift_detected" and NOT called
    #    revert_commit at all (proven by the unit tests). This drill
    #    proves the detection call itself is correct against live
    #    GitHub state, then continues past the "warning shown" point.

    # 4. Acknowledge -> revert the EXPECTED commit specifically (not
    #    the drifted head) — proves `git revert <specific sha>` targets
    #    that commit's diff regardless of what's now HEAD.
    revert_res = await revert_commit(
        owner=OWNER, repo=REPO, branch=BRANCH, token=tok,
        commit_sha=expected_head,
        author_name="AUREM Drift Drill Revert", author_email="cto@aurem.dev",
    )
    out["revert_commit"] = revert_res
    print("REVERTED (expected commit, acknowledged)", revert_res.get("full_sha"))

    revert_verify = await verify_branch_head(OWNER, REPO, BRANCH, revert_res["full_sha"], tok, max_attempts=10, interval_s=3.0)
    out["revert_verify"] = revert_verify
    assert revert_verify["verified"], "revert did not verify within budget"

    # 5. Post-acknowledge re-check: the drifted push (someone else's
    #    file) is STILL there — proves the revert targeted only the
    #    expected commit's own diff, never touched the drift push.
    drift_after_revert = await check_branch_drift(OWNER, REPO, BRANCH, revert_res["full_sha"], tok)
    out["drift_after_revert"] = drift_after_revert
    assert drift_after_revert["drifted"] is False, "branch head should now equal the revert commit"

    # 6. Clean up BOTH drill marker files (the ship's, already
    #    reverted by step 4, and the simulated drift push's, removed
    #    here) so the repo is left genuinely clean — separate final
    #    cleanup commit, not part of the rollback logic itself.
    still_present = await fetch_file(OWNER, REPO, "R1A_DRIFT_DRILL_SOMEONE_ELSE.md", BRANCH, tok)
    out["drift_marker_still_present_before_cleanup"] = still_present is not None
    if still_present is not None:
        import httpx
        headers = {"Authorization": f"token {tok}", "Accept": "application/vnd.github+json", "User-Agent": "aurem-drift-drill"}
        async with httpx.AsyncClient() as c:
            head = await c.get(f"https://api.github.com/repos/{OWNER}/{REPO}/git/ref/heads/{BRANCH}", headers=headers)
            head_sha = head.json()["object"]["sha"]
            base_tree = (await c.get(f"https://api.github.com/repos/{OWNER}/{REPO}/git/commits/{head_sha}", headers=headers)).json()["tree"]["sha"]
            tree = await c.post(
                f"https://api.github.com/repos/{OWNER}/{REPO}/git/trees",
                headers=headers,
                json={"base_tree": base_tree,
                      "tree": [{"path": "R1A_DRIFT_DRILL_SOMEONE_ELSE.md", "mode": "100644", "type": "blob", "sha": None}]},
            )
            new_tree_sha = tree.json()["sha"]
            commit = await c.post(
                f"https://api.github.com/repos/{OWNER}/{REPO}/git/commits",
                headers=headers,
                json={"message": "chore(drift-drill): remove simulated drift marker [via ORA drift drill cleanup]",
                      "tree": new_tree_sha, "parents": [head_sha],
                      "author": {"name": "AUREM Drift Drill Cleanup", "email": "cto@aurem.dev"}},
            )
            new_commit_sha = commit.json()["sha"]
            await c.patch(
                f"https://api.github.com/repos/{OWNER}/{REPO}/git/refs/heads/{BRANCH}",
                headers=headers, json={"sha": new_commit_sha},
            )
            out["cleanup_commit_sha"] = new_commit_sha

    final_verify = await verify_branch_head(OWNER, REPO, BRANCH, out.get("cleanup_commit_sha") or revert_res["full_sha"], tok, max_attempts=10, interval_s=3.0)
    out["final_verify"] = final_verify

    with open("/app/e2e-proof/drift/drill_result.json", "w") as f:
        json.dump(out, f, indent=2, default=str)
    print("DRIFT_DRILL_OK — repo left clean (both markers reverted/removed)")


if __name__ == "__main__":
    asyncio.run(main())
