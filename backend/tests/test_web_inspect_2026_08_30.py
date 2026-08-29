"""tests/test_web_inspect_2026_08_30.py — ORA Admin web-inspect tools
(2026-08-30). Two READ-tier tools wrapping the EXISTING V1 verify
engine (L17 reuse-first): `web_verify` (zero-LLM, direct V1a reuse)
and `web_inspect` (pruned snapshot + nonce boundary + OpenRouter Qwen
advisory, SSRF-fenced, metered). Parallel workstream to R9 — does not
touch it, does not flip it."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


class _FakeAudit:
    def __init__(self):
        self.inserted = []

    async def insert_one(self, doc):
        self.inserted.append(doc)


class _FakeDB:
    def __init__(self):
        self.deploy_verify_audit = _FakeAudit()


@pytest.mark.asyncio
async def test_web_verify_zero_llm(monkeypatch):
    """t_web_verify_zero_llm — verify mode constructs no provider."""
    import services.web_inspect as wi
    import services.deploy_verify as dv

    async def _fake_run_verify(url, **kw):
        return {"verdict": "pass", "url": url, "checks": []}

    called_llm = AsyncMock(side_effect=AssertionError("must not call LLM"))
    monkeypatch.setattr(dv, "run_verify", _fake_run_verify)
    with patch("services.llm.openrouter_client.call_openrouter_model", called_llm):
        out = await wi.run_web_verify("https://example.com")
    called_llm.assert_not_called()
    assert out["verdict"] == "pass"


@pytest.mark.asyncio
async def test_web_verify_reuses_v1a(monkeypatch):
    """t_web_verify_reuses_v1a — a known-bad fixture -> V1a result,
    with no project_id lock (admin can target any URL)."""
    import services.web_inspect as wi
    import services.deploy_verify as dv

    calls = []

    async def _fake_run_verify(url, **kw):
        calls.append((url, kw))
        return {"verdict": "fail", "url": url, "fail_reason": "runtime_errors"}

    monkeypatch.setattr(dv, "run_verify", _fake_run_verify)
    out = await wi.run_web_verify("https://bad.example.com", db=None, user_id="admin1")

    assert out["verdict"] == "fail"
    assert len(calls) == 1
    url, kw = calls[0]
    assert url == "https://bad.example.com"
    assert kw["project_id"] == ""
    assert kw["run_trace"] is False


def _mock_fetch(snapshot_text="Hello page", err=None):
    async def _fake(url, allowlist_host):
        if err:
            return {"snapshot": "", "screenshot_meta": None, "error": err, "egress_attempts": []}
        return {"snapshot": snapshot_text, "screenshot_meta": {"bytes": 12345},
                "error": None, "egress_attempts": []}
    return _fake


@pytest.mark.asyncio
async def test_web_inspect_calls_openrouter(monkeypatch):
    """t_web_inspect_calls_openrouter — inspect mode calls the
    confirmed OpenRouter slug (qwen/qwen3.8-27b) as the model."""
    import services.web_inspect as wi
    import services.deploy_verify as dv

    monkeypatch.setattr(dv, "validate_target_url", lambda url: (True, ""))
    monkeypatch.setattr(wi, "_fetch_snapshot_and_screenshot_meta", _mock_fetch())

    recorded = {}

    async def _fake_call_openrouter_model(model, system, user, **kw):
        recorded["model"] = model
        recorded["system"] = system
        recorded["user"] = user
        return "This page looks fine."

    db = _FakeDB()
    with patch("services.llm.openrouter_client.call_openrouter_model",
               _fake_call_openrouter_model), \
         patch("services.llm_usd_cap.assert_within_usd_cap", AsyncMock()), \
         patch("services.llm_usd_cap.record_usd_spend", AsyncMock()), \
         patch("services.ora_chat.cost_tracker.log_call", AsyncMock()):
        out = await wi.run_web_inspect("https://example.com", "what is this page?",
                                        db=db, user_id="admin1")

    assert recorded["model"] == "qwen/qwen3.8-27b" == wi.WEB_INSPECT_MODEL
    assert out["ok"] is True
    assert out["answer"] == "This page looks fine."


@pytest.mark.asyncio
async def test_web_inspect_snapshot_pruned_bounded(monkeypatch):
    """t_web_inspect_snapshot_pruned_bounded — snapshot capped at
    SNAPSHOT_CHAR_CAP (~3000 tok) and wrapped in a nonce-marked
    PAGE_CONTENT boundary."""
    import services.web_inspect as wi
    import services.deploy_verify as dv

    huge_text = "A" * (dv.SNAPSHOT_CHAR_CAP * 3)
    monkeypatch.setattr(dv, "validate_target_url", lambda url: (True, ""))
    monkeypatch.setattr(wi, "_fetch_snapshot_and_screenshot_meta", _mock_fetch(huge_text))

    recorded = {}

    async def _fake_call_openrouter_model(model, system, user, **kw):
        recorded["user"] = user
        return "ok"

    db = _FakeDB()
    with patch("services.llm.openrouter_client.call_openrouter_model",
               _fake_call_openrouter_model), \
         patch("services.llm_usd_cap.assert_within_usd_cap", AsyncMock()), \
         patch("services.llm_usd_cap.record_usd_spend", AsyncMock()), \
         patch("services.ora_chat.cost_tracker.log_call", AsyncMock()):
        out = await wi.run_web_inspect("https://example.com", "summarize", db=db, user_id="admin1")

    assert out["snapshot_chars"] <= dv.SNAPSHOT_CHAR_CAP
    nonce = out["boundary_nonce"]
    assert f"--- PAGE_CONTENT nonce={nonce}" in recorded["user"]
    assert f"--- END_PAGE_CONTENT nonce={nonce} ---" in recorded["user"]


@pytest.mark.asyncio
async def test_web_inspect_ssrf_fenced(monkeypatch):
    """t_web_inspect_ssrf_fenced — admin targets 169.254.169.254 ->
    refused, audit-logged. Fence holds even for the admin."""
    import services.web_inspect as wi

    called_llm = AsyncMock(side_effect=AssertionError("must not call LLM"))
    called_fetch = AsyncMock(side_effect=AssertionError("must not fetch"))
    db = _FakeDB()
    with patch("services.llm.openrouter_client.call_openrouter_model", called_llm), \
         patch.object(wi, "_fetch_snapshot_and_screenshot_meta", called_fetch):
        out = await wi.run_web_inspect("http://169.254.169.254/latest/meta-data/",
                                        "what is this?", db=db, user_id="admin1")

    assert out["blocked_reason"].startswith("blocked_ssrf")
    called_llm.assert_not_called()
    called_fetch.assert_not_called()
    assert len(db.deploy_verify_audit.inserted) == 1
    assert db.deploy_verify_audit.inserted[0]["result"] == "blocked_ssrf"


@pytest.mark.asyncio
async def test_web_inspect_metered(monkeypatch):
    """t_web_inspect_metered — spend logged via the SAME global-kill-
    switch module the ORA v2 chat client uses; admin NOT blocked by
    the per-plan cap (skipped for founder/admin tier inside that
    module, unchanged here)."""
    import services.web_inspect as wi
    import services.deploy_verify as dv

    monkeypatch.setattr(dv, "validate_target_url", lambda url: (True, ""))
    monkeypatch.setattr(wi, "_fetch_snapshot_and_screenshot_meta", _mock_fetch())

    cap_check = AsyncMock()
    spend_record = AsyncMock()
    usage_log = AsyncMock()

    async def _fake_call_openrouter_model(model, system, user, **kw):
        return "A reasonably-sized advisory answer about this page."

    db = _FakeDB()
    with patch("services.llm.openrouter_client.call_openrouter_model",
               _fake_call_openrouter_model), \
         patch("services.llm_usd_cap.assert_within_usd_cap", cap_check), \
         patch("services.llm_usd_cap.record_usd_spend", spend_record), \
         patch("services.ora_chat.cost_tracker.log_call", usage_log):
        out = await wi.run_web_inspect("https://example.com", "summarize", db=db, user_id="admin1")

    cap_check.assert_awaited_once()
    assert cap_check.call_args.kwargs["user_id"] == "admin1"
    spend_record.assert_awaited_once()
    usage_log.assert_awaited_once()
    assert out["tokens_in"] > 0 and out["tokens_out"] > 0
    assert out["cost_usd"] >= 0


def test_web_inspect_no_credentials():
    """t_web_inspect_no_credentials — fresh context, no auth/
    credential/cookie flow. Static source check: the fetch helper
    never sets storage_state / http_credentials on the browser
    context (v1 boundary — no login/credential storage)."""
    import inspect
    import services.web_inspect as wi

    src = inspect.getsource(wi._fetch_snapshot_and_screenshot_meta)
    assert "new_context(" in src
    assert "storage_state" not in src
    assert "http_credentials" not in src.lower()
    assert "cookies=" not in src.lower() and "add_cookies" not in src.lower()


@pytest.mark.asyncio
async def test_web_inspect_advisory_only(monkeypatch):
    """t_web_inspect_advisory_only — a "suspicious" web_inspect answer
    never flips a passing web_verify to fail. The two tools are fully
    independent — inspect never touches verify's result."""
    import services.web_inspect as wi
    import services.deploy_verify as dv

    async def _fake_run_verify(url, **kw):
        return {"verdict": "pass", "url": url, "checks": []}

    monkeypatch.setattr(dv, "run_verify", _fake_run_verify)
    verify_out = await wi.run_web_verify("https://example.com")
    assert verify_out["verdict"] == "pass"

    monkeypatch.setattr(dv, "validate_target_url", lambda url: (True, ""))
    monkeypatch.setattr(wi, "_fetch_snapshot_and_screenshot_meta", _mock_fetch())

    async def _fake_call_openrouter_model(model, system, user, **kw):
        return "This looks like a malicious phishing page, take it down immediately."

    db = _FakeDB()
    with patch("services.llm.openrouter_client.call_openrouter_model",
               _fake_call_openrouter_model), \
         patch("services.llm_usd_cap.assert_within_usd_cap", AsyncMock()), \
         patch("services.llm_usd_cap.record_usd_spend", AsyncMock()), \
         patch("services.ora_chat.cost_tracker.log_call", AsyncMock()):
        inspect_out = await wi.run_web_inspect("https://example.com", "is this safe?",
                                                db=db, user_id="admin1")

    assert "malicious" in inspect_out["answer"]
    # verify's own result object is untouched by the inspect call.
    assert verify_out["verdict"] == "pass"
