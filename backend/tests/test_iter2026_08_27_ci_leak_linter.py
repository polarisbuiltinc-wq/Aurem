"""Regression — CI machinery-leak-copy linter (2026-08-27, P2).

Proves the linter (`scripts/ci_check_machinery_leak_copy.py`) actually
catches a real violation (the "PR it blocked" proof), and that it does
NOT false-positive on "Vanguard" (the product's own public feature
name — confirmed via `Landing.jsx`/`MessageBubble.jsx`) or on
non-visible attributes like `data-testid`/`className`.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile

_SCRIPT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "scripts",
    "ci_check_machinery_leak_copy.py",
)


def _load_module():
    spec = importlib.util.spec_from_file_location("ci_leak_linter", _SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestCiLeakLinterCatchesRealViolation:
    def test_narration_call_with_banned_token_is_caught(self):
        mod = _load_module()
        src = (
            "class X:\n"
            "    async def f(self):\n"
            "        await self._narrate(step='scan', tone='pending', "
            "text='calling the 5-adviser council for a chairman verdict')\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write(src)
            path = f.name
        try:
            violations = mod._check_loop_engine_narration(path)
            assert len(violations) >= 1
            assert any("chairman" in v.lower() or "adviser" in v.lower()
                       for v in violations)
        finally:
            os.unlink(path)

    def test_errors_catalog_with_banned_token_is_caught(self):
        mod = _load_module()
        import json
        catalog = {"SOME_CODE": {
            "title": "oops", "what_happened": "the chairman rejected this",
            "what_to_try": ["retry"],
        }}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(catalog, f)
            path = f.name
        try:
            violations = mod._check_errors_catalog(path)
            assert len(violations) == 1
            assert "chairman" in violations[0].lower()
        finally:
            os.unlink(path)

    def test_jsx_child_text_with_banned_token_is_caught(self):
        mod = _load_module()
        src = '<div>· 5-adviser council · chairman verdict</div>\n'
        with tempfile.NamedTemporaryFile("w", suffix=".jsx", delete=False) as f:
            f.write(src)
            path = f.name
        try:
            violations = mod._check_frontend_jsx(path)
            assert len(violations) >= 1
        finally:
            os.unlink(path)


class TestCiLeakLinterNoFalsePositives:
    def test_vanguard_is_not_banned(self):
        mod = _load_module()
        assert not mod._BANNED_RE.search("Vanguard")
        src = '<div>Vanguard Security scan complete</div>\n'
        with tempfile.NamedTemporaryFile("w", suffix=".jsx", delete=False) as f:
            f.write(src)
            path = f.name
        try:
            assert mod._check_frontend_jsx(path) == []
        finally:
            os.unlink(path)

    def test_e2b_in_data_testid_is_not_flagged(self):
        mod = _load_module()
        src = '<div data-testid="preview-e2b-iframe" className="e2b-box">hello</div>\n'
        with tempfile.NamedTemporaryFile("w", suffix=".jsx", delete=False) as f:
            f.write(src)
            path = f.name
        try:
            assert mod._check_frontend_jsx(path) == []
        finally:
            os.unlink(path)

    def test_current_codebase_is_clean(self):
        """Live proof: running the linter against the real repo right
        now reports zero violations (the real leak it found —
        MessageBubble.jsx's "5-adviser council · chairman verdict"
        badge — was fixed as part of this same change)."""
        import subprocess
        result = subprocess.run(
            [sys.executable, _SCRIPT_PATH], capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stdout
        assert "OK — no machinery-leak-copy violations found." in result.stdout


class TestCiLeakLinterScriptLevelFixtureProof:
    """P2 item 1 — the two proofs the founder asked for, run through
    the ACTUAL `main()` entrypoint (the one CI invokes), not just the
    internal `_check_*` helpers: (a) a fixture PR that adds a banned
    token must make CI fail; (b) the real codebase must make CI pass.
    """

    def _run_main_against(self, tmp_backend_root, tmp_frontend_src):
        mod = _load_module()
        mod._BACKEND_ROOT = tmp_backend_root
        mod._FRONTEND_SRC = tmp_frontend_src
        return mod.main([])

    def test_fixture_pr_adding_banned_token_fails_ci(self, tmp_path, capsys):
        """(a) — a PR that hardcodes a banned machinery term into a
        JSX file must exit 1 and print the offending line, exactly
        like a real CI run would block the merge."""
        services_dir = tmp_path / "services"
        services_dir.mkdir()
        (services_dir / "loop_engine.py").write_text(
            "class X:\n"
            "    async def f(self):\n"
            "        await self._narrate(step='scan', tone='pending', "
            "text='asking the chairman for a verdict')\n"
        )
        i18n_dir = tmp_path / "i18n"
        i18n_dir.mkdir()
        (i18n_dir / "errors_en.json").write_text("{}")
        frontend_src = tmp_path / "frontend_src"
        frontend_src.mkdir()

        exit_code = self._run_main_against(str(tmp_path), str(frontend_src))
        out = capsys.readouterr().out
        assert exit_code == 1, out
        assert "machinery-leak-copy violation" in out
        assert "chairman" in out.lower()

    def test_real_codebase_passes_ci(self):
        """(b) — running the SAME `main()` entrypoint against the real
        repo paths (the ones CI actually scans) reports zero
        violations, proving the earlier real leak this pass found and
        fixed (MessageBubble.jsx) stays fixed."""
        mod = _load_module()
        real_backend_root = mod._BACKEND_ROOT
        real_frontend_src = mod._FRONTEND_SRC
        exit_code = self._run_main_against(real_backend_root, real_frontend_src)
        assert exit_code == 0
