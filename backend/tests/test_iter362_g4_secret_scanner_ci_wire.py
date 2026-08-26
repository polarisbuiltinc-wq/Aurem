"""
tests/test_iter362_g4_secret_scanner_ci_wire.py — Guard 4 CI wiring (2026-08-27)

g4_secret_scanner.py existed but was never wired into any CI workflow —
"a scanner that doesn't run is no scanner." This proves both directions
of the actual scanning logic against a real local HTTP server (the same
mechanism `.github/workflows/ci.yml`'s new "Guard 4" step uses against
`yarn preview`), and asserts the CI step itself is present and wired to
run on every push.
"""
from __future__ import annotations

import http.server
import pathlib
import socket
import sys
import threading
import time

import yaml

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(BACKEND_DIR / "scripts"))

import g4_secret_scanner as g4  # noqa: E402


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _serve_dir(tmp_path, html: str):
    (tmp_path / "index.html").write_text(html)
    port = _free_port()
    handler = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(  # noqa: E731
        *a, directory=str(tmp_path), **kw)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    time.sleep(0.2)
    return server, port


def test_ts1_real_rendered_secret_blocks(tmp_path):
    """A REAL rendered Stripe live key / GitHub PAT must exit non-zero."""
    server, port = _serve_dir(tmp_path, (
        '<html><body><script>window.__debug = '
        '{ stripeKey: "sk_live_AbCdEfGhIjKlMnOpQrStUvWx12" };</script>'
        '<div data-pat="ghp_1234567890abcdefghijklmnopqrstuvwx"></div>'
        '</body></html>'
    ))
    try:
        text = g4._fetch(f"http://127.0.0.1:{port}/")
        hits = g4._scan_text(text, "/")
        kinds = {name for _, name, _ in hits}
        assert "stripe_live" in kinds
        assert "github_pat" in kinds
    finally:
        server.shutdown()


def test_ts2_placeholder_docstring_examples_not_flagged(tmp_path):
    """Masked/placeholder examples (the same ones used in .env docs and
    error-message copy) must NEVER be flagged — this is the verified,
    not-heuristic bar the founder required."""
    server, port = _serve_dir(tmp_path, (
        '<html><body>'
        '<p>Set your key like: <code>sk-aurem-XXXX</code> or '
        '<code>ghp_your_token</code></p>'
        '<p>Stripe test mode example: sk_test_XXXX (masked docs placeholder)</p>'
        '</body></html>'
    ))
    try:
        text = g4._fetch(f"http://127.0.0.1:{port}/")
        hits = g4._scan_text(text, "/")
        assert hits == [], f"placeholder page must not be flagged, got: {hits}"
    finally:
        server.shutdown()


def test_ts3_ci_step_present_and_wired_on_push():
    """The new Guard 4 CI step exists in ci.yml, inside the frontend-build
    job, and the workflow triggers on every push."""
    wf = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text())
    assert "push" in wf.get("on", wf.get(True, {}))  # PyYAML parses `on:` as True in some versions
    steps = wf["jobs"]["frontend-build"]["steps"]
    names = [s.get("name", "") for s in steps]
    guard4 = [s for s in steps if "Guard 4" in s.get("name", "")]
    assert guard4, f"Guard 4 step missing from frontend-build steps: {names}"
    run_text = guard4[0]["run"]
    assert "g4_secret_scanner.py" in run_text
    assert "yarn preview" in run_text
    # must actually propagate the scanner's exit code, not swallow it
    assert "exit $EXIT" in run_text
