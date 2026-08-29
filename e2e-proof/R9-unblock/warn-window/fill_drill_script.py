"""R9 gate #2 (2026-08-30) — read-only data-gathering exception: the
Preview warn-window only had 1 legit ship write in the last 48h
(loop_7014cd440aaf4c, the pre-existing P6 drill). Per the founder's
explicit instruction ("trigger a few more controlled test ships in
Preview... to fill the window, THEN report. Do NOT invent numbers"),
this performs 4 small, clean (non-deny-listed) writes through the
SAME guarded choke point (`services.github_api_writer.commit_files`,
which runs `write_guard.check_write_paths` on every call) against the
same reachable drill repo already used for T2/R1a — TJSNDHU/Aurem,
installation 157161705 — then cleans up.
"""
import asyncio
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

    from services.github_api_writer import commit_files

    ts = int(time.time())
    results = []
    for i in range(1, 5):
        res = await commit_files(
            owner=OWNER, repo=REPO, branch=BRANCH, token=tok,
            files={"R9_WARN_WINDOW_FILL.md": f"# R9 warn-window fill drill\nwrite {i}/4, ts={ts}\n"},
            commit_message=f"chore(r9-warn-window-drill): fill write {i}/4 [via ORA R9 gate#2 drill]",
            author_name="AUREM R9 Warn-Window Drill", author_email="cto@aurem.dev",
        )
        results.append(res["full_sha"])
        print(f"WRITE {i}/4 OK", res["full_sha"])

    # cleanup — remove the marker file, same raw-REST pattern as the
    # drift drill's own cleanup (not through commit_files, so it does
    # NOT count itself as one of the 4 "guarded writer" writes above —
    # pure drill hygiene).
    import httpx
    headers = {"Authorization": f"token {tok}", "Accept": "application/vnd.github+json", "User-Agent": "aurem-r9-warnwindow-drill"}
    async with httpx.AsyncClient() as c:
        head = await c.get(f"https://api.github.com/repos/{OWNER}/{REPO}/git/ref/heads/{BRANCH}", headers=headers)
        head_sha = head.json()["object"]["sha"]
        base_tree = (await c.get(f"https://api.github.com/repos/{OWNER}/{REPO}/git/commits/{head_sha}", headers=headers)).json()["tree"]["sha"]
        tree = await c.post(
            f"https://api.github.com/repos/{OWNER}/{REPO}/git/trees",
            headers=headers,
            json={"base_tree": base_tree,
                  "tree": [{"path": "R9_WARN_WINDOW_FILL.md", "mode": "100644", "type": "blob", "sha": None}]},
        )
        new_tree_sha = tree.json()["sha"]
        commit = await c.post(
            f"https://api.github.com/repos/{OWNER}/{REPO}/git/commits",
            headers=headers,
            json={"message": "chore(r9-warn-window-drill): remove fill marker [via ORA R9 gate#2 drill cleanup]",
                  "tree": new_tree_sha, "parents": [head_sha],
                  "author": {"name": "AUREM R9 Warn-Window Drill Cleanup", "email": "cto@aurem.dev"}},
        )
        new_commit_sha = commit.json()["sha"]
        await c.patch(
            f"https://api.github.com/repos/{OWNER}/{REPO}/git/refs/heads/{BRANCH}",
            headers=headers, json={"sha": new_commit_sha},
        )
        print("CLEANUP OK", new_commit_sha)

    print("ALL_WRITES", results)


if __name__ == "__main__":
    asyncio.run(main())
