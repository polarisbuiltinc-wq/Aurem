import sys, time, re
sys.path.insert(0, "/app/e2e-proof/R8")
from smoke_lib import login, send

token = login()
pid = "p_6d0be78cdd"
base_sid = "r8-fence-2026-08-30"

FENCE_RX = re.compile(r"```aurem-handoff")

prompts = [
    ("root_file_readme", "Please fix this: README.md is missing a license line at the bottom. Propose the exact fix."),
    ("nested_py_file", "There's a bug in services/orchestrator.py — propose a fix for any real issue you can find reading that file."),
    ("nested_config", "pyproject.toml should declare a minimum Python version of 3.11 if it doesn't already. Propose the fix."),
    ("test_file", "tests/test_grounding.py — check it for any obvious missing assertion and propose a fix."),
    ("root_config_gitignore", ".gitignore is missing an entry for __pycache__/ if it's not there — propose the fix."),
]

results = []
for key, prompt in prompts:
    r = send(token, prompt, f"{base_sid}-{key}", pid, key)
    has_fence = bool(FENCE_RX.search(r.get("content", "")))
    results.append((key, has_fence, r.get("provider"), r.get("content", "")[:300]))
    time.sleep(2)

print("\n=== FENCE EMIT RATE ===")
n_fence = sum(1 for _, hf, _, _ in results if hf)
print(f"{n_fence}/{len(results)} emitted a valid aurem-handoff fence")
for key, hf, provider, excerpt in results:
    print(f"- {key}: fence={hf} provider={provider}")
    if not hf:
        print(f"  MISS excerpt: {excerpt!r}")
