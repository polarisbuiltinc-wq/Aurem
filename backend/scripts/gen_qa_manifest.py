"""Regenerate backend/qa_manifest.json from live test counts.

Run from /app/backend (predeploy_gate.sh does this automatically) so
prod pods — which ship without backend/tests — can still report real
test-suite numbers via the /admin/qa endpoints (Iter 351).
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routers.admin_qa import _harvest_counts  # noqa: E402


def main() -> None:
    counts = _harvest_counts()
    counts.pop("source", None)
    counts.pop("manifest_generated_at", None)
    if (counts.get("backend_pytest") or {}).get("files", 0) == 0:
        print("refusing to write manifest from a 0-file environment")
        sys.exit(1)
    out = {
        "generated_at": time.time(),
        "test_counts": {
            k: v for k, v in counts.items()
            if k in ("backend_pytest", "frontend_vitest",
                     "playwright", "reasoning_evals")
        },
        "grand_total_tests": counts.get("grand_total_tests", 0),
    }
    path = Path(__file__).resolve().parents[1] / "qa_manifest.json"
    path.write_text(json.dumps(out, indent=2) + "\n")
    print(f"wrote {path} — grand_total_tests={out['grand_total_tests']}")


if __name__ == "__main__":
    main()
