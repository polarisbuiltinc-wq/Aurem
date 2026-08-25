"""
Edge-case coverage added by testing agent for the W3 R1/R2/R4-minimal/R5
capabilities seam. Complements test_iter_w3_r4_capabilities_seam.py.
"""
from __future__ import annotations

import asyncio
import json


def test_error_code_and_i18n_catalog_have_file_language_unverified():
    """The error_code emitted on skip rows must be a real ErrorCode
    member AND have an i18n catalog entry (no orphan code strings)."""
    from core.errors import ErrorCode

    assert ErrorCode.FILE_LANGUAGE_UNVERIFIED.value == "FILE_LANGUAGE_UNVERIFIED"

    with open("/app/backend/i18n/errors_en.json") as fh:
        catalog = json.load(fh)
    assert "FILE_LANGUAGE_UNVERIFIED" in catalog, (
        f"i18n catalog missing FILE_LANGUAGE_UNVERIFIED entry: "
        f"{list(catalog.keys())}"
    )


def test_r2_missing_linter_binary_rc127_is_marked_unverified(monkeypatch):
    """The rc=127 (linter binary missing) skip branch must also carry
    verified=False + error_code=FILE_LANGUAGE_UNVERIFIED — same honest
    treatment as the unmapped-extension branch."""
    from services import loop_verify as lv

    async def _fake_run(cmd, cwd, timeout=8):
        # Simulate the linter binary missing on the pod (rc=127 path).
        return 127, b"", b"linter binary 'ruff' not installed"

    monkeypatch.setattr(lv, "_run", _fake_run)

    report = asyncio.run(lv.verify_files([
        {"path": "app.py", "content": "x = 1\n"},
    ]))
    row = report["results"][0]
    assert row["linter"] == "skip", row
    assert row["ok"] is True, row
    assert row["verified"] is False, row
    assert row["error_code"] == "FILE_LANGUAGE_UNVERIFIED", row


def test_r2_real_python_with_lint_failure_still_verified_true():
    """A real .py file that ruff FAILS on must still be verified=True
    (a real check actually ran) — verified is orthogonal to ok."""
    from services.loop_verify import verify_files

    # unused import is a ruff F401 error — real, deterministic
    report = asyncio.run(verify_files([
        {"path": "bad.py", "content": "import os\n"},
    ]))
    row = report["results"][0]
    assert row["linter"] == "ruff", row
    assert row["verified"] is True, (
        f"a real linter run must set verified=True even when the "
        f"lint failed: {row}"
    )
    assert row["ok"] is False, row
    # Top-level ok should reflect the failure
    assert report["ok"] is False


def test_r2_mixed_batch_top_level_ok_reflects_only_real_failures():
    """Batch: 1 unverified file (README.md) + 1 clean .py.
    Both ok=True → top-level ok=True. Confirms an unverified-language
    file doesn't drag down the overall report."""
    from services.loop_verify import verify_files

    report = asyncio.run(verify_files([
        {"path": "README.md", "content": "# hi\n"},
        {"path": "ok.py", "content": "x = 1\n"},
    ]))
    assert report["ok"] is True
    by_path = {r["path"]: r for r in report["results"]}
    assert by_path["README.md"]["verified"] is False
    assert by_path["README.md"]["ok"] is True
    assert by_path["ok.py"]["verified"] is True
    assert by_path["ok.py"]["ok"] is True


def test_capabilities_dict_shape_stability():
    """Snapshot the shape of get_capabilities() so accidental key
    removal breaks a test rather than a downstream consumer."""
    from services.capabilities import get_capabilities

    caps = get_capabilities()
    required_keys = {
        "can_edit_text_files", "can_edit_binary_files",
        "verified_extensions", "verify_tools",
        "unverified_extensions_note", "can_run_tests",
    }
    assert required_keys.issubset(caps.keys()), (
        f"missing keys: {required_keys - caps.keys()}"
    )
    # verified_extensions should be sorted (deterministic public API)
    assert caps["verified_extensions"] == sorted(caps["verified_extensions"])
    # No extension should map to a tool string that's empty
    for ext, tool in caps["verify_tools"].items():
        assert ext.startswith(".") and tool, (ext, tool)
