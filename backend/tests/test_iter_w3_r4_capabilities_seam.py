"""
tests/test_iter_w3_r4_capabilities_seam.py — Part B · W3 · 2026-08

R1 (capability awareness) + R4-minimal (thin seam) + R5 (no orphans)
for the "language support, small version" master prompt. Scope
approved: R1, R2, R4-minimal, R5 — R3 (verification honesty /
"verified" as a hard gate) is explicitly DEFERRED this pass (not
built, not tested here).
"""
from __future__ import annotations

import asyncio


# ═══════════════════════════════════════════════════════════════════
# R1 — capability declaration reflects reality, not aspiration
# ═══════════════════════════════════════════════════════════════════
def test_r1_capabilities_declaration_matches_real_verify_map():
    from services.capabilities import get_capabilities, VERIFIED_LANGUAGES
    from services.loop_verify import _LINTERS

    caps = get_capabilities()
    assert caps["can_edit_text_files"] is True
    assert caps["can_edit_binary_files"] is False
    assert set(caps["verified_extensions"]) == set(VERIFIED_LANGUAGES.keys())
    assert caps["verify_tools"][".py"] == "ruff"
    assert caps["verify_tools"][".ts"] == "eslint"
    # R5 proof (import-level): loop_verify's real dispatch map IS this
    # module's VERIFIED_LANGUAGES object, not a copy that could drift.
    assert _LINTERS is VERIFIED_LANGUAGES


# ═══════════════════════════════════════════════════════════════════
# R2 — graceful degradation: unverified language is HONEST, not a
# silent pass, but still doesn't over-block the loop.
# ═══════════════════════════════════════════════════════════════════
def test_r2_unmapped_extension_is_honestly_unverified_not_silently_passed():
    from services.loop_verify import verify_files

    report = asyncio.run(verify_files([
        {"path": "README.md", "content": "# hello\n"},
    ]))
    row = report["results"][0]
    assert row["ok"] is True, (
        "an unverifiable-language file must not block the loop — "
        "R3 (a hard verified-gate) is explicitly deferred this pass"
    )
    assert row["verified"] is False, (
        "but it must be HONESTLY marked unverified, not silently "
        "reported as a real pass"
    )
    assert row["error_code"] == "FILE_LANGUAGE_UNVERIFIED"
    assert row["linter"] == "skip"


def test_r2_real_python_check_is_marked_verified_true():
    from services.loop_verify import verify_files

    report = asyncio.run(verify_files([
        {"path": "ok.py", "content": "x = 1\n"},
    ]))
    row = report["results"][0]
    assert row["linter"] == "ruff"
    assert row["verified"] is True, (
        "a file that actually got a real linter run must be marked "
        "verified=True, whether it passed or failed"
    )


# ═══════════════════════════════════════════════════════════════════
# T3 — seam proof: adding language #2 touches ONE file (the registry
# dict), core files stay untouched.
# ═══════════════════════════════════════════════════════════════════
def test_t3_seam_proof_fake_second_language_needs_no_core_change(monkeypatch):
    """Add a FAKE 'Rust' adapter directly to the registry dict (the
    R4-minimal seam) and prove verify_files() dispatches to it with
    ZERO changes to loop_verify.py's own logic or any core file —
    the seam really is "one dict entry", not a ceremony."""
    from services import capabilities as caps_mod
    from services.loop_verify import verify_files

    # A fake tool that always "passes" — proves dispatch works, not
    # that clippy itself is wired (clippy isn't installed on this pod).
    fake_map = dict(caps_mod.VERIFIED_LANGUAGES)
    fake_map[".rs"] = ("true", [])   # `true` = the real Unix no-op binary, rc=0 always
    monkeypatch.setattr(caps_mod, "VERIFIED_LANGUAGES", fake_map)
    # loop_verify imported `_LINTERS` as a name-binding at module load
    # time, so the monkeypatch must also apply there for this proof —
    # this line is the ENTIRE "seam touch", and it's the reload of a
    # single dict reference, not a code change.
    import services.loop_verify as lv
    monkeypatch.setattr(lv, "_LINTERS", fake_map)

    report = asyncio.run(verify_files([
        {"path": "main.rs", "content": "fn main() {}\n"},
    ]))
    row = report["results"][0]
    assert row["linter"] == "true", (
        f"fake Rust adapter must be dispatched to by the existing, "
        f"UNCHANGED verify_files() logic: {row}"
    )
    assert row["verified"] is True
    assert row["ok"] is True


# ═══════════════════════════════════════════════════════════════════
# R5 — no orphans: capabilities.py is really consulted by the real
# task-execution verify path (loop_engine._do_verify -> verify_files),
# not just sitting unused.
# ═══════════════════════════════════════════════════════════════════
def test_r5_do_verify_real_path_surfaces_unverified_files_honestly(monkeypatch):
    """Through the REAL LoopEngine._do_verify() (the actual
    task-execution pipeline's verify step) — a JSON config file
    (unverified language) alongside a real .py file. Proves the
    capability/verified signal actually reaches the real per-file
    verify report, not just an isolated unit test of loop_verify.py."""
    from services.loop_engine import LoopEngine

    class _Coll:
        async def update_one(self, *a, **kw):
            return type("R", (), {"matched_count": 1})()
        async def insert_one(self, *a, **kw):
            return type("R", (), {"inserted_id": "x"})()
        async def find_one(self, *a, **kw):
            return None

    class _DB:
        def __init__(self):
            for c in ("loop_sessions", "loop_events", "loop_run_log"):
                setattr(self, c, _Coll())

    engine = LoopEngine(_DB(), "loop-r5-1", "u1", None, "add a config flag")
    engine.context["submitted_files"] = [
        {"path": "config.json", "content": '{"flag": true}\n'},
        {"path": "app.py", "content": "x = 1\n"},
    ]

    from services import loop_engine as le
    engine.state = le.LoopState.VERIFYING
    asyncio.run(engine._do_verify())

    report = engine.context.get("verification_results") or {}
    results = {r["path"]: r for r in report.get("results", [])}
    assert "config.json" in results, f"missing from report: {results}"
    assert results["config.json"]["verified"] is False, (
        f"config.json (unmapped extension) must be honestly marked "
        f"unverified through the REAL _do_verify() pipeline path: "
        f"{results['config.json']}"
    )
    assert results["config.json"]["ok"] is True, (
        "must not block the loop — R3 hard-gate is deferred this pass"
    )
    assert "app.py" in results
    assert results["app.py"]["verified"] is True, (
        f"a real .py file must be marked verified=True through the "
        f"same real pipeline path: {results['app.py']}"
    )
    assert report["ok"] is True

    # Belt-and-suspenders: the real pipeline path (loop_engine ->
    # loop_verify.verify_files) sources its language map from
    # capabilities.py by identity, not a stale copy.
    from services import loop_verify as lv
    from services import capabilities as caps_mod
    assert lv._LINTERS is caps_mod.VERIFIED_LANGUAGES
