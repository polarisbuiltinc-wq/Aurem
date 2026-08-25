"""
services/generation_rules.py  —  Directive Session 1 · Part A
=============================================================

Machine-readable **generation-time rules manifest** derived from the
platform's own post-hoc scanners. Injected into the system prompt of
any code-writing LLM call so the model sees the rules BEFORE writing
code, not only after Vanguard / Bug Hunt / Health / Docker CIS / HTTP
headers catch a violation.

Design decisions locked into this file:

  • Rule identities and severities are **extracted at import time**
    from the actual scanner modules. If someone adds a new rule to
    `bug_hunt_rules.py` or `vanguard_scanner.py`, the manifest picks
    it up automatically — there is no duplicate hand-maintained list
    to drift.

  • The condensed form is **rule_id + one-line trigger condition**,
    not the full regex or fix hint. This is a deliberate cap on
    prompt size (target: ≤ 3 KB total addition to the persona) so we
    don't linearly balloon token cost per code-write.

  • HTTP headers + Docker CIS rules are hand-curated here because
    those scanners emit findings via inline branches (no clean
    top-level rule table). The set is small and stable.

Public API:
  build_condensed_manifest(*, include_low: bool = False) -> str
      Returns the prompt-ready manifest block, roughly 2–3 KB. Low
      severity rules are excluded by default because the model doesn't
      benefit much from noise-tier rules at generation time.

  get_rule_index() -> dict
      Returns the full structured inventory for dashboards / debug
      views: {"vanguard_secrets": [...], "vanguard_dangerous": [...],
      "bug_hunt_secrets": [...], "bug_hunt_vulns": [...],
      "bug_hunt_endpoints": [...], "bug_hunt_cves": [...],
      "docker_cis": [...], "http_headers": [...]}

  MANIFEST_VERSION: str
      Bumped whenever the underlying rule set changes materially.
"""
from __future__ import annotations

from typing import Iterable

from services import bug_hunt_rules as _bh
from services import vanguard_scanner as _van
from services.generation_rules_triggers import TRIGGER_ONELINE as _TRIGGER_ONELINE

MANIFEST_VERSION = "1.0.0"

# Severity ranks used when filtering + sorting the manifest.
_SEV_RANK: dict[str, int] = {
    "CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4,
}


# One-line trigger descriptions now live in
# services/generation_rules_triggers.py (imported above as
# `_TRIGGER_ONELINE`) to keep this module under the file-size guard.


# ──────────────────────────────────────────────────────────────────────
# Bug Hunt dependency CVEs — represented as one-line "avoid version X of
# package Y" hints. Kept separate from _TRIGGER_ONELINE because the
# manifest identity here is (package, version-cap), not a fixed rule
# id, so we render them from the source of truth directly.
# ──────────────────────────────────────────────────────────────────────
def _dep_cve_lines() -> list[tuple[str, str]]:
    """Yields (rule_id, one_line) for every known dependency CVE."""
    lines: list[tuple[str, str]] = []
    for pkg, max_bad, cve, sev, _msg in _bh._DEP_CVES:
        rid = f"cve_{pkg}"
        trigger = f"any manifest declaring `{pkg}` below `{max_bad}` — {cve} — {sev}"
        lines.append((rid, trigger))
    return lines


# ──────────────────────────────────────────────────────────────────────
# Structured index — used by the dashboard "Docs" tab (future) and by
# the /api/aurem-dev/generation-rules endpoint (future).
# ──────────────────────────────────────────────────────────────────────
def get_rule_index() -> dict:
    idx: dict[str, list[dict]] = {
        "vanguard_secrets":   [],
        "vanguard_dangerous": [],
        "bug_hunt_secrets":   [],
        "bug_hunt_vulns":     [],
        "bug_hunt_endpoints": [],
        "bug_hunt_cves":      [],
        "docker_cis":         [],
        "http_headers":       [],
    }

    for rid, _rx, sev in _van._SECRET_PATTERN_DEFS:
        idx["vanguard_secrets"].append({
            "id": rid, "severity": sev,
            "trigger": _TRIGGER_ONELINE.get(rid, ""),
        })
    for rid, _rx, sev in _van._DANGEROUS_PATTERN_DEFS:
        idx["vanguard_dangerous"].append({
            "id": rid, "severity": sev,
            "trigger": _TRIGGER_ONELINE.get(rid, ""),
        })
    for rid, _rx, sev, _msg in _bh._SECRET_RULES:
        idx["bug_hunt_secrets"].append({
            "id": rid, "severity": sev,
            "trigger": _TRIGGER_ONELINE.get(rid, ""),
        })
    for rid, _rx, sev, _msg in _bh._VULN_RULES:
        idx["bug_hunt_vulns"].append({
            "id": rid, "severity": sev,
            "trigger": _TRIGGER_ONELINE.get(rid, ""),
        })
    for rid, _rx, sev, _msg in _bh._ENDPOINT_RULES:
        idx["bug_hunt_endpoints"].append({
            "id": rid, "severity": sev,
            "trigger": _TRIGGER_ONELINE.get(rid, ""),
        })
    for pkg, max_bad, cve, sev, msg in _bh._DEP_CVES:
        idx["bug_hunt_cves"].append({
            "id": f"cve_{pkg}", "severity": sev,
            "trigger": f"{pkg} < {max_bad} ({cve})",
            "message": msg,
        })

    # HTTP headers — single repo-level rule.
    idx["http_headers"].append({
        "id": "http_headers_missing", "severity": "MEDIUM",
        "trigger": _TRIGGER_ONELINE["http_headers_missing"],
    })

    # Docker CIS — enumerated from the trigger table (source of truth
    # lives in full_scan_scanners.py logic, ids are stable strings).
    _docker_ids: list[tuple[str, str]] = [
        ("docker_cis_4_1_no_user",         "HIGH"),
        ("docker_cis_4_6_no_healthcheck",  "LOW"),
        ("docker_cis_4_7_latest_tag",      "MEDIUM"),
        ("docker_cis_4_9_add_instead_copy","LOW"),
        ("docker_cis_4_10_secret_in_env",  "CRITICAL"),
        ("docker_cis_curl_pipe_sh",        "HIGH"),
        ("docker_cis_apt_upgrade",         "LOW"),
        ("docker_cis_5_4_privileged",      "HIGH"),
        ("docker_cis_5_31_docker_sock",    "CRITICAL"),
    ]
    for rid, sev in _docker_ids:
        idx["docker_cis"].append({
            "id": rid, "severity": sev,
            "trigger": _TRIGGER_ONELINE.get(rid, ""),
        })

    return idx


def _flatten(index: dict, *, include_low: bool) -> list[dict]:
    all_rules: list[dict] = []
    for bucket_rules in index.values():
        all_rules.extend(bucket_rules)
    if not include_low:
        all_rules = [
            r for r in all_rules
            if _SEV_RANK.get((r.get("severity") or "").upper(), 9) <= _SEV_RANK["MEDIUM"]
        ]
    # Sort by severity rank then id for deterministic output — matters
    # because prompt caches key on exact string content.
    all_rules.sort(key=lambda r: (
        _SEV_RANK.get((r.get("severity") or "").upper(), 9),
        r.get("id") or "",
    ))
    return all_rules


def build_condensed_manifest(*, include_low: bool = False) -> str:
    """Return the prompt-ready condensed manifest.

    Format is deliberately dense (bullet lines, ≤ 100 chars each) so
    it costs approximately 700–900 tokens for the full CRITICAL/HIGH/
    MEDIUM set. The block is fenced with sentinel comment lines so
    downstream idempotency checks (`if manifest already in persona`)
    can detect duplicates cheaply.
    """
    index = get_rule_index()
    flat  = _flatten(index, include_low=include_low)
    if not flat:
        return ""

    header = (
        "# ── AUREM — Generation-Time Safety Rules "
        "(v" + MANIFEST_VERSION + ") ──"
    )
    guidance = (
        "You are ORA. Before you write ANY code, remember these house rules — "
        "they are exactly what the platform's own Vanguard, Bug Hunt, Health, "
        "HTTP-headers, and Docker-CIS scanners will flag against you afterwards. "
        "Preventing a violation now is always cheaper than fixing it later."
    )
    lines: list[str] = [header, guidance, ""]

    for rule in flat:
        rid    = rule.get("id") or "unknown"
        sev    = (rule.get("severity") or "MEDIUM").upper()
        trigger= (rule.get("trigger") or "").strip()
        if not trigger:
            continue
        # Truncate to keep every line predictable-length.
        if len(trigger) > 140:
            trigger = trigger[:137] + "…"
        lines.append(f"[{sev[0]}] {rid}: {trigger}")

    # Append CVE list separately — one line per package, no severity
    # prefix to save tokens.
    lines.append("")
    lines.append("# Vulnerable dependency versions (upgrade to ≥ noted floor):")
    for _rid, one in _dep_cve_lines():
        lines.append(f"- {one}")

    lines.append(
        "# End rules. If your generated code hits any of these, rewrite before returning."
    )
    return "\n".join(lines) + "\n"


# ──────────────────────────────────────────────────────────────────────
# Integration helpers — used by orchestrator.py and loop.py to add the
# manifest to a persona layer stack idempotently.
# ──────────────────────────────────────────────────────────────────────
_MANIFEST_SENTINEL = "AUREM — Generation-Time Safety Rules"


def already_injected(persona_or_prompt: str | Iterable[str]) -> bool:
    """Cheap containment check so we never inject the manifest twice
    on nested / multi-layer prompt assembly paths."""
    if isinstance(persona_or_prompt, str):
        return _MANIFEST_SENTINEL in persona_or_prompt
    return any(_MANIFEST_SENTINEL in (s or "") for s in persona_or_prompt)


def inject_into_layers(layers: list[str], *, include_low: bool = False) -> list[str]:
    """Return a new layer list with the manifest appended (once).

    Placement rule: the manifest goes AFTER the persona layers but
    BEFORE any user-specific context so the model treats it as
    always-on housekeeping, not as an ad-hoc instruction from the
    current prompt.
    """
    if already_injected(layers):
        return layers
    manifest = build_condensed_manifest(include_low=include_low)
    if not manifest:
        return layers
    return list(layers) + [manifest]
