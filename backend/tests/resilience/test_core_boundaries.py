"""tests/resilience/test_core_boundaries.py - Resilience Layer Phase 1 (2026-08-25).

Covers core/boundaries.py: coerce() + normalize_payload() + ContractError.
"""
from __future__ import annotations

import pytest

from core.boundaries import ContractError, coerce, normalize_payload
from core.errors import ErrorCode, classify_exception


class TestCoerce:
    def test_dict_passthrough(self):
        d = {"a": 1}
        assert coerce(d, dict) is d

    def test_str_json_object_coerces_to_dict(self):
        assert coerce('{"path": "a.py"}', dict) == {"path": "a.py"}

    def test_str_invalid_json_raises_contract_error(self):
        with pytest.raises(ContractError):
            coerce("not json at all", dict)

    def test_str_json_array_raises_contract_error(self):
        # Valid JSON, but not the expected shape (dict).
        with pytest.raises(ContractError):
            coerce("[1, 2, 3]", dict)

    def test_wrong_type_entirely_raises_contract_error(self):
        with pytest.raises(ContractError):
            coerce(42, dict)
        with pytest.raises(ContractError):
            coerce(None, dict)

    def test_contract_error_classifies_as_schema_mismatch(self):
        try:
            coerce("bad json{", dict, context="test_boundary")
        except ContractError as exc:
            assert classify_exception(exc) == ErrorCode.SCHEMA_MISMATCH
            assert "test_boundary" in str(exc)


class TestNormalizePayload:
    def test_accepts_dict(self):
        assert normalize_payload({"x": 1}) == {"x": 1}

    def test_accepts_json_string(self):
        assert normalize_payload('{"x": 1}') == {"x": 1}

    def test_rejects_plain_string_that_should_have_been_a_dict(self):
        """This is exactly the production incident's shape: a plain
        path string arriving where a per-file dict was expected."""
        with pytest.raises(ContractError):
            normalize_payload("backend/tests/test_admin_panel_features.py")

    def test_rejects_list(self):
        with pytest.raises(ContractError):
            normalize_payload([{"path": "a.py"}])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
