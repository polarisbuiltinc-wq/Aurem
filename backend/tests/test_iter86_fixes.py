"""
test_iter86_fixes — two real bug fixes:

  (1) UI Gate 7 false-positive — refined to require AT LEAST ONE
      verified path in the brief, not ALL paths. This lets legit
      new-file-creation briefs ("Create backend/tests/test_foo.py")
      through, while still rejecting pure fabrication (zero verified
      paths anywhere in the fence).

  (2) Chat HARD_TIMEOUT_S — was a flat 90 s; on real user repos with
      cold GitHub cache + cold OpenRouter routing the first tool call
      alone could eat the entire budget, then "do it" on the retry
      hit the same wall. Now 150 s default, env-configurable via
      CHAT_HARD_TIMEOUT_S.

Both fixes are user-facing: a real ORA user on auremcto.com reported
a 90 s cut-off plus a missing Ship-via-CTO button on a brief that
included a brand-new test file. These tests lock the contracts.
"""
from __future__ import annotations

import os
import re

BASE = os.path.join(os.path.dirname(__file__), "..", "..")


def _read(rel: str) -> str:
    with open(os.path.join(BASE, rel), encoding="utf-8") as fh:
        return fh.read()


# ── (1) Gate 7 refinement ─────────────────────────────────────────────

def test_gate7_now_requires_at_least_one_match_not_all():
    """The relaxed contract must be documented AND implemented."""
    src = _read("frontend/src/components/MessageBubble.jsx")
    # The old "fabricated.length > 0) return null" must be GONE.
    assert "fabricated.length > 0) return null" not in src, (
        "Gate 7 still using the strict 'every path must match' rule — "
        "this kills legit new-file-creation briefs"
    )
    # The new contract: match at least one verified path.
    assert "matched.length === 0" in src
    assert "AT LEAST ONE path that IS" in src
    assert "Iter 86" in src   # rationale comment must persist


def test_messagebubble_path_extraction_still_global():
    """The path extraction must still enumerate every token so
    Gate 7 can compute matched-vs-fabricated."""
    src = _read("frontend/src/components/MessageBubble.jsx")
    assert "brief.match(FILE_PATH_TOKEN_GLOBAL)" in src


# ── (2) Configurable HARD_TIMEOUT_S ───────────────────────────────────

def test_chat_hard_timeout_is_env_configurable():
    """Iter 86 introduced env-configurable HARD_TIMEOUT_S so real
    user repos with cold-cache GitHub fetches don't get killed at a
    hardcoded 90s ceiling.

    Iter 212m-* later bumped the default from 150s → 180s to match
    the real p95 for GitHub-heavy prompts; the assertion tracks the
    current default. The env-configurable contract (the ONLY thing
    the test actually cares about) is preserved."""
    src = _read("backend/routers/chat/stream.py")
    # Old flat literal must be gone.
    assert "HARD_TIMEOUT_S = 90.0" not in src, (
        "HARD_TIMEOUT_S still hardcoded to 90 — real user repos blow "
        "this budget on a single cold-cache GitHub fetch"
    )
    # New form: env-configurable via os.getenv. Accept the current
    # 180s default (Iter 212m-* bump from 150s).
    assert re.search(
        r'os\.getenv\(\s*"CHAT_HARD_TIMEOUT_S"\s*,\s*"1[58]0"\s*\)',
        src,
    ), (
        "routers/chat/stream.py must set HARD_TIMEOUT_S via "
        'os.getenv("CHAT_HARD_TIMEOUT_S", "150" or "180") — the '
        "env-configurable contract is what Iter 86 locked in."
    )
    # The file must actually import os so the getenv call doesn't NameError.
    assert re.search(r"^import os$", src, re.MULTILINE), (
        "routers/chat/stream.py uses os.getenv but doesn't `import os` — that "
        "would NameError at startup"
    )


def test_chat_hard_timeout_default_is_a_real_float():
    """Confirm the env knob round-trips via the module's symbol table.
    (Value is defined inside an async generator so it can't be
    inspected directly at import time — checked via source instead.
    NOTE: do NOT importlib.reload(routers.chat) here — the package's
    4 submodules share ONE `router` object via `from . import router`;
    reloading __init__.py replaces that attribute with a fresh, empty
    APIRouter and orphans every route registered by the submodules,
    which silently 404s every chat endpoint in any test file that
    runs after this one in the same session.)"""
    os.environ.pop("CHAT_HARD_TIMEOUT_S", None)
    src = _read("backend/routers/chat/stream.py")
    m = re.search(
        r'HARD_TIMEOUT_S\s*=\s*float\(os\.getenv\("CHAT_HARD_TIMEOUT_S",\s*"(\d+(?:\.\d+)?)"\)\)',
        src,
    )
    assert m, "HARD_TIMEOUT_S env form not found"
    assert float(m.group(1)) >= 120.0, (
        "Default must be at least 120 s — anything tighter blows up on "
        "real user repos."
    )


# ── (3) Behavioural sanity — the bad brief still gets rejected ────────

def test_pure_fabrication_brief_still_rejected_by_gate7_documentation():
    """The system-prompt rule (d) plus the UI Gate 7 must still reject
    a brief where 100% of the path tokens are fabricated."""
    src = _read("frontend/src/components/MessageBubble.jsx")
    # The implementation must short-circuit when zero brief paths match
    # verifiedPaths AND verifiedPaths is non-empty.
    assert "matched = briefPaths.filter((p) => seen.has(p))" in src
    assert "matched.length === 0" in src
