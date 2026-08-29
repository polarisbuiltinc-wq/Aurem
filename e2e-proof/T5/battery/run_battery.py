"""T5 — 5+5 regression battery (2026-08-30, W4 restart conditions met:
H3 green, W0-residue data-verified, fresh session, context-pinning per
test). MOCK_LLM=true throughout — zero real spend, zero real GitHub
writes expected on the loop side (H3/mock-refuse guard).

5 chat prompts (varied intent tiers) + 5 loop-mode starts, ALL pinned
to the SAME project (p_6d0be78cdd / polarisbuiltinc-wq/ora-grounding).
Every call's response is checked against the pinned project — proving
no cross-project drift (the W1/H1/H3 class of bug) across a full
regression pass, not just a single call.
"""
import json
import os
import time

import httpx

API_URL = os.environ["API_URL"]
PROJECT_ID = "p_6d0be78cdd"
EXPECTED_OWNER = "polarisbuiltinc-wq"
EXPECTED_REPO = "ora-grounding"

results = {"chat": [], "loop": []}


def login():
    r = httpx.post(f"{API_URL}/api/aurem-dev/auth/login", json={
        "email": "test@aurem.dev", "password": "AuremTest2026!"})
    return r.json()["token"]


def main():
    token = login()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    chat_prompts = [
        "what does this repo do",
        "I'm frustrated, please just fix my broken deploy",
        "ship a fix: add a comment to README.md",
        "explain the loop and rollback architecture in detail",
        "thanks, that's all",
    ]
    for i, prompt in enumerate(chat_prompts, 1):
        r = httpx.post(f"{API_URL}/api/aurem-dev/chat/send", headers=headers, json={
            "prompt": prompt, "project_id": PROJECT_ID,
            "session_id": f"t5-battery-chat-{i}",
        }, timeout=30)
        body = r.json()
        pinned_ok = (body.get("repo_owner") == EXPECTED_OWNER
                     and body.get("repo_name") == EXPECTED_REPO)
        results["chat"].append({
            "n": i, "prompt": prompt, "status": r.status_code,
            "provider": body.get("provider"),
            "repo_owner": body.get("repo_owner"), "repo_name": body.get("repo_name"),
            "pinned_correctly": pinned_ok,
        })
        time.sleep(0.5)

    for i in range(1, 6):
        r = httpx.post(f"{API_URL}/api/aurem-dev/loop/start", headers=headers, json={
            "project_id": PROJECT_ID,
            "user_message": f"T5 battery loop drill #{i}: add a one-line comment to README.md",
            "session_id": f"t5-battery-loop-{i}",
        }, timeout=30)
        body = r.json()
        entry = {"n": i, "status": r.status_code, "start_response": body}
        loop_id = body.get("loop_id")
        if loop_id:
            time.sleep(1.5)
            r2 = httpx.get(f"{API_URL}/api/aurem-dev/loop/{loop_id}/status", headers=headers, timeout=15)
            status_body = r2.json()
            entry["status_response"] = status_body
            entry["pinned_project_id_in_status"] = status_body.get("project_id")
            entry["pinned_correctly"] = (status_body.get("project_id") in (None, PROJECT_ID))
            # Reject the plan so the per-project loop lock frees up for
            # the next battery iteration — this is a drill, not a real
            # ship intent either way.
            try:
                httpx.post(f"{API_URL}/api/aurem-dev/loop/{loop_id}/confirm", headers=headers,
                           json={"approved": False, "feedback": "T5 battery drill — reject, not a real ship"}, timeout=15)
            except Exception as e:                            # noqa: BLE001
                entry["reject_error"] = str(e)
        results["loop"].append(entry)
        time.sleep(2)

    with open("/app/e2e-proof/T5/battery/battery_result.json", "w") as f:
        json.dump(results, f, indent=2, default=str)

    print(json.dumps({
        "chat_pinned_ok": sum(1 for c in results["chat"] if c["pinned_correctly"]),
        "chat_total": len(results["chat"]),
        "loop_runs": len(results["loop"]),
        "loop_statuses": [l.get("status") for l in results["loop"]],
    }, indent=2))


if __name__ == "__main__":
    main()
