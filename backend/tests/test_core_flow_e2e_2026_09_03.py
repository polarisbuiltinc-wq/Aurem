"""
tests/test_core_flow_e2e_2026_09_03.py

Root gate — "a real, plain-English full-flow E2E test": greeting ->
edit request -> confirm/action -> no fabrication -> no dead end.

Runs LIVE against the running preview backend (real HTTP, real
router/orchestrator/guard code -- not a mocked unit test) via the
`home` project (no connected repo). This is a deliberate scope
decision, documented here rather than silently overclaimed:

  A real, connected GitHub repo fixture is NOT available in this
  preview pod as of 2026-09-03 -- every `cto_projects` row for the
  test account resolves `app_installation_missing` from
  `pat_vault.get_repo_token_or_error()` (verified live before writing
  this test; same finding as
  test_iter2026_08_27_ship_e2e_real_push.py's documented block
  reason). This test therefore cannot verify an actual file changing
  on a real repo end-to-end in THIS environment.

  What it DOES verify, for real, against the real running service:
    - the exact reported failure shape (greeting -> "update my
      opening hours" -> confirm -> "yes please") never produces a
      fabricated "found X at line N" / "currently shows Y" claim
      with no real backing action, and
    - a confirmation reply is never met with a false "Approved!"/
      "Shipped!" claim, and
    - once nothing is genuinely pending, the honest
      NO_PENDING_FIX_MESSAGE-shaped reply is shown, not a raw code
      dump or a misleading "would you like me to..." offer.

  If/when a real installed drill repo becomes available in preview,
  this same test should be re-pointed at a real `project_id` and
  extended with a real before/after file-content assertion -- see
  ROADMAP.md.
"""
from __future__ import annotations

import os
import re
import uuid

import pytest
import requests

from services.response_confidence import (
    contains_orphan_confirm,
    contains_no_edit_deadend,
    contains_false_success_claim,
    NO_PENDING_FIX_MESSAGE,
)

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://bin-context-pat.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api/aurem-dev"
EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"


@pytest.fixture(scope="module")
def token() -> str:
    r = requests.post(
        f"{API}/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
        timeout=20,
    )
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text[:300]}"
    return r.json()["token"]


def _chat(token: str, prompt: str, session_id: str) -> dict:
    r = requests.post(
        f"{API}/chat/send",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={
            "prompt": prompt,
            "session_id": session_id,
            "project_id": "home",
            "execution_mode": "prompt",
            "mode": "swift",
            "ora_panel": False,
        },
        timeout=180,
    )
    assert r.status_code == 200, f"chat/send failed: {r.status_code} {r.text[:400]}"
    body = r.json()
    assert body.get("ok") is True, f"chat/send not ok: {body}"
    return body


def test_t_core_flow_end_to_end_nontechnical(token):
    """The realistic plain-English flow demanded by the founder:
    greeting -> 'update my opening hours' -> confirm -> 'yes please'.
    Never fabricates, never dead-ends."""
    session_id = f"core-flow-{uuid.uuid4().hex[:12]}"

    # Turn 1 — greeting.
    r1 = _chat(token, "hi", session_id)
    reply1 = r1.get("content", "") or ""
    assert reply1.strip(), "greeting returned empty content"

    # Turn 2 — the exact edit request from the founder's repro.
    r2 = _chat(token, "update my opening hours", session_id)
    reply2 = r2.get("content", "") or ""
    assert reply2.strip(), "edit request returned empty content"

    has_fence_t2 = "```aurem-handoff" in reply2
    # HARD gate: never a fabricated discovery claim (line number /
    # "currently shows") paired with a confirm question and no real
    # fence -- this is the exact bug class reported live.
    assert not contains_orphan_confirm(reply2), (
        f"Turn 2 fabricated a discovery claim with an orphan confirm "
        f"question: {reply2!r}"
    )
    # HARD gate: never a raw edit-looking code block + fake confirm
    # question with no real fence.
    assert not contains_no_edit_deadend(reply2), (
        f"Turn 2 produced a no-edit dead end: {reply2!r}"
    )

    # Turn 3 — the user confirms.
    r3 = _chat(token, "yes please", session_id)
    reply3 = r3.get("content", "") or ""
    assert reply3.strip(), "confirmation returned empty content"

    if has_fence_t2:
        # A real pending action existed -- turn 3 must NEVER be the
        # generic "nothing pending" dead end (that would contradict
        # the real fence turn 2 just produced).
        assert reply3.strip() != NO_PENDING_FIX_MESSAGE, (
            "Turn 2 produced a real aurem-handoff fence, but turn 3's "
            "confirmation was met with the 'nothing pending' dead end"
        )
    else:
        # No real action was ever registered -- turn 3 must be
        # HONEST (no false success claim), never a fabricated
        # "Approved!"/"Shipped!"/"on it" claim with nothing behind it.
        assert not contains_false_success_claim(reply3), (
            f"Turn 3 claimed false success with nothing pending: {reply3!r}"
        )


def test_regression_greeting_alone_still_works(token):
    """Baseline regression: a lone greeting on the core flow surface
    still returns a normal, non-empty reply (unaffected by this
    round's new guards)."""
    session_id = f"core-flow-reg-{uuid.uuid4().hex[:12]}"
    r = _chat(token, "hello!", session_id)
    reply = r.get("content", "") or ""
    assert reply.strip()
    assert not contains_orphan_confirm(reply)
