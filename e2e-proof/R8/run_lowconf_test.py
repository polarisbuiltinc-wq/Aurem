import sys, time
sys.path.insert(0, "/app/e2e-proof/R8")
from smoke_lib import login, send

token = login()
pid = "p_6d0be78cdd"
prompt = "What do you think of this project overall? Any thoughts?"

FALLBACK_MARKERS = [
    "wasn't able to re-confirm", "isn't able to confirm",
    "There's nothing pending", "having trouble confirming",
]

for i in range(3):
    r = send(token, prompt, f"r8-lowconf-2026-08-30-{i}", pid, f"lowconf{i}")
    content = r.get("content", "")
    suppressed = any(m.lower() in content.lower() for m in FALLBACK_MARKERS)
    print(f"[lowconf{i}] suppressed={suppressed}")
    time.sleep(2)
