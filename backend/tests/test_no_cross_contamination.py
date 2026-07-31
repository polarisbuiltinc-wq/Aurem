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
MARKERS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bhunt_live\b"),          "hunt_live (Aurem sales module)"),
    (re.compile(r"\bflame_auto_dialer\b"),  "flame_auto_dialer (Aurem sales)"),
    (re.compile(r"\bdrip_sequencer\b"),     "drip_sequencer (Aurem sales)"),
    (re.compile(r"\ba2a_bus\b"),            "a2a_bus (Aurem sales)"),
    (re.compile(r"Scout\s*[→>-]+\s*Verify\s*[→>-]+\s*Website\s*[→>-]+\s*Blast"),
                                             "OODA pipeline (Aurem sales docstring)"),
    (re.compile(r'\bTERRITORY_DISTRIBUTION\b.*(?:Ontario|Quebec|Alberta)',
                re.DOTALL),                  "TERRITORY_DISTRIBUTION (Aurem sales)"),
]

# ── Paths that are allowed to mention markers (docs, this test, PRD) ──
# NEVER add code paths here — only docs / this guard itself.
ALLOWLIST_SUFFIXES = (
    "tests/test_no_cross_contamination.py",  # self-reference
    "memory/PRD.md",                          # historical audit note
    "memory/CHANGELOG.md",                    # once we start one
    "memory/PROD_DEPLOY_2026-07-31.md",       # timeline docs
)


def _is_allowlisted(relpath: str) -> bool:
    return any(relpath.endswith(suf) for suf in ALLOWLIST_SUFFIXES)


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
