"""
services/ora_chat/upload_redactor.py — credential redaction pipeline.

Iter 386 · Session 2.7 · Fix F — vision + document upload path was
feeding raw extracted text into the LLM context. In the 2026-02-08
prod incident the founder uploaded a screenshot of the Emergent
dashboard that happened to show `test_credentials.md` open in a
side panel; the vision LLM extracted the file's contents verbatim
(email + password markers + admin-console notes) into ORA's context
window. That is the SAME class of leak as tossing the file into
chat directly, just laundered through the vision path.

This module runs a strict redaction pass on any text produced by
`_describe_image_via_vision` or MarkItDown BEFORE that text is
returned to the frontend / persisted / echoed back to the LLM.

Threat model:
  · Screenshots that happen to show a terminal open on ~/.aws/,
    a code editor open on a secrets file, or a browser tab with
    an admin session key visible.
  · Uploaded PDFs / DOCX exports of onboarding docs that inline
    credentials.
  · Adversarial uploads deliberately crafted to smuggle a fake
    "system prompt" or credential into ORA's context.

Design:
  · Pattern-match, don't ML — deterministic, fast (<1ms per KB),
    reviewable in the diff.
  · Replace with `[REDACTED:<kind>]` so the LLM still sees
    STRUCTURE (a line was here) but not CONTENT — this preserves
    the LLM's ability to answer questions about the layout of the
    file without leaking the secret.
  · Case-insensitive where credentials are label-value pairs
    ("Password:", "password :", "Password    :" — all covered).
  · No PII redaction beyond credentials — emails without an
    adjacent password remain (ORA is often shown an email
    legitimately as part of a customer-context task).
"""
from __future__ import annotations

import re
from typing import Iterable

# ── Redaction patterns ────────────────────────────────────────────────
# Each tuple: (compiled regex, replacement kind-label, description).
# Order matters — more-specific patterns first so a generic catch-all
# doesn't swallow a labelled match before we've tagged it precisely.
_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    # OpenAI-family API keys — `sk-` (32+ chars) and `sk-ant-` variant.
    (re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"), "anthropic_key",
     "Anthropic API key"),
    (re.compile(r"sk-proj-[A-Za-z0-9_-]{20,}"), "openai_project_key",
     "OpenAI project-scoped key"),
    (re.compile(r"sk-[A-Za-z0-9]{32,}"), "openai_key",
     "OpenAI API key"),

    # AWS credentials.
    (re.compile(r"AKIA[0-9A-Z]{16}"), "aws_access_key_id",
     "AWS access key id"),
    (re.compile(r"aws_secret_access_key\s*[:=]\s*[A-Za-z0-9+/=]{40}",
                re.IGNORECASE), "aws_secret",
     "AWS secret access key"),

    # GitHub personal access tokens.
    (re.compile(r"ghp_[A-Za-z0-9]{36}"), "github_pat",
     "GitHub PAT"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{40,}"), "github_fine_grained_pat",
     "GitHub fine-grained PAT"),

    # Stripe keys.
    (re.compile(r"sk_live_[A-Za-z0-9]{20,}"), "stripe_live_key",
     "Stripe live secret"),
    (re.compile(r"sk_test_[A-Za-z0-9]{20,}"), "stripe_test_key",
     "Stripe test secret"),
    (re.compile(r"rk_(live|test)_[A-Za-z0-9]{20,}"), "stripe_restricted_key",
     "Stripe restricted key"),

    # JWT (3 dot-separated base64url segments starting with header).
    (re.compile(r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
     "jwt", "JSON Web Token"),

    # Bearer tokens carrying any long random string after "Bearer".
    (re.compile(r"[Bb]earer\s+[A-Za-z0-9._~+/=-]{16,}"),
     "bearer_token", "Bearer token"),

    # Private key blocks.
    (re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED |PGP )?PRIVATE KEY"
        r"[- ]*-----[\s\S]*?-----END (?:RSA |EC |OPENSSH |DSA |ENCRYPTED |PGP )?PRIVATE KEY"
        r"[- ]*-----"), "private_key", "PEM/OpenSSH private key"),

    # Labelled password lines — `password: xyz` / `Password : xyz` /
    # `**Password**: xyz` (markdown). Case-insensitive. Match label
    # ANYWHERE on the line (start-of-line or preceded by whitespace/
    # list-marker) — the 2026-02-08 replay had `"2. Set your
    # password: <secret>"` which a start-anchored version missed.
    # Grabs everything after the colon up to end-of-line so we don't
    # leak a long value even when the label was innocuous.
    (re.compile(
        r"(?im)(^|[\s>*_`\-.])"
        r"([*_`]*\s*(?:password|passwd|pwd)\s*[*_`]*\s*[:=])"
        r"[ \t]*(.+)$"),
     "password_line", "Label-value password line"),

    # Labelled API-key / secret / access-token lines. Same "match
    # anywhere on the line" strategy as above.
    (re.compile(
        r"(?im)(^|[\s>*_`\-.])"
        r"([*_`]*\s*"
        r"(?:api[_ -]?key|api[_ -]?secret|access[_ -]?token|secret|"
        r"client[_ -]?secret|auth[_ -]?token|private[_ -]?key)"
        r"\s*[*_`]*\s*[:=])[ \t]*(.+)$"),
     "labelled_secret", "Label-value secret line"),

    # MongoDB / Postgres / Redis connection strings that carry
    # user:password@ inline.
    (re.compile(
        r"(?im)\b(mongodb(?:\+srv)?|postgres(?:ql)?|mysql|redis|rediss|amqp|"
        r"amqps)://[^:\s]+:([^@\s]+)@[^\s]+"), "conn_string_creds",
     "Connection string with inline credentials"),

    # Founder-account leak canary — the 2026-02-08 incident. This
    # matches the exact filename because that's what the vision LLM
    # extracted; any future upload that hits this triggers a bright
    # marker in the redacted output so we can spot repeat incidents.
    (re.compile(r"test_credentials\.md", re.IGNORECASE),
     "test_credentials_filename",
     "Reference to test_credentials.md file"),
]


def _dispatch(regex: re.Pattern, kind: str, description: str,
              text: str) -> tuple[str, int]:
    """Apply one pattern. Returns (new_text, replacement_count)."""
    hits = 0

    def _sub(match: re.Match) -> str:
        nonlocal hits
        hits += 1
        # If the pattern has capture groups AND the groups look like
        # a label→value split (3 groups: prefix, label, value), keep
        # the prefix + label and blank the value.
        groups = match.groups()
        if kind in ("password_line", "labelled_secret") and \
                len(groups) >= 3 and groups[0] is not None and \
                groups[1] is not None:
            prefix = groups[0]
            label = groups[1]
            return f"{prefix}{label} [REDACTED:{kind}]"
        return f"[REDACTED:{kind}]"

    return regex.sub(_sub, text), hits


def redact(text: str) -> tuple[str, dict[str, int]]:
    """Run all redaction patterns over `text`. Returns the redacted
    text and a `{kind: count}` map showing what fired. The count map
    is small enough to log to Sentry so on-call sees when uploads
    smuggle credentials into the pipeline."""
    if not text:
        return text, {}
    hits: dict[str, int] = {}
    out = text
    for regex, kind, _desc in _PATTERNS:
        out, n = _dispatch(regex, kind, _desc, out)
        if n:
            hits[kind] = hits.get(kind, 0) + n
    return out, hits


def redaction_kinds() -> Iterable[str]:
    """Public introspection — the set of `kind` labels this module
    can emit. Pytest uses this to assert every declared pattern has
    at least one positive test."""
    return {k for _r, k, _d in _PATTERNS}
