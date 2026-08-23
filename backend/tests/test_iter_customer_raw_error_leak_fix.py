"""Regression tests for the 2026-08-25 customer-reported bug:
   Raw Python `AttributeError: 'str' object has no attribute 'get'`
   leaked into the chat UI from OpenRouter response parsing.

Covers:
   1. openrouter_providers._call_deepseek converts non-dict `message`
      into a controlled RuntimeError('OpenRouter malformed response…').
   2. _call_deepseek_direct raises TypeError (caught upstream) on the
      same non-dict-message shape.
   3. failure_signature.compute_signature is stable across noise
      (whitespace / case / hex ids) — required for repeat detection.
   4. failure_signature.record_and_check increments repeat_count.
   5. error_classifier.classify_error never returns raw exception text.
   6. error_translator new static rule matches the malformed pattern.
   7. Existing static rules still match (regression guard).
"""
import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, "/app/backend")


# ── 1 & 2. OpenRouter/DeepSeek-direct non-dict message guard ─────────
def _make_httpx_response(payload):
    r = MagicMock()
    r.status_code = 200
    r.raise_for_status = MagicMock()
    r.json = MagicMock(return_value=payload)
    return r


class _FakeAsyncClient:
    def __init__(self, payload):
        self._payload = payload
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def post(self, *a, **kw): return _make_httpx_response(self._payload)


@pytest.mark.asyncio
async def test_call_deepseek_non_dict_message_raises_runtime_error():
    os.environ["OPENROUTER_API_KEY"] = "test-key"
    from services.llm import openrouter_providers as opv
    # message is a bare STRING instead of {role, content} → previously
    # AttributeError. Must now surface as clean RuntimeError.
    malformed = {"choices": [{"message": "hello I'm a string not a dict"}]}
    with patch.object(opv.httpx, "AsyncClient",
                      return_value=_FakeAsyncClient(malformed)):
        with pytest.raises(RuntimeError) as excinfo:
            await opv._call_deepseek(
                messages=[{"role": "user", "content": "hi"}])
    assert "malformed response" in str(excinfo.value).lower()
    # And crucially NOT an AttributeError anywhere in the chain.
    assert "'str' object has no attribute 'get'" not in str(excinfo.value)


@pytest.mark.asyncio
async def test_call_deepseek_direct_non_dict_message_raises_typeerror():
    os.environ["DEEPSEEK_API_KEY"] = "test-key"
    from services.llm import openrouter_providers as opv
    malformed = {"choices": [{"message": "not-a-dict"}]}
    with patch.object(opv.httpx, "AsyncClient",
                      return_value=_FakeAsyncClient(malformed)):
        with pytest.raises(TypeError) as excinfo:
            await opv._call_deepseek_direct(
                messages=[{"role": "user", "content": "hi"}])
    assert "not dict" in str(excinfo.value)


@pytest.mark.asyncio
async def test_call_deepseek_normal_dict_message_still_works():
    """Happy path — must not regress."""
    os.environ["OPENROUTER_API_KEY"] = "test-key"
    from services.llm import openrouter_providers as opv
    ok = {"choices": [{"message": {"role": "assistant", "content": "hi!"}}]}
    with patch.object(opv.httpx, "AsyncClient",
                      return_value=_FakeAsyncClient(ok)):
        out = await opv._call_deepseek(
            messages=[{"role": "user", "content": "hi"}])
    assert out == "hi!"


# ── 3. failure_signature stability ────────────────────────────────────
def test_failure_signature_stable_across_noise():
    from services.failure_signature import compute_signature
    s1 = compute_signature("p_abc123", "Add a comment to README.md",
                           "internal", "OpenRouter malformed response: ...")
    s2 = compute_signature("p_abc123", "  add a  COMMENT to README.md  ",
                           "internal", "OpenRouter malformed response: ...")
    # normalisation (whitespace, case) → same hash
    assert s1 == s2
    # different project → different hash
    s3 = compute_signature("p_other", "Add a comment to README.md",
                           "internal", "OpenRouter malformed response: ...")
    assert s3 != s1
    # different category → different hash
    s4 = compute_signature("p_abc123", "Add a comment to README.md",
                           "network", "OpenRouter malformed response: ...")
    assert s4 != s1


def test_failure_signature_hex_normalization():
    """Random hex ids/SHAs in error text must not fragment the signature."""
    from services.failure_signature import compute_signature
    s1 = compute_signature("p_1", "task", "internal",
                           "error at commit abc1234deadbeef")
    s2 = compute_signature("p_1", "task", "internal",
                           "error at commit 9f8e7d6c5b4a3210")
    assert s1 == s2


# ── 4. record_and_check increment behaviour ──────────────────────────
@pytest.mark.asyncio
async def test_record_and_check_increments():
    from services.failure_signature import record_and_check

    class _FakeColl:
        def __init__(self):
            self.count = 0
        async def find_one_and_update(self, filt, upd, upsert, return_document, projection):
            self.count += 1
            return {"repeat_count": self.count}

    class _FakeDB:
        def __init__(self): self.task_failure_signatures = _FakeColl()

    db = _FakeDB()
    r1 = await record_and_check(db, project_id="p1", signature="sig1")
    r2 = await record_and_check(db, project_id="p1", signature="sig1")
    assert r1["repeat_count"] == 1
    assert r2["repeat_count"] == 2


@pytest.mark.asyncio
async def test_record_and_check_never_raises_on_db_error():
    """Best-effort — a DB blip must not fail the task."""
    from services.failure_signature import record_and_check
    class _Bad:
        async def find_one_and_update(self, *a, **kw):
            raise RuntimeError("mongo down")
    class _DB: task_failure_signatures = _Bad()
    out = await record_and_check(_DB(), project_id="p", signature="s")
    assert out["repeat_count"] == 1


# ── 5. error_classifier never leaks raw text ─────────────────────────
def test_classify_error_no_raw_text():
    from services.error_classifier import classify_error
    e = AttributeError("'str' object has no attribute 'get'")
    out = classify_error(e)
    assert out["category"] in ("internal", "input", "network", "auth", "quota")
    assert "user_message" in out and out["user_message"]
    # Critical: user_message must NOT contain any raw exception text.
    assert "'str' object" not in out["user_message"]
    assert "AttributeError" not in out["user_message"]
    assert "get" not in out["user_message"].lower().split()  # not a Python phrase


def test_classify_error_returns_human_message_for_malformed():
    from services.error_classifier import classify_error
    e = RuntimeError("OpenRouter malformed response: 'str' object has no attribute 'get'")
    out = classify_error(e)
    assert out["user_message"]
    assert "malformed" not in out["user_message"].lower()  # sanitized
    assert "'str' object" not in out["user_message"]


# ── 6 & 7. error_translator static-rule regression ───────────────────
def test_error_translator_matches_openrouter_malformed():
    from services.error_translator import _static_match as translate_error_static
    out = translate_error_static("OpenRouter malformed response: {...}")
    assert out is not None
    assert out.get("plain")
    assert "provider" in out["plain"].lower() or "unexpected shape" in out["plain"].lower()


def test_error_translator_matches_deepseek_direct_malformed():
    from services.error_translator import _static_match as translate_error_static
    out = translate_error_static(
        "DeepSeek-direct malformed response: message is str, not dict")
    assert out is not None
    assert out.get("plain")


def test_error_translator_matches_message_shape_msg():
    from services.error_translator import _static_match as translate_error_static
    out = translate_error_static("message is str, expected dict")
    assert out is not None


def test_error_translator_existing_rules_still_match():
    """Regression guard — new addition must not break existing static rules."""
    from services.error_translator import _static_match as translate_error_static
    # Rate limit
    r = translate_error_static("HTTP 429 rate limit exceeded")
    assert r is not None
    # Lint fail
    lint = translate_error_static("eslint error: unused var")
    assert lint is not None
    # Token exhaust
    tok = translate_error_static("tokens remaining 0 for user")
    assert tok is not None


# ── 8. Backend health smoke check ────────────────────────────────────
def test_backend_health():
    import requests
    base = os.environ.get(
        "REACT_APP_BACKEND_URL",
        "https://bin-context-pat.preview.emergentagent.com").rstrip("/")
    r = requests.get(f"{base}/api/health", timeout=10)
    assert r.status_code == 200
