"""
Iter 212m-114 — REAL finding-fix pipeline for Security Scan + Bug Hunt.

Verifies:
  • services/finding_fix_applier.apply_finding_fix() — full pipeline
    (fetch file → LLM patch → re-validate → commit) returns ok=True
    only when ALL stages succeed.
  • Re-validation gate: if the patched content STILL triggers the
    original rule_id, the call returns ok=False and NEVER pushes a
    commit. This is the "no dummy fix" guarantee.
  • Security scan POST /security-scan/fix endpoint:
        - Per-finding token deduction
        - Founder bypass (is_admin / is_unlimited / tier=='founder')
        - Token REFUND on any apply failure
        - Real commit_sha on success
  • Codebase health POST /codebase-health/fix endpoint same guarantees.
"""
from __future__ import annotations

import os
import pytest
from unittest.mock import AsyncMock


# ─── 1. apply_finding_fix end-to-end ──────────────────────────────────
@pytest.mark.asyncio
async def test_apply_finding_fix_happy_path(monkeypatch):
    """When the LLM produces a patch that DOES remove the finding, the
    pipeline must call commit_files and return ok=True with the commit
    SHA."""
    from services import finding_fix_applier as ff

    # Mock project lookup
    class _Proj:
        async def find_one(self, q, proj=None):
            return {
                "github_owner": "polarisbuiltinc",
                "github_repo":  "auremdev",
                "github_branch": "main",
                "github_token": None,
            }
    class _Users:
        async def find_one(self, q, proj=None):
            return {"github": {"access_token": "ghp_realtoken"}}
    class _Fixes:
        async def insert_one(self, doc): pass
    class _DB:
        cto_projects   = _Proj()
        dev_users      = _Users()
        finding_fixes  = _Fixes()

    # Mock the PAT decrypt
    import routers.security_scan as ss
    async def fake_decrypt(uid, tok): return None  # fall back to OAuth
    monkeypatch.setattr(ss, "_decrypt_pat", fake_decrypt)

    # Mock the GitHub fetch
    async def fake_fetch(owner, repo, branch, path, token):
        assert token == "ghp_realtoken"
        return f"API_KEY = '{os.environ.get('TEST_AWS_ACCESS_KEY', '')}'  # TODO: set env var TEST_AWS_ACCESS_KEY\n", None
    monkeypatch.setattr(ff, "_fetch_file_content", fake_fetch)

    # Mock the LLM patch generator
    async def fake_llm(*, path, current_content, finding, user_id):
        return ('import os\nAPI_KEY = os.environ.get("AWS_ACCESS_KEY_ID")  # TODO: set env var\n',
                None)
    monkeypatch.setattr(ff, "_generate_patched_content", fake_llm)

    # Mock the re-validation — return False = finding is gone
    monkeypatch.setattr(ff, "_finding_still_present",
                        lambda patched, path, finding: False)

    # Mock the commit_files call
    commits = []
    async def fake_commit(**kw):
        commits.append(kw)
        return {
            "sha":      "1234abc",
            "full_sha": "1234abcdef" * 4,
            "html_url": "https://github.com/polarisbuiltinc/auremdev/commit/1234abc",
        }
    import services.github_api_writer as gw
    monkeypatch.setattr(gw, "commit_files", fake_commit)

    finding = {
        "rule_id":  "secret_aws_access_key",
        "file":     "app.py",
        "line":     1,
        "severity": "critical",
        "title":    "Hardcoded AWS access key id",
        "message":  "AKIA literal in source",
        "snippet":  "API_KEY = 'AKIA…XX'",
    }
    res = await ff.apply_finding_fix(
        db=_DB(), user={"user_id": "u1"},
        project_id="proj_1", finding=finding,
    )

    assert res["ok"] is True
    assert res["commit_sha"] == "1234abc"
    assert res["rule_id"] == "secret_aws_access_key"
    assert "app.py" in res["message"]
    assert commits and commits[0]["files"] == {
        "app.py": 'import os\nAPI_KEY = os.environ.get("AWS_ACCESS_KEY_ID")  # TODO: set env var\n',
    }


@pytest.mark.asyncio
async def test_apply_finding_fix_rejects_unresolved_patch(monkeypatch):
    """The CRITICAL safety gate. If the LLM produces a patch that still
    has the same rule_id triggering, NO commit is pushed and ok=False."""
    from services import finding_fix_applier as ff

    class _Proj:
        async def find_one(self, q, proj=None):
            return {"github_owner": "o", "github_repo": "r",
                    "github_branch": "main", "github_token": "ghp_x"}
    class _DB:
        cto_projects  = _Proj()
        finding_fixes = type("F", (), {"insert_one": AsyncMock()})()

    import routers.security_scan as ss
    async def fake_decrypt(uid, tok): return "ghp_realtoken"
    monkeypatch.setattr(ss, "_decrypt_pat", fake_decrypt)
    async def fake_fetch(*a, **k):
        return "AWS = 'AKIAEXAMPLE'\n", None
    monkeypatch.setattr(ff, "_fetch_file_content", fake_fetch)
    async def fake_llm(*, path, current_content, finding, user_id):
        # LLM "fixes" it but still leaves an AKIA literal — re-scan trips.
        return "AWS = 'AKIAOTHEREXAMPLE'\n", None
    monkeypatch.setattr(ff, "_generate_patched_content", fake_llm)
    monkeypatch.setattr(ff, "_finding_still_present",
                        lambda patched, path, finding: True)  # still there

    commits = []
    async def fake_commit(**kw):
        commits.append(kw)
        return {}
    import services.github_api_writer as gw
    monkeypatch.setattr(gw, "commit_files", fake_commit)

    res = await ff.apply_finding_fix(
        db=_DB(), user={"user_id": "u1"}, project_id="p1",
        finding={"rule_id": "secret_aws_access_key", "file": "x.py",
                 "severity": "critical", "title": "AWS leak"},
    )
    assert res["ok"] is False
    assert res["error"] == "patch_did_not_resolve_finding"
    assert commits == [], "MUST NOT commit when re-validation fails"


@pytest.mark.asyncio
async def test_apply_finding_fix_no_credentials_returns_clean_error(monkeypatch):
    from services import finding_fix_applier as ff

    class _DB:
        cto_projects = type("P", (), {
            "find_one": AsyncMock(return_value={
                "github_owner": "", "github_repo": "", "github_branch": "main",
                "github_token": None,
            }),
        })()
        dev_users = type("U", (), {
            "find_one": AsyncMock(return_value={"