"""
One-time golden-capture script for the stream.py StreamState refactor
(2026-09-08). Run BEFORE any refactor code changes:

    cd /app/backend && python -m tests._capture_stream_golden

Writes tests/_golden/stream_refactor_2026_09_08.json — the exact raw
SSE response text for each of the 6 scenarios in
tests/_stream_golden_harness.py::SCENARIOS. The permanent regression
test (test_stream_golden_regression.py) re-runs the same scenarios
post-refactor and asserts byte-identical equality against this file.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from _pytest.monkeypatch import MonkeyPatch

from tests._stream_golden_harness import SCENARIOS

GOLDEN_PATH = os.path.join(os.path.dirname(__file__), "_golden",
                           "stream_refactor_2026_09_08.json")


def main():
    os.makedirs(os.path.dirname(GOLDEN_PATH), exist_ok=True)
    out = {}
    for name, runner in SCENARIOS.items():
        mp = MonkeyPatch()
        try:
            text = runner(mp)
            out[name] = text
            print(f"captured {name}: {len(text)} chars, "
                  f"{text.count(chr(10)+chr(10))} events")
        finally:
            mp.undo()
    with open(GOLDEN_PATH, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)
    print(f"wrote {GOLDEN_PATH}")


if __name__ == "__main__":
    main()
