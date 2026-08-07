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
        body = _SRC[idx:idx + 4000]
        # These are the exact keys /upload/convert returns — mirror
        # so the frontend pill uses one code path.
        for key in ('"ok"', '"kind"', '"filename"', '"content_type"',
                     '"original_size"', '"md_size"', '"truncated"',
                     '"markdown"'):
            assert key in body, f"response missing key {key}"
