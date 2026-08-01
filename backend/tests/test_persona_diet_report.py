"""
tests/test_persona_diet_report.py — Proof for scripts/persona_diet_report.py

Verifies the helper script:
  - parses AUREM_CTO_PERSONA into sections
  - sums to the true total char count (no double-count, no bytes lost)
  - ranks sections descending by size
  - emits valid JSON in --json mode
  - respects --top cap
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from services.orchestrator import AUREM_CTO_PERSONA


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "persona_diet_report.py"


def _run(*args: str) -> tuple[int, str]:
    r = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, timeout=15,
    )
    return r.returncode, r.stdout


def test_script_exists_and_is_executable():
    assert SCRIPT.exists(), f"missing helper: {SCRIPT}"
    assert SCRIPT.stat().st_mode & 0o111, "script is not executable"


def test_json_mode_returns_valid_shape():
    rc, out = _run("--json")
    assert rc == 0
    data = json.loads(out)
    assert data["total_chars"] == len(AUREM_CTO_PERSONA)
    assert data["budget"] == 22_000
    assert data["warn_threshold"] == 20_000
    assert isinstance(data["sections"], list)
    assert len(data["sections"]) > 3, "should split into multiple sections"


def test_sections_sum_close_to_total():
    """Split sections must account for at least 99% of the persona
    (allowing tiny gaps at boundary whitespace)."""
    rc, out = _run("--json")
    data = json.loads(out)
    section_total = sum(s["chars"] for s in data["sections"])
    ratio = section_total / data["total_chars"]
    assert ratio >= 0.99, (
        f"section sum {section_total} << total {data['total_chars']} "
        f"(ratio {ratio:.3f}) — split is losing content"
    )
    # And must not over-count either.
    assert section_total <= data["total_chars"]


def test_sections_are_ranked_descending():
    rc, out = _run("--json")
    data = json.loads(out)
    sizes = [s["chars"] for s in data["sections"]]
    assert sizes == sorted(sizes, reverse=True), (
        f"sections not sorted heaviest-first: {sizes[:5]!r}"
    )


def test_top_flag_caps_output():
    rc, out = _run("--json", "--top", "3")
    data = json.loads(out)
    assert len(data["sections"]) == 3


def test_human_output_flags_current_state():
    """The live persona is currently over the 20k warn threshold —
    the human output must call that out prominently."""
    rc, out = _run()
    assert rc == 0
    if len(AUREM_CTO_PERSONA) >= 22_000:
        assert "OVER HARD BUDGET" in out
    elif len(AUREM_CTO_PERSONA) >= 20_000:
        assert "OVER WARN THRESHOLD" in out
    else:
        assert "under warn threshold" in out


def test_heaviest_section_is_reasonable():
    """The heaviest single section should not dwarf the rest — if
    one section is > 50% of the whole persona, either the section
    split is broken OR the persona has one runaway rules block."""
    rc, out = _run("--json")
    data = json.loads(out)
    heaviest = data["sections"][0]
    assert heaviest["pct_total"] < 50, (
        f"one section owns {heaviest['pct_total']}% of persona — "
        f"either the split is broken or {heaviest['heading']!r} "
        f"needs its own dedupe pass"
    )
