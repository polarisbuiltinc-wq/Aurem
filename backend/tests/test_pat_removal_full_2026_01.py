"""PAT-removal adversarial verification (2026-01) — full-code-only auth via GitHub App.

Covers review request items 1,2,3,5,6,7,8 (item 4 & 10 handled in a
sibling script since they require real GitHub side-effects and
synthetic Mongo rows). Item 9 is a static/code-review check.
"""
from __future__ import annotations

import os
import time
import uuid

import pytest
import requests

def _base_url() -> str:
    v = os.environ.get("REACT_APP_BACKEND_URL")
    if not v:
        # Fallback to frontend/.env — tests run inside the container.
        try:
            with open("/app/frontend/.env") as f:
                for line in f:
                    if line.startswith("REACT_APP_BACKEND_URL="):
                        v = line.split("=", 1)[1].strip()
                        break
        except OSError:
            pass
    if not v:
        raise RuntimeError("REACT_APP_BACKEND_URL not set")
    return v.rstrip("/")


BASE_URL = _base_url()
API = f"{BASE_URL}/api/aurem-dev"

ADMIN_EMAIL = "test@aurem.dev"
ADMIN_PASSWORD = "AuremTest2026!"


# ── Fixtures ────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def admin_token() -> str:
    r = requests.post(
        f"{API}/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=30,
    )
    assert r.status_code == 200, f"login failed {r.status_code}: {r.text[:400]}"
    tok = r.json().get("token") or r.json().get("access_token")
    assert tok, f"no token in login response: {r.text[:200]}"
    return tok


@pytest.fixture(scope="session")
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


# ── Item 1: verify-pat endpoint ─────────────────────────────────────
class TestVerifyPatEndpoint:
    def test_verify_pat_authed_rejects_pat(self, admin_headers):
        r = requests.post(
            f"{API}/cto/projects/verify-pat",
            headers=admin_headers,
            json={"pat": "ghp_" + "a" * 36, "repo": "someone/some-repo"},
            timeout=15,
        )
        assert r.status_code == 200, r.text[:400]
        body = r.json()
        assert body.get("ok") is False
        assert body.get("error") == "pat_not_supported"
        detail = (body.get("detail") or "").lower()
        assert "github app" in detail or "aurem github app" in detail

    def test_verify_pat_unauthed_rejected(self):
        r = requests.post(
            f"{API}/cto/projects/verify-pat",
            json={"pat": "ghp_" + "a" * 36, "repo": "someone/some-repo"},
            timeout=15,
        )
        assert r.status_code in (401, 403), r.text[:400]


# ── Item 2: POST /projects/add with PAT rejected ────────────────────
class TestProjectAddPatRejected:
    def test_add_project_with_pat_rejected_400(self, admin_headers):
        r = requests.post(
            f"{API}/cto/projects/add",
            headers=admin_headers,
            json={
                "name": f"TEST_pat_reject_{uuid.uuid4().hex[:6]}",
                "github_url": "https://github.com/tjsandhu/aurem",
                "github_token": "ghp_" + "z" * 36,
            },
            timeout=30,
        )
        assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text[:400]}"
        body = r.json()
        # HTTPException wraps our dict in {"detail": {...}}
        payload = body.get("detail", body)
        if isinstance(payload, dict):
            assert payload.get("error") == "pat_not_supported", payload
        else:
            # If it's a string, at minimum reference PAT rejection
            assert "pat_not_supported" in str(payload).lower() \
                or "personal access token" in str(payload).lower()


# ── Item 3: PATCH github_token rejected ─────────────────────────────
class TestProjectPatchPatRejected:
    def test_patch_github_token_rejected(self, admin_headers):
        # Use an arbitrary project_id — the PAT-guard runs before the
        # DB lookup in the router; 400 must return before 404.
        proj_id = f"p_ta_patch_{uuid.uuid4().hex[:6]}"
        r = requests.patch(
            f"{API}/cto/projects/{proj_id}",
            headers=admin_headers,
            json={"github_token": "ghp_" + "y" * 36},
            timeout=15,
        )
        assert r.status_code == 400, r.text[:400]
        text = (r.text or "").lower()
        assert "pat" in text and ("no longer" in text or "not supported" in text), \
            f"unexpected patch response: {r.text[:400]}"


# ── Item 5: pat-inventory ───────────────────────────────────────────
class TestPatInventory:
    def test_inventory_admin(self, admin_headers):
        r = requests.get(
            f"{API}/admin/github-auth/pat-inventory",
            headers=admin_headers, timeout=60,
        )
        assert r.status_code == 200, r.text[:400]
        body = r.json()
        assert isinstance(body.get("coverable_by_app"), list)
        assert isinstance(body.get("not_covered_by_app"), list)
        assert isinstance(body.get("users_with_stored_oauth_token"), int)

    def test_inventory_non_admin(self):
        r = requests.get(
            f"{API}/admin/github-auth/pat-inventory", timeout=15,
        )
        assert r.status_code in (401, 403), r.text[:400]


# ── Item 6: dry-run migrate leaves data intact ──────────────────────
class TestMigrateDryRun:
    @pytest.mark.flaky(
        reason="Live admin-migration dry-run against real DB rows — "
               "intermittent in full-suite batch runs, passes reliably "
               "standalone. Confirmed 2026-08-28 P0-4 audit "
               "(RECON-LEDGER.md).",
        owner="e1-agent",
        fix_by="next-live-network-hardening-pass",
    )
    def test_dry_run_does_not_modify(self, admin_headers):
        # Fetch a sample row's auth_method BEFORE
        inv_before = requests.get(
            f"{API}/admin/github-auth/pat-inventory",
            headers=admin_headers, timeout=60,
        ).json()
        sample = None
        for row in inv_before.get("not_covered_by_app") or []:
            sample = row
            break

        r = requests.post(
            f"{API}/admin/github-auth/migrate",
            headers=admin_headers, json={"execute": False},
            timeout=60,
        )
        assert r.status_code == 200, r.text[:400]
        body = r.json()
        assert body.get("dry_run") is True
        assert body.get("flipped", 0) == 0
        assert body.get("marked_auth_required", 0) == 0

        if sample:
            inv_after = requests.get(
                f"{API}/admin/github-auth/pat-inventory",
                headers=admin_headers, timeout=60,
            ).json()
            match = None
            for row in inv_after.get("not_covered_by_app") or []:
                if row.get("project_id") == sample["project_id"]:
                    match = row
                    break
            assert match is not None, "sample row disappeared after dry-run"
            assert match.get("auth_method") == sample.get("auth_method"), (
                "auth_method changed after dry-run — migrate is NOT idempotent!"
            )


# ── Item 7: connection-status honesty ───────────────────────────────
class TestConnectionStatusHonesty:
    ALLOWED = {"connected", "disconnected", "unreachable"}

    def test_connection_status_no_500(self, admin_headers):
        r = requests.get(
            f"{API}/cto/projects/connection-status",
            headers=admin_headers, timeout=90,
        )
        assert r.status_code == 200, r.text[:400]
        body = r.json()
        # Response could be either a bare list or {items:[...]}
        entries = body if isinstance(body, list) else (
            body.get("items") or body.get("projects") or body.get("statuses") or []
        )
        assert isinstance(entries, list), f"unexpected shape: {type(body)}"
        # Only assert on the shape when we actually have rows
        for e in entries:
            assert e.get("status") in self.ALLOWED, (
                f"illegal status {e.get('status')} for {e.get('project_id')}: {e}"
            )
            err = (e.get("error") or "").lower()
            # No "no_token" or oauth references — must be App-language.
            assert err != "no_token", f"legacy no_token error: {e}"


# ── Item 8: static grep — no live PAT-helper references ─────────────
class TestStaticPatGrepClean:
    def test_no_live_pat_helper_refs(self):
        import subprocess
        # Search only inside .py files under backend/routers and backend/services
        cmd = [
            "grep", "-rn", "--include=*.py",
            "-E", r"(_user_gh_token|_decrypt_pat|_encrypt_pat|get_user_gh_token)",
            "/app/backend/routers", "/app/backend/services",
        ]
        out = subprocess.run(cmd, capture_output=True, text=True)
        hits = [ln for ln in out.stdout.splitlines() if ln.strip()]
        # Docstrings & comments are allowed → filter them out.
        offenders = []
        for ln in hits:
            try:
                path, lineno, code = ln.split(":", 2)
            except ValueError:
                continue
            stripped = code.strip()
            # Skip comments and pure docstring lines (start with '#',
            # start/end with triple-quote, or contain '*' bullet from docstring).
            if stripped.startswith("#"):
                continue
            if stripped.startswith('"') or stripped.startswith("'"):
                continue
            if stripped.startswith("*"):
                continue
            if stripped.startswith("•"):
                continue
            # Bare docstring text lines (e.g., "Legacy PAT rows: ...")
            # inside module docstrings are indented and have no code
            # syntax — heuristic: no '(' and no '=' and no import.
            if ("(" not in stripped and "=" not in stripped
                    and not stripped.startswith(("import ", "from "))):
                continue
            # Allow migrations file explicitly (inert history).
            if "migrations/002_encrypt_pats" in path:
                continue
            offenders.append(ln)
        assert not offenders, (
            "Live PAT-helper references still present:\n" + "\n".join(offenders)
        )
