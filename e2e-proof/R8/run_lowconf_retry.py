import sys, time
sys.path.insert(0, "/app/e2e-proof/R8")
from smoke_lib import login, send

token = login()
pid = "p_6d0be78cdd"
prompt = "What do you think of this project overall? Any thoughts?"
r = send(token, prompt, "r8-lowconf-2026-08-30-retry", pid, "lowconf-retry")
print("content_len:", len(r.get("content","")))
print("head:", r.get("content","")[:400])
