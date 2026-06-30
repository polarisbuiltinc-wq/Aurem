/**
 * PreviewPanel.jsx — Right-side live preview for code blocks parsed from
 * the latest assistant message.
 *
 *   - HTML/CSS/JS → rendered inside a sandboxed iframe via `srcDoc`
 *   - JSX/React   → transpiled at runtime via @babel/standalone (loaded
 *                    inside the iframe so it never touches the host page)
 *   - Other langs (python, bash, …) → syntax-coloured code viewer
 *
 * The iframe is sandboxed with `allow-scripts` only — no same-origin —
 * so any code rendered here cannot read parent state.
 */
import React, { useEffect, useMemo, useState } from "react";
import { X, Copy, RefreshCw, Code2, Eye, ExternalLink, Loader2, Rocket } from "lucide-react";
import { toast } from "./Toast";
import { api } from "../lib/api";
import DeployPanel from "./DeployPanel";

const RENDERABLE = new Set([
  "html", "htm",
  "jsx", "tsx",
  "js", "javascript",
  "live_url",  // iframe of an arbitrary preview URL
]);

function filename(block, idx) {
  if (!block) return `file_${idx}`;
  if (block.label) {
    // For codebase tabs, show only the basename so 20+ tabs fit
    // without each being squished to a single character. The full
    // path is preserved in the tab's `title` tooltip.
    const parts = block.label.split("/");
    return parts[parts.length - 1] || block.label;
  }
  const l = block.lang.toLowerCase();
  if (l === "live_url") return "Live Site";
  if (l === "jsx" || l === "tsx") return `App.${l}`;
  if (l === "html" || l === "htm") return "index.html";
  if (l === "css") return "styles.css";
  if (l === "js" || l === "javascript") return "script.js";
  if (l === "python" || l === "py") return "main.py";
  if (l === "yaml" || l === "yml") return "config.yml";
  if (l === "json") return "data.json";
  if (l === "bash" || l === "sh") return "script.sh";
  return `file_${idx}.${l}`;
}

function buildIframeDoc(block) {
  const code = block?.code || "";
  const l = (block?.lang || "").toLowerCase();
  // Direct HTML
  if (l === "html" || l === "htm") return code;
  // JSX — load React + Babel inside the iframe
  if (l === "jsx" || l === "tsx") {
    // Strip ES module syntax so we can run with `new Function()` after Babel
    let stripped = code
      .replace(/^\s*import\s+[^;]+;?\s*$/gm, "")      // import lines
      .replace(/^\s*export\s+default\s+/gm, "const __default__ = ")
      .replace(/^\s*export\s+\{[^}]*\}\s*;?\s*$/gm, "")
      .replace(/^\s*export\s+(const|let|var|function|class)\s+/gm, "$1 ");
    // If they exported "App" via default but defined it inline, our replace
    // turned `export default App;` into `const __default__ = App;` — fine.
    // If they exported an arrow fn directly: `export default () => …`
    // we still capture it in __default__.
    return `<!doctype html>
<html><head><meta charset="utf-8" /><style>
  html,body{margin:0;font-family:system-ui,sans-serif;color:#0a0a0a;background:#fff;padding:14px}
  pre{color:#b00;background:#fee;padding:12px;border-radius:4px;white-space:pre-wrap}
</style></head>
<body><div id="root"></div>
<script crossorigin src="https://unpkg.com/react@18/umd/react.development.js"></script>
<script crossorigin src="https://unpkg.com/react-dom@18/umd/react-dom.development.js"></script>
<script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>
<script>
  try {
    const src = ${JSON.stringify(stripped)};
    const out = Babel.transform(src, { presets: ['react'] }).code;
    const fn = new Function('React', 'ReactDOM',
      out + '\\n; return (typeof __default__!=="undefined") ? __default__ : (typeof App!=="undefined" ? App : (typeof Component!=="undefined" ? Component : null));');
    const Comp = fn(React, ReactDOM);
    const root = ReactDOM.createRoot(document.getElementById('root'));
    if (Comp) root.render(React.createElement(Comp));
    else { var _p = document.createElement('pre'); _p.textContent = 'No exported component found. Define a function named App or export default.'; document.getElementById('root').appendChild(_p); }
  } catch (e) {
    var _ep = document.createElement('pre'); _ep.textContent = (e && e.message || e); document.getElementById('root').appendChild(_ep);
  }
</script>
</body></html>`;
  }
  // Plain JS — run in body
  return `<!doctype html>
<html><head><meta charset="utf-8" /><style>
  html,body{margin:0;padding:14px;font-family:system-ui;color:#0a0a0a;background:#fff}
  pre{color:#b00;background:#fee;padding:12px;border-radius:4px;white-space:pre-wrap}
</style></head>
<body><div id="output"></div>
<script>
  try {
    const log = [];
    const _c = console.log;
    console.log = (...args) => { log.push(args.map(a => typeof a === 'string' ? a : JSON.stringify(a)).join(' ')); _c(...args); };
    ${code}
    if (log.length) { var _lp = document.createElement('pre'); _lp.textContent = log.join('\\n'); document.getElementById('output').appendChild(_lp); }
  } catch (e) {
    var _ep = document.createElement('pre'); _ep.textContent = (e && e.message || e); document.getElementById('output').appendChild(_ep);
  }
</script>
</body></html>`;
}

export default function PreviewPanel({ blocks, onClose, activeProject, initialViewMode }) {
  const [activeTab, setActiveTab] = useState(0);
  const [viewMode, setViewMode] = useState(initialViewMode || "preview"); // 'preview' | 'code' | 'deploy'
  const [refreshKey, setRefreshKey] = useState(0);
  // Iter 170c — Codebase browse mode.
  //   `codebase`        — { files: [{path, size}], owner, repo, branch } once loaded
  //   `codebaseLoading` — true while the tree fetch is in flight
  //   `codebaseErr`     — populated when the GitHub call fails (bad PAT, missing
  //                       branch, etc.) so we can show a one-line hint instead of a blank panel
  //   `fileContents`    — Map<path, {code, loading, err}> populated on tab click
  const [codebase, setCodebase] = useState(null);
  const [codebaseLoading, setCodebaseLoading] = useState(false);
  const [codebaseErr, setCodebaseErr] = useState(null);
  const [fileContents, setFileContents] = useState({});

  // Note: We deliberately don't useEffect to reset on project change.
  // ChatPanel passes `key={activeProject?.project_id}` so a project
  // switch unmounts and remounts the panel — cleaner than effects.

  // Fired when the user toggles into Code mode and the chat hasn't
  // produced any real code blocks yet (only the live_url placeholder).
  // We fetch the repo tree once per project and lazy-load files on tab
  // click. Errors are surfaced inline.
  const fetchCodebaseTree = async () => {
    if (!activeProject?.project_id) return;
    let go = true;
    setCodebaseLoading((cur) => {
      if (cur) { go = false; return cur; }   // already loading
      return true;
    });
    if (!go) return;
    let alreadyHave = false;
    setCodebase((cur) => { if (cur) alreadyHave = true; return cur; });
    if (alreadyHave) { setCodebaseLoading(false); return; }
    setCodebaseErr(null);
    try {
      const r = await api.get(
        `/cto/projects/${activeProject.project_id}/tree`
      );
      setCodebase({
        files: r.data?.files || [],
        owner: r.data?.owner,
        repo:  r.data?.repo,
        branch: r.data?.branch,
        truncated: !!r.data?.truncated,
      });
    } catch (e) {
      const msg = e?.response?.data?.detail
        || e?.message
        || "Couldn't load codebase.";
      setCodebaseErr(msg);
    } finally {
      setCodebaseLoading(false);
    }
  };

  const fetchFileContent = async (path) => {
    if (!activeProject?.project_id || !path) return;
    // Use functional setState so we don't need `fileContents` in deps.
    let shouldFetch = true;
    setFileContents((m) => {
      if (m[path]?.code !== undefined || m[path]?.loading) {
        shouldFetch = false;
        return m;
      }
      return { ...m, [path]: { loading: true } };
    });
    if (!shouldFetch) return;
    try {
      const r = await api.get(
        `/cto/projects/${activeProject.project_id}/file`,
        { params: { path } }
      );
      setFileContents((m) => ({
        ...m,
        [path]: { code: r.data?.content || "", truncated: !!r.data?.