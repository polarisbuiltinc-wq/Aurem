import sys, time
sys.path.insert(0, "/app/e2e-proof/R8")
from smoke_lib import login, send, API

token = login()
sid = "r8-smoke-2026-08-30"
pid = "p_6d0be78cdd"

print("=== 2 PLAIN CHAT ===")
send(token, "What's 7 plus 5? Answer in one short sentence.", sid + "-plain1")
time.sleep(1)
send(token, "Say hello in one short sentence.", sid + "-plain2")
time.sleep(1)

print("=== 3 TOOL-CALL FLOWS (read-only) ===")
send(token, "List the top-level files in this repo.", sid + "-tool1", pid)
time.sleep(1)
send(token, "Read README.md and summarize it in one sentence.", sid + "-tool2", pid)
time.sleep(1)
send(token, "Does this repo have a LICENSE file? Just check, don't change anything.", sid + "-tool3", pid)
time.sleep(1)

print("=== ACTION 1: READ-tier propose/approve/execute ===")
send(token, "Check the pyproject.toml file and tell me the project name declared in it.", sid + "-action-read", pid)
time.sleep(1)

print("=== RATE LIMIT BOUNDARY (per-IP 30/min) ===")
# The codebase's ACTUAL limiter is 30 req/min per IP (routers/chat.py:1139),
# not a 21-msg/hour cap (no such mechanism found in this codebase — see
# report). Testing the real boundary that exists instead of one that doesn't.
import httpx
hit_429 = False
for i in range(32):
    r = httpx.post(f"{API}/api/aurem-dev/chat/stream",
                    json={"prompt": f"rate limit probe {i}", "session_id": sid + f"-rl{i}"},
                    headers={"Authorization": f"Bearer {token}"}, timeout=15)
    if r.status_code == 429:
        print(f"[rate-limit] hit 429 at request #{i+1}: {r.text[:200]}")
        hit_429 = True
        break
if not hit_429:
    print("[rate-limit] did NOT hit 429 within 32 rapid requests (founder tier may bypass IP limit, or 30/min needs less-bursty timing)")
