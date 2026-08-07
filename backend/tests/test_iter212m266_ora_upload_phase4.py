"""
tests/test_iter212m266_ora_upload_phase4.py — Phase 4 · Feb 2026

Static contract tests for the tier-gated /ora-chat/upload endpoint.
Runtime tests (real MarkItDown / vision LLM) are out of scope for
the fast pytest suite — those are exercised via the existing
`test_iter59_upload_image_vision.py` against `/upload/convert`.
This file locks the Phase 4 wrapper's specific guarantees:

  1. 10 MB cap constant present (matches frontend contract).
  2. Free / Starter tiers are refused with a structured 402 payload.
  3. Pro / Team / Founder are the exact allow-list.
  4. Endpoint is admin-gated (require_admin).
  5. Response shape mirrors /upload/convert so the frontend
     attachment-pill component stays single-source.
"""
from __future__ import annotations

from pathlib import Path


_SRC = Path("/app/backend/routers/ora_chat.py").read_text()


class TestUploadContract:
    def test_endpoint_present(self):
        assert '@router.post("/upload")' in _SRC

    def test_endpoint_is_admin_gated(self):
        idx = _SRC.find('@router.post("/upload")')
        body = _SRC[idx:idx + 4000]
        assert "await require_admin(authorization)" in body

    def test_10mb_cap_constant_matches_brief(self):
        assert "_ORA_UPLOAD_MAX_BYTES = 10 * 1024 * 1024" in _SRC

    def test_allowed_tiers_are_exactly_pro_team_founder(self):
        assert '_ORA_UPLOAD_ALLOWED_TIERS = {"pro", "team", "founder"}' in _SRC

    def test_whitelist_is_exactly_png_jpg_webp_pdf(self):
        # Founder brief (2026-02-08 Phase 4): tighten to these four.
        # Everything the generic /upload/convert supports (docx/xlsx/
        # txt/csv/html/gif/bmp/…) must NOT be in this endpoint's list.
        assert '"image/png"' in _SRC
        assert '"image/jpeg"' in _SRC
        assert '"image/webp"' in _SRC
        assert '"application/pdf"' in _SRC
        for banned in (
            '"application/vnd.openxmlformats-officedocument.wordprocessingml.document"',
            '"text/csv"', '"image/gif"', '"image/bmp"', '"text/html"',
        ):
            assert banned not in _SRC, f"disallowed MIME still whitelisted: {banned}"

    def test_disallowed_type_returns_structured_415(self):
        idx = _SRC.find('@router.post("/upload")')
        body = _SRC[idx:idx + 5000]
        assert '415' in body
        assert '"error":    "file_type_not_allowed"' in body
        # allowed list must be echoed in the error payload so the
        # frontend can render a specific toast without parsing prose.
        assert '"allowed":  ["png", "jpg", "webp", "pdf"]' in body

    def test_both_ext_AND_mime_must_match(self):
        # Defense-in-depth: a `.jpg` file with a masqueraded MIME
        # (e.g. text/html) MUST still be refused.  Test the source
        # asserts an `and` between the two checks.
        idx = _SRC.find("_ORA_UPLOAD_ALLOWED_EXTS")
        body = _SRC[idx:idx + 4000]
        assert "_ext not in _ORA_UPLOAD_ALLOWED_EXTS or _mime not in _ORA_UPLOAD_ALLOWED_MIMES" in body

    def test_free_tier_returns_structured_402(self):
        idx = _SRC.find('@router.post("/upload")')
        body = _SRC[idx:idx + 4000]
        # Structured payload for the upgrade nudge — frontend renders
        # this differently from the free-form 402 messages.
        assert '"error":   "tier_locked"' in body
        assert '"feature": "file_upload"' in body
        assert '"upgrade_url"' in body

    def test_oversized_upload_returns_413_with_size_info(self):
        idx = _SRC.find('@router.post("/upload")')
        body = _SRC[idx:idx + 4000]
        assert '413' in body
        assert '"error":   "file_too_large"' in body
        assert '"max_mb"' in body

    def test_reuses_shared_conversion_helpers(self):
        # Vision + MarkItDown machinery lives in routers/upload.py —
        # this endpoint MUST import + reuse it, never re-implement.
        idx = _SRC.find('@router.post("/upload")')
        body = _SRC[idx:idx + 4000]
        assert "from routers.upload import" in body
        assert "_describe_image_via_vision" in body
        assert "IMAGE_EXTS" in body
        assert "MAX_MD_CHARS" in body

    def test_response_shape_matches_upload_convert(self):
        idx = _SRC.find('@router.post("/upload")')
        body = _SRC[idx:idx + 6000]
        # These are the exact keys /upload/convert returns — mirror
        # so the frontend pill uses one code path.
        for key in ('"ok"', '"kind"', '"filename"', '"content_type"',
                     '"original_size"', '"md_size"', '"truncated"',
                     '"markdown"'):
            assert key in body, f"response missing key {key}"
