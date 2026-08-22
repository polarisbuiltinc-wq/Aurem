"""
services/deploy_readiness.py — Deploy Readiness (Option A, 2026-08-24).

ADVISORY ONLY: the Emergent "Deploy" button is a manual platform action
with no gate/API/webhook (platform-confirmed 2026-08-24). This card can
not physically block a deploy — it verifies Rule C (PRD 2026-08-24):
deploy only when the latest pushed SHA is CI+QG green, OR a full
testing_agent verification report exists for the exact workspace state.
"""
from __future__ import annotations

import os
import subprocess
import time

import httpx

_GH_API = "https://api.github.com"
_CI_NAME = "AUREM CI — Build + Test Guard"
_QG_NAME = "Quality Gate — Bug-fix Discipline"
_CACHE: dict = {"ts": 0.0, "data": None}
CACHE_TTL_S = 60


def _git(args: list[str]) -> str | None:
    try:
        out = subprocess.run(["git"] + args, cwd="/app", capture_output=True,
                             text=True, timeout=5)
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception:
        return None


def _workspace_state() -> dict:
    sha = _git(["rev-parse", "HEAD"])
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"])
    porcelain = _git(["status", "--porcelain"])
    dirty = len([l for l in (porcelain or "").splitlines() if l.strip()]) if porcelain is not None else None
    return {"sha": sha, "short_sha": (sha or "")[:7] or None,
            "branch": branch, "dirty_files": dirty,
            "git_available": sha is not None}


async def _remote_state(token: str, repo: str) -> dict:
    headers = {"Authorization": f"Bearer {token}",
               "Accept": "application/vnd.github+json"}
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        r = await client.get(f"{_GH_API}/repos/{repo}/commits/main", headers=headers)
        r.raise_for_status()
        head = r.json()
        head_sha = head.get("sha") or ""
        runs_r = await client.get(
            f"{_GH_API}/repos/{repo}/actions/runs",
            params={"head_sha": head_sha, "per_page": 30}, headers=headers)
        runs = runs_r.json().get("workflow_runs", []) if runs_r.is_success else []
    checks = {}
    for run in runs:
        name = run.get("name") or ""
        key = "ci" if name == _CI_NAME else ("quality_gate" if name == _QG_NAME else None)
        if key and key not in checks:
            checks[key] = {"name": name, "conclusion": run.get("conclusion"),
                           "status": run.get("status"), "url": run.get("html_url")}
    return {"sha": head_sha, "short_sha": head_sha[:7],
            "date": ((head.get("commit") or {}).get("committer") or {}).get("date"),
            "checks": checks}


async def get_deploy_readiness() -> dict:
    now = time.time()
    if _CACHE["data"] and now - _CACHE["ts"] < CACHE_TTL_S:
        return _CACHE["data"]

    ws = _workspace_state()
    token = (os.environ.get("GITHUB_ACTIONS_TOKEN")
             or os.environ.get("GITHUB_TOKEN") or "").strip()
    repo = (os.environ.get("GITHUB_REPO") or "").strip()

    reasons: list[str] = []
    remote: dict | None = None
    if not (token and repo):
        reasons.append("GitHub not wired (GITHUB_ACTIONS_TOKEN / GITHUB_REPO unset)")
    else:
        try:
            remote = await _remote_state(token, repo)
        except httpx.HTTPStatusError as exc:
            reasons.append(f"GitHub API {exc.response.status_code} on {exc.request.url.path}")
        except Exception as exc:
            reasons.append(f"GitHub unreachable: {type(exc).__name__}")

    matches = bool(ws.get("sha") and remote and ws["sha"] == remote["sha"])
    if ws.get("git_available"):
        if ws.get("dirty_files"):
            reasons.append(f"workspace has {ws['dirty_files']} uncommitted change(s) — not on GitHub")
        if remote and not matches:
            reasons.append(f"workspace {ws.get('short_sha')} ≠ GitHub main {remote.get('short_sha')} — unpushed commits")
    else:
        reasons.append("no .git in this runtime — workspace SHA unknown (production pod)")

    ci = (remote or {}).get("checks", {}).get("ci")
    qg = (remote or {}).get("checks", {}).get("quality_gate")
    if remote:
        for label, chk in (("CI", ci), ("Quality Gate", qg)):
            if not chk:
                reasons.append(f"{label} has not run for GitHub main {remote.get('short_sha')}")
            elif chk.get("conclusion") != "success":
                reasons.append(f"{label} is {chk.get('conclusion') or chk.get('status')} for {remote.get('short_sha')}")

    verdict = "ready" if (matches and not reasons) else "not_ready"
    data = {
        "verdict": verdict,
        "reasons": reasons,
        "workspace": ws,
        "remote": remote,
        "workspace_matches_remote": matches,
        "rule_c": "Deploy only when the latest Save-to-GitHub SHA is CI+QG green, "
                  "OR a full testing_agent verification report exists for the exact "
                  "workspace state being deployed.",
        "advisory_note": "ADVISORY ONLY — the Emergent Deploy button cannot be "
                         "mechanically blocked (no platform gate/API/webhook, "
                         "confirmed 2026-08-24).",
        "checked_at": now,
    }
    _CACHE.update(ts=now, data=data)
    return data
