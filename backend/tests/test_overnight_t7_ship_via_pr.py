"""Overnight T7 (Wave 2 · ship-via-PR) — guardrail tests.

Named tests (per the overnight run spec):
  t_ship_via_pr                    — flag on/off both paths
  t_no_orphan_branches             — close_and_retract deletes the branch
  t_label_dispatch_no_crosswrite   — aura:ship vs auremcto/visibility-kit-*
                                       write to DIFFERENT collections
  t_revert_reverse_path_alive      — existing reverse-commit revert path
                                       (user_rollback -> loop_rollback ->
                                       revert_commit) is UNTOUCHED
  t_delete_ship_branch_namespaced  — only auremcto/ branches deletable

Uses respx to mock GitHub's REST API (no real network calls) and the
same lightweight FakeDB/FakeCollection pattern already established in
tests/test_iter362_guard19_recovery.py (upsert support added here).
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
import respx
import httpx

from services import loop_safety as ls
from services.feature_flags import is_enabled as ff_is_enabled


class FakeCollection:
    def __init__(self):
        self.docs = []

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": len(self.docs)})()

    async def find_one(self, query, proj=None):
        for d in self.docs:
            if self._match(d, query):
                return d
        return None

    async def update_one(self, query, update, upsert=False):
        for d in self.docs:
            if self._match(d, query):
                self._apply(d, update)
                return type("R", (), {"modified_count": 1, "upserted_id": None})()
        if upsert:
            new = {}
            for k, cond in query.items():
                if not isinstance(cond, dict):
                    new[k] = cond
            self._apply(new, update)
            self.docs.append(new)
            return type("R", (), {"modified_count": 0, "upserted_id": "new"})()
        return type("R", (), {"modified_count": 0, "upserted_id": None})()

    @staticmethod
    def _apply(d, update):
        for k, v in update.get("$set", {}).items():
            d[k] = v

    @staticmethod
    def _match(d, query):
        for k, cond in query.items():
            if isinstance(cond, dict):
                continue  # not needed for these tests
            if d.get(k) != cond:
                return False
        return True


class FakeDB:
    def __init__(self):
        self.loop_outcomes = FakeCollection()
        self.ship_pr_events = FakeCollection()
        self.visibility_kit_pr_events = FakeCollection()


GH = "https://api.github.com"


# ── t_ship_via_pr ───────────────────────────────────────────────────
class TestShipViaPr:
    @pytest.mark.asyncio
    async def test_flag_off_by_default_no_row(self):
        # No feature_flags row anywhere (Preview Mongo not seeded in
        # this unit test's DB) -> is_enabled() returns False. This IS
        # the prod-fence behavior: no row = OFF, no env var needed.
        from services import feature_flags as ff
        ff.invalidate_cache()
        ff._cache = {}  # force a fresh (empty) load in this process
        ff._cache_ts = time.monotonic()  # pretend just-loaded so _load_flags returns {}
        assert await ff_is_enabled("ship_via_pr", user_id="u1") is False

    @pytest.mark.asyncio
    @respx.mock
    async def test_flag_on_path_creates_auremcto_branch_and_opens_pr(self):
        respx.get(f"{GH}/repos/o/r/git/refs/heads/main").respond(
            200, json={"object": {"sha": "basesha123"}})
        respx.post(f"{GH}/repos/o/r/git/refs").respond(201, json={"ref": "ok"})
        respx.post(f"{GH}/repos/o/r/pulls").respond(
            201, json={"html_url": "https://github.com/o/r/pull/42"})
        respx.post(f"{GH}/repos/o/r/issues/42/labels").respond(201, json=[])

        branch = ls.ship_branch_name("loop-abc123")
        assert branch.startswith("auremcto/ship-")

        ok, err = await ls.create_or_reuse_branch(
            owner="o", repo="r", base_branch="main",
            new_branch=branch, token="tkn",
        )
        assert ok and err is None

        pr_url, pr_err = await ls.open_draft_pr(
            owner="o", repo="r", head_branch=branch, base_branch="main",
            title="ship: test", body="body", token="tkn",
        )
        assert pr_err is None
        assert pr_url == "https://github.com/o/r/pull/42"

        label_ok, label_err = await ls.add_pr_label(
            owner="o", repo="r", pr_number=42, label="aura:ship", token="tkn",
        )
        assert label_ok and label_err is None


# ── t_no_orphan_branches ────────────────────────────────────────────
class TestNoOrphanBranches:
    @pytest.mark.asyncio
    @respx.mock
    async def test_close_and_retract_deletes_auremcto_branch(self):
        respx.patch(f"{GH}/repos/o/r/pulls/7").respond(200, json={"state": "closed"})
        respx.delete(f"{GH}/repos/o/r/git/refs/heads/auremcto/ship-x-1").respond(204)

        result = await ls.close_and_retract(
            owner="o", repo="r", pr_number=7,
            branch="auremcto/ship-x-1", token="tkn",
        )
        assert result["pr_closed"] is True
        assert result["branch_deleted"] is True
        assert result["errors"] == []


# ── t_label_dispatch_no_crosswrite ──────────────────────────────────
class TestLabelDispatchNoCrosswrite:
    @pytest.mark.asyncio
    async def test_ship_label_writes_only_loop_outcomes(self):
        db = FakeDB()
        await db.loop_outcomes.insert_one({"ship_branch": "auremcto/ship-x-1",
                                            "pr_status": "open"})
        payload = {
            "pull_request": {
                "number": 42, "merged": True,
                "html_url": "https://github.com/o/r/pull/42",
                "head": {"ref": "auremcto/ship-x-1"},
                "labels": [{"name": "aura:ship"}],
            },
            "repository": {"full_name": "o/r"},
        }
        out = await ls.dispatch_pull_request_webhook(db, payload=payload, action="closed")
        assert out["routed"] == "ship"
        assert out["status"] == "merged"
        row = await db.loop_outcomes.find_one({"ship_branch": "auremcto/ship-x-1"})
        assert row["pr_status"] == "merged"
        # NO cross-write into the kit collection.
        assert db.visibility_kit_pr_events.docs == []

    @pytest.mark.asyncio
    async def test_kit_label_writes_only_kit_collection(self):
        db = FakeDB()
        payload = {
            "pull_request": {
                "number": 99, "merged": False,
                "head": {"ref": "auremcto/visibility-kit-seo"},
                "labels": [{"name": "auremcto/visibility-kit-seo"}],
            },
            "repository": {"full_name": "o/r"},
        }
        out = await ls.dispatch_pull_request_webhook(db, payload=payload, action="opened")
        assert out["routed"] == "kit"
        assert len(db.visibility_kit_pr_events.docs) == 1
        # NO cross-write into loop_outcomes.
        assert db.loop_outcomes.docs == []

    @pytest.mark.asyncio
    async def test_unknown_label_writes_nothing(self):
        db = FakeDB()
        payload = {
            "pull_request": {
                "number": 5, "merged": False,
                "head": {"ref": "some-other-branch"},
                "labels": [{"name": "totally-unrelated"}],
            },
            "repository": {"full_name": "o/r"},
        }
        out = await ls.dispatch_pull_request_webhook(db, payload=payload, action="opened")
        assert out["routed"] == "none"
        assert db.loop_outcomes.docs == []
        assert db.visibility_kit_pr_events.docs == []


# ── t_revert_reverse_path_alive ─────────────────────────────────────
class TestRevertReversePathAlive:
    def test_user_rollback_loop_rollback_revert_commit_chain_present(self):
        # Regression guardrail: T7 must NOT replace the existing
        # audited revert chain with branch-deletion/force-push for the
        # already-merged/direct-push case.
        src = Path("/app/backend/services/github_api_writer.py").read_text()
        assert "def revert_commit" in src or "async def revert_commit" in src

    def test_close_and_retract_does_not_force_push_or_rewrite_history(self):
        src = Path("/app/backend/services/loop_safety.py").read_text()
        body = src.split("async def close_and_retract")[1].split("\nasync def ")[0]
        assert '"force": true' not in body.lower()
        assert "force=true" not in body.lower().replace(" ", "")
        assert "delete_ship_branch(" in body  # routes through the namespace-guarded helper

    def test_finding_fix_applier_revert_reuses_shared_helper_not_a_new_one(self):
        src = Path("/app/backend/services/finding_fix_applier.py").read_text()
        assert "close_and_retract" in src
        assert "revert_finding_fix" in src


# ── t_delete_ship_branch_namespaced ─────────────────────────────────
class TestDeleteShipBranchNamespaced:
    @pytest.mark.asyncio
    async def test_rejects_non_auremcto_branch_with_zero_github_calls(self):
        with respx.mock:
            # No routes registered at all — if the function makes ANY
            # HTTP call here, respx raises AllMockedAssertionError.
            ok, err = await ls.delete_ship_branch(
                owner="o", repo="r", branch="main", token="tkn",
            )
            assert ok is False
            assert err == "GW_BLOCK_non_namespaced_branch"

    @pytest.mark.asyncio
    async def test_rejects_legacy_aurem_fix_branch_too(self):
        with respx.mock:
            ok, err = await ls.delete_ship_branch(
                owner="o", repo="r", branch="aurem/fix-secret-123", token="tkn",
            )
            assert ok is False
            assert err == "GW_BLOCK_non_namespaced_branch"

    @pytest.mark.asyncio
    @respx.mock
    async def test_accepts_auremcto_namespaced_branch(self):
        respx.delete(f"{GH}/repos/o/r/git/refs/heads/auremcto/ship-x-1").respond(204)
        ok, err = await ls.delete_ship_branch(
            owner="o", repo="r", branch="auremcto/ship-x-1", token="tkn",
        )
        assert ok is True
        assert err is None



# ── t_status_chip_pr_status_field (Wave 2, 2026-09-08) ───────────────
# The frontend status chip (LoopLiveFeed.jsx::ShippedRow) polls
# GET /loop/{id}/status to learn whether the opened PR has since been
# merged/closed on GitHub. loop_status() must surface loop_outcomes'
# pr_status (written by dispatch_pull_request_webhook, matched by
# ship_branch) as an additive `pr_status` field — omitted when the
# loop never went through ship_via_pr.
class TestPrStatusChipField:
    @pytest.mark.asyncio
    async def test_pr_status_defaults_to_open_when_no_outcome_row_yet(self):
        import routers.loop as loop_router

        db = FakeDB()
        db.loop_outcomes = FakeCollection()  # empty — webhook hasn't fired yet

        async def fake_load_session(_db, loop_id):
            return {
                "loop_id": loop_id, "user_id": "u1", "state": "completed",
                "context": {"commit": {"pr_branch": "auremcto/ship-x-1"}},
            }

        with respx.mock:
            pass  # no HTTP calls expected

        import unittest.mock as mock
        with mock.patch.object(loop_router, "current_dev", new=mock.AsyncMock(return_value={"user_id": "u1"})), \
             mock.patch.object(loop_router, "get_db", return_value=db), \
             mock.patch.object(loop_router.eng, "load_session", new=fake_load_session), \
             mock.patch("services.feature_flags.is_enabled", new=mock.AsyncMock(return_value=False)):
            result = await loop_router.loop_status("loop-x", authorization="Bearer t")
        assert result["pr_status"] == "open"

    @pytest.mark.asyncio
    async def test_pr_status_reflects_merged_outcome(self):
        import routers.loop as loop_router
        import unittest.mock as mock

        db = FakeDB()
        await db.loop_outcomes.insert_one({"ship_branch": "auremcto/ship-x-2", "pr_status": "merged"})

        async def fake_load_session(_db, loop_id):
            return {
                "loop_id": loop_id, "user_id": "u1", "state": "completed",
                "context": {"commit": {"pr_branch": "auremcto/ship-x-2"}},
            }

        with mock.patch.object(loop_router, "current_dev", new=mock.AsyncMock(return_value={"user_id": "u1"})), \
             mock.patch.object(loop_router, "get_db", return_value=db), \
             mock.patch.object(loop_router.eng, "load_session", new=fake_load_session), \
             mock.patch("services.feature_flags.is_enabled", new=mock.AsyncMock(return_value=False)):
            result = await loop_router.loop_status("loop-x", authorization="Bearer t")
        assert result["pr_status"] == "merged"

    @pytest.mark.asyncio
    async def test_pr_status_omitted_for_direct_commit_ship(self):
        """No pr_branch on the session (ship_via_pr never fired) ->
        no pr_status key at all — the chip must not render for
        direct-commit ships."""
        import routers.loop as loop_router
        import unittest.mock as mock

        db = FakeDB()

        async def fake_load_session(_db, loop_id):
            return {"loop_id": loop_id, "user_id": "u1", "state": "completed",
                    "context": {"commit": {"sha": "abc1234"}}}

        with mock.patch.object(loop_router, "current_dev", new=mock.AsyncMock(return_value={"user_id": "u1"})), \
             mock.patch.object(loop_router, "get_db", return_value=db), \
             mock.patch.object(loop_router.eng, "load_session", new=fake_load_session), \
             mock.patch("services.feature_flags.is_enabled", new=mock.AsyncMock(return_value=False)):
            result = await loop_router.loop_status("loop-x", authorization="Bearer t")
        assert "pr_status" not in result
