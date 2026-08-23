"""
Iter 170c — codebase browsing endpoints test.

Covers:
  GET /cto/projects/{id}/tree   — happy path + filters + sort order
  GET /cto/projects/{id}/file   — happy path + path-traversal guard
                                   + truncation marker

The actual GitHub HTTP calls are mocked via monkey-patching httpx.AsyncClient
and the gh_api_fetch_file helper so we never hit github.com from tests.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch, MagicMock

# Make backend importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient


def _client_with_seeded_project():
    """Boot the app with an in-memory project that has fake GitHub creds."""
    from main import app
    from routers import cto_projects as cp

    # Stub auth to a fixed user
    async def fake_current_dev(_authorization=None):
        return {"user_id": "u_test", "email": "t@t.t"}
    cp.current_dev = fake_current_dev

    # Stub DB to return a project with github connection.
    # 2026-06 PAT-removal — App-only auth: get_repo_token_or_error()
    # requires auth_method="github_app" + installation_id, else it
    # raises app_installation_missing (403), so the seeded project must
    # carry those fields; the actual token mint is stubbed separately
    # below rather than mocking the real GitHub HTTP call here.
    project_doc = {
        "project_id": "p_test",
        "user_id": "u_test",
        "github_owner": "octo",
        "github_repo": "demo",
        "branch": "main",
        "auth_method": "github_app",
        "installation_id": 9001,
        "name": "demo",
    }
    fake_db = MagicMock()
    fake_db.cto_projects = MagicMock()
    fake_db.cto_projects.find_one = AsyncMock(return_value=project_doc)
    cp.require_db = lambda: fake_db
    cp.get_db = lambda: fake_db

    from services import pat_vault as _pv
    async def _fake_get_repo_token_or_error(_proj):
        return "ghs_fake_app_token", None, None
    _pv.get_repo_token_or_error = _fake_get_repo_token_or_error

    return TestClient(app), project_doc


class TestTreeEndpoint(unittest.TestCase):
    def test_tree_filters_and_sorts(self):
        client, _ = _client_with_seeded_project()

        # Fake GitHub /git/trees/{branch} payload
        fake_tree = {
            "tree": [
                {"type": "blob", "path": "node_modules/lib/x.js", "size": 100},
                {"type": "blob", "path": "README.md",            "size":  500},
                {"type": "blob", "path": "src/app.py",           "size": 1000},
                {"type": "blob", "path": "package.json",         "size":  300},
                {"type": "blob", "path": "deep/sub/file.ts",     "size":  200},
                {"type": "blob", "path": "image.png",            "size":  500},
                {"type": "blob", "path": "huge.txt",             "size": 999999},
                {"type": "tree", "path": "src", "size": 0},
            ],
            "truncated": False,
        }

        async def fake_get(self, url, headers=None):  # noqa: ARG001
            resp = MagicMock()
            resp.status_code = 200
            resp.json = lambda: fake_tree
            resp.raise_for_status = lambda: None
            return resp

        with patch("httpx.AsyncClient.get", new=fake_get):
            r = client.get(
                "/api/aurem-dev/cto/projects/p_test/tree",
                headers={"Authorization": "Bearer x"},
            )
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        paths = [f["path"] for f in data["files"]]
        # node_modules + image + huge filtered out
        self.assertNotIn("node_modules/lib/x.js", paths)
        self.assertNotIn("image.png", paths)
        self.assertNotIn("huge.txt", paths)
        # README first, then root package.json, then by depth/alpha
        self.assertEqual(paths[0], "README.md")
        self.assertEqual(paths[1], "package.json")
        # owner/repo/branch echoed back
        self.assertEqual(data["owner"], "octo")
        self.assertEqual(data["repo"], "demo")
        self.assertEqual(data["branch"], "main")


class TestFileEndpoint(unittest.TestCase):
    def test_file_happy_path(self):
        client, _ = _client_with_seeded_project()
        from routers import cto_projects as cp

        # 2026-08-22 — bugfix: gh_api_fetch_file (services.github_api_writer
        # .fetch_file) takes 5 args (owner, repo, path, ref, token) — it opens
        # its OWN httpx client internally, no `client` param. The route used
        # to pass an extra client arg, which raised a TypeError on every
        # real call (caught + converted to a clean HTTP 502 that Cloudflare
        # then replaced with its own generic error page — see the bug
        # report this fixed). This mock's signature now matches the real
        # (fixed) call site instead of the old broken one.
        async def fake_fetch_file(_o, _r, path, _b, _t):
            return f"# content of {path}\nprint('hi')\n"

        with patch.object(cp, "gh_api_fetch_file", side_effect=fake_fetch_file):
            r = client.get(
                "/api/aurem-dev/cto/projects/p_test/file?path=src/app.py",
                headers={"Authorization": "Bearer x"},
            )
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertIn("content of src/app.py", data["content"])
        self.assertFalse(data["truncated"])

    def test_file_happy_path_exercises_real_fetch_file(self):
        """2026-08-22 — regression for a real production bug: the route
        called `gh_api_fetch_file(client, owner, repo, path, branch, token)`
        (6 args) but the real function only takes 5 (owner, repo, path,
        ref, token) — it opens its own internal http client. Every real
        call raised TypeError, caught and turned into a clean 502, which
        Cloudflare then replaced with its own generic error page. Unlike
        `test_file_happy_path` above (which mocks gh_api_fetch_file
        itself and so can't catch a signature mismatch), this test mocks
        only the raw GitHub HTTP response and lets the REAL fetch_file
        run, so a signature regression here fails loudly."""
        client, _ = _client_with_seeded_project()
        import base64

        async def fake_get(self, url, headers=None):  # noqa: ARG001
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = lambda: None
            resp.json = lambda: {
                "encoding": "base64",
                "content": base64.b64encode(b"# real README\n").decode(),
            }
            return resp

        with patch("httpx.AsyncClient.get", new=fake_get):
            r = client.get(
                "/api/aurem-dev/cto/projects/p_test/file?path=README.md",
                headers={"Authorization": "Bearer x"},
            )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("real README", r.json()["content"])

    def test_file_path_traversal_rejected(self):
        client, _ = _client_with_seeded_project()
        for bad in ["../etc/passwd", "/abs/path", "a/../b"]:
            r = client.get(
                f"/api/aurem-dev/cto/projects/p_test/file?path={bad}",
                headers={"Authorization": "Bearer x"},
            )
            self.assertEqual(r.status_code, 400, f"{bad}: {r.text}")

    def test_file_missing_returns_404(self):
        client, _ = _client_with_seeded_project()
        from routers import cto_projects as cp

        async def fake_fetch_file(*_a, **_k):
            return None

        with patch.object(cp, "gh_api_fetch_file", side_effect=fake_fetch_file):
            r = client.get(
                "/api/aurem-dev/cto/projects/p_test/file?path=missing.md",
                headers={"Authorization": "Bearer x"},
            )
        self.assertEqual(r.status_code, 404)

    def test_file_truncation_marker(self):
        client, _ = _client_with_seeded_project()
        from routers import cto_projects as cp

        # 300 KB of "a" → exceeds 200KB cap
        huge = "a" * (300 * 1024)

        async def fake_fetch_file(*_a, **_k):
            return huge

        with patch.object(cp, "gh_api_fetch_file", side_effect=fake_fetch_file):
            r = client.get(
                "/api/aurem-dev/cto/projects/p_test/file?path=big.txt",
                headers={"Authorization": "Bearer x"},
            )
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertTrue(data["truncated"])
        self.assertIn("# … (truncated)", data["content"])
        # Truncated payload must NOT exceed the budget (plus tail marker)
        self.assertLess(len(data["content"]), 210 * 1024)


if __name__ == "__main__":
    unittest.main()
