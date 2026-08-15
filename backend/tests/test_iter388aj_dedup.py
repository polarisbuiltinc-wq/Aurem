"""test_iter388aj_dedup.py — Iter 388-aj (2026-02-14).

Founder-reported bug: canary Run 1 showed
`fabricated_total: ["test_security_gate.py", "test_security_gate.py"]`
— same invented path listed twice in a single reply's classification
result. The alert / dashboard would then double-count the violation.

Fix: `classify_claims()` deduplicates its `fabricated` and
`unverified` output lists in first-seen order.
"""
from __future__ import annotations

from services.ora_chat.grounding_check import classify_claims


CANONICAL_EMPTY = {"paths": set(), "basenames": set(), "defs": set()}


def test_fabricated_dedup_when_same_path_repeats():
    # Same fabricated path appearing twice must appear ONCE in output.
    claims = ["test_security_gate.py", "test_security_gate.py"]
    out = classify_claims(claims, canonical=CANONICAL_EMPTY)
    assert out["fabricated"] == ["test_security_gate.py"], out


def test_fabricated_preserves_first_seen_order_with_dedup():
    claims = ["a.py", "b.py", "a.py", "c.py", "b.py"]
    out = classify_claims(claims, canonical=CANONICAL_EMPTY)
    assert out["fabricated"] == ["a.py", "b.py", "c.py"], out


def test_unverified_dedup_when_repeated_real_path():
    canonical = {
        "paths": {"backend/services/loop_engine.py"},
        "basenames": {"loop_engine.py"},
        "defs": set(),
    }
    claims = ["loop_engine.py", "loop_engine.py"]
    out = classify_claims(claims, canonical=canonical, user_query="",
                           turn_contexts=[])
    assert out["unverified"] == ["loop_engine.py"], out


def test_symbol_claim_dedup():
    canonical = {"paths": set(), "basenames": set(), "defs": set()}
    claims = ["run_canary", "run_canary", "extract_claims"]
    out = classify_claims(claims, canonical=canonical, turn_contexts=[])
    assert out["unverified"] == ["run_canary", "extract_claims"], out
