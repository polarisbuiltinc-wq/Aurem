import json, os, time, sys
import httpx

API = os.environ["API_URL"]

def login():
    r = httpx.post(f"{API}/api/aurem-dev/auth/login", json={
        "email": "test@aurem.dev", "password": "AuremTest2026!"}, timeout=20)
    return r.json()["token"]

def send(token, prompt, session_id, project_id=None, label=""):
    body = {"prompt": prompt, "session_id": session_id}
    if project_id:
        body["project_id"] = project_id
    t0 = time.time()
    text_parts = []
    provider = None
    meta = {}
    done_payload = {}
    try:
        with httpx.stream("POST", f"{API}/api/aurem-dev/chat/stream", json=body,
                           headers={"Authorization": f"Bearer {token}"}, timeout=90) as r:
            for line in r.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                try:
                    payload = json.loads(line[5:].strip())
                except Exception:
                    continue
                if payload.get("meta"):
                    provider = payload.get("provider")
                    meta = payload
                if "token" in payload:
                    text_parts.append(payload["token"])
                if payload.get("done"):
                    done_payload = payload
    except Exception as e:
        print(f"[{label}] EXC: {e!r}")
        return {"content": "", "provider": None, "error": str(e)}
    content = "".join(text_parts)
    dt = time.time() - t0
    print(f"[{label}] provider={provider} dt={dt:.1f}s len={len(content)} head={content[:200]!r}")
    return {"content": content, "provider": provider, "meta": meta, "done": done_payload}

if __name__ == "__main__":
    token = login()
    cmd = sys.argv[1]
    globals()[cmd](token, *sys.argv[2:])
