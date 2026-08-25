"""
tests/test_iter_hardening_i18n_catalog_completeness.py — Production
Hardening Fix 1 (2026-08).

Standing guard: every core/errors.py::ErrorCode MUST have a matching
entry in i18n/errors_en.json, with the same {title, what_happened,
what_to_try} shape as every other entry. Without this, a new
ErrorCode can be added and silently fall back to the generic
INTERNAL_UNKNOWN message forever (exactly what happened to
LOOP_SELF_HEAL_EXHAUSTED before this fix) — this test makes that
drift impossible to land silently again.
"""
import json
import os

from core.errors import ErrorCode

_CATALOG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "i18n", "errors_en.json",
)


def _load_catalog() -> dict:
    with open(_CATALOG_PATH, encoding="utf-8") as f:
        return json.load(f)


def test_every_error_code_has_a_catalog_entry():
    catalog = _load_catalog()
    missing = [c.value for c in ErrorCode if c.value not in catalog]
    assert not missing, (
        f"i18n/errors_en.json is missing entries for: {missing}. "
        f"Every core.errors.ErrorCode must have a translated message."
    )


def test_every_catalog_entry_has_the_required_shape():
    catalog = _load_catalog()
    required_keys = {"title", "what_happened", "what_to_try"}
    for code, entry in catalog.items():
        assert required_keys.issubset(entry.keys()), (
            f"{code}: catalog entry missing one of {required_keys} — "
            f"has {set(entry.keys())}"
        )
        assert isinstance(entry["title"], str) and entry["title"]
        assert isinstance(entry["what_happened"], str) and entry["what_happened"]
        assert isinstance(entry["what_to_try"], list) and entry["what_to_try"]


def test_loop_self_heal_exhausted_is_paused_not_failed_language():
    """This session's decision: self-heal exhaustion = PAUSED_FOR_USER,
    never 'failed'. Lock the exact wording so it can't regress back to
    failure-language."""
    catalog = _load_catalog()
    entry = catalog["LOOP_SELF_HEAL_EXHAUSTED"]
    assert "paused" in entry["title"].lower()
    assert entry["what_happened"] == (
        "I tried to fix this automatically a couple of times and it "
        "didn't work, so I've paused. Here's what's going on and how "
        "you can help."
    )
    assert "fail" not in entry["what_happened"].lower()
