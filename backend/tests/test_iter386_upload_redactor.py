"""Iter 386 · Session 2.7 · Fix F — upload-redactor coverage.

Prod incident 2026-02-08: founder uploaded a screenshot of their
Emergent dashboard that happened to have `test_credentials.md`
open in a side panel. Gemini/GPT-4o vision OCR extracted the file
CONTENT verbatim — email + password label + admin console notes —
into ORA's context window. This test file locks the redactor
contract with real-world examples so a future regression trips
immediately.

Every test uses SYNTHETIC credentials shaped like the real thing
but obviously fake — no actual secrets in this file.
"""
from __future__ import annotations
import sys
sys.path.insert(0, "/app/backend")

import pytest
from services.ora_chat.upload_redactor import (   # noqa: E402
    redact, redaction_kinds,
)


# ══════════════════════════════════════════════════════════════════════
# 1) The 2026-02-08 prod incident replay
# ══════════════════════════════════════════════════════════════════════
_PROD_INCIDENT_EXTRACTION = """\
☑ Viewed /app/memory/test_credentials.md
# Test Credentials — AUREM CTO

## Production (auremcto.com) — Founder Account
**URL**: https://auremcto.com
**Email**: teji.ss1986@gmail.com
**Password**: `SyntheticPassword123!ForTesting`
**Role**: Founder / Admin (bypass tier)
**Connected Repo**: tjsandhu/aurem (already linked in production)

** 🔒 SECURITY POLICY (iter 309 · Phase 0.2)**: The founder's prod
password MUST never appear in agent chat, curl examples, logs.
"""


class TestProdIncidentReplay:
    def test_password_label_gets_redacted(self):
        redacted, hits = redact(_PROD_INCIDENT_EXTRACTION)
        assert "SyntheticPassword123!ForTesting" not in redacted
        assert "password_line" in hits
        # Label preserved (structural hint) but value replaced.
        assert "[REDACTED:password_line]" in redacted

    def test_test_credentials_filename_flagged(self):
        redacted, hits = redact(
            "See test_credentials.md for the founder account.")
        assert "test_credentials_filename" in hits
        assert "test_credentials.md" not in redacted

    def test_email_is_not_redacted_when_alone(self):
        # Emails are often legitimately in ORA's context (customer
        # support tasks). Don't over-redact.
        redacted, hits = redact("Contact: teji.ss1986@gmail.com for support")
        assert "teji.ss1986@gmail.com" in redacted
        assert hits == {}


# ══════════════════════════════════════════════════════════════════════
# 2) API-key shapes (synthetic — none are real keys)
# ══════════════════════════════════════════════════════════════════════
class TestApiKeyShapes:
    def test_openai_key(self):
        s = "OPENAI_API_KEY=sk-abcdef1234567890ABCDEF1234567890abcdef12"
        r, h = redact(s)
        assert "sk-abcdef" not in r
        assert h.get("openai_key") == 1

    def test_anthropic_key(self):
        s = "key=sk-ant-api03-1234567890ABCDEFghijklmn"
        r, h = redact(s)
        assert "sk-ant-api03" not in r
        assert h.get("anthropic_key") == 1

    def test_openai_project_key(self):
        s = "sk-proj-abcdef1234567890ABCDEF"
        r, h = redact(s)
        assert "sk-proj" not in r
        assert h.get("openai_project_key") == 1

    def test_stripe_live_key(self):
        s = "STRIPE=sk_live_51ABCdefGHIjkl1234567890"
        r, h = redact(s)
        assert "sk_live_51" not in r
        assert h.get("stripe_live_key") == 1

    def test_stripe_test_key(self):
        r, h = redact("sk_test_51ABCdefGHIjkl1234567890")
        assert h.get("stripe_test_key") == 1

    def test_github_pat(self):
        s = "GH_TOKEN=ghp_AbCdEf1234567890AbCdEf1234567890ABCD"
        r, h = redact(s)
        assert "ghp_" not in r
        assert h.get("github_pat") == 1

    def test_github_fine_grained_pat(self):
        s = "github_pat_11ABCdefGHIjkl1234567890abcdef1234567890"
        r, h = redact(s)
        assert h.get("github_fine_grained_pat") == 1

    def test_aws_access_key_id(self):
        r, h = redact("AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE")
        assert h.get("aws_access_key_id") == 1

    def test_jwt(self):
        # Standard header.payload.sig JWT shape (synthetic).
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.abc123def456"
        r, h = redact(f"Authorization: Bearer {jwt}")
        assert jwt not in r
        # Either the jwt matcher OR the bearer matcher fires — both
        # remove the sensitive material.
        assert "jwt" in h or "bearer_token" in h

    def test_bearer_token(self):
        s = "Authorization: Bearer sk_live_51ABCdefGHIjkl1234567890"
        r, h = redact(s)
        assert "sk_live_51" not in r


# ══════════════════════════════════════════════════════════════════════
# 3) Labelled secret lines (case + label variants)
# ══════════════════════════════════════════════════════════════════════
class TestLabelledSecrets:
    @pytest.mark.parametrize("label", [
        "Password", "password", "PASSWORD", "passwd", "Pwd",
        "**Password**",   # markdown bold
        "  password  ",   # extra whitespace
    ])
    def test_password_labels(self, label):
        r, h = redact(f"{label}: SuperSecret!123")
        assert "SuperSecret!123" not in r
        assert h.get("password_line") == 1

    def test_api_key_label(self):
        r, h = redact("api_key: XyZ-1234567890-abcdef")
        assert "XyZ-1234567890-abcdef" not in r
        assert h.get("labelled_secret") == 1

    def test_client_secret_label(self):
        r, h = redact("client_secret = MyBigSecretString")
        assert "MyBigSecretString" not in r
        assert h.get("labelled_secret") == 1

    def test_private_key_label(self):
        r, h = redact("private_key: shhh_this_is_secret_data")
        assert "shhh_this_is_secret" not in r
        assert h.get("labelled_secret") == 1


# ══════════════════════════════════════════════════════════════════════
# 4) PEM / OpenSSH private key blocks
# ══════════════════════════════════════════════════════════════════════
class TestPrivateKeyBlocks:
    def test_rsa_private_key(self):
        pem = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEowIBAAKCAQEArandombytes...\n"
            "-----END RSA PRIVATE KEY-----"
        )
        r, h = redact(pem)
        assert "MIIEowIBAAKCAQEArandombytes" not in r
        assert h.get("private_key") == 1

    def test_openssh_private_key(self):
        pem = (
            "-----BEGIN OPENSSH PRIVATE KEY-----\n"
            "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQ==\n"
            "-----END OPENSSH PRIVATE KEY-----"
        )
        r, h = redact(pem)
        assert "b3BlbnNzaC" not in r
        assert h.get("private_key") == 1


# ══════════════════════════════════════════════════════════════════════
# 5) Connection strings with inline credentials
# ══════════════════════════════════════════════════════════════════════
class TestConnectionStrings:
    def test_mongodb(self):
        r, h = redact(
            "MONGO=mongodb+srv://admin:supersecret@cluster.mongodb.net/db")
        assert "supersecret" not in r
        assert h.get("conn_string_creds") == 1

    def test_postgres(self):
        r, h = redact(
            "DB=postgresql://user:pwd@localhost:5432/mydb")
        assert "pwd@localhost" not in r

    def test_redis(self):
        r, h = redact("REDIS_URL=redis://default:abc123@upstash.io:6379")
        assert "abc123" not in r


# ══════════════════════════════════════════════════════════════════════
# 6) Non-secret text passes through unchanged
# ══════════════════════════════════════════════════════════════════════
class TestBenignInput:
    def test_empty_string(self):
        r, h = redact("")
        assert r == ""
        assert h == {}

    def test_none(self):
        r, h = redact(None)  # type: ignore[arg-type]
        # Never raise on None.
        assert r in ("", None)

    def test_prose_untouched(self):
        prose = "The user wants a logo. AUREM is a fintech platform."
        r, h = redact(prose)
        assert r == prose
        assert h == {}

    def test_code_snippet_untouched(self):
        # A short code snippet with a variable named "password" but
        # no VALUE — must not redact just the label.
        code = "def check_password(user):\n    return user.password"
        r, h = redact(code)
        assert r == code

    def test_short_string_below_key_length_threshold(self):
        """`sk-short` is 8 chars — below the {32,} threshold for
        `sk-` keys. Must NOT redact — real OpenAI keys are 51 chars."""
        r, h = redact("This mentions sk-short as a variable name.")
        assert "sk-short" in r


# ══════════════════════════════════════════════════════════════════════
# 7) Structural output — label preserved, value blanked
# ══════════════════════════════════════════════════════════════════════
class TestStructuralPreservation:
    def test_label_kept_value_gone(self):
        """The redaction MUST preserve enough structure for ORA to
        answer "the file had a password on line 5" without leaking
        the actual value."""
        r, _h = redact("**Password**: TotallyRealSecret2026!")
        # Label chunk still present.
        assert "Password" in r
        # Value gone.
        assert "TotallyRealSecret2026" not in r
        # Marker present so LLM can reason about what was scrubbed.
        assert "[REDACTED:password_line]" in r

    def test_multiline_document_partial_redaction(self):
        doc = (
            "# User Onboarding Guide\n"
            "1. Set your email in the account.\n"
            "2. Set your password:  MyPassword2026!\n"
            "3. Copy your API key: sk-abcdef1234567890ABCDEF1234567890abcdef12\n"
            "4. That's it.\n"
        )
        r, h = redact(doc)
        assert "MyPassword2026" not in r
        assert "sk-abcdef1234567890" not in r
        # Non-secret prose survived.
        assert "User Onboarding Guide" in r
        assert "That's it." in r


# ══════════════════════════════════════════════════════════════════════
# 8) Introspection surface
# ══════════════════════════════════════════════════════════════════════
class TestRedactionKinds:
    def test_all_kinds_are_stable_strings(self):
        kinds = redaction_kinds()
        # If a kind label changes, downstream Sentry filters break —
        # lock the surface.
        expected = {
            "openai_key", "anthropic_key", "openai_project_key",
            "aws_access_key_id", "aws_secret",
            "github_pat", "github_fine_grained_pat",
            "stripe_live_key", "stripe_test_key", "stripe_restricted_key",
            "jwt", "bearer_token", "private_key",
            "password_line", "labelled_secret",
            "conn_string_creds",
            "test_credentials_filename",
        }
        assert set(kinds) == expected


# ══════════════════════════════════════════════════════════════════════
# 9) The exact PROD-INCIDENT string round-trips safely
# ══════════════════════════════════════════════════════════════════════
class TestProdIncidentFullReplay:
    def test_full_extraction_contains_no_credential_material(self):
        """The whole `test_credentials.md`-style extraction must, after
        redaction, contain NONE of the credential material. Names /
        URLs are OK; emails are OK; passwords + filename references
        are stripped."""
        r, h = redact(_PROD_INCIDENT_EXTRACTION)
        # Real secret gone.
        assert "SyntheticPassword123!ForTesting" not in r
        # Filename canary flagged.
        assert "test_credentials.md" not in r.lower()
        # Password line got the structural marker.
        assert "[REDACTED:password_line]" in r
        # Both kinds recorded so Sentry alert fires.
        assert "password_line" in h
        assert "test_credentials_filename" in h
