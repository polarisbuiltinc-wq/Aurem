"""
test_b1_repo_status_invalidate.py — Overnight Master Loop 2, W2/B1.

t_commit_success_clears_not_connected — a successful ship drops the
cached connection-status row for that project so the very next poll
re-checks GitHub instead of replaying a stale "disconnected" reading.
"""
from routers import repo_status


def test_t_commit_success_clears_not_connected():
    repo_status._CACHE["proj_1"] = {"project_id": "proj_1", "status": "disconnected"}
    repo_status._CACHE["proj_2"] = {"project_id": "proj_2", "status": "connected"}

    repo_status.invalidate("proj_1")

    assert "proj_1" not in repo_status._CACHE
    assert "proj_2" in repo_status._CACHE  # unrelated project untouched


def test_t_invalidate_missing_key_is_a_noop():
    repo_status._CACHE.pop("does_not_exist", None)
    repo_status.invalidate("does_not_exist")  # must not raise
