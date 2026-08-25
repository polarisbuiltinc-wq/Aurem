"""tests/resilience/test_core_errors.py - Resilience Layer Phase 1 (2026-08-25).

Covers core/errors.py: classify_exception (type/structure-based, not
message-based), ref_id format, envelope shape, i18n fallback, and the
RETRYABLE_CODES contract used by the (Phase 2) retry policy.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from core.errors import (
    ContractError, ErrorCode, RETRYABLE_CODES, build_error_envelope,
    classify_exception, new_ref_id, translate_error,
)


class TestClassifyExceptionSchemaMismatch:
    def test_reproduces_the_production_incident(self):
        """The exact bug: dict-shaped .get() called on a str."""
        try:
            "a plain path string".get("path")
        except AttributeError as exc:
            assert classify_exception(exc) == ErrorCode.SCHEMA_MISMATCH
        else:
            pytest.fail("expected AttributeError")

    def test_items_and_keys_also_classify(self):
        for method in ("items", "keys", "values"):
            try:
                getattr("x", method)()
            except AttributeError as exc:
                assert classify_exception(exc) == ErrorCode.SCHEMA_MISMATCH

    def test_classification_independent_of_message_language(self):
        """Same TYPE/STRUCTURE, non-English message text -- must not
        change classification (never parses str(exc))."""
        for msg in (
            "yeh ek takniki error hai",
            "Dies ist ein technischer Fehler",
            "kore wa gijutsuteki na error desu",
        ):
            exc = AttributeError(msg)
            exc.name = "get"
            exc.obj = "some string"
            assert classify_exception(exc) == ErrorCode.SCHEMA_MISMATCH

    def test_attribute_error_on_real_dict_is_not_schema_mismatch(self):
        # A genuinely missing attribute on an actual dict is a
        # different failure mode -- don't over-classify.
        try:
            {}.nonexistent_attr
        except AttributeError as exc:
            assert classify_exception(exc) == ErrorCode.INTERNAL_UNKNOWN

    def test_contract_error_always_schema_mismatch(self):
        assert classify_exception(ContractError("x")) == ErrorCode.SCHEMA_MISMATCH

    def test_type_error_and_key_error_and_json_decode_error(self):
        assert classify_exception(TypeError()) == ErrorCode.SCHEMA_MISMATCH
        assert classify_exception(KeyError("x")) == ErrorCode.SCHEMA_MISMATCH
        try:
            json.loads("{not json")
        except json.JSONDecodeError as exc:
            assert classify_exception(exc) == ErrorCode.SCHEMA_MISMATCH


class TestClassifyExceptionOtherClasses:
    def test_asyncio_timeout(self):
        assert classify_exception(asyncio.TimeoutError()) == ErrorCode.TIMEOUT

    def test_builtin_timeout(self):
        assert classify_exception(TimeoutError()) == ErrorCode.TIMEOUT

    def test_breaker_open_is_dependency_down(self):
        from services.retry_guard import BreakerOpenError
        assert classify_exception(BreakerOpenError("x", retry_after_s=5)) == ErrorCode.DEPENDENCY_DOWN

    def test_permission_error(self):
        assert classify_exception(PermissionError()) == ErrorCode.PERMISSION_DENIED

    def test_unknown_exception_falls_back_to_internal_unknown(self):
        class SomeRandomLibraryError(Exception):
            pass
        assert classify_exception(SomeRandomLibraryError("boom")) == ErrorCode.INTERNAL_UNKNOWN

    def test_rate_limited_via_status_code_attr(self):
        exc = Exception("throttled")
        exc.status_code = 429
        assert classify_exception(exc) == ErrorCode.RATE_LIMITED


class TestRefIdAndRetryable:
    def test_ref_id_format(self):
        r = new_ref_id()
        assert r.startswith("ORA-")
        assert len(r) == len("ORA-") + 6

    def test_ref_id_unique_across_calls(self):
        assert new_ref_id() != new_ref_id()

    def test_retryable_codes_membership(self):
        assert ErrorCode.TIMEOUT in RETRYABLE_CODES
        assert ErrorCode.DEPENDENCY_DOWN in RETRYABLE_CODES
        assert ErrorCode.RATE_LIMITED in RETRYABLE_CODES

    def test_deterministic_codes_never_retryable(self):
        assert ErrorCode.SCHEMA_MISMATCH not in RETRYABLE_CODES
        assert ErrorCode.AUTH_FAILED not in RETRYABLE_CODES
        assert ErrorCode.PERMISSION_DENIED not in RETRYABLE_CODES


class TestTranslateErrorAndEnvelope:
    def test_translate_error_known_code(self):
        content = translate_error(ErrorCode.SCHEMA_MISMATCH)
        assert content["title"] == "Data format issue"
        assert "what_to_try" in content

    def test_translate_error_unknown_locale_falls_back_to_en(self):
        content = translate_error(ErrorCode.TIMEOUT, locale="fr")
        assert content["title"] == "Request timed out"

    def test_build_error_envelope_shape_and_no_raw_text(self):
        try:
            "x".get("y")
        except AttributeError as exc:
            envelope = build_error_envelope(exc)
        assert envelope["error_code"] == ErrorCode.SCHEMA_MISMATCH.value
        assert envelope["ref_id"].startswith("ORA-")
        assert envelope["can_retry"] is False
        assert set(envelope) == {
            "error_code", "title", "what_happened", "what_to_try",
            "can_retry", "ref_id",
        }
        # Never leak the raw exception text anywhere in the envelope.
        rendered = json.dumps(envelope)
        assert "attribute" not in rendered.lower()

    def test_build_error_envelope_accepts_explicit_ref_id(self):
        envelope = build_error_envelope(RuntimeError("x"), ref_id="ORA-fixed1")
        assert envelope["ref_id"] == "ORA-fixed1"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
