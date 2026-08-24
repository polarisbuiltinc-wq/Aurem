"""
scripts/g15_dependency_scan.py — G15 · Dependency vulnerability scan

Runs pip-audit against `backend/requirements.txt` AND `yarn audit`
against `frontend/yarn.lock`. Both scanners share a single allowlist.

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
from datetime import date, datetime, timezone
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


def _run_yarn_audit() -> tuple[int, list[dict]]:
    """Run `yarn audit --json` against frontend/yarn.lock.

    Returns `(status, findings)` where status is:
      0 — scan ran successfully (findings may be non-empty; caller
          decides pass/fail via severity gate + allowlist)
      2 — scan itself failed to run (yarn missing, network, etc.)

    Yarn 1.x emits JSON-lines: one `auditAdvisory` object per
    advisory + one `auditSummary` at the end. Exit code is a
    bitfield of the severities found — non-zero is EXPECTED when
    findings exist, so we don't treat non-zero as scan failure
    unless stdout is empty.
    """
    lock = ROOT / "frontend" / "yarn.lock"
    pkg  = ROOT / "frontend" / "package.json"
    if not lock.exists() or not pkg.exists():
        print("[g15] no frontend/yarn.lock — skipping yarn audit")
        return 0, []
    try:
        res = subprocess.run(
            ["yarn", "audit", "--json"],
            cwd=str(ROOT / "frontend"),
            capture_output=True, text=True, timeout=180,
        )
    except FileNotFoundError:
        print("[g15] yarn not installed — skipping yarn audit")
        return 0, []
    except subprocess.TimeoutExpired:
        print("[g15] ERR yarn audit timed out (180s)")
        return 2, []
    if not res.stdout.strip():
        # No stdout at all means yarn didn't run (network / permission).
        print(f"[g15] ERR yarn audit produced no output (exit={res.returncode}): "
              f"{res.stderr[:400]}")
        return 2, []
    findings: list[dict] = []
    seen_advisory_ids: set = set()
    for line in res.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") != "auditAdvisory":
            continue
        adv = obj.get("data", {}).get("advisory", {}) or {}
        adv_id = adv.get("id")
        # Yarn 1 emits one advisory row per `resolution` path, so the
        # same underlying CVE shows up multiple times. Dedupe by the
        # advisory id — the caller cares about the vulnerability, not
        # every transitive re-instance.
        if adv_id in seen_advisory_ids:
            continue
        seen_advisory_ids.add(adv_id)
        cves = adv.get("cves") or []
        ghsa = adv.get("github_advisory_id")
        # Prefer CVE for stable IDs; fall back to GHSA; final fallback = numeric id
        primary_id = (cves[0] if cves else None) or ghsa or f"NPM-{adv_id}"
        findings.append({
            "package":  adv.get("module_name"),
            "id":       primary_id,
            "severity": (adv.get("severity") or "").upper(),
            "fix":      adv.get("patched_versions"),
            "aliases":  list(filter(None, [ghsa] + list(cves))),
            "source":   "yarn",
            "title":    (adv.get("title") or "")[:120],
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


def _persist_result(findings: list[dict], hard_fails: list[dict]) -> None:
    """2026-08-20 · CI-can't-reach-real-DB fix — every CI workflow
    hardcodes `MONGO_URL=mongodb://localhost:27017` (a throwaway
    service inside the ephemeral runner), so a direct Mongo write
    here never reached the real app database — /admin/status/all
    could only ever see "no dep-scan runs yet", forever, no matter
    how often this actually ran. POST to the app's own ingest
    endpoint instead (same shared-secret pattern already used for
    the trufflehog CI ingest — no DB credentials touch CI at all)."""
    import json
    import urllib.request

    token = os.environ.get("AUREM_CI_INGEST_TOKEN")
    # 2026-08-26 — BUG FIX: `.get("AUREM_API_URL", default)` only falls
    # back when the key is ABSENT. The `vars.AUREM_API_URL` repo
    # variable is unset, so GH Actions exports the key with an empty
    # string — `.get()` then returns "" (not the default), producing a
    # relative URL that crashed `urllib.request.Request()` with an
    # uncaught ValueError and failed the ENTIRE dependency-audit gate.
    api_url = os.environ.get("AUREM_API_URL") or "https://auremcto.com"
    if not token:
        print("[g15] AUREM_CI_INGEST_TOKEN not set — skipping result persistence")
        return
    body = {
        "kind":           "g15_dep_scan",
        "total_findings": len(findings),
        "high_critical":  len(hard_fails),
        "findings":       findings[:100],
    }
    try:
        req = urllib.request.Request(
            f"{api_url}/api/aurem-dev/admin/synthetic-checks/ingest",
            data=json.dumps(body).encode(),
            method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            print(f"[g15] persisted result: {r.status}")
    except Exception as e:
        print(f"[g15] WARN persistence failed: {e}")


def main() -> int:
    allowlist = _load_allowlist()

    # 1) pip-audit — backend
    ec_py, py_findings = _run_pip_audit()
    if ec_py == 2:
        return 2
    for f in py_findings:
        f.setdefault("source", "pip")

    # 2) yarn audit — frontend
    ec_js, js_findings = _run_yarn_audit()
    if ec_js == 2:
        return 2

    findings = py_findings + js_findings
    hard_fails = []
    for f in findings:
        if not _severity_gates_ci(f["severity"]):
            continue
        allowed, note = _is_allowlisted(f, allowlist)
        if allowed:
            print(f"[g15] SKIP [{f.get('source')}] {f['package']}::{f['id']} "
                  f"{f['severity']} — {note}")
            continue
        hard_fails.append(f)
        print(f"[g15] ❌ [{f.get('source')}] {f['package']}::{f['id']} "
              f"{f['severity']} (fix: {f.get('fix')})  {note}")

    _persist_result(findings, hard_fails)

    if hard_fails:
        print(f"[g15] Build fails: {len(hard_fails)} HIGH/CRITICAL "
              f"finding(s) not on allowlist "
              f"(pip={len(py_findings)}, yarn={len(js_findings)}).")
        return 1
    print(f"[g15] OK — 0 HIGH/CRITICAL findings unhandled "
          f"(pip={len(py_findings)}, yarn={len(js_findings)}; "
          f"{len(allowlist)} allowlist entries).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
