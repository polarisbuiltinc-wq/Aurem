"""Live API-driven multi-turn recall verification for Issue C.

Uses real preview backend and REAL LLM (MOCK_LLM=false). Establishes 6-8 facts
across distinct turns, then asks recall questions. Also tests session switching
by using two distinct session_ids and verifying isolation + recall on switch-back.
"""
import os
import time
import uuid
import json
import requests

BASE = os.environ.get("BASE", "https://bin-context-pat.preview.emergentagent.com").rstrip("/")
API = f"{BASE}/api/aurem-dev"
EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"

s = requests.Session()

def login():
    r = s.post(f"{API}/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:400]}"
    data = r.json()
    token = data.get("access_token") or data.get("token")
    assert token, f"no token: {data}"
    s.headers.update({"Authorization": f"Bearer {token}"})
    print(f"[LOGIN OK] user={data.get('user',{}).get('email')}")
    return token

def send(prompt, session_id, project_id="home"):
    t = time.time()
    r = s.post(
        f"{API}/chat/send",
        json={"prompt": prompt, "session_id": session_id, "project_id": project_id, "agent": "auto", "mode": "swift"},
        timeout=180,
    )
    dt = time.time() - t
    if r.status_code != 200:
        print(f"[SEND FAIL {r.status_code}] {r.text[:300]}")
        return None
    data = r.json()
    content = data.get("content") or data.get("reply") or data.get("message") or ""
    tier = data.get("tier") or data.get("intent_tier") or "?"
    print(f"  [t={dt:4.1f}s tier={tier}] USER: {prompt!r}")
    print(f"                          ORA : {content[:300]!r}{'…' if len(content)>300 else ''}")
    return content

def contains_any(text, needles):
    tl = text.lower()
    return [n for n in needles if n.lower() in tl]

def contains_none_of(text, banned):
    tl = text.lower()
    return [b for b in banned if b.lower() in tl]

def run():
    login()
    results = {"scenarios": []}

    # ==== Scenario 1: multi-turn recall in single session (10+ turns) ====
    sid_a = f"testC-A-{uuid.uuid4().hex[:8]}"
    print(f"\n=== SCENARIO 1: multi-turn recall (session={sid_a}) ===")
    turns = [
        "hey ora, I'm going to describe a few bugs in our checkout flow one at a time. Ready?",
        "First bug: the promo code PROMO50 stopped applying discount after the tax refactor last week.",
        "Second bug: on mobile Safari, the Pay Now button double-fires and creates duplicate Stripe charges.",
        "Third bug: our webhook /api/stripe/webhook returns 500 whenever the event type is charge.dispute.created.",
        "Also, one small nit: the confirmation email uses my old company name 'Acme Old' instead of 'Aurem'.",
        "The engineer on this is Priya, and her deadline is next Friday, September 5th.",
    ]
    for p in turns:
        send(p, sid_a)
        time.sleep(0.5)

    # Recall question — casual-tier phrasing (no concrete noun in current msg)
    print("\n--- Recall question 1 (casual-style) ---")
    r1 = send("ok so what bugs did I mention to you so far?", sid_a) or ""
    needles = ["promo50", "pay now", "webhook", "acme", "priya", "safari", "dispute", "confirmation", "email"]
    hits = contains_any(r1, needles)
    banned = ["clarify what you", "i don't recall", "don't have any record", "no prior", "can you tell me", "not sure what you"]
    bad = contains_none_of(r1, banned)
    ok1 = len(hits) >= 3 and not bad
    print(f"  → hits={hits}  banned_hits={bad}  PASS={ok1}")
    results["scenarios"].append({"name": "recall_after_6turns", "passed": ok1, "hits": hits, "banned": bad, "reply": r1[:600]})

    # Add a few more turns to cross the 10-turn summary threshold
    more = [
        "Also worth mentioning: our uptime SLA target is 99.9% and last month we hit 99.7%.",
        "And Priya's teammate Marco is helping with the webhook fix specifically.",
        "One more: signup conversion dropped 12% after we added the phone-number field.",
        "Cool, remember all of this please.",
    ]
    for p in more:
        send(p, sid_a)
        time.sleep(0.5)

    print("\n--- Recall question 2 (after 10+ turns, tests summary path) ---")
    r2 = send("who did I say is fixing the webhook, and what was the promo code again?", sid_a) or ""
    needles2 = ["marco", "priya", "promo50"]
    hits2 = contains_any(r2, needles2)
    bad2 = contains_none_of(r2, banned)
    # Need at least marco OR priya AND promo50 hint
    ok2 = ("marco" in r2.lower() or "priya" in r2.lower()) and "promo50" in r2.lower() and not bad2
    print(f"  → hits={hits2}  banned={bad2}  PASS={ok2}")
    results["scenarios"].append({"name": "recall_after_10turns_summary", "passed": ok2, "hits": hits2, "banned": bad2, "reply": r2[:600]})

    # Check DB summary state via history endpoint
    try:
        hr = s.get(f"{API}/chat/history?session_id={sid_a}", timeout=20)
        if hr.status_code == 200:
            hdata = hr.json()
            n_turns = len(hdata.get("turns") or hdata.get("messages") or [])
            has_summary = bool(hdata.get("summary"))
            print(f"[HISTORY] turns_in_db={n_turns}  summary_present={has_summary}")
            results["history_probe"] = {"turns": n_turns, "summary_present": has_summary}
    except Exception as e:
        print(f"[HISTORY probe error] {e}")

    # ==== Scenario 2: session switch (session B independent, then back to A) ====
    sid_b = f"testC-B-{uuid.uuid4().hex[:8]}"
    print(f"\n=== SCENARIO 2: session switch to fresh session_id={sid_b} ===")
    send("hi", sid_b)
    time.sleep(0.5)
    # In session B — asking about session A's bugs should NOT know them
    rb = send("what bugs did I mention to you so far?", sid_b) or ""
    leaked = contains_any(rb, ["promo50", "priya", "marco", "acme"])
    ok_iso = len(leaked) == 0
    print(f"  → session-B bleed check: leaked={leaked}  ISOLATED={ok_iso}")
    results["scenarios"].append({"name": "session_isolation", "passed": ok_iso, "leaked": leaked, "reply": rb[:400]})

    # Now switch BACK to session A and ask a follow-up
    print(f"\n=== SCENARIO 3: switch back to session A ({sid_a}) and recall ===")
    r3 = send("remind me — what was Priya's deadline again?", sid_a) or ""
    ok3 = ("september 5" in r3.lower() or "sept 5" in r3.lower() or "friday" in r3.lower() or "next friday" in r3.lower())
    bad3 = contains_none_of(r3, banned)
    ok3 = ok3 and not bad3
    print(f"  → PASS={ok3}  banned={bad3}")
    results["scenarios"].append({"name": "recall_after_switch_back", "passed": ok3, "reply": r3[:400]})

    # ==== Scenario 4: regression — fresh session 'hi' still casual ====
    sid_c = f"testC-C-{uuid.uuid4().hex[:8]}"
    print(f"\n=== SCENARIO 4: fresh session 'hi' regression (session={sid_c}) ===")
    rh = send("hi", sid_c) or ""
    # Must be a short/normal greeting, no reference to past facts
    stale = contains_any(rh, ["promo50", "priya", "marco", "acme", "webhook"])
    ok4 = len(rh) < 400 and not stale
    print(f"  → len={len(rh)} stale_leak={stale}  PASS={ok4}")
    results["scenarios"].append({"name": "fresh_hi_regression", "passed": ok4, "leaked": stale, "reply": rh[:400]})

    # ==== Scenario 5: Issue B regression (short follow-up after 'looking into') ====
    sid_d = f"testC-D-{uuid.uuid4().hex[:8]}"
    print(f"\n=== SCENARIO 5: Issue B regression (session={sid_d}) ===")
    send("can you take a look in my project and find any auth-related issues?", sid_d)
    time.sleep(0.5)
    rd = send("did you find any?", sid_d) or ""
    # Must reference the prior turn (repo/project/scan) NOT ask 'what are you looking for'
    ok5 = not contains_none_of(rd, ["clarify what you", "what are you looking for", "not sure what you"])
    # Better if it mentions repo/project/scan
    ctx_hit = contains_any(rd, ["repo", "project", "scan", "connect", "connected"])
    print(f"  → banned_avoided={ok5}  ctx_hit={ctx_hit}")
    results["scenarios"].append({"name": "issue_b_regression", "passed": ok5, "ctx_hit": ctx_hit, "reply": rd[:400]})

    # Summary
    passed = sum(1 for s_ in results["scenarios"] if s_["passed"])
    total = len(results["scenarios"])
    print(f"\n===== SUMMARY: {passed}/{total} passed =====")
    for sc in results["scenarios"]:
        print(f"  [{'PASS' if sc['passed'] else 'FAIL'}] {sc['name']}")

    with open("/app/test_reports/issue_c_live_multiturn_result.json", "w") as fp:
        json.dump(results, fp, indent=2, default=str)
    print("\nWrote /app/test_reports/issue_c_live_multiturn_result.json")
    return passed == total

if __name__ == "__main__":
    ok = run()
    raise SystemExit(0 if ok else 1)
