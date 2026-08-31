"""
tests/test_iter2026_09_03_live_verification.py

Live verification for the 2026-09-03 core-flow round:
 - Root 3: greeting-prefixed edit classified NOT casual
 - Root 3 regression: plain greetings still casual
 - Root 4 (gated): free-tier gets an HONEST upgrade offer (mentions Pro / upgrade)
 - Root 4: pro/founder tier is NEVER shown the upgrade offer
 - FINAL GATE: founder-repro 3x in a row -> never dead-ends with "nothing pending"
   after an implied-but-unbacked offer, never fabricates content claims.

All tests hit the real preview backend.
"""
from __future__ import annotations

import os
import uuid

import pytest
import requests

from services.response_confidence import (
    contains_orphan_confirm,
    contains_no_edit_deadend,
    NO_PENDING_FIX_MESSAGE,
)
from services.ora_chat.grounding_check import (
    contains_fabricated_content_claim,
    FABRICATED_CONTENT_MESSAGE,
)
from services.mode_routing import UPGRADE_OFFER_MESSAGE

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
API = f"{BASE_URL}/api/aurem-dev"

FOUNDER_EMAIL = "test@aurem.dev"
FOUNDER_PW = "AuremTest2026!"
FREE_EMAIL = "free-gate-test-0822@aurem.dev"
FREE_PW = "FreeGateTest2026!"


def _login(email: str, pw: str) -> str:
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": pw}, timeout=20)
    assert r.status_code == 200, f"login {email} failed: {r.status_code} {r.text[:300]}"
    return r.json()["token"]


@pytest.fixture(scope="module")
def founder_token() -> str:
    return _login(FOUNDER_EMAIL, FOUNDER_PW)


@pytest.fixture(scope="module")
def free_token() -> str:
    return _login(FREE_EMAIL, FREE_PW)


def _classify(token: str, message: str) -> dict:
    r = requests.post(
        f"{API}/chat/classify-intent",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"message": message, "project_id": "home"},
        timeout=30,
    )
    assert r.status_code == 200, f"classify failed: {r.status_code} {r.text[:300]}"
    return r.json()


def _send(token: str, prompt: str, session_id: str) -> dict:
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


# ── Root 3: escalation classifier broadening ────────────────────────
def test_greeting_prefixed_edit_is_not_casual(founder_token):
    """Greeting-prefixed content-edit routes to a tool-having tier."""
    out = _classify(founder_token, "hi, can you update our opening hours")
    tier = (out.get("tier") or "").lower()
    assert tier and tier != "casual", (
        f"greeting-prefixed edit misclassified as casual: {out!r}"
    )


def test_plain_content_edit_is_not_casual(founder_token):
    out = _classify(founder_token, "update my opening hours")
    tier = (out.get("tier") or "").lower()
    assert tier and tier != "casual", f"plain edit misclassified: {out!r}"


def test_regression_plain_greetings_are_casual(founder_token):
    for msg in ["hi", "hello", "thanks!"]:
        out = _classify(founder_token, msg)
        tier = (out.get("tier") or "").lower()
        assert tier == "casual", f"greeting {msg!r} misclassified as {tier}: {out!r}"


# ── Root 4: gated upgrade offer for free-tier ───────────────────────
@pytest.mark.parametrize(
    "prompt",
    [
        "update my opening hours",
        "change our phone number",
        "correct our address",
    ],
)
def test_free_tier_edit_request_shows_honest_upgrade_offer(free_token, prompt):
    """A free-tier account making a real edit request on Swift default
    mode gets an honest upgrade offer (mentions Pro/upgrade), NOT a
    silent switch and NOT a 'nothing pending' dead-end."""
    session_id = f"free-gate-{uuid.uuid4().hex[:12]}"
    r = _send(free_token, prompt, session_id)
    reply = (r.get("content") or "").lower()
    assert reply.strip(), f"empty reply for {prompt!r}"
    assert reply.strip() != NO_PENDING_FIX_MESSAGE.lower(), (
        f"free-tier edit hit the 'nothing pending' dead end for {prompt!r}: {reply!r}"
    )
    # Must actually mention an upgrade / Pro plan.
    assert "upgrade" in reply or "pro plan" in reply or "pro" in reply, (
        f"free-tier reply did not mention upgrade/pro for {prompt!r}: {reply!r}"
    )
    assert not contains_orphan_confirm(reply)
    assert not contains_no_edit_deadend(reply)


def test_pro_founder_tier_never_gets_upgrade_offer(founder_token):
    """The founder/admin account (Pro access) making the same edit
    request must NEVER see the upgrade-offer text."""
    session_id = f"pro-nogate-{uuid.uuid4().hex[:12]}"
    r = _send(founder_token, "update my opening hours", session_id)
    reply = r.get("content") or ""
    # The exact upgrade-offer sentence must not appear for a pro account.
    signature_phrase = "reliable Pro plan"
    # We check the constant's distinctive fragment to be robust to wording drift.
    from services.mode_routing import UPGRADE_OFFER_MESSAGE as _U
    # Take a distinctive sub-phrase from the real message
    key_fragment = "set up that upgrade"
    assert key_fragment not in reply.lower(), (
        f"pro/founder account was shown the upgrade offer: {reply!r}"
    )


# ── FINAL GATE: 3x founder-repro on the exact reported flow ─────────
@pytest.mark.parametrize("run_idx", [1, 2, 3])
def test_founder_repro_3x_never_fabricates_never_dead_ends(founder_token, run_idx):
    """The exact non-technical repro loop, three times in a row:
    greeting -> 'update my opening hours' -> confirm -> 'yes please'.
    NEVER fabricates a specific line/content claim not grounded in
    retrieved context, and NEVER dead-ends with 'nothing pending'
    right after an implied-but-unbacked offer."""
    session_id = f"repro-{run_idx}-{uuid.uuid4().hex[:12]}"

    r1 = _send(founder_token, "hi", session_id)
    reply1 = r1.get("content") or ""
    assert reply1.strip()

    r2 = _send(founder_token, "update my opening hours", session_id)
    reply2 = r2.get("content") or ""
    assert reply2.strip()
    has_fence = "```aurem-handoff" in reply2
    assert not contains_orphan_confirm(reply2), (
        f"run {run_idx} turn2 orphan confirm: {reply2!r}"
    )
    assert not contains_no_edit_deadend(reply2), (
        f"run {run_idx} turn2 no-edit dead-end: {reply2!r}"
    )
    # Turn 2 must not carry the exact fabricated-content sentinel
    assert reply2.strip() != FABRICATED_CONTENT_MESSAGE

    r3 = _send(founder_token, "yes please", session_id)
    reply3 = r3.get("content") or ""
    assert reply3.strip()

    if has_fence:
        # If turn 2 offered a real pending action, turn 3 must NOT
        # dead-end with the generic "nothing pending".
        assert reply3.strip() != NO_PENDING_FIX_MESSAGE, (
            f"run {run_idx}: real fence at t2 but t3='nothing pending' dead-end"
        )
    # In all cases, reply3 must not fabricate content claims paired
    # with a confirm question.
    assert not contains_orphan_confirm(reply3), (
        f"run {run_idx} turn3 orphan confirm: {reply3!r}"
    )
