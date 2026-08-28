"""
scripts/track_b_rerun.py — Loop N item 5.

Single-command rerun of the 5 originally-failed founder tasks, so the
official post-P0 success-rate measurement is one command once the
founder confirms the P0 hotfix on production. Submits via the exact
same POST /cto/tasks/submit path the founder's chat-driven ships use
(routers/cto_projects.py::submit_task -> _run_task_via_api /
_run_task_with_git), polls GET /cto/tasks/{task_id} to a terminal
state, and reports per-task PASS/FAIL + a success rate.

DRY-RUN NOTE: this pod has no CTO project pointed at the founder's
real repo — it runs against whatever --project-id you pass (default:
this pod's connected polarisbuiltinc-wq/ora-grounding fixture, the
same disposable repo used by the existing rollback-drill mechanism).
A result from THIS script is a same-pipeline proxy measurement, not a
substitute for a rerun against the founder's actual repo/tasks.

Usage:
  python3 scripts/track_b_rerun.py \\
      --base-url https://<pod>.preview.emergentagent.com \\
      --email test@aurem.dev --password AuremTest2026! \\
      --project-id p_418e5fa6b8 [--timeout 90]
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request

# The 5 originally-failed founder tasks (4 distinct types; orchestrator.py
# was attempted twice per the founder's own report).
ORIGINAL_FAILED_TASKS = [
    {"label": "orchestrator.py comment (attempt 1)",
     "task": "Add a one-line module-level comment at the top of "
             "services/orchestrator.py noting the file's purpose.",
     "files": ["services/orchestrator.py"]},
    {"label": "orchestrator.py comment (attempt 2)",
     "task": "Add a one-line module-level comment at the top of "
             "services/orchestrator.py noting the file's purpose.",
     "files": ["services/orchestrator.py"]},
    {"label": "payments.py comment",
     "task": "Add a one-line module-level comment at the top of "
             "routers/payments.py noting the file's purpose.",
     "files": ["routers/payments.py"]},
    {"label": "test_dynamic_30_percent_discount.py",
     "task": "Add a one-line comment at the top of "
             "test_dynamic_30_percent_discount.py noting the file's purpose.",
     "files": ["test_dynamic_30_percent_discount.py"]},
    {"label": "test_admin_panel_features.py",
     "task": "Add a one-line comment at the top of "
             "test_admin_panel_features.py noting the file's purpose.",
     "files": ["test_admin_panel_features.py"]},
]


def _post(url: str, body: dict, token: str = None) -> dict:
    headers = {"Content-Type": "application/json", "User-Agent": "curl/8.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers=headers, method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _get(url: str, token: str) -> dict:
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token}", "User-Agent": "curl/8.0"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def run(base_url: str, email: str, password: str, project_id: str, timeout: int):
    login = _post(f"{base_url}/api/aurem-dev/auth/login",
                   {"email": email, "password": password})
    token = login.get("token")
    if not token:
        print(f"LOGIN FAILED: {login}")
        sys.exit(1)

    results = []
    for spec in ORIGINAL_FAILED_TASKS:
        try:
            sub = _post(
                f"{base_url}/api/aurem-dev/cto/tasks/submit",
                {"project_id": project_id, "task": spec["task"],
                 "files": spec["files"], "context": ""},
                token=token,
            )
        except urllib.error.HTTPError as e:
            results.append({"label": spec["label"], "status": "submit_failed",
                             "error": f"HTTP {e.code}: {e.read()[:300]}"})
            continue
        task_id = sub.get("task_id")
        if not task_id:
            results.append({"label": spec["label"], "status": "submit_failed",
                             "error": str(sub)})
            continue

        deadline = time.time() + timeout
        final = {"status": "timeout"}
        while time.time() < deadline:
            time.sleep(3)
            got = _get(f"{base_url}/api/aurem-dev/cto/tasks/{task_id}", token)
            t = got.get("task") or {}
            if t.get("status") in ("done", "failed"):
                final = t
                break
        results.append({
            "label": spec["label"], "task_id": task_id,
            "status": final.get("status"), "error": final.get("error"),
            "commit_sha": final.get("commit_sha"),
        })
        print(f"  {spec['label']:42s} -> {final.get('status')}"
              f"{' — ' + str(final.get('error'))[:120] if final.get('error') else ''}")

    passed = sum(1 for r in results if r["status"] == "done")
    total = len(results)
    print(f"\n=== TRACK B DRY-RUN RESULT (PENDING PRODUCTION CONFIRMATION) ===")
    print(f"Ran against: {base_url} / project {project_id}")
    print(f"NOT the founder's real repo/tasks — same-pipeline proxy only.")
    print(f"{passed}/{total} passed = {100*passed/total:.1f}% "
          f"(baseline being compared against: 54.3%)")
    return results


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--project-id", required=True)
    ap.add_argument("--timeout", type=int, default=90)
    args = ap.parse_args()
    run(args.base_url, args.email, args.password, args.project_id, args.timeout)
