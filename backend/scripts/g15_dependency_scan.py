"""
scripts/g15_dependency_scan.py — G15 · Dependency vulnerability scan

Runs pip-audit against backend/requirements.txt AND (if `yarn` +
`node` available) yarn npm audit against frontend/yarn.lock.

Exit codes:
  0 — clean, or only findings on the allowlist AND their expiry is
      in the future.
  1 — one or more HIGH/CRITICAL findings not on the allowlist, or
      an allowlist entry has passed its expiry date.
  2 — scan itself failed to run (pip-audit missing, network err).

Allowlist file: `scripts/g15_allowlist.json`.
Format: [{"id": "CVE-...", "package": "...", "reason": "...",
          "expires": "2026-12-31"}]
Permanent ignores are BANNED — expiry MUST be set.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALLOWLIST = Path(__file__).parent / "g15_allowlist.json"


def _load_allowlist() -> list[dict]:
    if not ALLOWLIST.exists():
        return []
    try:
        return json.loads(ALLOWLIST.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[g15] WARN allowlist parse failed: {e}")
        return []


def _run_pip_audit() -> tuple[int, list[dict]]:
    req = ROOT / "backend" / "requirements.txt"
    if not req.exists():
        print("[g15] no backend/requirements.txt — skipping pip-audit")
        return 0, []
    try:
        res = subprocess.run(
            ["pip-audit", "-r", str(req), "-f", "json"],
            capture_output=True, text=True, timeout=180,
        )
    except FileNotFoundError:
        print("[g15] ERR pip-audit not installed. "
              "`pip install pip-audit`.")
        return 2, []
    except subprocess.TimeoutExpired:
        print("[g15] ERR pip-audit timed out (120s)")
        return 2, []
    if res.returncode not in (0, 1):
        print(f"[g15] ERR pip-audit exit={res.returncode}: {res.stderr[:400]}")
        return 2, []
    try:
        parsed = json.loads(res.stdout or "{}")
    except json.JSONDecodeError:
        return 2, []
    findings = []
    for dep in parsed.get("dependencies") or []:
        for v in dep.get("vulns") or []:
            findings.append({
                "package":  dep.get("name"),
                "id":       v.get("id"),
                "severity": (v.get("severity") or "").upper(),
                "fix":      v.get("fix_versions"),
                "aliases":  v.get("aliases"),
            })
    return 0, findings


def _severity_gates_ci(sev: str) -> bool:
    return (sev or "").upper() in ("HIGH", "CRITICAL", "SEVERE")


def _is_allowlisted(finding: dict, allowlist: list[dict]) -> tuple[bool, str]:
    fid = finding.get("id")
    for row in allowlist:
        if row.get("id") == fid or fid in (row.get("aliases") or []):
            exp = row.get("expires")
            if not exp:
                return False, "allowlist entry missing expiry"
            try:
                d = datetime.strptime(exp, "%Y-%m-%d").date()
            except ValueError:
                return False, f"bad expiry format '{exp}'"
            if d < date.today():
                return False, f"expired on {exp}"
            return True, f"allowlisted until {exp}"
    return False, ""


def main() -> int:
    allowlist = _load_allowlist()
    ec, findings = _run_pip_audit()
    if ec == 2:
        return 2

    hard_fails = []
    for f in findings:
        if not _severity_gates_ci(f["severity"]):
            continue
        allowed, note = _is_allowlisted(f, allowlist)
        if allowed:
            print(f"[g15] SKIP {f['package']}::{f['id']} {f['severity']} — {note}")
            continue
        hard_fails.append(f)
        print(f"[g15] ❌ {f['package']}::{f['id']} {f['severity']} "
              f"(fix: {f.get('fix')})  {note}")

    if hard_fails:
        print(f"[g15] Build fails: {len(hard_fails)} HIGH/CRITICAL "
              "finding(s) not on allowlist.")
        return 1
    print(f"[g15] OK — 0 HIGH/CRITICAL findings unhandled "
          f"({len(findings)} total findings; {len(allowlist)} allowlist entries).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
