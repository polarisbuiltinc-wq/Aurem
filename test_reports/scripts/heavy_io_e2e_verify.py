"""
E2E verification for AUREM CTO ship/rollback heavy-I/O exception scenarios.
Scenarios:
 1. GitHub API rejection during ship (bad installation_id)
 2. Git subprocess failure during ship (bad branch)
 3. Retry-after-fix (branch back to main + retry endpoint)
Final: verify project state restored.
"""
import os, sys, time, json, requests

BASE = os.environ.get("REACT_APP_BACKEND_URL", "https://bin-context-pat.preview.emergentagent.com").rstrip("/")
EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"
PROJECT_ID = "p_6d0be78cdd"
ORIGINAL_INSTALLATION_ID = 152797252
ORIGINAL_BRANCH = "main"

results = {"steps": [], "pass": True}

def log(step, ok, details=None):
    results["steps"].append({"step": step, "ok": ok, "details": details})
    if not ok:
        results["pass"] = False
    print(f"[{'PASS' if ok else 'FAIL'}] {step}: {details if details else ''}")

def login():
    r = requests.post(f"{BASE}/api/aurem-dev/auth/login",
                      json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    r.raise_for_status()
    tok = r.json().get("token") or r.json().get("access_token")
    if not tok:
        # try common keys
        for k in ("jwt", "id_token"):
            if k in r.json():
                tok = r.json()[k]; break
    return tok, r.json()

def hdr(t):
    return {"Authorization": f"Bearer {t}", "Content-Type": "application/json"}

def patch_project(t, payload):
    r = requests.patch(f"{BASE}/api/aurem-dev/cto/projects/{PROJECT_ID}",
                       headers=hdr(t), json=payload, timeout=30)
    return r

def get_project(t):
    r = requests.get(f"{BASE}/api/aurem-dev/cto/projects/list", headers=hdr(t), timeout=30)
    r.raise_for_status()
    projs = r.json().get("projects") or r.json().get("items") or r.json()
    if isinstance(projs, dict) and "projects" in projs:
        projs = projs["projects"]
    for p in projs:
        if p.get("id") == PROJECT_ID or p.get("project_id") == PROJECT_ID:
            return p
    return None

def submit(t):
    body = {"project_id": PROJECT_ID, "task": "Add a one-line comment to README.md",
            "files": [], "context": ""}
    r = requests.post(f"{BASE}/api/aurem-dev/cto/tasks/submit",
                      headers=hdr(t), json=body, timeout=60)
    return r

def poll_task(t, tid, target_status, timeout_s=60):
    start = time.time()
    last = None
    while time.time() - start < timeout_s:
        r = requests.get(f"{BASE}/api/aurem-dev/cto/tasks/{tid}", headers=hdr(t), timeout=30)
        if r.status_code == 200:
            j = r.json()
            last = j
            st = j.get("status")
            if st in ("done", "failed", "error", "success", "completed"):
                return j
            if isinstance(target_status, (list, tuple)) and st in target_status:
                return j
            if st == target_status:
                return j
        time.sleep(4)
    return last

def restore(t):
    patch_project(t, {"installation_id": ORIGINAL_INSTALLATION_ID})
    patch_project(t, {"branch": ORIGINAL_BRANCH})

def main():
    try:
        tok, login_json = login()
        log("login", bool(tok), f"keys={list(login_json.keys())}")
        if not tok:
            return

        # Check project initial state
        p0 = get_project(tok)
        log("initial_project_fetch", p0 is not None, {"branch": p0.get("branch") if p0 else None,
                                                       "installation_id": p0.get("installation_id") if p0 else None,
                                                       "installation_active": p0.get("installation_active") if p0 else None})

        # ---- Scenario 1: bad installation_id ----
        r = patch_project(tok, {"installation_id": 999999999})
        log("s1_patch_bad_installation", r.status_code in (200, 204), {"status": r.status_code, "body": r.text[:300]})

        r = submit(tok)
        s1_body = None
        try: s1_body = r.json()
        except: s1_body = r.text
        s1_ok = (r.status_code == 403) and ("task_id" not in (s1_body if isinstance(s1_body, dict) else {}))
        log("s1_submit_expect_403_no_task_id", s1_ok, {"status": r.status_code, "body": s1_body})

        # restore installation immediately
        r = patch_project(tok, {"installation_id": ORIGINAL_INSTALLATION_ID})
        log("s1_restore_installation", r.status_code in (200, 204), {"status": r.status_code})

        p1 = get_project(tok)
        log("s1_verify_restored", p1 and p1.get("installation_id") == ORIGINAL_INSTALLATION_ID and p1.get("installation_active") in (True, None),
            {"installation_id": p1.get("installation_id") if p1 else None, "installation_active": p1.get("installation_active") if p1 else None})

        # ---- Scenario 2: bad branch ----
        r = patch_project(tok, {"branch": "does-not-exist-xyz-branch"})
        log("s2_patch_bad_branch", r.status_code in (200, 204), {"status": r.status_code})

        r = submit(tok)
        s2_body = None
        try: s2_body = r.json()
        except: s2_body = r.text
        failed_task_id = s2_body.get("task_id") if isinstance(s2_body, dict) else None
        log("s2_submit_accepted_with_task_id", bool(failed_task_id), {"status": r.status_code, "task_id": failed_task_id})

        final = None
        if failed_task_id:
            final = poll_task(tok, failed_task_id, "failed", timeout_s=90)
            st = final.get("status") if final else None
            err = final.get("error") or final.get("error_message") if final else None
            err_plain = final.get("error_plain") if final else None
            err_sugg = final.get("error_suggestion") if final else None
            has_branch_msg = err and ("does-not-exist-xyz-branch" in str(err) or "not found in upstream" in str(err).lower())
            log("s2_task_failed_with_git_stderr", st == "failed" and has_branch_msg,
                {"status": st, "error_snippet": (str(err)[:400] if err else None), "error_plain": err_plain, "error_suggestion": err_sugg})
            log("s2_user_facing_error_present", bool(err_plain) or bool(err_sugg),
                {"error_plain": err_plain, "error_suggestion": err_sugg})

        # ---- Scenario 3: retry after fix ----
        r = patch_project(tok, {"branch": ORIGINAL_BRANCH})
        log("s3_patch_branch_back_to_main", r.status_code in (200, 204), {"status": r.status_code})

        new_task_id = None
        if failed_task_id:
            rr = requests.post(f"{BASE}/api/aurem-dev/cto/tasks/{failed_task_id}/retry",
                               headers=hdr(tok), timeout=60)
            try: rb = rr.json()
            except: rb = rr.text
            new_task_id = rb.get("task_id") if isinstance(rb, dict) else None
            retry_of = rb.get("retry_of") if isinstance(rb, dict) else None
            carried = rb.get("carried_failure_context") if isinstance(rb, dict) else None
            log("s3_retry_endpoint_response", rr.status_code == 200 and bool(new_task_id) and retry_of == failed_task_id and carried is True,
                {"status": rr.status_code, "body": rb})

        if new_task_id:
            final3 = poll_task(tok, new_task_id, "done", timeout_s=120)
            st = final3.get("status") if final3 else None
            commit_sha = final3.get("commit_sha") if final3 else None
            gh_url = final3.get("github_url") if final3 else None
            ok_done = st in ("done", "success", "completed") and bool(commit_sha) and bool(gh_url)
            log("s3_retry_task_done_with_commit", ok_done,
                {"status": st, "commit_sha": commit_sha, "github_url": gh_url})

        # ---- Final: restore + verify ----
        restore(tok)
        p_final = get_project(tok)
        ok_restored = (p_final and p_final.get("branch") == ORIGINAL_BRANCH
                       and p_final.get("installation_id") == ORIGINAL_INSTALLATION_ID
                       and p_final.get("installation_active") in (True, None))
        log("final_project_state_restored", ok_restored,
            {"branch": p_final.get("branch") if p_final else None,
             "installation_id": p_final.get("installation_id") if p_final else None,
             "installation_active": p_final.get("installation_active") if p_final else None})

    except Exception as e:
        log("exception", False, repr(e))

    print("\n===== SUMMARY =====")
    print(json.dumps(results, indent=2, default=str))
    with open("/app/test_reports/scripts/heavy_io_e2e_result.json", "w") as f:
        json.dump(results, f, indent=2, default=str)
    sys.exit(0 if results["pass"] else 1)

if __name__ == "__main__":
    main()
