"""
test_no_cross_contamination.py — 2026-08-01

Guards against a repeat of the 2026-05-29 incident where an initial
bulk-import commit accidentally seeded ~1500 LOC from a DIFFERENT
product ("Aurem" sales/outreach) into this AuremCTO codebase:
`backend/shared/agents/*` (Hunter/Follow-up/Closer/Referral personas
for a B2B lead-gen tool).

Discovered on 2026-08-01 during the Batch-1 discovery audit. Deleted
the same day. This test locks the fix in place: if the same product's
code (or any other clearly-not-AuremCTO code) ever lands in the git-
tracked tree again — whether via manual paste, a tool that writes
into a git-tracked path, or a cache-leak sweep — CI fails loudly
BEFORE a deploy or an audit has to catch it.

Signals we look for (bare-minimum high-confidence set):

  • Sales-outreach persona markers: `hunt_live`, `flame_auto_dialer`,
    `drip_sequencer`, `a2a_bus` — these are module NAMES from the
    other product; none of them exist here and none should.
  • OODA pipeline pattern: "Scout → Verify → Website → Blast" (exact
    order — a very tight fingerprint of the other product's docstrings).
  • Territory-based sales fields: `TERRITORY_DISTRIBUTION` with the
    combined "Ontario/BC/Alberta/Quebec" + "Eastern/Central/Mountain"
    dict shape (fingerprint of hunter_ora.py).

We do NOT check for generic words like "agent", "closer", "referral"
alone — those are common English words that would false-positive on
legit AuremCTO code (e.g. `services/agents.py` uses "CoordinatorAgent"
and it IS legitimate). The signals above are high-signal,
low-false-positive.

If a legitimate future feature needs one of these markers, add it to
the ALLOWLIST below with a comment explaining why.
"""
from __future__ import annotations
import os
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent   # /app
BACKEND   = REPO_ROOT / "backend"

# ── Contamination markers (see file docstring) ───────────────────────
# Each entry: (regex, human-readable label)
#
# LOW-FALSE-POSITIVE RULE: only add markers here that would clearly
# never appear in a legitimate AuremCTO source path. Generic English
# words are OUT. Specific product-fingerprints (unique docstring
# phrases, import module names, tightly-shaped constants) are IN.
MARKERS: list[tuple[re.Pattern, str]] = [
    # ── Original set (2026-08-01, from `shared/agents/*` deletion) ──
    (re.compile(r"\bhunt_live\b"),          "hunt_live (Aurem sales module)"),
    (re.compile(r"\bflame_auto_dialer\b"),  "flame_auto_dialer (Aurem sales)"),
    (re.compile(r"\bdrip_sequencer\b"),     "drip_sequencer (Aurem sales)"),
    (re.compile(r"\ba2a_bus\b"),            "a2a_bus (Aurem sales)"),
    (re.compile(r"Scout\s*[→>-]+\s*Verify\s*[→>-]+\s*Website\s*[→>-]+\s*Blast"),
                                             "OODA pipeline: Scout→Verify→Website→Blast (Aurem sales docstring)"),
    (re.compile(r'\bTERRITORY_DISTRIBUTION\b.*(?:Ontario|Quebec|Alberta)',
                re.DOTALL),                  "TERRITORY_DISTRIBUTION (Aurem sales)"),

    # ── Safety-extension (2026-08-01, from `shared/*` broader audit) ──
    # Additional Aurem sales-outreach fingerprints found while scanning
    # the wider `backend/shared/` tree. Files were NOT deleted yet
    # (dedicated audit session planned) — but any NEW file matching
    # these must fail CI so contamination can't grow.
    (re.compile(r"Scout\s*[→>-]+\s*Architect\s*[→>-]+\s*Envoy\s*[→>-]+\s*Closer"),
                                             "OODA pipeline: Scout→Architect→Envoy→Closer (Aurem sales docstring)"),
    # Multi-line variant — same 4 personas listed separately in a docstring
    # (as in `shared/memory_tiers.py`). Any 4-persona co-occurrence within
    # a 400-char window flags the OODA pattern regardless of formatting.
    (re.compile(r"Scout\b.{0,400}Architect\b.{0,400}Envoy\b.{0,400}Closer",
                re.DOTALL),                  "OODA persona quartet: Scout+Architect+Envoy+Closer (Aurem sales)"),
    (re.compile(r"AUREM\s+Agent\s+RBAC"),   "'AUREM Agent RBAC' header (Aurem sales role-model)"),
    # Match the exact SCOUT-role/CLOSER-role pairing that Aurem uses to
    # define per-agent permissions. Legit RBAC in AuremCTO uses roles
    # like "admin"/"user"/"founder" — never "SCOUT"/"CLOSER".
    (re.compile(r"\bSCOUT\s*=\s*read-only\b"), "SCOUT=read-only role (Aurem sales RBAC)"),
    (re.compile(r"\bCLOSER\s*=\s*write\b"),   "CLOSER=write role (Aurem sales RBAC)"),
    # `WHAPI service` is the WhatsApp-API-replacement phrase in the
    # Aurem Twilio shim's docstring. AuremCTO does NOT do WhatsApp,
    # so this string uniquely fingerprints the sales-comms shim.
    (re.compile(r"\bWHAPI\s+service\b"),    "WHAPI service (Aurem sales-comms Twilio shim)"),
    # `B2B email finder` + `Phone validation` co-occurrence uniquely
    # fingerprints the Aurem lead-enrichment provider bundle. A single
    # match of either alone is too weak — the docstring pairs them, so
    # we match the paired phrase.
    (re.compile(r"B2B\s+email\s+finder"),   "B2B email finder (Aurem lead-enrichment)"),
    (re.compile(r"Phone\s+validation.*lead\s+enrichment", re.IGNORECASE),
                                             "Phone-validation+lead-enrichment (Aurem sales)"),
]

# ── Paths that are allowed to mention markers (docs, this test, PRD) ──
# NEVER add code paths here — only docs / this guard itself.
ALLOWLIST_SUFFIXES = (
    "tests/test_no_cross_contamination.py",  # self-reference
    "memory/PRD.md",                          # historical audit note
    "memory/CHANGELOG.md",                    # once we start one
    "memory/PROD_DEPLOY_2026-07-31.md",       # timeline docs
)

# ── Grandfathered known-contamination (2026-08-01) ────────────────────
#
# These files ARE cross-contamination from the Aurem sales-outreach
# product (same 2026-05-29 bulk-import commit as `shared/agents/*`).
# They are ALREADY confirmed to have zero live callers in AuremCTO.
#
# They are NOT deleted YET because the Batch-1 audit only sanctioned
# `shared/agents/*` deletion. A dedicated `backend/shared/*` audit
# session is queued (same discipline: verify zero-live-callers per
# file, confirm delete). Until that session runs, these paths are
# grandfathered — the guard SKIPS them so pytest stays green, but
# any NEW file matching the same markers OR any change to these
# files will still surface as a diff review.
#
# WHEN YOU DELETE ONE OF THESE FILES: remove its entry here. The
# guard will then enforce marker-freedom for that path going forward.
GRANDFATHERED_CONTAMINATION: set[str] = {
    "backend/shared/providers/free_apis.py",     # B2B lead enrichment
    "backend/shared/providers/twilio.py",        # WHAPI/WhatsApp sales
    "backend/shared/auth/rbac.py",               # SCOUT/CLOSER RBAC
    "backend/shared/memory_tiers.py",            # OODA (Scout→Architect→Envoy→Closer)
    # `shared/providers/email_legacy.py` (SendGrid→Resend shim) is
    # NOT in this list — no marker currently flags it. If a future
    # marker catches it, add it here (or delete the file in the
    # scheduled `shared/*` audit).
    # Add others here as broader audit confirms them.
}


def _is_allowlisted(relpath: str) -> bool:
    if any(relpath.endswith(suf) for suf in ALLOWLIST_SUFFIXES):
        return True
    if relpath in GRANDFATHERED_CONTAMINATION:
        return True
    return False


def _iter_source_files():
    """Walk the git-tracked source tree. Skips node_modules, .venv,
    __pycache__, .git, and the tool-cache dir (`.aurem_cache/`)."""
    SKIP_DIRS = {
        "node_modules", ".venv", ".git", "__pycache__", ".aurem_cache",
        "dist", "build", ".next", ".cache", ".pytest_cache",
        "_extract", "shared_extract",   # tool-created tmp dirs
    }
    for root, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            # Only scan text-ish source files.
            if not name.endswith((".py", ".jsx", ".tsx", ".js", ".ts",
                                   ".md", ".yaml", ".yml", ".json")):
                continue
            yield Path(root) / name


def test_no_cross_product_contamination():
    """Scan every tracked source file for the sales-outreach product's
    fingerprints. Fail with a clear message if any hit."""
    hits: list[tuple[str, str, int]] = []
    for path in _iter_source_files():
        rel = str(path.relative_to(REPO_ROOT))
        if _is_allowlisted(rel):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for pat, label in MARKERS:
            m = pat.search(text)
            if m:
                # Line number for the hit — makes fixing trivial.
                line = text[:m.start()].count("\n") + 1
                hits.append((rel, label, line))

    if hits:
        detail = "\n".join(f"  {r}:{ln} — {lbl}" for r, lbl, ln in hits)
        raise AssertionError(
            "Cross-product contamination detected — AuremCTO source tree "
            "must NOT contain code/docs from the 'Aurem' sales-outreach "
            f"product.\nOffenders:\n{detail}\n\n"
            "If a legitimate AuremCTO feature genuinely needs one of "
            "these markers, add its path to ALLOWLIST_SUFFIXES in this "
            "test file with a comment explaining why."
        )


def test_shared_agents_dir_is_gone():
    """Guard against `backend/shared/agents/` reappearing. If someone
    ever recreates this dir, this test fails immediately."""
    p = BACKEND / "shared" / "agents"
    assert not p.exists(), (
        f"{p} was deleted on 2026-08-01 as cross-product contamination "
        "(Aurem sales-outreach agents). If you're re-adding a directory "
        "with this exact name, use a different name — this path is "
        "poisoned by prior contamination."
    )


def test_aurem_cache_is_gitignored():
    """Ensure `.aurem_cache/` stays gitignored — it's a runtime tool
    cache and must never enter the tracked tree."""
    gitignore = REPO_ROOT / ".gitignore"
    assert gitignore.exists(), ".gitignore missing"
    content = gitignore.read_text(encoding="utf-8")
    assert ".aurem_cache" in content, (
        "'.aurem_cache/' must be in .gitignore — this is the tool-cache "
        "directory where snapshots of external repos land. Any code path "
        "that writes there must never leak into the tracked tree."
    )


def test_grandfathered_paths_still_exist_and_still_contaminated():
    """Two-sided guard on the grandfathered list:

    1. If a grandfathered path is DELETED (dedicated Batch audit
       actually cleaned it up), the entry should be REMOVED from
       GRANDFATHERED_CONTAMINATION — else the guard silently keeps
       allowlisting a path that no longer exists.
    2. If a grandfathered path has been CLEANED (no longer contains
       any contamination marker), same removal is needed.

    Either drift means someone edited the list incorrectly. This test
    catches it in one line.
    """
    stale: list[str] = []
    cleaned: list[str] = []
    for relpath in GRANDFATHERED_CONTAMINATION:
        p = REPO_ROOT / relpath
        if not p.exists():
            stale.append(relpath)
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        hit = any(pat.search(text) for pat, _lbl in MARKERS)
        if not hit:
            cleaned.append(relpath)

    problems: list[str] = []
    if stale:
        problems.append(
            "Deleted grandfathered paths still in the allowlist "
            "(remove them from GRANDFATHERED_CONTAMINATION): "
            + ", ".join(stale)
        )
    if cleaned:
        problems.append(
            "Grandfathered paths that no longer contain contamination "
            "markers — remove them from GRANDFATHERED_CONTAMINATION so "
            "the guard enforces marker-freedom for them going forward: "
            + ", ".join(cleaned)
        )
    assert not problems, "\n".join(problems)
