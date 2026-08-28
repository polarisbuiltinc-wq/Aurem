"""R2 — T7 live PR drill against polarisbuiltinc-wq/ora-grounding
(installation 152797252). Standalone harness — NOT part of the app.
Writes proof artifacts as separate JSON files under /app/e2e-proof/T7-live/.
"""
from __future__ import annotations
import asyncio
import base64
import json
import os
import sys
import time

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

PROOF_DIR = "/app/e2e-proof/T7-live"
OWNER = "polarisbuiltinc-wq"
REPO = "ora-grounding"
INSTALLATION_ID = 152797252
BASE_BRANCH = "main"


def save(name: str, obj) -> None:
    path = os.path.join(PROOF_DIR, name)
    with open(path, "w") as f:
        json.dump(obj, f, indent=2, default=str)
    print(f"saved {path}")


async def main():
    from motor.motor_asyncio import AsyncIOMotorClient
    from cto_services.db import set_db
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    set_db(db)

    from services import github_app as ga
    from services.github_app_config import ensure_configured_from_db
    await ensure_configured_from_db(db)
    token, _ = await ga.get_installation_token(INSTALLATION_ID)

    from services import loop_safety as ls
    from services.github_api_writer import commit_files

    import httpx
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "aurem-t7-drill",
    }

    async def gh(method, path, **kw):
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.request(method, f"https://api.github.com{path}", headers=headers, **kw)
        return r

    results = {}

    # ═══ DRILL A — open → merge ═══
    branch_a = ls.ship_branch_name("t7-drill-merge")
    ok, err = await ls.create_or_reuse_branch(
        owner=OWNER, repo=REPO, base_branch=BASE_BRANCH,
        new_branch=branch_a, token=token,
    )
    results["drill_a_branch_create"] = {"branch": branch_a, "ok": ok, "err": err}
    assert ok, f"branch A create failed: {err}"

    await commit_files(
        owner=OWNER, repo=REPO, branch=branch_a, token=token,
        files={f".aurem/t7-drill-{int(time.time())}.md": (
            "# T7 live PR drill (R2)\n\nMarker file for the R2 live drill. "
            "Safe to ignore / will be cleaned up.\n"
        )},
        commit_message="chore: T7 live drill marker (merge case)",
        author_name="AUREM T7 Drill", author_email="cto@auremcto.com",
    )

    pr_url, pr_err = await ls.open_draft_pr(
        owner=OWNER, repo=REPO, head_branch=branch_a, base_branch=BASE_BRANCH,
        title="ship: T7 live drill (merge case)",
        body="R2 live PR drill — merge path. Opened by the drill harness.",
        token=token,
    )
    assert pr_err is None, f"open PR A failed: {pr_err}"
    pr_number_a = int(pr_url.rsplit("/", 1)[-1])
    await ls.add_pr_label(owner=OWNER, repo=REPO, pr_number=pr_number_a, label="aura:ship", token=token)

    r = await gh("GET", f"/repos/{OWNER}/{REPO}/pulls/{pr_number_a}")
    pr_open_json = r.json()
    save("pr_open.json", pr_open_json)

    # Mark ready for review (GraphQL — REST has no field for this).
    node_id = pr_open_json["node_id"]
    gql_headers = dict(headers)
    gql_headers["Content-Type"] = "application/json"
    async with httpx.AsyncClient(timeout=15.0) as c:
        gr = await c.post(
            "https://api.github.com/graphql", headers=gql_headers,
            json={"query": "mutation($id:ID!){markPullRequestReadyForReview(input:{pullRequestId:$id}){pullRequest{isDraft}}}",
                  "variables": {"id": node_id}},
        )
    results["mark_ready_for_review"] = gr.json()

    # Merge.
    r = await gh("PUT", f"/repos/{OWNER}/{REPO}/pulls/{pr_number_a}/merge",
                 json={"merge_method": "squash"})
    merge_resp = r.json()
    merge_resp["_status_code"] = r.status_code
    # Full PR state after merge (has merged:true + merge_commit_sha).
    r2 = await gh("GET", f"/repos/{OWNER}/{REPO}/pulls/{pr_number_a}")
    pr_after_merge = r2.json()
    save("pr_merge.json", {"merge_call_response": merge_resp, "pr_state_after_merge": pr_after_merge})

    # Delete the now-merged head branch (no orphan after merge).
    ok, err = await ls.delete_ship_branch(owner=OWNER, repo=REPO, branch=branch_a, token=token)
    results["drill_a_branch_delete"] = {"branch": branch_a, "ok": ok, "err": err}

    # ═══ DRILL B — open → close unmerged → delete branch ═══
    branch_b = ls.ship_branch_name("t7-drill-close")
    ok, err = await ls.create_or_reuse_branch(
        owner=OWNER, repo=REPO, base_branch=BASE_BRANCH,
        new_branch=branch_b, token=token,
    )
    results["drill_b_branch_create"] = {"branch": branch_b, "ok": ok, "err": err}
    assert ok, f"branch B create failed: {err}"

    await commit_files(
        owner=OWNER, repo=REPO, branch=branch_b, token=token,
        files={f".aurem/t7-drill-close-{int(time.time())}.md": (
            "# T7 live PR drill (R2)\n\nMarker file for the close/unmerged case.\n"
        )},
        commit_message="chore: T7 live drill marker (close case)",
        author_name="AUREM T7 Drill", author_email="cto@auremcto.com",
    )

    pr_url_b, pr_err_b = await ls.open_draft_pr(
        owner=OWNER, repo=REPO, head_branch=branch_b, base_branch=BASE_BRANCH,
        title="ship: T7 live drill (close/unmerged case)",
        body="R2 live PR drill — close-unmerged path.",
        token=token,
    )
    assert pr_err_b is None, f"open PR B failed: {pr_err_b}"
    pr_number_b = int(pr_url_b.rsplit("/", 1)[-1])
    await ls.add_pr_label(owner=OWNER, repo=REPO, pr_number=pr_number_b, label="aura:ship", token=token)

    close_result = await ls.close_and_retract(
        owner=OWNER, repo=REPO, pr_number=pr_number_b, branch=branch_b, token=token,
    )
    results["drill_b_close_and_retract"] = close_result

    r = await gh("GET", f"/repos/{OWNER}/{REPO}/pulls/{pr_number_b}")
    save("pr_close.json", r.json())

    r = await gh("GET", f"/repos/{OWNER}/{REPO}/git/refs/heads/{branch_b}")
    save("branch_delete.json", {
        "branch": branch_b,
        "get_ref_status_after_delete": r.status_code,
        "body": (r.json() if r.status_code != 404 else {"message": "Not Found (confirmed deleted)"}),
    })

    # ═══ no_orphans check ═══
    r = await gh("GET", f"/repos/{OWNER}/{REPO}/branches", params={"per_page": 100})
    all_branches = [b["name"] for b in r.json()]
    orphans = [b for b in all_branches if b.startswith("auremcto/")]
    save("no_orphans.json", {
        "total_branches": len(all_branches),
        "auremcto_prefixed_branches": orphans,
        "orphan_count": len(orphans),
        "clean": len(orphans) == 0,
    })

    # ═══ webhook_payload — capture REAL delivery via App Deliveries API ═══
    app_headers = {
        "Authorization": f"Bearer {ga.app_jwt()}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "aurem-t7-drill",
    }
    async with httpx.AsyncClient(timeout=15.0) as c:
        dr = await c.get("https://api.github.com/app/hook/deliveries?per_page=100", headers=app_headers)
    deliveries = dr.json() if dr.status_code == 200 else []
    matches = [
        d for d in deliveries
        if d.get("event") == "pull_request"
        and d.get("repository_id") == pr_open_json.get("base", {}).get("repo", {}).get("id")
    ]
    webhook_captures = []
    async with httpx.AsyncClient(timeout=15.0) as c:
        for d in matches[:10]:
            dd = await c.get(f"https://api.github.com/app/hook/deliveries/{d['id']}", headers=app_headers)
            if dd.status_code == 200:
                webhook_captures.append(dd.json())
    save("webhook_payload.json", {
        "deliveries_list_status": dr.status_code,
        "total_deliveries_seen": len(deliveries),
        "matched_pull_request_deliveries": len(matches),
        "captured": webhook_captures,
    })

    # Replay the real captured payload(s) through OUR dispatch function
    # directly (webhook_url on GitHub's side points at auremcto.com
    # production, not this preview pod — see report) to prove the
    # label-routing logic itself is correct against the exact real
    # GitHub payload.
    from services.loop_safety import dispatch_pull_request_webhook
    replay_results = []
    for cap in webhook_captures:
        payload = cap.get("request", {}).get("payload", {})
        action = payload.get("action", "")
        if payload:
            out = await dispatch_pull_request_webhook(db, payload=payload, action=action)
            replay_results.append({"delivery_id": cap.get("id"), "action": action, "dispatch_result": out})
    results["webhook_replay_against_real_payloads"] = replay_results

    # ═══ ship_pr_events — analytics events written to DB ═══
    events = await db.ship_pr_events.find(
        {"pr_number": {"$in": [pr_number_a, pr_number_b]}}
    ).to_list(length=100)
    save("ship_pr_events.json", {
        "pr_number_a": pr_number_a, "pr_number_b": pr_number_b,
        "events_found_in_db": events,
        "note": ("ship_pr_opened is written by loop_engine.py's own ship-via-PR "
                  "path (only fires inside a real loop ship, NOT by this raw-API "
                  "drill harness, which calls open_draft_pr()/add_pr_label() "
                  "directly). ship_pr_merged/ship_pr_closed are written by "
                  "dispatch_pull_request_webhook — see webhook_replay results above."),
    })

    save("misc_results.json", results)
    print("DRILL COMPLETE")
    print(json.dumps({k: (v if not isinstance(v, list) else f"[{len(v)} items]") for k, v in results.items()}, default=str, indent=2))


asyncio.run(main())
