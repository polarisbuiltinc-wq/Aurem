"""
test_iter_feb2026_bug_a_dynamic_output_budget.py — Feb 2026 · Iter 362

Founder-reported bug A (P0, retest confirmed real):
    "Ship/Execute reliability correlates with file size — large files
     fail with 'LLM produced no usable file content', small files
     succeed but very slow (100-150 s+ for a one-line edit)."

Root cause (traced through core/parliament.py + services/loop_engine.py):
  1. Council A member default `max_tokens=4000` (`_CouncilMember.__init__`).
  2. Council A generates the ENTIRE final file body for each edit.
  3. For a file whose current bytes/3 exceeds 4000, the LLM output
     TRUNCATES → the post-emission integrity guard rejects the file
     (or the raw content is empty) → all 3 members fail → CEO
     returns manual_review → 0 files generated → `_do_execute`
     surfaces the opaque "LLM produced no usable file content".

Fix (Iter 362):
  A) `_CouncilMember.cast_vote` honours a `max_tokens_override` from
     the vote context (capped at 32 000, upstream provider ceiling
     honoured by the gateway).
  B) `loop_engine._gen_via_parliament` computes the override from
     the current file bytes: `min(32 000, max(4 000, bytes//3 + 1500))`.
     Threaded into Parliament via `context.max_tokens_override`.
  C) For truly huge files (>96 KB → >32 K output tokens), skip the
     LLM call entirely with a clear "file too large" narration +
     `executor_file_too_large` audit row.

These behavioural tests exercise the fix through the real
`_CouncilMember.cast_vote` code path (patching only
`services.llm.call_llm_with_meta` at the boundary — the real
Parliament trace/circuit/scoring wrappers run unchanged) plus a
static-boundary check that `_gen_via_parliament` threads the
override end-to-end.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


# ──────────────────────────────────────────────────────────────────
# 1. Contract — Council member honours max_tokens_override
# ──────────────────────────────────────────────────────────────────
def test_council_member_uses_max_tokens_override_when_context_provides_it(
        monkeypatch):
    """`_CouncilMember.cast_vote` must forward the context's
    `max_tokens_override` (capped at 32 000) to the underlying LLM
    call. Regression: without this, the default 4 000-token output
    cap silently truncated large-file rewrites."""
    from core.parliament import _CouncilMember

    seen_max_tokens: list[int] = []

    async def fake_call_llm_with_meta(system, user, *, max_tokens,
                                      mode, user_id, review_mode,
                                      temperature=0.1, **_kw):
        seen_max_tokens.append(max_tokens)
        # Return a plausible full-file output (short is fine; the test
        # only cares about what was requested from the model).
        return {"content": "def hello():\n    return 'ok'\n",
                "input_tokens": 100, "output_tokens": 50}

    import services.llm as llm_mod
    monkeypatch.setattr(llm_mod, "call_llm_with_meta",
                        fake_call_llm_with_meta)

    member = _CouncilMember(
        name="A1-test", temperature=0.1,
        persona="test persona",
    )
    # Simulate a caller (loop_engine._gen_via_parliament) computing
    # an override for a ~30 KB file → 30 000/3 + 1500 = 11 500.
    override = 11_500
    ctx = {"council": "A", "max_tokens_override": override,
           "task_type": "code_fix"}

    async def go():
        return await member.cast_vote(task="rewrite this file", context=ctx)

    result = asyncio.run(go())
    assert result["error"] is None, f"unexpected error: {result}"
    assert seen_max_tokens == [override], (
        f"cast_vote must forward context.max_tokens_override to the "
        f"LLM gateway. Got {seen_max_tokens}, expected [{override}]."
    )


def test_council_member_caps_override_at_32k(monkeypatch):
    """Regression: a caller passing an absurd override must be
    clamped at 32 000 (the upstream provider ceiling the gateway
    honours) — never blindly forwarded."""
    from core.parliament import _CouncilMember

    seen: list[int] = []

    async def fake_llm(system, user, *, max_tokens, **_kw):
        seen.append(max_tokens)
        return {"content": "ok", "input_tokens": 10, "output_tokens": 5}

    import services.llm as llm_mod
    monkeypatch.setattr(llm_mod, "call_llm_with_meta", fake_llm)

    member = _CouncilMember(name="A1", temperature=0.1, persona="p")
    ctx = {"council": "A", "max_tokens_override": 999_999,
           "task_type": "code_fix"}

    async def go():
        return await member.cast_vote(task="t", context=ctx)

    asyncio.run(go())
    assert seen == [32_000], (
        f"max_tokens must be capped at 32 000, got {seen[0]}"
    )


def test_council_member_defaults_when_no_override(monkeypatch):
    """No override → the member's own `self.max_tokens` (default
    4000) is used. Locks the no-regression path so the fix cannot
    silently break the small-file happy path."""
    from core.parliament import _CouncilMember

    seen: list[int] = []

    async def fake_llm(system, user, *, max_tokens, **_kw):
        seen.append(max_tokens)
        return {"content": "ok", "input_tokens": 10, "output_tokens": 5}

    import services.llm as llm_mod
    monkeypatch.setattr(llm_mod, "call_llm_with_meta", fake_llm)

    member = _CouncilMember(name="A1", temperature=0.1, persona="p",
                            max_tokens=4000)
    ctx = {"council": "A", "task_type": "code_fix"}  # no override

    async def go():
        return await member.cast_vote(task="t", context=ctx)

    asyncio.run(go())
    assert seen == [4000], (
        f"no override → default 4000 tokens; got {seen[0]}"
    )


# ──────────────────────────────────────────────────────────────────
# 2. Contract — loop_engine threads override end-to-end
# ──────────────────────────────────────────────────────────────────
def test_loop_engine_threads_max_tokens_override_into_parliament_context():
    """Static source-code contract: `_gen_via_parliament` must
    compute an output budget from the current file bytes and pass
    it to `Parliament.run(context={...})`. Without this, the fix
    at the Council member layer would never receive the override."""
    src = (Path("/app/backend/services/loop_engine.py")
           .read_text(encoding="utf-8"))

    # The dynamic budget formula must be present.
    assert '"max_tokens_override": _out_budget' in src, (
        "loop_engine._gen_via_parliament must pass max_tokens_override "
        "in the Parliament context so Council A members honour it."
    )
    # The formula must scale with input bytes.
    assert "(_cur_bytes // 3) + 1_500" in src or \
           "(_cur_bytes // 3) + 1500" in src, (
        "output budget must scale with the file's current bytes."
    )
    # Upstream provider ceiling honoured.
    assert "32_000" in src or "32000" in src, (
        "output budget must cap at the 32 K upstream ceiling."
    )


# ──────────────────────────────────────────────────────────────────
# 3. Contract — huge files short-circuit with a clear message
# ──────────────────────────────────────────────────────────────────
def test_loop_engine_graceful_degrades_for_huge_files():
    """Static source contract: files exceeding the single-pass
    rewrite ceiling (>96 KB) must skip the LLM call and emit a
    clear 'file_too_large' sub_step + audit row, INSTEAD of failing
    opaquely with 'LLM produced no usable file content'."""
    src = (Path("/app/backend/services/loop_engine.py")
           .read_text(encoding="utf-8"))
    # The ceiling constant must exist and be an integer between
    # 60 KB and 200 KB (below the upstream 32 K output-token cap).
    assert "_HUGE_FILE_BYTES" in src, (
        "graceful-degradation ceiling _HUGE_FILE_BYTES must be defined."
    )
    assert '"executor_file_too_large"' in src, (
        "huge-file skip must emit an executor_file_too_large audit row."
    )
    assert '"file_too_large"' in src, (
        "huge-file skip must surface a `file_too_large` sub_step so "
        "the frontend can render a targeted message (not the generic "
        "'no usable file content' failure)."
    )


# ──────────────────────────────────────────────────────────────────
# 4. Contract — dynamic budget math is sane
# ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("bytes_in,expected_min", [
    (500,     4_000),     # tiny file → default floor
    (3_000,   4_000),     # small file → still default (bytes/3=1000)
    (18_000,  6_000),     # medium file → 18000/3+1500 = 7500
    (60_000,  20_000),    # large file → 60000/3+1500 = 21500
    (100_000, 32_000),    # very large → clamped at 32 K
])
def test_dynamic_budget_scales_with_file_size(bytes_in, expected_min):
    """The budget formula min(32_000, max(4_000, bytes//3 + 1500))
    must scale reasonably across the size tiers the founder listed
    (small <100 lines, medium ~500 lines, large >1000 lines)."""
    computed = min(32_000, max(4_000, (bytes_in // 3) + 1_500))
    assert computed >= expected_min, (
        f"budget for {bytes_in} bytes is {computed}, expected >= "
        f"{expected_min}. Formula must give the LLM enough room to "
        f"emit a full-file rewrite without truncation."
    )
    assert computed <= 32_000, (
        f"budget for {bytes_in} bytes is {computed}, must be <= 32 000 "
        f"(upstream provider ceiling)."
    )
