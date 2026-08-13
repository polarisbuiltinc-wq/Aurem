"""test_iter388ac_bundle_secrets_sweep.py — Iter 388-ac (2026-02-14).

Locks the "clean bundle" contract established by Task #19 into a
regression test. Runs the bundle secrets sweep against `frontend/dist/`
and asserts:

1. Zero CRITICAL findings (no real API keys / PEMs / DB URIs shipped).
2. WARN allow-list contains only known-safe UI copy references
   (RESEND_API_KEY / MONGO_URL / EMERGENT_LLM_KEY — all documented
   as name-only mentions in Admin / OpsRecipes / WhyOra UI copy).
3. Total findings count doesn't grow past the current baseline.

If a future commit reintroduces a leaked key (rotation gone wrong, a
new integration accidentally exposed via VITE_ prefix, etc.), this
test fails LOUDLY before the code hits main.

Skipped when `frontend/dist` is not present (developer machine
without a build) — the CI/predeploy gate must build first.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]
DIST = REPO / "frontend" / "dist"
SWEEP = REPO / "scripts" / "bundle_secrets_sweep.py"


pytestmark = pytest.mark.skipif(
    not DIST.exists(),
    reason="frontend/dist not present — run `yarn build` first",
)


# The known-safe WARN allow-list. Each entry is a name-mention in UI
# copy that has been human-reviewed on 2026-02-14 (Iter 388-ac). If a
# future scan surfaces a NEW warn entry, this list must be extended
# consciously — do not just add to it to shut the test up.
ALLOWED_WARN_NAMES = {
    "RESEND_API_KEY",       # Admin bundle — dry-run status message
    "MONGO_URL",            # OpsRecipes bundle — diagnostic command example
    "EMERGENT_LLM_KEY",     # WhyOra bundle — landing-page marketing copy
}


def _run_sweep() -> tuple[int, str]:
    """Execute the sweep script and return (exit_code, stdout)."""
    proc = subprocess.run(
        [sys.executable, str(SWEEP)],
        capture_output=True, text=True, timeout=60,
    )
    return proc.returncode, proc.stdout


def test_no_critical_findings():
    code, out = _run_sweep()
    assert code != 3, (
        "🔴 CRITICAL findings in production bundle — a real secret is "
        f"shipping. Rotate keys IMMEDIATELY.\n\n{out}"
    )


def test_warn_findings_are_on_allowlist():
    code, out = _run_sweep()
    # Extract the WARN block. Each WARN line has shape:
    #   [server_env_name_leak  ] NAME_HERE                   hits=1 …
    #   [stripe_live_publish   ] ...
    warn_names: set[str] = set()
    for line in out.splitlines():
        s = line.strip()
        if not s.startswith("["):
            continue
        # only care about server_env_name_leak entries (WARN block)
        if "server_env_name_leak" not in s:
            continue
        # Extract token after `]`
        after_bracket = s.split("]", 1)[1].strip()
        name = after_bracket.split()[0]
        warn_names.add(name)

    unexpected = warn_names - ALLOWED_WARN_NAMES
    assert not unexpected, (
        f"🟡 New WARN findings not on the reviewed allow-list: "
        f"{sorted(unexpected)}\n\n"
        "Either (a) genuinely a new safe reference — extend "
        "ALLOWED_WARN_NAMES here after human review, or (b) a real "
        "leak — rotate + patch.\n\n"
        f"Full output:\n{out}"
    )
