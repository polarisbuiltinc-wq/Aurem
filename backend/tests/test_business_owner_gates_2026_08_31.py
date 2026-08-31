"""
Business Owner Voice — 3 Acceptance Gates (2026-08-31)

Runs Gate 1 (4 non-technical prompts), Gate 2 (design ask x2), Gate 3
(self-repair report) live against the preview backend via the real
/api/aurem-dev/chat/send endpoint (home project, no repo needed).

The primary deliverable is the FULL TRANSCRIPT per gate written to
/app/test_reports/business_voice_gates_transcript_2026_08_31.json.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://bin-context-pat.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api/aurem-dev"
EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"

TRANSCRIPT_PATH = Path("/app/test_reports/business_voice_gates_transcript_2026_08_31.json")
TRANSCRIPT_PATH.parent.mkdir(parents=True, exist_ok=True)


# Banned developer jargon patterns for Gate 1 (case-insensitive word boundary).
_JARGON_TERMS = [
    r"commit", r"code", r"file", r"\.jsx", r"\.tsx", r"\.py", r"\.md", r"\.html",
    r"markup", r"push", r"deploy", r"endpoint", r"\b404\b", r"\b500\b", r"token",
    r"session", r"access to", r"API",
]
_JARGON_RE = re.compile("|".join(_JARGON_TERMS), re.IGNORECASE)

_DEAD_END_RE = re.compile(
    r"try rephrasing|i'?m not confident|could you clarify|ask again",
    re.IGNORECASE,
)

_DANGLING_RE = re.compile(r"(let me\s*[^.!?]*$)|(:\s*$)", re.IGNORECASE)

_REFUSAL_RE = re.compile(
    r"i (need|require) (your |the )?(brand (book|guidelines?|assets|docs?)|"
    r"design (assets|guidelines?)|strategy docs?)",
    re.IGNORECASE,
)


@pytest.fixture(scope="module")
def token() -> str:
    r = requests.post(
        f"{API}/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
        timeout=20,
    )
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text[:300]}"
    return r.json()["token"]


@pytest.fixture(scope="module")
def transcript() -> dict:
    """Accumulator for full-transcript deliverable, persisted at teardown."""
    data = {"base_url": BASE_URL, "gate1": [], "gate2": [], "gate3": [], "regression": []}
    yield data
    TRANSCRIPT_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def _chat(token: str, prompt: str, session_id: str, project_id: str = "home") -> dict:
    """POST /chat/send and return the parsed body (ok, content, ...)."""
    r = requests.post(
        f"{API}/chat/send",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={
            "prompt": prompt,
            "session_id": session_id,
            "project_id": project_id,
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


def _check_gate1(reply: str) -> dict:
    """Return dict of per-rule pass/fail + evidence for a single Gate 1 reply."""
    jargon_hits = [m.group(0) for m in _JARGON_RE.finditer(reply)]
    dead_end_hits = [m.group(0) for m in _DEAD_END_RE.finditer(reply)]
    stripped = reply.rstrip()
    ends_terminal = stripped.endswith((".", "!", "?", ")", "\"", "'")) if stripped else False
    dangles = bool(_DANGLING_RE.search(stripped))
    has_handoff = "aurem-handoff" in reply
    return {
        "no_jargon": not jargon_hits,
        "jargon_hits": jargon_hits,
        "no_dead_end": not dead_end_hits,
        "dead_end_hits": dead_end_hits,
        "ends_with_terminal_punct": ends_terminal,
        "no_dangling": not dangles,
        "has_handoff_fence": has_handoff,
    }


def _check_gate2(reply: str) -> dict:
    from services.design_refusal_guard import (
        has_refusal, has_session_jargon, has_can_do_now,
        has_concrete_directions, asks_at_most_one_input, has_honest_scope_line,
    )
    return {
        "no_refusal": not has_refusal(reply),
        "no_session_jargon": not has_session_jargon(reply),
        "has_can_do_now": has_can_do_now(reply),
        "has_concrete_directions": has_concrete_directions(reply),
        "at_most_one_question": asks_at_most_one_input(reply),
        "has_scope_line": has_honest_scope_line(reply),
        "question_count": reply.count("?"),
    }


def _check_gate3(reply: str) -> dict:
    from services.self_bug_reply_guard import (
        has_ownership, blames_user, has_path_forward, has_error_code,
        is_compliant_self_bug_reply,
    )
    return {
        "has_ownership_early": has_ownership(reply),
        "no_user_blame": not blames_user(reply),
        "has_path_forward": has_path_forward(reply),
        "no_error_code_or_stack": not has_error_code(reply),
        "compliant": is_compliant_self_bug_reply(reply),
    }


# ── GATE 1 — Non-technical business-owner run ─────────────────────────────
def test_gate1_business_owner_flow(token, transcript):
    session_id = f"gate1-{uuid.uuid4().hex[:12]}"
    prompts = [
        "hi, what can you do for my website?",
        "please put our opening hours at the top of our main page",
        "9am to 5pm every day",
        "add our phone number 1-800-555-0199 to the bottom of my main page",
    ]
    failures = []
    for i, p in enumerate(prompts, 1):
        body = _chat(token, p, session_id)
        reply = body.get("content", "") or ""
        checks = _check_gate1(reply)
        transcript["gate1"].append({
            "turn": i,
            "prompt": p,
            "reply": reply,
            "checks": checks,
            "provider": body.get("provider"),
            "tier": body.get("tier"),
            "bail_reason": body.get("bail_reason"),
            "ship_suppressed": body.get("ship_suppressed"),
        })
        # Only critical checks fail the assertion; other rules are reported.
        if checks["jargon_hits"]:
            failures.append(f"T{i} jargon: {checks['jargon_hits']}")
        if checks["dead_end_hits"]:
            failures.append(f"T{i} dead-end: {checks['dead_end_hits']}")
        time.sleep(1)  # small breather between turns
    # Report but don't hard-fail on cosmetic dangling — jargon/dead-end are hard.
    assert not failures, f"Gate 1 failures: {failures}"


# ── GATE 2 — Design ask scenario ──────────────────────────────────────────
@pytest.mark.parametrize("prompt", [
    "can you redesign our brand identity?",
    "our site looks dated — can you modernize it?",
])
def test_gate2_design_ask(token, transcript, prompt):
    session_id = f"gate2-{uuid.uuid4().hex[:12]}"
    body = _chat(token, prompt, session_id)
    reply = body.get("content", "") or ""
    checks = _check_gate2(reply)
    transcript["gate2"].append({
        "prompt": prompt,
        "reply": reply,
        "checks": checks,
        "provider": body.get("provider"),
        "tier": body.get("tier"),
    })
    # Hard rules for the design-ask guarantee:
    assert checks["no_refusal"], f"Gate 2 REFUSAL leak: {reply[:200]}"
    assert checks["no_session_jargon"], f"Gate 2 session/access-to jargon leak: {reply[:200]}"


# ── GATE 3 — Self-repair scenario ─────────────────────────────────────────
@pytest.mark.parametrize("prompt", [
    "the approve button isn't showing",
    "I clicked it and nothing happened, and you keep saying try again — what do I even try?",
])
def test_gate3_self_repair(token, transcript, prompt):
    session_id = f"gate3-{uuid.uuid4().hex[:12]}"
    body = _chat(token, prompt, session_id)
    reply = body.get("content", "") or ""
    checks = _check_gate3(reply)
    transcript["gate3"].append({
        "prompt": prompt,
        "reply": reply,
        "checks": checks,
        "provider": body.get("provider"),
        "fallback_chain": body.get("fallback_chain"),
    })
    assert checks["has_ownership_early"], f"Gate 3 missing ownership: {reply[:200]}"
    assert checks["no_user_blame"], f"Gate 3 user-blame leak: {reply[:200]}"
    assert checks["has_path_forward"], f"Gate 3 no path forward: {reply[:200]}"
    assert checks["no_error_code_or_stack"], f"Gate 3 error code leak: {reply[:200]}"


# ── Regression smoke — normal casual chat still returns something ─────────
def test_regression_casual_hello_still_works(token, transcript):
    session_id = f"reg-{uuid.uuid4().hex[:12]}"
    body = _chat(token, "hello!", session_id)
    reply = body.get("content", "") or ""
    transcript["regression"].append({
        "prompt": "hello!",
        "reply": reply,
        "provider": body.get("provider"),
        "tier": body.get("tier"),
    })
    assert reply.strip(), "Regression: casual hello returned empty content"
