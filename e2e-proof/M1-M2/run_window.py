"""M1+M2 bounded real-model window (2026-08-30). MOCK_LLM=false for
this script's duration only. Writes raw evidence to
/app/e2e-proof/M1-M2/ for every claim (P0-B evidence rule)."""
import json
import os
import time

import httpx

API_URL = os.environ["API_URL"]
PROJECT_ID = "p_6d0be78cdd"
OUT = "/app/e2e-proof/M1-M2"


def login():
    r = httpx.post(f"{API_URL}/api/aurem-dev/auth/login", json={
        "email": "test@aurem.dev", "password": "AuremTest2026!"})
    return r.json()["token"]


def send(token, prompt, session_id, tag):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    t0 = time.time()
    r = httpx.post(f"{API_URL}/api/aurem-dev/chat/send", headers=headers, json={
        "prompt": prompt, "project_id": PROJECT_ID, "session_id": session_id,
    }, timeout=90)
    dt = time.time() - t0
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"raw": r.text}
    with open(f"{OUT}/{tag}.json", "w") as f:
        json.dump({"prompt": prompt, "status": r.status_code, "dt_s": round(dt, 2), "response": body}, f, indent=2)
    return body, dt


def main():
    token = login()
    results = {}

    # ── M1a: fresh session, first-contact "what does this do?" ──────
    body, dt = send(token, "Hi, what does this tool do?", "m1a-fresh-1", "m1a_first_contact")
    results["m1a_first_contact"] = {"content": body.get("content"), "provider": body.get("provider"), "dt_s": dt}

    # ── M1b: same question twice in the SAME session ────────────────
    q = "Explain in detail how the loop, PR, and rollback pipeline works end to end"
    b1, dt1 = send(token, q, "m1b-repeat-1", "m1b_first_ask")
    time.sleep(1)
    b2, dt2 = send(token, q, "m1b-repeat-1", "m1b_reask_same")
    results["m1b_repeat"] = {
        "first_len": len(b1.get("content") or ""), "first": b1.get("content"),
        "reask_len": len(b2.get("content") or ""), "reask": b2.get("content"),
        "reask_is_shorter": len(b2.get("content") or "") < len(b1.get("content") or ""),
    }

    # ── M2.1: fence-emit rate, N=5, varied file locations incl root ─
    fence_prompts = [
        ("ship a fix: add an HTML comment noting the current date to README.md", "root file — the exact prior 4/4 repro case"),
        ("ship a fix: add a docstring one-liner to services/response_confidence.py", "nested, deep"),
        ("ship a fix: fix any obvious typo in requirements.txt", "root file, different from README"),
        ("ship a fix: add a comment to routers/health.py explaining its purpose", "nested, shallow"),
        ("ship a fix: add a trailing newline check comment to .gitignore", "root, dotfile"),
    ]
    fence_results = []
    for i, (prompt, note) in enumerate(fence_prompts, 1):
        b, dt = send(token, prompt, f"m2-fence-{i}", f"m2_fence_{i}")
        content = b.get("content") or ""
        has_fence = "```aurem-handoff" in content
        fence_results.append({
            "n": i, "prompt": prompt, "note": note, "provider": b.get("provider"),
            "has_fence": has_fence, "dt_s": dt, "content_excerpt": content[:400],
        })
    results["m2_fence"] = fence_results
    results["m2_fence_rate"] = f"{sum(1 for f in fence_results if f['has_fence'])}/{len(fence_results)}"

    # ── M2.2: low-confidence re-test — known-good first message must
    #    not be suppressed. Uses the SAME target prompt R8 used
    #    (deliberately no _FIX_INTENT_TOKENS) to retest the exact
    #    prior-round timeout finding.
    lowconf_prompt = "What do you think of this project overall? Any thoughts?"
    b, dt = send(token, lowconf_prompt, "m2-lowconf-1", "m2_lowconf")
    results["m2_lowconf"] = {
        "prompt": lowconf_prompt, "dt_s": dt, "status_content_len": len(b.get("content") or ""),
        "provider": b.get("provider"), "low_confidence_flag": b.get("low_confidence"),
        "suppressed": (b.get("content") or "") == "",
    }

    with open(f"{OUT}/window_summary.json", "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(json.dumps({
        "m1a_provider": results["m1a_first_contact"]["provider"],
        "m1b_reask_shorter": results["m1b_repeat"]["reask_is_shorter"],
        "m2_fence_rate": results["m2_fence_rate"],
        "m2_lowconf_suppressed": results["m2_lowconf"]["suppressed"],
        "m2_lowconf_dt": results["m2_lowconf"]["dt_s"],
    }, indent=2))


if __name__ == "__main__":
    main()
