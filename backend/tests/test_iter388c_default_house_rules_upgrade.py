"""
test_iter388c_default_house_rules_upgrade.py — locks in the 2026-02-12
upgrade from the 30-word default to the anti-fabrication + retract
discipline baseline.

Contracts:
  1. DEFAULT_HOUSE_RULES is materially longer than the old 30-word text
  2. It contains each of the 5 required behavioral rules (anti-fab,
     proactive-caveat, retract-on-pushback, direct-honest, verify-first)
  3. `get_effective_text(user_id)` returns exactly the new default when
     the user has NO custom row in `ora_chat_house_rules`
  4. `get_effective_text(user_id)` returns the CUSTOM row when one
     exists (founder's personal rules stay untouched — regression guard)
  5. `assemble_system_prompt(DEFAULT_HOUSE_RULES, include_runtime=False)`
     emits a prompt that carries every required rule keyword all the way
     through to the final LLM-facing string
"""
from __future__ import annotations

import pytest

from services.ora_chat.safety import (
    DEFAULT_HOUSE_RULES, assemble_system_prompt,
)


# ── 1. Static contract on the default string ────────────────────────
def test_default_house_rules_is_meaningfully_longer_than_old_baseline():
    # Old default was 111 chars (30 words). New must be materially
    # bigger — at least 600 chars — but still under a sane upper bound.
    assert 600 <= len(DEFAULT_HOUSE_RULES) <= 2000, (
        f"DEFAULT_HOUSE_RULES len={len(DEFAULT_HOUSE_RULES)} outside "
        f"[600, 2000] window"
    )


def test_default_house_rules_covers_all_required_behaviors():
    """Each of the 5 rules the founder asked for MUST be present."""
    text = DEFAULT_HOUSE_RULES.lower()

    # 1. anti-fabrication / never cite unverified filenames
    assert "never cite a specific filename" in text, (
        "rule 1 (anti-fabrication filename citation) missing"
    )
    assert "/read" in DEFAULT_HOUSE_RULES and "/find" in DEFAULT_HOUSE_RULES, (
        "rule 1 must reference the /read, /find slash-commands as the "
        "only source of verified filename citations"
    )
    assert ("filename index" in text) or ("index block" in text), (
        "rule 1 must reference the FILENAME INDEX block escape hatch"
    )

    # 2. proactive-caveat rule
    assert "proactive-caveat" in text or "flag it explicitly" in text, (
        "rule 2 (proactive-caveat) missing"
    )
    assert "unverified" in text and "same response" in text, (
        "rule 2 must instruct to flag unverified claims in the SAME "
        "response (not wait to be challenged)"
    )

    # 3. retract on pushback
    assert "retract" in text, "rule 3 (retract-on-pushback) missing"
    assert "double" in text or "defend" in text, (
        "rule 3 must forbid doubling down / defending a shaky claim"
    )

    # 4. direct honest answers
    assert "direct" in text and "honest" in text, (
        "rule 4 (direct + honest) missing"
    )
    assert "soften" in text, "rule 4 must include 'never soften bad news'"

    # 5. verify before stating
    assert "verify" in text and "push back" in text, (
        "rule 5 (verify claims + push back on flawed requests) missing"
    )


def test_default_has_multiple_numbered_rules():
    """Structure guard: enforce the numbered-list format so the LLM
    sees them as distinct rules rather than one long paragraph."""
    lines = [ln for ln in DEFAULT_HOUSE_RULES.splitlines() if ln.strip()]
    assert len(lines) >= 5, (
        f"expected ≥5 numbered lines, got {len(lines)}"
    )
    numbered = [ln for ln in lines if ln.strip()[:2] in
                {"1.", "2.", "3.", "4.", "5."}]
    assert len(numbered) >= 5, (
        f"expected rules to be numbered 1–5, got {numbered}"
    )


# ── 2. End-to-end: assembly emits every rule ────────────────────────
def test_assembled_system_prompt_carries_every_rule_through():
    """The final LLM-facing prompt (Layer 1 + Layer 2 + Layer 3 default)
    must carry the anti-fabrication + retract discipline all the way
    through the layer-assembly."""
    prompt = assemble_system_prompt(DEFAULT_HOUSE_RULES, include_runtime=False)
    prompt_lc = prompt.lower()
    # <user_preferences> wrapper is present
    assert "<user_preferences>" in prompt and "</user_preferences>" in prompt
    # Every keyword survives assembly (case-insensitive — the source
    # uses "NEVER cite" in caps but callers may not know that)
    for needle in (
        "never cite a specific filename",
        "proactive-caveat rule",
        "retract clearly",
        "anti-fabrication",
        "never soften bad news",
    ):
        assert needle in prompt_lc, (
            f"assembled prompt missing keyword {needle!r}"
        )


# ── 3. Runtime fallback: new-user path returns the upgraded default ─
@pytest.mark.asyncio
async def test_get_effective_text_uses_new_default_for_unknown_user(monkeypatch):
    """Simulate a fresh user_id that has no row in ora_chat_house_rules
    — get_effective_text() must return the upgraded default verbatim."""
    from services.ora_chat import house_rules as hr_mod

    async def _no_row(_uid):
        return None

    monkeypatch.setattr(hr_mod, "get_current", _no_row)
    got = await hr_mod.get_effective_text("brand-new-user-uid")
    assert got == DEFAULT_HOUSE_RULES, (
        "unknown-user fallback broken — upgraded default not being served"
    )


@pytest.mark.asyncio
async def test_get_effective_text_preserves_custom_founder_rules(monkeypatch):
    """Regression guard — the founder's own 2945-char personal rules
    (or any user who has saved custom rules) must still be returned
    verbatim. The upgrade must NOT overwrite custom rows."""
    from services.ora_chat import house_rules as hr_mod

    custom = "FOUNDER CUSTOM RULES — do not overwrite this."

    async def _founder_row(_uid):
        return {"rules_text": custom, "active": True}

    monkeypatch.setattr(hr_mod, "get_current", _founder_row)
    got = await hr_mod.get_effective_text("any-uid-that-has-custom-rules")
    assert got == custom, (
        "custom rules were overwritten by the default — regression!"
    )


# ── 4. Char budget guard vs MAX_LEN ─────────────────────────────────
def test_default_fits_under_house_rules_max_len():
    """The house-rules layer enforces MAX_LEN=4000 on updates. Our new
    default must comfortably fit so `reset_to_default()` cannot ever
    fail with a length overflow."""
    from services.ora_chat.house_rules import MAX_LEN
    assert len(DEFAULT_HOUSE_RULES) < MAX_LEN, (
        f"DEFAULT_HOUSE_RULES ({len(DEFAULT_HOUSE_RULES)}) would "
        f"overflow MAX_LEN ({MAX_LEN})"
    )
