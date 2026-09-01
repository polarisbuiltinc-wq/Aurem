"""
tests/test_stream_golden_regression.py — 2026-09-08 StreamState
refactor anti-regression gate.

Compares the 6 scenarios in _stream_golden_harness.SCENARIOS against
the golden reference captured pre-refactor
(tests/_golden/stream_refactor_2026_09_08.json, via
_capture_stream_golden.py). Wall-clock-derived fields
(thinking_s/elapsed_s/t_started) are normalized before comparison —
see _stream_golden_harness.py module docstring for why we don't fake
time.monotonic globally. Every other field — event order, event
types, content, mode, provider, council/verified_paths data — is
compared byte-for-byte via the normalized event list.

This is the anti-regression net for the worker.py/watchdog.py/
retries.py/context_build.py/sse_stream.py split. If this test is
green, the refactor did not change observable behavior for any of
the 6 covered code paths (orchestrator fallback, Mode-D fast-path,
Mode-F engage, confidence-mismatch retry, soft timeout, hard timeout).
"""
import json
import os

import pytest

from tests._stream_golden_harness import SCENARIOS, normalize_events, parse_events

GOLDEN_PATH = os.path.join(os.path.dirname(__file__), "_golden",
                           "stream_refactor_2026_09_08.json")

with open(GOLDEN_PATH, encoding="utf-8") as _fh:
    _GOLDEN = json.load(_fh)


@pytest.mark.parametrize("scenario_name", sorted(SCENARIOS.keys()))
def test_stream_matches_golden(scenario_name, monkeypatch):
    runner = SCENARIOS[scenario_name]
    actual_text = runner(monkeypatch)
    actual = normalize_events(parse_events(actual_text))
    golden = normalize_events(parse_events(_GOLDEN[scenario_name]))
    assert actual == golden, (
        f"stream.py refactor changed observable behavior for "
        f"scenario '{scenario_name}' — event sequence no longer "
        f"matches the pre-refactor golden reference."
    )
