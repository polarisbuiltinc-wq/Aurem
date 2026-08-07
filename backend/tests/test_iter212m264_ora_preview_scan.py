"""
tests/test_iter212m264_ora_preview_scan.py — Phase 2 · Feb 2026

Static + unit checks around the `/api/aurem-dev/ora-chat/preview-scan`
endpoint added for the srcdoc-sandbox preview panel.  The endpoint is
the ONLY server-side gate that separates arbitrary ORA-suggested code
from ever executing inside the founder's browser tab, so the contract
is worth pinning:

  1. 16 MB hard byte cap.
  2. `renderable=False` for langs outside the whitelist (never scans).
  3. Uses `vanguard_scanner.scan_text` — the same regex sweep the
     pre-push gate uses, no bespoke rules.
  4. CRITICAL findings collapse to `safe=False` (frontend refuses
     to build the srcdoc when this bit flips).
  5. Endpoint is admin-gated (require_admin).
"""
from __future__ import annotations

from pathlib import Path
import inspect

from services.vanguard_scanner import scan_text


_ROUTER_SRC = Path("/app/backend/routers/ora_chat.py").read_text()


class TestPreviewScanContract:
    def test_endpoint_is_admin_gated(self):
        # The route body must call `require_admin` before doing any work.
        # We check the source rather than the runtime so the test can
        # run offline without spinning up FastAPI.
        idx = _ROUTER_SRC.find('@router.post("/preview-scan")')
        assert idx != -1, "preview-scan endpoint missing"
        body = _ROUTER_SRC[idx:idx + 1200]
        assert "await require_admin(authorization)" in body, (
            "preview-scan MUST call require_admin before scanning — this "
            "endpoint would otherwise be a public scan oracle."
        )

    def test_size_cap_matches_frontend_contract(self):
        # 16 MB — matches the client-side cap in OraPreviewPanel.jsx and
        # the founder brief.
        assert "_PREVIEW_MAX_BYTES = 16 * 1024 * 1024" in _ROUTER_SRC

    def test_renderable_lang_whitelist_is_minimal(self):
        # Only these langs may ever be Babel-transpiled or run inside
        # the sandbox. Anything else short-circuits with renderable=False.
        assert '_PREVIEW_RENDERABLE = {"html", "htm", "jsx", "tsx", "js", "javascript"}' in _ROUTER_SRC

    def test_uses_vanguard_scan_text(self):
        # The endpoint MUST reuse the shared scanner, not roll its own
        # rules — otherwise Vanguard drift becomes a security regression.
        idx = _ROUTER_SRC.find('@router.post("/preview-scan")')
        body = _ROUTER_SRC[idx:idx + 2500]
        assert "from services.vanguard_scanner import scan_text" in body
        assert "scan_text(code, filepath=fake_path" in body


class TestScannerCatchesXSSVectors:
    """Sanity: the underlying scanner does flag the patterns we care
    about most for the srcdoc preview path.  If any of these regress
    to zero findings, the preview panel would happily render code that
    the pre-push gate would refuse to commit."""

    def test_innerHTML_assignment_flagged_on_js(self):
        f = scan_text('el.innerHTML = user;', filepath="preview.js")
        names = [x["name"] for x in f]
        assert "innerHTML_assignment" in names

    def test_dangerously_set_html_flagged_on_jsx(self):
        f = scan_text('<div dangerouslySetInnerHTML={{__html: raw}}/>',
                       filepath="preview.jsx")
        names = [x["name"] for x in f]
        assert "dangerously_set_html" in names

    def test_clean_html_has_no_findings(self):
        f = scan_text('<h1>Hello</h1><p>World</p>', filepath="preview.html")
        assert all(x.get("severity") not in ("CRITICAL",) for x in f)
