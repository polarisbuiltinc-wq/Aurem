"""
tests/test_github_app_service.py — Phase 1.1 unit coverage for
services/github_app.py.

Uses httpx.MockTransport (built-in, no external dep) to intercept
GitHub HTTP calls. JWT signing runs for real against a fresh
in-memory RSA keypair — no live network, no reliance on the
admin_settings.github_app_config Mongo doc.
"""
from __future__ import annotations

import hmac
import hashlib
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
import httpx
import jwt as _pyjwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding, PrivateFormat, NoEncryption, PublicFormat,
)

from services import github_app as ga
from services.github_app_config import (
    set_runtime_github_app_config,
    get_runtime_github_app_config,
)


# ─── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def rsa_keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = key.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption(),
    ).decode()
    pub_pem = key.public_key().public_bytes(
        Encoding.PEM, PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return {"private": priv_pem, "public": pub_pem}


@pytest.fixture
def configured_app(rsa_keypair):
    set_runtime_github_app_config({
        "app_id":         "123456",
        "app_slug":       "aurem-test",
        "private_key":    rsa_keypair["private"],
        "webhook_secret": "test-webhook-secret-1234",
    })
    ga._APP_JWT_CACHE = None
    ga._INSTALL_TOKEN_CACHE.clear()
    yield rsa_keypair
    set_runtime_github_app_config(None)
    ga._APP_JWT_CACHE = None
    ga._INSTALL_TOKEN_CACHE.clear()


@pytest.fixture
def not_configured():
    set_runtime_github_app_config(None)
    ga._APP_JWT_CACHE = None
    ga._INSTALL_TOKEN_CACHE.clear()
    yield
    ga._APP_JWT_CACHE = None
    ga._INSTALL_TOKEN_CACHE.clear()


def _make_mock_client(handler):
    """Return a factory that produces httpx.AsyncClient bound to a
    MockTransport running `handler`. Captures the real AsyncClient
    class BEFORE any patching so it can safely be substituted for
    the real one in `patch.object(httpx, 'AsyncClient', ...)`."""
    _RealAsyncClient = httpx.AsyncClient

    def factory(*args, **kwargs):
        return _RealAsyncClient(transport=httpx.MockTransport(handler))
    return factory


# ═════════════════════════════════════════════════════════════════════
# app_jwt()
# ═════════════════════════════════════════════════════════════════════

class TestAppJWT:
    def test_signs_and_verifies_with_matching_pubkey(self, configured_app):
        token = ga.app_jwt()
        decoded = _pyjwt.decode(
            token, configured_app["public"], algorithms=["RS256"],
        )
        assert decoded["iss"] == "123456"
        now = int(time.time())
        assert decoded["iat"] <= now
        assert decoded["exp"] > now

    def test_cache_reuse_within_ttl(self, configured_app):
        t1 = ga.app_jwt()
        t2 = ga.app_jwt()
        assert t1 == t2
        assert ga._APP_JWT_CACHE is not None

    def test_cache_invalidated_on_credential_rotation(self, configured_app):
        t1 = ga.app_jwt()
        cfg = get_runtime_github_app_config()
        set_runtime_github_app_config({**cfg, "app_id": "999999"})
        t2 = ga.app_jwt()
        assert t1 != t2
        assert ga._APP_JWT_CACHE[2] == "999999"

    def test_raises_when_not_configured(self, not_configured):
        with pytest.raises(ga.GitHubAppNotConfigured):
            ga.app_jwt()

    def test_raises_on_invalid_pem(self, rsa_keypair):
        set_runtime_github_app_config({
            "app_id":         "1",
            "app_slug":       "x",
            "private_key":    "-----BEGIN PRIVATE KEY-----\ngarbage\n-----END PRIVATE KEY-----",
            "webhook_secret": "abcdefgh",
        })
        ga._APP_JWT_CACHE = None
        try:
            with pytest.raises(ga.GitHubAppNotConfigured):
                ga.app_jwt()
        finally:
            set_runtime_github_app_config(None)


# ═════════════════════════════════════════════════════════════════════
# get_installation_token()
# ═════════════════════════════════════════════════════════════════════

class TestInstallationToken:
    @pytest.mark.asyncio
    async def test_mints_and_caches(self, configured_app):
        exp_iso = (datetime.now(timezone.utc) + timedelta(minutes=60)
                   ).strftime("%Y-%m-%dT%H:%M:%SZ")
        call_count = {"n": 0}

        def handler(request):
            call_count["n"] += 1
            assert request.url.path == "/app/installations/42/access_tokens"
            assert request.method == "POST"
            assert request.headers["authorization"].startswith("Bearer ")
            return httpx.Response(
                201, json={"token": "ghs_realtoken_fake_1", "expires_at": exp_iso},
            )

        with patch.object(httpx, "AsyncClient", _make_mock_client(handler)):
            token, exp = await ga.get_installation_token(42)
        assert token == "ghs_realtoken_fake_1"
        assert exp > time.time() + (55 * 60)
        assert call_count["n"] == 1

        # Second call within safety margin → cache hit, no HTTP.
        with patch.object(httpx, "AsyncClient", _make_mock_client(handler)):
            token2, _ = await ga.get_installation_token(42)
        assert token2 == token
        assert call_count["n"] == 1                                # unchanged

    @pytest.mark.asyncio
    async def test_early_expiry_forces_remint(self, configured_app):
        cfg = get_runtime_github_app_config()
        # Seed a token expiring in 30s (well inside 5-min margin)
        ga._INSTALL_TOKEN_CACHE[7] = (
            "ghs_almost_expired", time.time() + 30, cfg["app_id"],
        )

        exp_iso = (datetime.now(timezone.utc) + timedelta(minutes=60)
                   ).strftime("%Y-%m-%dT%H:%M:%SZ")

        def handler(request):
            return httpx.Response(
                201, json={"token": "ghs_fresh_token", "expires_at": exp_iso},
            )

        with patch.object(httpx, "AsyncClient", _make_mock_client(handler)):
            token, _ = await ga.get_installation_token(7)
        assert token == "ghs_fresh_token"

    @pytest.mark.asyncio
    async def test_404_evicts_cache_and_raises(self, configured_app):
        cfg = get_runtime_github_app_config()
        # Seed a stale cache row (expiring inside safety margin) so the
        # next call MUST mint fresh — and the fresh mint returns 404.
        ga._INSTALL_TOKEN_CACHE[13] = ("stale", time.time() + 30, cfg["app_id"])

        def handler(request):
            return httpx.Response(404, json={"message": "Not Found"})

        with patch.object(httpx, "AsyncClient", _make_mock_client(handler)):
            with pytest.raises(httpx.HTTPStatusError):
                await ga.get_installation_token(13)
        assert 13 not in ga._INSTALL_TOKEN_CACHE

    @pytest.mark.asyncio
    async def test_credential_rotation_invalidates_install_token_cache(
        self, configured_app,
    ):
        """A token minted under app_id=X must NOT be returned when the
        runtime cache flips to app_id=Y."""
        cfg = get_runtime_github_app_config()
        ga._INSTALL_TOKEN_CACHE[99] = (
            "ghs_old_app", time.time() + 4000, cfg["app_id"],
        )
        # Rotate app_id
        set_runtime_github_app_config({**cfg, "app_id": "999"})
        ga._APP_JWT_CACHE = None

        exp_iso = (datetime.now(timezone.utc) + timedelta(minutes=60)
                   ).strftime("%Y-%m-%dT%H:%M:%SZ")

        def handler(request):
            return httpx.Response(
                201, json={"token": "ghs_new_app", "expires_at": exp_iso},
            )

        with patch.object(httpx, "AsyncClient", _make_mock_client(handler)):
            token, _ = await ga.get_installation_token(99)
        assert token == "ghs_new_app"

    @pytest.mark.asyncio
    async def test_not_configured_raises(self, not_configured):
        with pytest.raises(ga.GitHubAppNotConfigured):
            await ga.get_installation_token(1)


# ═════════════════════════════════════════════════════════════════════
# verify_webhook_signature() — THE security-critical function
# ═════════════════════════════════════════════════════════════════════

class TestWebhookSignature:
    def _sig(self, secret: str, body: bytes) -> str:
        return "sha256=" + hmac.new(
            secret.encode(), body, hashlib.sha256,
        ).hexdigest()

    def test_valid_signature_passes(self, configured_app):
        body = b'{"action":"created","installation":{"id":1}}'
        header = self._sig("test-webhook-secret-1234", body)
        assert ga.verify_webhook_signature(body, header) is True

    def test_valid_signature_case_insensitive_hex(self, configured_app):
        body = b'{"a":1}'
        header = self._sig("test-webhook-secret-1234", body).upper()
        # sha256= prefix intact but hex UPPER
        header = "sha256=" + header.split("=", 1)[1]
        assert ga.verify_webhook_signature(body, header) is True

    def test_tampered_body_fails(self, configured_app):
        body = b'{"payload":"real"}'
        header = self._sig("test-webhook-secret-1234", body)
        assert ga.verify_webhook_signature(b'{"payload":"tampered"}', header) is False

    def test_wrong_secret_fails(self, configured_app):
        body = b'{"a":1}'
        header = self._sig("some-other-secret", body)
        assert ga.verify_webhook_signature(body, header) is False

    def test_missing_prefix_fails(self, configured_app):
        assert ga.verify_webhook_signature(b'body', "abc123") is False
        assert ga.verify_webhook_signature(b'body', "sha1=abc123") is False

    def test_wrong_length_fails(self, configured_app):
        assert ga.verify_webhook_signature(b'body', "sha256=abc") is False

    def test_missing_header_fails(self, configured_app):
        assert ga.verify_webhook_signature(b'body', None) is False
        assert ga.verify_webhook_signature(b'body', "") is False

    def test_not_configured_returns_false(self, not_configured):
        # Never raises — always False. Route MUST turn this into 401.
        assert ga.verify_webhook_signature(b'body', "sha256=" + ("a" * 64)) is False


# ═════════════════════════════════════════════════════════════════════
# install_url()
# ═════════════════════════════════════════════════════════════════════

class TestInstallURL:
    def test_bare(self, configured_app):
        assert ga.install_url() == (
            "https://github.com/apps/aurem-test/installations/new"
        )

    def test_with_state(self, configured_app):
        assert ga.install_url("abc:123") == (
            "https://github.com/apps/aurem-test/installations/new?state=abc%3A123"
        )

    def test_state_with_special_chars_is_percent_encoded(self, configured_app):
        url = ga.install_url("has spaces & symbols")
        # spaces → %20, & → %26
        assert "has%20spaces%20%26%20symbols" in url

    def test_not_configured_raises(self, not_configured):
        with pytest.raises(ga.GitHubAppNotConfigured):
            ga.install_url()


# ═════════════════════════════════════════════════════════════════════
# _next_link() pagination
# ═════════════════════════════════════════════════════════════════════

class TestNextLink:
    def test_finds_next(self):
        header = (
            '<https://api.github.com/x?page=2>; rel="next", '
            '<https://api.github.com/x?page=9>; rel="last"'
        )
        assert ga._next_link(header) == "https://api.github.com/x?page=2"

    def test_no_next(self):
        header = '<https://api.github.com/x?page=1>; rel="prev"'
        assert ga._next_link(header) is None

    def test_empty(self):
        assert ga._next_link("") is None
        assert ga._next_link(None) is None
