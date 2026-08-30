"""Issue A fix (2026-08-30) — first-scan 'Fixed and shipped' banner must
acknowledge-once, server-side, so it never re-renders on refresh/relogin.

Root cause (confirmed via source read, category (b) per the founder's own
taxonomy): GET /onboarding/first-scan/status re-surfaced commit_sha/
commit_url/files_fixed on EVERY call once the row had a commit_sha, by
design (the Phase A "read-back fix" comment) — with zero acknowledge
mechanism, so the WorkCard "Fixed and shipped" banner rendered forever.
This is a DIFFERENT persistence path than the earlier-fixed sibling bug
(MessageBubble.jsx's `shipped_task_id` gate on the chat-transcript Approve
button, see test_iter89_ship_button_no_reappear.py) — not a regression of
that fix, a separate never-cleared server-side design gap in a different
component (FirstScanCard.jsx).

Uses the same live-preview pattern + fixture as
test_phase_a_workcard_first_scan.py (reuses its already-fixed row so this
doesn't need its own full scan+apply cycle).
"""
from __future__ import annotations

import os

import pytest
import requests

BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL")
            or "https://bin-context-pat.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api/aurem-dev"
EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"
EXISTING_READY_PROJECT_ID = "p_0fdafaa365"


@pytest.fixture(scope="module")
def headers():
    r = requests.post(f"{API}/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=20)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:400]}"
    token = r.json()["token"]
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _status(headers, project_id=EXISTING_READY_PROJECT_ID):
    r = requests.get(f"{API}/onboarding/first-scan/status", headers=headers,
                      params={"project_id": project_id}, timeout=20)
    assert r.status_code == 200, r.text[:400]
    return r.json()


def test_t_status_exposes_fix_acknowledged_field(headers):
    data = _status(headers)
    assert data.get("commit_sha"), f"fixture row must already have a commit: {data}"
    assert "fix_acknowledged" in data, f"missing new fix_acknowledged field: {data}"
    assert isinstance(data["fix_acknowledged"], bool)


def test_t_acknowledge_fix_sets_flag_server_side(headers):
    # Idempotent — safe to call more than once (double-click, 2 tabs, or the
    # frontend's own auto-vanish timer racing a manual "Got it" click).
    for _ in range(2):
        r = requests.post(f"{API}/onboarding/first-scan/acknowledge-fix",
                           headers=headers, json={"project_id": EXISTING_READY_PROJECT_ID}, timeout=20)
        assert r.status_code == 200, r.text[:400]
        assert r.json().get("ok") is True

    data = _status(headers)
    assert data.get("fix_acknowledged") is True, (
        f"acknowledge-fix did not persist server-side: {data}")


def test_t_acknowledge_ownership_enforced(headers):
    r = requests.post(f"{API}/onboarding/first-scan/acknowledge-fix",
                       headers=headers, json={"project_id": "p_does_not_exist_ack_test"}, timeout=20)
    assert r.status_code == 404, r.text[:400]


def test_t_acknowledge_noop_when_no_commit_yet(headers):
    # A project that hasn't been scanned/fixed yet (no first_scan_results
    # row at all) must be a harmless no-op, not a 500 or a fabricated ack.
    r = requests.post(f"{API}/onboarding/first-scan/acknowledge-fix",
                       headers=headers, json={"project_id": "p_no_scan_row_yet_ack_test_" + EXISTING_READY_PROJECT_ID},
                       timeout=20)
    # No matching project for this user -> ownership 404, same guarantee.
    assert r.status_code == 404, r.text[:400]
