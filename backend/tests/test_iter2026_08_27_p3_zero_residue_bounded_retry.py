"""
tests/test_iter2026_08_27_p3_zero_residue_bounded_retry.py — P3 (Zero-
Residue Failure + Honest Failure State + Bounded Retry), Journey/
Intent-Grounding build round.

Reproduces + fixes: "the loop wrote 3 of 4 files then failed: 'LLM
produced no usable file content. Try refining the plan.'  — no retry,
and UI never states whether the 3 written files persisted."
"""
import re

SRC_PATH = "/app/backend/services/loop_engine.py"


def _src() -> str:
    with open(SRC_PATH) as f:
        return f.read()


def test_bounded_retry_wrapper_present_and_capped_at_two():
    src = _src()
    assert "_gen_with_bounded_retry" in src
    idx = src.find("_MAX_ARTIFACT_ATTEMPTS = 2")
    assert idx > -1, "bounded retry must be capped at exactly 2 attempts per artifact"
    # The gather call must use the retry wrapper, not the bare generator.
    gather_idx = src.find("_tasks = [_gen_with_bounded_retry(p) for p in paths]")
    assert gather_idx > idx, "EXECUTE must dispatch through the retry wrapper"


def test_old_opaque_message_is_gone():
    src = _src()
    assert "LLM produced no usable file content. Try refining the plan." not in src, (
        "the old opaque, non-actionable failure message must be fully retired"
    )


def test_zero_generated_failure_is_plain_english_with_options():
    src = _src()
    idx = src.rfind("Couldn't generate usable content for")
    assert idx > -1
    tail = src[idx: idx + 1200]
    assert "Nothing was written or committed" in tail, (
        "zero-residue guarantee must be stated explicitly, not implied"
    )
    assert "retry_step" in tail and "show_details" in tail and "replan" in tail, (
        "must offer real, named options — not 'refine the plan' (not a real user action)"
    )
    assert '"zero_residue": True' in tail


def test_partial_failure_path_keeps_successful_files_and_reports_failed_ones():
    """Three good writes must not be burned by one empty generation —
    verify the partial-failure branch narrates what was KEPT and what
    FAILED by name, and does not call self._fail() (i.e. does not
    abort the whole loop over a partial success)."""
    src = _src()
    idx = src.find("execute_partial_generation")
    assert idx > -1, "a partial-generation path must exist and be distinctly named"
    window = src[max(0, idx - 1500): idx + 1600]
    assert "Kept" in window and "need review" in window
    assert "generation_failures" in window
    # Must not immediately fail the whole loop on a partial success —
    # look for the absence of a self._fail( call inside this specific
    # branch's narration block (the surrounding `if failed_paths:` body).
    branch_start = src.find("if failed_paths:")
    branch_end = src.find("# Persist + emit per-file events", branch_start)
    branch = src[branch_start:branch_end]
    assert "self._fail(" not in branch, (
        "a PARTIAL failure (some files generated) must not abort the "
        "whole loop — only a TOTAL failure (0 files) should"
    )


def test_failed_paths_computed_from_gather_results():
    src = _src()
    assert re.search(
        r"failed_paths\s*=\s*\[p for p, r in zip\(paths, _results\) if not r\]",
        src,
    ), "failed_paths must be derived from the same gather results as `generated`"
