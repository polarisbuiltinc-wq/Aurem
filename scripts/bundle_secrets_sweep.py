"""bundle_secrets_sweep.py — Iter 388-ac (2026-02-14).

Task #19 — frontend bundle secrets sweep.

Scans the built production bundle (`frontend/dist/`) for secrets that
should NEVER be shipped to the browser (server-only env variables,
private keys, API tokens, DSNs, etc.).

Only regex-based detection — no false-positive-free promise, but tuned
against the actual env variable names used in this codebase
(`backend/.env` + integration playbooks).

Runs in <2s against a 20 MB dist.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "frontend" / "dist"

# ── Detectors ────────────────────────────────────────────────────────
# Each tuple: (id, description, compiled regex, severity)
#
# Format-based detectors are broad (catch any string of the right shape).
# Name-based detectors look for env variable NAMES that server-only
# secrets live under (STRIPE_SECRET_KEY, MONGO_URL, etc.). Server-only
# names appearing anywhere in the bundle is a red flag.

# Only trigger the "server env var name" family when the token is NOT
# adjacent to REACT_APP_ (which is Vite's public-env convention).
NAME_LEAKED = re.compile(
    r"(?<![A-Z0-9_])"
    r"(?:STRIPE_SECRET_KEY|STRIPE_WEBHOOK_SECRET|STRIPE_API_KEY|"
    r"MONGO_URL|MONGODB_URI|"
    r"OPENAI_API_KEY|ANTHROPIC_API_KEY|GEMINI_API_KEY|"
    r"OPENROUTER_API_KEY|LITELLM_API_KEY|TAVILY_API_KEY|"
    r"GITHUB_APP_PRIVATE_KEY|GITHUB_APP_CLIENT_SECRET|GH_PAT|"
    r"UPSTASH_REDIS_REST_TOKEN|"
    r"RESEND_API_KEY|SENDGRID_API_KEY|TWILIO_AUTH_TOKEN|"
    r"JWT_SECRET|SESSION_SECRET|"
    r"EMERGENT_LLM_KEY|ORA_INTERNAL_TOKEN)"
    r"(?![A-Z0-9_])"
)

# Format-based — matches the actual token shape, catches keys even if
# they're stored in a different env var name.
FORMAT_DETECTORS = [
    # Stripe
    ("stripe_live_secret",  "Stripe live secret",       r"\bsk_live_[A-Za-z0-9]{20,}",              "CRITICAL"),
    ("stripe_live_publish", "Stripe live publishable",  r"\bpk_live_[A-Za-z0-9]{20,}",              "WARN"),
    ("stripe_restrict",     "Stripe restricted key",    r"\brk_(?:live|test)_[A-Za-z0-9]{20,}",     "CRITICAL"),
    ("stripe_webhook_sec",  "Stripe webhook secret",    r"\bwhsec_[A-Za-z0-9]{20,}",                "CRITICAL"),

    # GitHub
    ("gh_pat_classic",      "GitHub PAT (classic)",     r"\bghp_[A-Za-z0-9]{30,}",                  "CRITICAL"),
    ("gh_pat_fine",         "GitHub PAT (fine-grained)",r"\bgithub_pat_[A-Za-z0-9_]{30,}",          "CRITICAL"),
    ("gh_oauth",            "GitHub OAuth token",       r"\bgho_[A-Za-z0-9]{30,}",                  "CRITICAL"),
    ("gh_app",              "GitHub App JWT",           r"\bghs_[A-Za-z0-9]{30,}",                  "CRITICAL"),

    # OpenAI / Anthropic / Google
    ("openai_key",          "OpenAI API key",           r"\bsk-(?:proj-)?[A-Za-z0-9]{40,}",         "CRITICAL"),
    ("anthropic_key",       "Anthropic API key",        r"\bsk-ant-api\d{2}-[A-Za-z0-9_-]{30,}",    "CRITICAL"),
    ("openrouter_key",      "OpenRouter API key",       r"\bsk-or-v1-[a-f0-9]{50,}",                "CRITICAL"),

    # PEM / private keys
    ("pem_private",         "PEM private key",          r"-----BEGIN (?:RSA|EC|OPENSSH|PRIVATE) KEY-----", "CRITICAL"),

    # AWS
    ("aws_akid",            "AWS access-key ID",        r"\bAKIA[0-9A-Z]{16}",                      "CRITICAL"),

    # MongoDB
    ("mongo_url",           "MongoDB URI with creds",   r"mongodb(?:\+srv)?://[^:\s]+:[^@\s]+@",    "CRITICAL"),

    # JWT (three base64 segments) — heuristic; only flag if long
    ("jwt_shape",           "JWT-like token",           r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}", "WARN"),

    # Sentry DSN
    ("sentry_dsn_secret",   "Sentry DSN with secret",   r"https?://[a-f0-9]{32}:[a-f0-9]{32}@",     "CRITICAL"),
]

FORMAT_DETECTORS = [
    (id_, desc, re.compile(pat), sev)
    for id_, desc, pat, sev in FORMAT_DETECTORS
]


def _iter_bundle_files() -> Iterable[Path]:
    """Yield every JS/HTML/CSS/JSON file in dist/."""
    for ext in ("*.js", "*.mjs", "*.html", "*.css", "*.map", "*.json", "*.txt"):
        yield from DIST.rglob(ext)


def _scan_file(path: Path) -> list[dict]:
    """Return a list of findings for a single bundle file."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    findings: list[dict] = []

    # Format-based
    for id_, desc, rx, sev in FORMAT_DETECTORS:
        for m in rx.finditer(text):
            sample = m.group(0)
            findings.append({
                "detector": id_,
                "description": desc,
                "severity": sev,
                "file": str(path.relative_to(DIST)),
                "line_hint": text.count("\n", 0, m.start()) + 1,
                "sample": (sample[:8] + "…") if len(sample) > 8 else sample,
            })

    # Name-based (server env variable names that shouldn't be in the bundle)
    for m in NAME_LEAKED.finditer(text):
        # Filter false-positives: comments in .map source that
        # reference the name but not the value. We flag anyway — it
        # means server-side code names are leaking via source maps.
        findings.append({
            "detector": "server_env_name_leak",
            "description": f"Server-only env var name in bundle: {m.group(0)}",
            "severity": "WARN",
            "file": str(path.relative_to(DIST)),
            "line_hint": text.count("\n", 0, m.start()) + 1,
            "sample": m.group(0),
        })

    return findings


def _classify(findings: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    crit, warn, other = [], [], []
    for f in findings:
        sev = f.get("severity", "WARN")
        if sev == "CRITICAL":
            crit.append(f)
        elif sev == "WARN":
            warn.append(f)
        else:
            other.append(f)
    return crit, warn, other


def main() -> int:
    if not DIST.exists():
        print(f"!! dist not found at {DIST}. Run `yarn build` first.", file=sys.stderr)
        return 1

    files = list(_iter_bundle_files())
    print(f"══════════ FRONTEND BUNDLE SECRETS SWEEP ══════════")
    print(f"Scanning {len(files)} files under {DIST}")

    all_findings: list[dict] = []
    for f in files:
        all_findings.extend(_scan_file(f))

    crit, warn, _ = _classify(all_findings)

    print(f"Total findings: {len(all_findings)}   (critical={len(crit)}, warn={len(warn)})")
    print()

    if crit:
        print(f"🔴 CRITICAL ({len(crit)}):")
        for f in crit[:50]:
            print(f"  [{f['detector']:<22}] {f['file']}:{f['line_hint']}  sample={f['sample']!r}")
        if len(crit) > 50:
            print(f"  … +{len(crit)-50} more")

    if warn:
        # Group name-based by which env-var name leaked
        grouped: dict[str, list[dict]] = {}
        for f in warn:
            key = f["sample"] if f["detector"] == "server_env_name_leak" else f["detector"]
            grouped.setdefault(key, []).append(f)
        print()
        print(f"🟡 WARN ({len(warn)}):")
        for name, items in sorted(grouped.items(), key=lambda kv: -len(kv[1]))[:30]:
            first = items[0]
            print(f"  [{first['detector']:<22}] {name:<32}  hits={len(items)}  first={first['file']}:{first['line_hint']}")

    print()
    if not crit and not warn:
        print("✅ CLEAN — no leaked secrets or server env var names in production bundle.")
        return 0
    if crit:
        print("🔴 CRITICAL findings — production keys are in the shipped bundle. Rotate now.")
        return 3
    print("🟡 WARN findings only — review whether these are safe (public constants, testids, etc.).")
    return 2


if __name__ == "__main__":
    sys.exit(main())
