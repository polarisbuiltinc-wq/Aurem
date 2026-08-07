/**
 * OraPreviewPanel.jsx — Phase 2 · Feb 2026
 *
 * SECURITY-HARDENED live preview for HTML / JSX / JS code blocks that
 * ORA emits inside the /ora chat. Slides in as a right-side drawer
 * so the assistant message stays visible on the left.
 *
 * ── NON-NEGOTIABLE SECURITY CONTRACT (documented in the Phase 2 brief) ──
 *   1. iframe sandbox="allow-scripts"  ONLY.  Never combined with
 *      allow-same-origin.  Any code rendered here therefore cannot
 *      read parent cookies / storage / DOM.
 *   2. Strict Content-Security-Policy injected as a <meta http-equiv>
 *      into the srcdoc BEFORE anything else in <head>:
 *         default-src 'none';
 *         script-src 'unsafe-inline' https://unpkg.com;
 *         style-src  'unsafe-inline';
 *         img-src    data: https:;
 *         connect-src 'none';
 *         font-src   data:;
 *      → `connect-src 'none'` explicitly kills fetch/XHR/websocket
 *        from any previewed code, so an attacker payload cannot
 *        exfil to a remote host.
 *   3. 300 ms debounce on srcdoc rebuilds — protects against
 *      thrashing during streaming and against rapid re-scan storms.
 *   4. 16MB hard size cap enforced on BOTH client (before POST) and
 *      server (`/preview-scan` returns 413).
 *   5. Every code payload MUST clear the Vanguard scanner
 *      (`POST /ora-chat/preview-scan`) before we build the srcdoc.
 *      Any CRITICAL finding refuses render; HIGH findings surface
 *      as a warning banner but do not block.
 */
import React, { useEffect, useMemo, useRef, useState } from "react";
import { X, RefreshCw, ShieldCheck, ShieldAlert, Code2, Eye, Loader2 } from "lucide-react";
import { api } from "../lib/api";

const PREVIEW_MAX_BYTES = 16 * 1024 * 1024;      // 16 MB — matches backend
const DEBOUNCE_MS = 300;

const RENDERABLE_LANGS = new Set([
  "html", "htm", "jsx", "tsx", "js", "javascript",
]);

// Iter 212m-264 · Feb 2026 — Strict CSP for the sandboxed preview.
// `connect-src 'none'` is the anti-exfil hammer.
// Iter 212m-264 · Feb 2026 — CSP is split per-lang so the supply-chain
// blast radius stays as small as possible:
//
//   HTML / JS previews  → script-src 'unsafe-inline'
//                          (NO external hosts, NO 'unsafe-eval')
//   JSX / TSX previews  → script-src 'unsafe-inline' 'unsafe-eval'
//                          https://unpkg.com
//                          — 'unsafe-eval' is REQUIRED because
//                            @babel/standalone transpiles JSX at
//                            runtime via `eval` / `new Function`,
//                            and we then instantiate the transpiled
//                            component via `new Function` as well.
//                            Without it, JSX previews fail with
//                            "Refused to compile" / "unsafe-eval"
//                            CSP violations. Founder review flagged
//                            this as Issue 2 of the prod-verify pass.
//                          — unpkg.com is required to load the
//                            React + ReactDOM + Babel bundles.
//
// `connect-src 'none'` is the anti-exfil hammer in BOTH variants —
// even if unpkg were compromised, the payload cannot beacon out.
// The outer iframe's `sandbox="allow-scripts"` (no allow-same-origin)
// then keeps everything trapped away from the parent origin.
//
// Backlog / P2 improvement: bundle React + a pre-transpiled JSX
// runtime into the app's own static assets so JSX previews never
// hit unpkg AND we can drop `'unsafe-eval'` entirely.
const _CSP_COMMON =
  "default-src 'none'; " +
  "style-src 'unsafe-inline'; " +
  "img-src data: https:; " +
  "font-src data:; " +
  "connect-src 'none'; " +
  "base-uri 'none'; " +
  "form-action 'none'; " +
  "frame-ancestors 'none';";
const CSP_HTML_JS = "script-src 'unsafe-inline'; " + _CSP_COMMON;
const CSP_JSX     = "script-src 'unsafe-inline' 'unsafe-eval' https://unpkg.com; " + _CSP_COMMON;

function _cspFor(lang) {
  const l = (lang || "").toLowerCase();
  return (l === "jsx" || l === "tsx") ? CSP_JSX : CSP_HTML_JS;
}

function buildSrcDoc(code, lang) {
  const l = (lang || "").toLowerCase();
  const cspMeta =
    `<meta http-equiv="Content-Security-Policy" content="${_cspFor(lang)}">`;
  const baseHead =
    `<meta charset="utf-8">${cspMeta}` +
    `<style>html,body{margin:0;padding:14px;` +
    `font-family:-apple-system,BlinkMacSystemFont,'Inter',sans-serif;` +
    `color:#1C1C19;background:#fff;line-height:1.55}` +
    `pre.__ora_err{color:#b00;background:#fee;padding:12px;border-radius:4px;` +
    `white-space:pre-wrap;font-family:ui-monospace,monospace}</style>`;

  // Direct HTML → inject CSP + baseline CSS at the top.
  if (l === "html" || l === "htm") {
    // If the code already includes <html>/<head>, inject the CSP meta
    // right after <head>. Otherwise wrap it.
    if (/<head[\s>]/i.test(code)) {
      return code.replace(/<head([^>]*)>/i, `<head$1>${cspMeta}`);
    }
    return `<!doctype html><html><head>${baseHead}</head><body>${code}</body></html>`;
  }

  // JSX / TSX — Babel-transpile INSIDE the sandbox. React/Babel from
  // unpkg is CSP-allowlisted above (script-src includes unpkg).
  if (l === "jsx" || l === "tsx") {
    const stripped = code
      .replace(/^\s*import\s+[^;]+;?\s*$/gm, "")
      .replace(/^\s*export\s+default\s+/gm, "const __default__ = ")
      .replace(/^\s*export\s+\{[^}]*\}\s*;?\s*$/gm, "")
      .replace(/^\s*export\s+(const|let|var|function|class)\s+/gm, "$1 ");
    return `<!doctype html><html><head>${baseHead}</head>
<body><div id="root"></div>
<script crossorigin src="https://unpkg.com/react@18/umd/react.development.js"></script>
<script crossorigin src="https://unpkg.com/react-dom@18/umd/react-dom.development.js"></script>
<script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>
<script>
  try {
    var src = ${JSON.stringify(stripped)};
    // Force runtime:'classic' so Babel emits React.createElement
    // calls instead of the modern jsx-runtime output (which uses
    // ESM imports and would blow up under new Function() with
    // "Cannot use import statement outside a module"). Founder QA
    // (Issue 2 of the Phase 2 prod-verify) surfaced this after we
    // added 'unsafe-eval' to the JSX CSP.
    var out = Babel.transform(src, {
      presets: [['react', { runtime: 'classic' }]],
    }).code;
    var fn = new Function('React','ReactDOM',
      out + '\\n; return (typeof __default__!=="undefined") ? __default__ : (typeof App!=="undefined" ? App : (typeof Component!=="undefined" ? Component : null));');
    var Comp = fn(React, ReactDOM);
    var root = ReactDOM.createRoot(document.getElementById('root'));
    if (Comp) root.render(React.createElement(Comp));
    else document.getElementById('root').textContent = 'No exported component found. Define App or use export default.';
  } catch (e) {
    var p = document.createElement('pre');
    p.className = '__ora_err';
    p.textContent = (e && e.message) ? e.message : String(e);
    document.body.appendChild(p);
  }
</script>
</body></html>`;
  }

  // Plain JS → execute, capture console.log into a <pre> below.
  return `<!doctype html><html><head>${baseHead}</head>
<body><div id="__ora_out"></div>
<script>
  try {
    var __log = [];
    var _c = console.log;
    console.log = function(){ __log.push(Array.prototype.slice.call(arguments).map(function(a){
      return typeof a === 'string' ? a : JSON.stringify(a);
    }).join(' ')); _c.apply(console, arguments); };
    ${code}
    if (__log.length) {
      var p = document.createElement('pre');
      p.textContent = __log.join('\\n');
      document.getElementById('__ora_out').appendChild(p);
    }
  } catch (e) {
    var p2 = document.createElement('pre');
    p2.className = '__ora_err';
    p2.textContent = (e && e.message) ? e.message : String(e);
    document.body.appendChild(p2);
  }
</script>
</body></html>`;
}

// Debounce helper — always starts empty so the *first* render never
// triggers a downstream effect. The consumer's guard (`!debouncedCode`
// → return early) then blocks Vanguard from firing until the caller's
// value has been stable for `ms`. This is what makes the 300ms brief
// requirement observable from the outside (see Phase 2 vitest).
function useDebounced(value, ms) {
  const [v, setV] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export default function OraPreviewPanel({ code = "", lang = "", onClose }) {
  const [mode, setMode] = useState("preview");   // 'preview' | 'code'
  const [scan, setScan] = useState({ state: "idle", data: null, err: null });
  const [refreshKey, setRefreshKey] = useState(0);
  // Iter 212m-264 · Feb 2026 — Founder review flagged that the
  // original HIGH/MEDIUM banner was easy to miss: the iframe below
  // built automatically even when Vanguard reported a HIGH-severity
  // sink (e.g. innerHTML). HIGH findings now require an explicit
  // acknowledgement click (`ack`) before the srcdoc builds. MEDIUM
  // findings stay passive (mostly stylistic / informational).
  const [ack, setAck] = useState(false);
  // Reset the acknowledgement whenever the code changes so a stale
  // "Preview anyway" can't carry over to a NEW payload.
  useEffect(() => { setAck(false); }, [code, lang]);

  const codeSize = useMemo(
    () => (code ? new Blob([code]).size : 0),
    [code]
  );
  const tooBig = codeSize > PREVIEW_MAX_BYTES;
  const debouncedCode = useDebounced(code, DEBOUNCE_MS);

  // Vanguard scan on every debounced code change.
  useEffect(() => {
    if (!debouncedCode || !RENDERABLE_LANGS.has((lang || "").toLowerCase())) {
      setScan({ state: "idle", data: null, err: null });
      return;
    }
    if (tooBig) {
      setScan({ state: "error", data: null, err: "code_too_large" });
      return;
    }
    let alive = true;
    setScan({ state: "scanning", data: null, err: null });
    (async () => {
      try {
        const r = await api.post("/ora-chat/preview-scan", {
          code: debouncedCode, lang,
        });
        if (!alive) return;
        setScan({ state: "done", data: r.data, err: null });
      } catch (e) {
        if (!alive) return;
        const err = e?.response?.data?.detail
          || e?.response?.statusText
          || e?.message
          || "scan_failed";
        setScan({ state: "error", data: null, err });
      }
    })();
    return () => { alive = false; };
  }, [debouncedCode, lang, tooBig, refreshKey]);

  const langOk = RENDERABLE_LANGS.has((lang || "").toLowerCase());
  const highFindings = scan.data?.warnings?.filter(
    (w) => w.severity === "HIGH") || [];
  const hasHigh = scan.state === "done" && highFindings.length > 0;
  const canRender = langOk && !tooBig
    && scan.state === "done" && scan.data?.safe === true
    && (!hasHigh || ack);
  const srcDoc = useMemo(
    () => (canRender ? buildSrcDoc(debouncedCode, lang) : ""),
    [canRender, debouncedCode, lang]
  );

  return (
    <div data-testid="ora-preview-panel"
         style={{ position: "fixed", top: 0, right: 0, bottom: 0,
                    width: "min(680px, 55vw)", zIndex: 220,
                    background: "#FFFFFF",
                    borderLeft: "1px solid #E5E5DF",
                    boxShadow: "-8px 0 32px rgba(0,0,0,0.08)",
                    display: "flex", flexDirection: "column",
                    fontFamily: "-apple-system, BlinkMacSystemFont, 'Inter', sans-serif" }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center",
                     padding: "12px 16px",
                     borderBottom: "1px solid #E5E5DF",
                     gap: 8 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: "#1C1C19" }}>
          Preview
        </div>
        <div style={{ fontSize: 10, padding: "2px 6px", borderRadius: 4,
                        background: "#F4F3EE", color: "#6B6B63",
                        fontFamily: "ui-monospace, monospace" }}>
          {(lang || "?").toLowerCase()}
        </div>
        <div style={{ flex: 1 }} />
        <button data-testid="ora-preview-mode-preview"
                onClick={() => setMode("preview")}
                title="Live preview"
                style={pillStyle(mode === "preview")}>
          <Eye size={12} /> Preview
        </button>
        <button data-testid="ora-preview-mode-code"
                onClick={() => setMode("code")}
                title="Show raw source"
                style={pillStyle(mode === "code")}>
          <Code2 size={12} /> Code
        </button>
        <button data-testid="ora-preview-refresh"
                onClick={() => setRefreshKey(k => k + 1)}
                title="Re-scan + re-render"
                style={iconBtnStyle}>
          <RefreshCw size={13} />
        </button>
        <button data-testid="ora-preview-close" onClick={onClose}
                title="Close preview" style={iconBtnStyle}>
          <X size={14} />
        </button>
      </div>

      {/* Vanguard banner */}
      {scan.state === "scanning" && (
        <div data-testid="ora-preview-scanning"
             style={bannerStyle("#EEF0F6", "#3B4E7A")}>
          <Loader2 size={12} className="animate-spin" />
          Vanguard scanning preview payload…
        </div>
      )}
      {/* HIGH-severity findings require a click-through ack before
          the iframe builds — no more passive "banner you can miss". */}
      {hasHigh && !ack && (
        <div data-testid="ora-preview-high-ack"
             style={{ padding: "10px 16px", background: "#FBF1DC",
                        color: "#7A5A0F", borderBottom: "1px solid #E4C26B",
                        fontSize: 12, display: "flex", gap: 10,
                        alignItems: "center", flexWrap: "wrap" }}>
          <ShieldAlert size={14} />
          <span style={{ flex: 1, minWidth: 200 }}>
            <b>{highFindings.length} HIGH-severity</b> finding
            {highFindings.length === 1 ? "" : "s"} in this code
            (e.g. <code style={{ fontFamily: "ui-monospace, monospace" }}>
              {highFindings[0]?.name || highFindings[0]?.rule}
            </code>). Review the details below before rendering.
          </span>
          <button type="button"
                  data-testid="ora-preview-ack-btn"
                  onClick={() => setAck(true)}
                  style={{ padding: "5px 12px", borderRadius: 999,
                             background: "#7A5A0F", color: "#fff",
                             border: "none", fontSize: 11, fontWeight: 600,
                             cursor: "pointer", fontFamily: "inherit" }}>
            Preview anyway
          </button>
        </div>
      )}
      {hasHigh && ack && (
        <div data-testid="ora-preview-high-acked"
             style={bannerStyle("#FBF1DC", "#8A6512")}>
          <ShieldAlert size={12} />
          Rendering with {highFindings.length} HIGH finding
          {highFindings.length === 1 ? "" : "s"} acknowledged.
        </div>
      )}
      {scan.state === "done" && scan.data?.safe && !hasHigh
        && (scan.data?.warnings?.length > 0) && (
        <div data-testid="ora-preview-warnings"
             style={bannerStyle("#FBF1DC", "#8A6512")}>
          <ShieldAlert size={12} />
          {scan.data.warnings.length} MEDIUM-severity note
          {scan.data.warnings.length === 1 ? "" : "s"} — rendering anyway.
        </div>
      )}
      {scan.state === "done" && scan.data?.safe === false && (
        <div data-testid="ora-preview-blocked"
             style={bannerStyle("#fdeeea", "#8C2E1C")}>
          <ShieldAlert size={12} />
          Blocked by Vanguard: {scan.data.blockers?.length || 0} CRITICAL finding{scan.data.blockers?.length === 1 ? "" : "s"}.
          Preview refused.
        </div>
      )}
      {scan.state === "error" && (
        <div data-testid="ora-preview-scan-error"
             style={bannerStyle("#fdeeea", "#8C2E1C")}>
          <ShieldAlert size={12} />
          {scan.err === "code_too_large"
            ? `Payload too large (${(codeSize/1024/1024).toFixed(1)}MB > 16MB cap).`
            : `Scan failed: ${String(scan.err).slice(0, 120)}`}
        </div>
      )}
      {!langOk && (
        <div data-testid="ora-preview-nonrenderable"
             style={bannerStyle("#F4F3EE", "#6B6B63")}>
          <Code2 size={12} />
          <code>{lang || "?"}</code> isn&apos;t a renderable preview lang — showing source only.
        </div>
      )}

      {/* Body: iframe OR raw code */}
      <div style={{ flex: 1, overflow: "hidden",
                     background: mode === "code" ? "#0F0F0F" : "#FFFFFF" }}>
        {mode === "preview" && canRender && (
          <iframe key={refreshKey}
                  data-testid="ora-preview-iframe"
                  title="ORA sandboxed preview"
                  // ── CRITICAL: allow-scripts ONLY. Never add
                  //   allow-same-origin — combining the two would let
                  //   preview code read parent cookies/storage.
                  sandbox="allow-scripts"
                  referrerPolicy="no-referrer"
                  srcDoc={srcDoc}
                  style={{ width: "100%", height: "100%", border: "none",
                             background: "#FFFFFF" }} />
        )}
        {mode === "preview" && !canRender && (
          <div style={{ padding: 24, color: "#6B6B63", fontSize: 13,
                          lineHeight: 1.6 }}>
            {scan.state === "scanning"
              ? "Waiting for Vanguard scan…"
              : !langOk
                ? "Nothing to preview — switch to Code tab to see the source."
                : "Preview blocked. See banner above for details."}
          </div>
        )}
        {mode === "code" && (
          <pre data-testid="ora-preview-source"
               style={{ margin: 0, padding: 16, height: "100%",
                          overflow: "auto",
                          color: "#E5E5DF", background: "#0F0F0F",
                          fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                          fontSize: 12.5, lineHeight: 1.55 }}>
            {code || ""}
          </pre>
        )}
      </div>

      {/* Findings footer */}
      {scan.state === "done" && (scan.data?.blockers?.length || scan.data?.warnings?.length) ? (
        <div data-testid="ora-preview-findings"
             style={{ maxHeight: "26vh", overflow: "auto",
                        borderTop: "1px solid #E5E5DF",
                        padding: "8px 16px", background: "#FAFAF5",
                        fontSize: 11, fontFamily: "ui-monospace, monospace",
                        color: "#3D3D36" }}>
          {(scan.data.blockers || []).concat(scan.data.warnings || []).map((f, i) => (
            <div key={i} style={{ marginBottom: 6 }}>
              <span style={{
                display: "inline-block", padding: "1px 6px",
                borderRadius: 3, marginRight: 6,
                background: f.severity === "CRITICAL" ? "#8C2E1C" : "#8A6512",
                color: "#fff", fontSize: 10,
              }}>{f.severity}</span>
              <b>{f.name || f.rule}</b>
              {f.line ? ` · line ${f.line}` : ""}
              {f.snippet ? ` — ${f.snippet}` : ""}
            </div>
          ))}
        </div>
      ) : null}

      {/* Footer trust line */}
      <div style={{ padding: "6px 12px", fontSize: 10, color: "#8B8B7D",
                     borderTop: "1px solid #E5E5DF",
                     display: "flex", alignItems: "center", gap: 6 }}>
        <ShieldCheck size={11} />
        Sandboxed iframe · <code>allow-scripts</code> only · CSP <code>connect-src &apos;none&apos;</code>
      </div>
    </div>
  );
}

function pillStyle(active) {
  return {
    padding: "4px 10px", borderRadius: 999,
    background: active ? "#1C1C19" : "transparent",
    color: active ? "#fff" : "#6B6B63",
    border: `1px solid ${active ? "#1C1C19" : "#E5E5DF"}`,
    fontSize: 11, fontWeight: 500,
    display: "flex", alignItems: "center", gap: 4,
    cursor: "pointer",
    fontFamily: "inherit",
  };
}
const iconBtnStyle = {
  padding: 6, background: "transparent", border: "1px solid #E5E5DF",
  borderRadius: 8, color: "#6B6B63", cursor: "pointer",
  display: "flex", alignItems: "center",
};
function bannerStyle(bg, fg) {
  return {
    padding: "8px 16px", background: bg, color: fg,
    borderBottom: `1px solid ${bg}`,
    fontSize: 11.5, display: "flex", gap: 6, alignItems: "center",
  };
}
