"""
services/capabilities.py — Part B · W3 · 2026-08 (R1 + R4-minimal)

Single source of truth for what AUREM can ACTUALLY do to a repo's
files today. Read by the real verify path (services/loop_verify.py)
so its "verified" claims match reality — this is R4-minimal: per the
Step-1 audit (A5), the edit/verify/execute path is already LOOSELY
coupled to Python (verify is a plain extension→tool dict, execute has
no language branching at all), so the seam shrinks to just this
capability declaration + the degrade/verify hooks already built in
loop_verify.py and github_api_writer.py — no formal adapter interface
or registry class is needed.

Adding language #2 to VERIFY = one new dict entry here. Nothing else
in the edit/verify/execute pipeline needs to change (see
tests/test_iter_w3_r4_capabilities_seam.py::test_t3_seam_proof_...).
"""
from __future__ import annotations

# The canonical extension → (linter tool, cli flags) map. Moved here
# from services/loop_verify.py (Iter 212m-62) — loop_verify.py now
# imports this instead of defining its own copy (single source of
# truth; the "reuse before build" rule).
VERIFIED_LANGUAGES: dict[str, tuple[str, list[str]]] = {
    ".py":   ("ruff",   ["check", "--no-fix", "--output-format=concise"]),
    ".pyi":  ("ruff",   ["check", "--no-fix", "--output-format=concise"]),
    ".js":   ("eslint", ["--no-eslintrc", "--no-config-lookup",
                         "--rule", "no-undef:error",
                         "--rule", "no-unused-vars:warn",
                         "--rule", "no-unreachable:error",
                         "--no-color", "--format", "compact"]),
    ".jsx":  ("eslint", ["--no-eslintrc", "--no-config-lookup",
                         "--parser-options=ecmaVersion:latest,ecmaFeatures:{jsx:true}",
                         "--rule", "no-undef:error",
                         "--rule", "no-unreachable:error",
                         "--no-color", "--format", "compact"]),
    ".ts":   ("eslint", ["--no-eslintrc", "--no-config-lookup",
                         "--rule", "no-undef:error",
                         "--no-color", "--format", "compact"]),
    ".tsx":  ("eslint", ["--no-eslintrc", "--no-config-lookup",
                         "--parser-options=ecmaVersion:latest,ecmaFeatures:{jsx:true}",
                         "--rule", "no-undef:error",
                         "--no-color", "--format", "compact"]),
}


def get_capabilities() -> dict:
    """What AUREM can actually do in a repo right now — real, current
    adapter set, never aspirational. Consulted by loop_verify.py's
    real per-file verify decision (R5 — no orphans)."""
    return {
        # Edit is language-agnostic (A1/A5 audit finding — the LLM
        # rewrite prompt never branches on file extension). The only
        # edit-time refusal is binary/non-UTF-8 content (A4), which is
        # detected by content inspection, not by extension.
        "can_edit_text_files": True,
        "can_edit_binary_files": False,
        # Verify is the narrow part: only these extensions get a real
        # lint/type check. Anything else is still editable, but its
        # verify result is `verified: False` (skipped), never
        # silently reported as passed.
        "verified_extensions": sorted(VERIFIED_LANGUAGES.keys()),
        "verify_tools": {ext: tool
                         for ext, (tool, _flags) in VERIFIED_LANGUAGES.items()},
        "unverified_extensions_note": (
            "Files with an extension not in verified_extensions are "
            "still editable, but no real lint/type check runs on them. "
            "Their verify result is marked verified=False (skipped) — "
            "never reported as a real pass."
        ),
        "can_run_tests": True,   # existing pytest self-heal/verify pipeline
    }
