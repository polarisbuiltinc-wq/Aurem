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
/* eslint-disable react/no-danger */
/**
 * PreviewPanel.jsx — Right-side live preview for code blocks parsed from
 * the latest assistant message.
 *
 * SECURITY NOTE — the string-literal `.innerHTML = ...` occurrences in
 * `buildIframeDoc()` live INSIDE the srcDoc of a sandboxed iframe
 * (`sandbox="allow-scripts"` only, no `allow-same-origin`).  They can't
 * touch the parent DOM or read any AUREM cookies/storage.  Marked with
 * `// vanguard: ignore` on each line so the Vanguard scanner treats
 * them as intentional.
 */
import React, { useEffect, useMemo, useState } from "react";
import { X, Copy, RefreshCw, Code2, Eye, ExternalLink, Loader2, Rocket, Smartphone, Tablet, Monitor, Camera } from "lucide-react";
import { toast } from "./Toast";
import { api } from "../lib/api";
import DeployPanel from "./DeployPanel";

// Trust Surfaces Round (S1-P1), 2026-08-29 — device-frame widths.
// Pure CSS framing of the EXISTING iframe — no new library, no new
// browser launch (L14/L17).
const DEVICE_FRAMES = {
  phone:   { width: 375, label: "Phone" },
  tablet:  { width: 768, label: "Tablet" },
  desktop: { width: "100%", label: "Desktop" },
};

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
    else document.getElementById('root').innerHTML = '<pre>No exported component found. Define a function named <b>App</b> or <code>export default</code>.</pre>';  // vanguard: ignore — sandboxed iframe (iter 212m-227)
  } catch (e) {
    document.getElementById('root').innerHTML = '<pre>'+(e && e.message || e)+'</pre>';  // vanguard: ignore — sandboxed iframe (iter 212m-227)
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
    if (log.length) document.getElementById('output').innerHTML = '<pre>'+log.join('\\n')+'</pre>';  // vanguard: ignore — sandboxed iframe (iter 212m-227)
  } catch (e) {
    document.getElementById('output').innerHTML = '<pre>'+(e && e.message || e)+'</pre>';  // vanguard: ignore — sandboxed iframe (iter 212m-227)
  }
</script>
</body></html>`;
}

// Trust Surfaces Round (S2), 2026-08-29 — "What changed" default
// Code view. Deterministic summary + top-5 diff, "All files" stays
// the full read-only browser (unchanged) behind its own sub-tab.
function WhatChangedView({ data, loading, onOpenAllFiles }) {
  if (loading || !data) {
    return (
      <div data-testid="what-changed-loading" style={{
        display: "flex", alignItems: "center", gap: 8,
        padding: 20, color: "var(--text-faint)", fontSize: 12,
      }}>
        <Loader2 size={13} style={{ animation: "spin 1s linear infinite" }} />
        Checking what changed…
      </div>
    );
  }
  return (
    <div data-testid="what-changed-view" style={{ padding: 20, overflowY: "auto", height: "100%" }}>
      <div data-testid="what-changed-headline" style={{ fontSize: 14, color: "var(--text)", marginBottom: 4, fontWeight: 600 }}>
        {data.headline}
      </div>
      {data.commit_sha && (
        <div style={{ fontSize: 11, color: "var(--text-faint)", marginBottom: 16, fontFamily: "'JetBrains Mono', monospace" }}>
          @ {data.commit_sha.slice(0, 7)}
        </div>
      )}
      {data.diff_unavailable && data.n_files > 0 && (
        <div data-testid="what-changed-diff-unavailable" style={{ fontSize: 11, color: "var(--text-faint)", marginBottom: 14 }}>
          (File list is real — GitHub diff detail isn&apos;t reachable right now.)
        </div>
      )}
      {(data.files || []).map((f, i) => (
        <div key={f.path} data-testid={`what-changed-file-${i}`} style={{
          border: "1px solid var(--border)", borderRadius: 8, marginBottom: 10, overflow: "hidden",
        }}>
          <div style={{
            display: "flex", alignItems: "center", gap: 8,
            padding: "8px 12px", background: "var(--bg-elev)",
            fontSize: 12, fontFamily: "'JetBrains Mono', monospace",
          }}>
            <span style={{ color: "var(--text)", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{f.path}</span>
            {f.classification === "server" && (
              <span data-testid={`what-changed-server-badge-${i}`} style={{
                fontSize: 10, padding: "1px 6px", borderRadius: 4,
                background: "rgba(239,68,68,0.12)", color: "var(--danger)",
                border: "1px solid var(--danger)",
              }}>server &amp; data</span>
            )}
            {f.classification === "ui" && (
              <span style={{
                fontSize: 10, padding: "1px 6px", borderRadius: 4,
                background: "rgba(34,197,94,0.12)", color: "var(--accent-2)",
                border: "1px solid var(--accent-2)",
              }}>customer-facing</span>
            )}
            {typeof f.additions === "number" && (
              <span style={{ fontSize: 10, color: "#4ade80" }}>+{f.additions}</span>
            )}
            {typeof f.deletions === "number" && (
              <span style={{ fontSize: 10, color: "var(--danger)" }}>-{f.deletions}</span>
            )}
          </div>
          {f.patch && (
            <pre data-testid={`what-changed-patch-${i}`} style={{
              margin: 0, padding: 10, fontSize: 11, lineHeight: 1.5,
              fontFamily: "'JetBrains Mono', monospace",
              background: "var(--bg)", overflow: "auto", maxHeight: 200,
              whiteSpace: "pre-wrap", wordBreak: "break-word",
            }}>
              {f.patch.split("\n").map((line, li) => (
                <div key={li} style={{
                  color: line.startsWith("+") && !line.startsWith("+++") ? "#4ade80"
                    : line.startsWith("-") && !line.startsWith("---") ? "var(--danger)"
                    : "var(--text-faint)",
                }}>{line}</div>
              ))}
            </pre>
          )}
        </div>
      ))}
      {data.more > 0 && (
        <div data-testid="what-changed-more" style={{ fontSize: 12, color: "var(--text-faint)", marginTop: 6 }}>
          {data.more} more file{data.more === 1 ? "" : "s"} —{" "}
          <span
            data-testid="what-changed-view-all-files-link"
            onClick={onOpenAllFiles}
            style={{ color: "var(--accent-2)", cursor: "pointer", textDecoration: "underline" }}
          >
            view in All files
          </span>
        </div>
      )}
    </div>
  );
}

export default function PreviewPanel({ blocks, onClose, activeProject, initialViewMode }) {
  const [activeTab, setActiveTab] = useState(0);
  const [viewMode, setViewMode] = useState(initialViewMode || "preview"); // 'preview' | 'code' | 'deploy'
  const [refreshKey, setRefreshKey] = useState(0);
  // 2026-06 · Rule 6 — Live-preview must never be a silent blank.
  // "loading" until the iframe's onLoad fires; a 10s timer flips to
  // "slow" (site may block embedding → offer open-in-new-tab).
  const [liveState, setLiveState] = useState("loading"); // loading|loaded|slow
  useEffect(() => {
    setLiveState("loading");
    const t = setTimeout(() => {
      setLiveState((s) => (s === "loading" ? "slow" : s));
    }, 10000);
    return () => clearTimeout(t);
  }, [refreshKey]);
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

  // Trust Surfaces Round (S2), 2026-08-29 — "What changed" default
  // view for Code mode. `codeSubTab`: "changed" (default) | "all".
  const [codeSubTab, setCodeSubTab] = useState("changed");
  const [whatChanged, setWhatChanged] = useState(null);
  const [whatChangedLoading, setWhatChangedLoading] = useState(false);
  const fetchWhatChanged = () => {
    if (!activeProject?.project_id) return;
    setWhatChangedLoading(true);
    api.get(`/cto/projects/${activeProject.project_id}/what-changed`)
      .then((r) => setWhatChanged(r.data))
      .catch(() => setWhatChanged({ ok: false, n_files: 0, headline: "Couldn't load what changed.", files: [], more: 0 }))
      .finally(() => setWhatChangedLoading(false));
  };

  // Trust Surfaces Round (S1-P1/P2/P3), 2026-08-29 — device toggle +
  // Live-now/After-fix tabs, remembered per project (localStorage).
  const projectKey = activeProject?.project_id || "none";
  const [device, setDevice] = useState(() => {
    try {
      return localStorage.getItem(`aurem_preview_device_${projectKey}`) || "phone";
    } catch { return "phone"; }
  });
  const [previewSubTab, setPreviewSubTab] = useState(() => {
    try {
      return localStorage.getItem(`aurem_preview_subtab_${projectKey}`) || "live";
    } catch { return "live"; }
  });
  const setDeviceRemembered = (d) => {
    setDevice(d);
    try { localStorage.setItem(`aurem_preview_device_${projectKey}`, d); } catch { /* noop */ }
  };
  // S4 — fire-and-forget preview-session ping (admin monitor tile:
  // last-24h sessions by device). Never blocks rendering.
  useEffect(() => {
    if (!activeProject?.project_id) return;
    api.post(`/cto/projects/${activeProject.project_id}/preview/session`, { device }).catch(() => {});
  }, [activeProject?.project_id, device]);  const setPreviewSubTabRemembered = (t) => {
    setPreviewSubTab(t);
    try { localStorage.setItem(`aurem_preview_subtab_${projectKey}`, t); } catch { /* noop */ }
  };
  const [pendingChange, setPendingChange] = useState(null); // {state, routes, files}
  const [pendingChangeLoading, setPendingChangeLoading] = useState(false);
  const [captures, setCaptures] = useState({}); // route -> {status:'idle'|'loading'|'ok'|'error', key, reason}

  useEffect(() => {
    if (!activeProject?.project_id) return;
    let alive = true;
    setPendingChangeLoading(true);
    api.get(`/cto/projects/${activeProject.project_id}/preview/pending-change`)
      .then((r) => { if (alive) setPendingChange(r.data); })
      .catch(() => { if (alive) setPendingChange({ state: "clean", routes: [], files: [] }); })
      .finally(() => { if (alive) setPendingChangeLoading(false); });
    return () => { alive = false; };
  }, [activeProject?.project_id]);

  const captureRoute = async (route) => {
    if (!activeProject?.project_id) return;
    setCaptures((c) => ({ ...c, [route]: { status: "loading" } }));
    try {
      const r = await api.get(`/cto/projects/${activeProject.project_id}/preview/capture`, {
        params: { route, device },
      });
      if (r.data?.ok) {
        setCaptures((c) => ({ ...c, [route]: { status: "ok", key: r.data.receipt_key } }));
      } else {
        setCaptures((c) => ({ ...c, [route]: { status: "error", reason: r.data?.reason || "unknown" } }));
      }
    } catch (e) {
      setCaptures((c) => ({ ...c, [route]: { status: "error", reason: e?.message || "request_failed" } }));
    }
  };

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
        [path]: { code: r.data?.content || "", truncated: !!r.data?.truncated },
      }));
    } catch (e) {
      const msg = e?.response?.data?.detail || e?.message || "Failed to load file.";
      setFileContents((m) => ({ ...m, [path]: { err: msg } }));
    }
  };

  // Iter 170c — Compute the actual blocks list. When the chat hasn't
  // produced real code yet and we've fetched the GitHub tree, append
  // each file as a lazy-load tab. This is how the `</> Code` toggle
  // becomes useful even before the user has shipped a task.
  const codebaseBlocks = (() => {
    if (!codebase?.files) return [];
    const extLang = {
      py: "python", js: "javascript", jsx: "jsx", ts: "typescript",
      tsx: "tsx", html: "html", htm: "html", css: "css", scss: "css",
      json: "json", yml: "yaml", yaml: "yaml", md: "markdown",
      sh: "bash", bash: "bash", sql: "sql", toml: "toml", env: "bash",
      go: "go", rs: "rust", java: "java", rb: "ruby", php: "php",
    };
    return codebase.files.map((f) => {
      const ext = (f.path.split(".").pop() || "").toLowerCase();
      return {
        lang: extLang[ext] || "text",
        label: f.path,
        isCodebase: true,
        size: f.size,
      };
    });
  })();

  const realBlocks = blocks || [];
  const onlyLiveOrPlaceholder = realBlocks.length === 0 || realBlocks.every(
    (b) => {
      const l = (b?.lang || "").toLowerCase();
      return l === "live_url" || l === "text";
    }
  );

  // Iter 212m-110 (BUG 10) — When the project has a deployed
  // `preview_url` but the chat hasn't emitted a live_url block yet,
  // synthesise one so the Preview tab opens on the Live Site
  // instead of falling through to README.md (the alphabetical-first
  // file in the codebase tree).
  //
  // Feb 2026 · Preview-Live-Site-shows-raw-text fix — founder report:
  // Preview tab showed the raw URL string instead of an embedded
  // iframe. Root cause: this synthesizer only checked
  // `activeProject.preview_url` (user-supplied), but for Personal
  // Track builds Vercel auto-populates `activeProject.live_url`
  // (routers/scaffold.py line 878) and never sets `preview_url`. So
  // the synthetic block was never created, no live_url tab existed,
  // and the panel fell through to a codebase file (or raw text).
  // Now we fall back through both fields, trim whitespace, and
  // require a valid http(s) scheme so we never hand the iframe a
  // half-typed value that renders as text.
  const hasLiveBlock = realBlocks.some(
    (b) => (b?.lang || "").toLowerCase() === "live_url"
  );
  const _rawLiveUrl = (
    activeProject?.preview_url
    || activeProject?.live_url
    || ""
  );
  const _cleanLiveUrl = String(_rawLiveUrl).trim();
  const _validLiveUrl = /^https?:\/\/[^\s]+$/i.test(_cleanLiveUrl)
    ? _cleanLiveUrl
    : "";
  const syntheticLive = (!hasLiveBlock && _validLiveUrl)
    ? [{
        lang: "live_url",
        label: "Live Site",
        code: _validLiveUrl,
        synthetic: true,
      }]
    : [];

  // Iter 212m-206 — Founder request: `</> Code` mode should ALWAYS
  // surface the live repo file tree, not just when the chat is empty.
  // Previously the codebase blocks were only merged in when
  // `onlyLiveOrPlaceholder` was true, so any chat with even one code
  // block hid the repo browser entirely.  Now: in Code view mode we
  // ALWAYS append the codebase tabs (once loaded) alongside whatever
  // the chat produced, so the user can toggle between "what ORA just
  // wrote" and "what's in the repo" without leaving Preview.
  const effectiveBlocks = (viewMode === "code" && codebaseBlocks.length > 0)
    ? [
        ...realBlocks.filter((b) => (b?.lang || "").toLowerCase() === "live_url"),
        ...syntheticLive,
        ...realBlocks.filter((b) => (b?.lang || "").toLowerCase() !== "live_url"),
        ...codebaseBlocks,
      ]
    : (syntheticLive.length > 0
        ? [...syntheticLive, ...realBlocks]
        : realBlocks);

  const block = effectiveBlocks[activeTab];
  const isRenderable = !!block && RENDERABLE.has((block.lang || "").toLowerCase());
  const isLiveUrl = (block?.lang || "").toLowerCase() === "live_url";

  // Iter 212m-110 (BUG 10) — When the panel first opens (or after the
  // effective-blocks list changes), prefer the "Live Site" tab over
  // any README.md / other codebase file. Previously we left activeTab
  // at the literal index 0, which for fresh repos meant README.md
  // (alphabetical-first file) was shown by default instead of the
  // deployed live URL. We only auto-jump if the user hasn't already
  // selected a non-default tab.
  const liveUrlIndex = effectiveBlocks.findIndex(
    (b) => (b?.lang || "").toLowerCase() === "live_url"
  );
  useEffect(() => {
    if (liveUrlIndex >= 0 && activeTab === 0 && liveUrlIndex !== 0) {
      setActiveTab(liveUrlIndex);
    }
  }, [liveUrlIndex, effectiveBlocks.length]);
  // Note: We deliberately don't useEffect to lazy-load file content
  // on tab change. The tab button's onClick triggers the fetch inline
  // — see the file-tabs map below.
  const effectiveCode = block?.isCodebase
    ? (fileContents[block.label]?.code || "")
    : (block?.code || "");
  const codebaseTabState = block?.isCodebase
    ? fileContents[block.label]
    : null;
  const srcDoc = useMemo(
    () => (isRenderable && !isLiveUrl && !block?.isCodebase ? buildIframeDoc(block) : ""),
    [block, isRenderable, isLiveUrl, refreshKey]
  );

  // Trust Surfaces Round (S2) — fetch on-demand per code sub-tab.
  useEffect(() => {
    if (viewMode !== "code" || !activeProject?.project_id) return;
    if (codeSubTab === "changed" && !whatChanged && !whatChangedLoading) {
      fetchWhatChanged();
    }
    if (codeSubTab === "all" && !codebase && !codebaseLoading) {
      fetchCodebaseTree();
    }
  }, [viewMode, codeSubTab, activeProject?.project_id]);

  const copyCode = () => {
    if (!effectiveCode) return;
    navigator.clipboard.writeText(effectiveCode);
    toast({ message: "Code copied.", kind: "success", duration: 1800 });
  };

  const canShowCodeToggle = (
    isRenderable
    || effectiveBlocks.some((b) => (b?.lang || "").toLowerCase() !== "live_url")
    || !!activeProject?.project_id  // can fetch repo on demand
  );

  return (
    <aside
      data-testid="preview-panel"
      style={{
        display: "flex", flexDirection: "column",
        height: "100vh", width: "100%",
        background: "var(--bg)",
        borderLeft: "1px solid var(--border)",
        overflow: "hidden",
      }}
    >
      {/* Header */}
      <div style={{
        display: "flex", alignItems: "center", gap: 8,
        padding: "8px 12px",
        borderBottom: "1px solid var(--border)",
        background: "var(--bg-elev)",
      }}>
        {viewMode === "preview" && (
          <span style={{
            color: "var(--accent-2)",
            fontSize: 10, letterSpacing: "0.18em",
            fontFamily: "'JetBrains Mono', monospace",
            textTransform: "uppercase", marginRight: 6,
            flexShrink: 0,
          }}>
            live preview
          </span>
        )}
        {viewMode === "deploy" && (
          <span style={{
            color: "var(--accent-2)",
            fontSize: 10, letterSpacing: "0.18em",
            fontFamily: "'JetBrains Mono', monospace",
            textTransform: "uppercase", marginRight: 6,
            flexShrink: 0,
          }}>
            🚀 deploy
          </span>
        )}

        {/* File tabs — hidden while Code mode is on the "What
            changed" default sub-view; shown for chat-block tabs in
            Preview mode, and for "All files" in Code mode. */}
        {!(viewMode === "code" && codeSubTab === "changed") && (
        <div style={{
          display: "flex", gap: 4, flex: 1,
          overflowX: "auto", minWidth: 0,
        }}>
          {effectiveBlocks.map((b, i) => (
            <button
              key={`${i}-${b.label || b.lang}`}
              data-testid={`preview-tab-${i}`}
              onClick={() => {
                setActiveTab(i);
                if (b?.isCodebase && b.label) fetchFileContent(b.label);
              }}
              title={b.label || filename(b, i)}
              style={{
                fontSize: 11, padding: "4px 10px", borderRadius: 4,
                background: activeTab === i ? "var(--accent)" : "transparent",
                color: activeTab === i ? "#0a0a0a" : "var(--text-dim)",
                border: `1px solid ${activeTab === i ? "var(--accent)" : "var(--border)"}`,
                cursor: "pointer",
                fontFamily: "'JetBrains Mono', monospace",
                whiteSpace: "nowrap",
                // Tabs must NOT shrink under flex pressure — otherwise
                // 20+ codebase tabs collapse to single characters
                // ("s(", ".a") as reported by users.
                flexShrink: 0,
                maxWidth: 200,
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              {filename(b, i)}
            </button>
          ))}
          {codebaseLoading && (
            <span style={{
              display: "inline-flex", alignItems: "center", gap: 6,
              fontSize: 11, color: "var(--text-faint)",
              padding: "4px 8px", flexShrink: 0,
            }}>
              <Loader2 size={11} style={{ animation: "spin 1s linear infinite" }} />
              loading repo…
            </span>
          )}
        </div>
        )}
        {viewMode === "code" && (
          <div data-testid="code-subtab-toggle" style={{ display: "flex", gap: 2, flexShrink: 0 }}>
            <button
              type="button"
              data-testid="code-subtab-changed"
              onClick={() => setCodeSubTab("changed")}
              style={{
                fontSize: 11, padding: "4px 10px", borderRadius: 6,
                background: codeSubTab === "changed" ? "var(--accent-2)" : "transparent",
                color: codeSubTab === "changed" ? "var(--bg)" : "var(--text-dim)",
                border: `1px solid ${codeSubTab === "changed" ? "var(--accent-2)" : "var(--border)"}`,
                cursor: "pointer",
              }}
            >
              What changed
            </button>
            <button
              type="button"
              data-testid="code-subtab-all"
              onClick={() => setCodeSubTab("all")}
              style={{
                fontSize: 11, padding: "4px 10px", borderRadius: 6,
                background: codeSubTab === "all" ? "var(--accent-2)" : "transparent",
                color: codeSubTab === "all" ? "var(--bg)" : "var(--text-dim)",
                border: `1px solid ${codeSubTab === "all" ? "var(--accent-2)" : "var(--border)"}`,
                cursor: "pointer",
              }}
            >
              All files
            </button>
          </div>
        )}
        {/* preview/code toggle — Iter 169: when toggling FROM the
            Live Site URL block INTO Code mode, auto-jump to the
            first actual code/file block so the user sees real code
            instead of the raw URL string.
            Trust Surfaces (S2), 2026-08-29 — Code mode now defaults
            to the "What changed" sub-view (codeSubTab state); the
            full repo browser moved behind the "All files" sub-tab
            and is fetched lazily only when that sub-tab is opened
            (see the effect above), not eagerly on every toggle. */}
        {canShowCodeToggle && (
          <button
            data-testid="preview-view-toggle"
            onClick={() => {
              setViewMode((v) => {
                // From deploy → return to preview default
                if (v === "deploy") return "preview";
                const next = v === "preview" ? "code" : "preview";
                if (next === "code" && isLiveUrl && codeSubTab === "all") {
                  // Jump to the first non-live_url tab if currently on one.
                  const idx = effectiveBlocks.findIndex(
                    (b) => (b?.lang || "").toLowerCase() !== "live_url"
                  );
                  if (idx >= 0) {
                    setActiveTab(idx);
                    const tgt = effectiveBlocks[idx];
                    if (tgt?.isCodebase && tgt.label) fetchFileContent(tgt.label);
                  }
                }
                if (next === "preview") {
                  const liveIdx = effectiveBlocks.findIndex(
                    (b) => (b?.lang || "").toLowerCase() === "live_url"
                  );
                  if (liveIdx >= 0) setActiveTab(liveIdx);
                }
                return next;
              });
            }}
            className="btn-ghost"
            style={{ padding: "4px 10px", fontSize: 11, flexShrink: 0 }}
            title="Toggle preview/code"
          >
            {viewMode === "preview"
              ? <><Code2 size={11} /> Code</>
              : viewMode === "code"
                ? <><Eye size={11} /> Preview</>
                : <><Eye size={11} /> Preview</>}
          </button>
        )}
        {activeProject?.project_id && (
          <button
            data-testid="preview-deploy-toggle"
            onClick={() => setViewMode((v) => (v === "deploy" ? "preview" : "deploy"))}
            className="btn-ghost"
            style={{
              padding: "4px 10px", fontSize: 11, flexShrink: 0,
              background: viewMode === "deploy" ? "var(--accent-2)" : "transparent",
              color:      viewMode === "deploy" ? "var(--bg)"        : "var(--text-dim)",
              border: `1px solid ${viewMode === "deploy" ? "var(--accent-2)" : "var(--border)"}`,
              borderRadius: 4,
            }}
            title="Deploy to your VPS"
          >
            <Rocket size={11} /> Deploy
          </button>
        )}
        <button
          data-testid="preview-close"
          onClick={onClose}
          title="Close preview"
          style={{
            background: "none", border: "none",
            color: "var(--text-faint)", cursor: "pointer",
            padding: 4, flexShrink: 0,
          }}
        >
          <X size={15} />
        </button>
      </div>

      {/* Trust Surfaces Round (S1-P1/P2) — device toggle + Live now /
          After fix tabs. Only relevant when viewing the live site. */}
      {viewMode === "preview" && isLiveUrl && (
        <div
          data-testid="preview-live-toolbar"
          style={{
            display: "flex", alignItems: "center", gap: 10,
            padding: "6px 12px",
            borderBottom: "1px solid var(--border)",
            background: "var(--bg-elev)",
            flexWrap: "wrap",
          }}
        >
          <div data-testid="preview-device-toggle" style={{ display: "flex", gap: 2 }}>
            {Object.entries(DEVICE_FRAMES).map(([key, cfg]) => {
              const Icon = key === "phone" ? Smartphone : key === "tablet" ? Tablet : Monitor;
              const active = device === key;
              return (
                <button
                  key={key}
                  type="button"
                  data-testid={`preview-device-${key}`}
                  onClick={() => setDeviceRemembered(key)}
                  title={cfg.label}
                  style={{
                    display: "inline-flex", alignItems: "center", gap: 4,
                    fontSize: 11, padding: "4px 9px", borderRadius: 999,
                    background: active ? "var(--accent)" : "transparent",
                    color: active ? "#0a0a0a" : "var(--text-dim)",
                    border: `1px solid ${active ? "var(--accent)" : "var(--border)"}`,
                    cursor: "pointer",
                  }}
                >
                  <Icon size={11} /> {cfg.label}
                </button>
              );
            })}
          </div>
          <div style={{ width: 1, height: 16, background: "var(--border)" }} />
          <div data-testid="preview-subtab-toggle" style={{ display: "flex", gap: 2 }}>
            <button
              type="button"
              data-testid="preview-subtab-live"
              onClick={() => setPreviewSubTabRemembered("live")}
              style={{
                fontSize: 11, padding: "4px 10px", borderRadius: 6,
                background: previewSubTab === "live" ? "var(--accent-2)" : "transparent",
                color: previewSubTab === "live" ? "var(--bg)" : "var(--text-dim)",
                border: `1px solid ${previewSubTab === "live" ? "var(--accent-2)" : "var(--border)"}`,
                cursor: "pointer",
              }}
            >
              Live now
            </button>
            <button
              type="button"
              data-testid="preview-subtab-afterfix"
              onClick={() => setPreviewSubTabRemembered("afterfix")}
              style={{
                fontSize: 11, padding: "4px 10px", borderRadius: 6,
                background: previewSubTab === "afterfix" ? "var(--accent-2)" : "transparent",
                color: previewSubTab === "afterfix" ? "var(--bg)" : "var(--text-dim)",
                border: `1px solid ${previewSubTab === "afterfix" ? "var(--accent-2)" : "var(--border)"}`,
                cursor: "pointer",
              }}
            >
              After fix
            </button>
          </div>
          {previewSubTab === "live" && pendingChange && pendingChange.state !== "clean" && (
            <span
              data-testid="preview-nothing-changed-line"
              style={{ fontSize: 11, color: "var(--text-faint)", marginLeft: "auto" }}
            >
              Nothing on your live site has changed yet. It changes only when you choose &quot;Go live&quot;.
            </span>
          )}
        </div>
      )}

      {/* Body */}
      <div style={{ flex: 1, overflow: "hidden", minHeight: 0 }}>
        {viewMode === "deploy" ? (
          <DeployPanel activeProject={activeProject} />
        ) : viewMode === "code" && codeSubTab === "changed" ? (
          <WhatChangedView
            data={whatChanged}
            loading={whatChangedLoading}
            onOpenAllFiles={() => setCodeSubTab("all")}
          />
        ) : viewMode === "preview" && isLiveUrl && previewSubTab === "afterfix" ? (
          <div data-testid="preview-afterfix-view" style={{ padding: 20, overflowY: "auto", height: "100%" }}>
            {pendingChangeLoading ? (
              <div style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--text-faint)", fontSize: 12 }}>
                <Loader2 size={13} style={{ animation: "spin 1s linear infinite" }} /> Checking for pending changes…
              </div>
            ) : pendingChange?.state === "pending" ? (
              <div data-testid="preview-afterfix-pending" style={{ fontSize: 13, color: "var(--text)" }}>
                A fix is being worked on right now — check back shortly.
              </div>
            ) : pendingChange?.state === "shipped_not_deployed" ? (
              <div data-testid="preview-afterfix-shipped">
                <div style={{ fontSize: 13, color: "var(--text)", marginBottom: 4 }}>
                  Your fix shipped to GitHub but hasn&apos;t gone live yet.
                </div>
                <div style={{ fontSize: 11, color: "var(--text-faint)", marginBottom: 14 }}>
                  The full fixed site appears here when you go live.
                </div>
                {(pendingChange.routes || []).map((route) => {
                  const cap = captures[route] || { status: "idle" };
                  return (
                    <div key={route} data-testid={`preview-afterfix-route-${route}`} style={{
                      border: "1px solid var(--border)", borderRadius: 8,
                      padding: 12, marginBottom: 10,
                    }}>
                      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
                        <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 12 }}>{route}</span>
                        <button
                          type="button"
                          data-testid={`preview-afterfix-capture-btn-${route}`}
                          onClick={() => captureRoute(route)}
                          disabled={cap.status === "loading"}
                          className="btn-ghost"
                          style={{ fontSize: 11, padding: "3px 10px", display: "inline-flex", alignItems: "center", gap: 4 }}
                        >
                          {cap.status === "loading"
                            ? <><Loader2 size={11} style={{ animation: "spin 1s linear infinite" }} /> Capturing…</>
                            : <><Camera size={11} /> See today&apos;s page</>}
                        </button>
                      </div>
                      {cap.status === "ok" && cap.key && (
                        <img
                          data-testid={`preview-afterfix-image-${route}`}
                          src={`${api.defaults.baseURL}/cto/projects/${activeProject.project_id}/preview/receipt/${cap.key}`}
                          alt={`Live page at ${route}`}
                          style={{ maxWidth: "100%", borderRadius: 6, border: "1px solid var(--border)" }}
                        />
                      )}
                      {cap.status === "error" && (
                        <div data-testid={`preview-afterfix-error-${route}`} style={{ fontSize: 11, color: "var(--danger, #ef4444)" }}>
                          Couldn&apos;t capture that page right now ({cap.reason}). <a
                            href={(activeProject?.preview_url || "").replace(/\/$/, "") + route}
                            target="_blank" rel="noopener noreferrer" style={{ color: "#60a5fa" }}
                          >Open it directly ↗</a>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            ) : (
              <div data-testid="preview-afterfix-clean" style={{ fontSize: 13, color: "var(--text-faint)" }}>
                No pending changes — you&apos;re showing the live site.
              </div>
            )}
          </div>
        ) : viewMode === "preview" && isLiveUrl ? (
          <div
            data-testid="preview-device-frame"
            style={{
              width: "100%", height: "100%",
              display: "flex", justifyContent: "center",
              background: device === "desktop" ? "transparent" : "var(--bg-elev)",
              overflow: "auto",
            }}
          >
          <div style={{ position: "relative", width: DEVICE_FRAMES[device].width, maxWidth: "100%", height: "100%" }}>
            <iframe
              key={`liveurl-${block.code}-${refreshKey}`}
              data-testid="preview-iframe-live"
              src={String(block.code || "").trim()}
              title="live-site"
              onLoad={() => setLiveState("loaded")}
              sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-modals"
              style={{
                width: "100%", height: "100%",
                border: device === "desktop" ? "none" : "1px solid var(--border)",
                background: "white",
              }}
            />
            {liveState !== "loaded" && (
              <div
                data-testid="preview-live-state-overlay"
                style={{
                  position: "absolute", inset: 0, zIndex: 2,
                  display: "flex", flexDirection: "column",
                  alignItems: "center", justifyContent: "center", gap: 10,
                  background: "var(--bg, #111)", color: "var(--text-dim, #bbb)",
                  fontSize: 13, textAlign: "center", padding: 24,
                }}
              >
                {liveState === "loading" ? (
                  <>
                    <span className="animate-spin" style={{
                      width: 18, height: 18, borderRadius: "50%",
                      border: "2px solid rgba(255,102,8,0.25)",
                      borderTopColor: "#FF6608", display: "inline-block",
                    }} />
                    <span>Loading live preview…</span>
                    <span style={{ fontSize: 11, color: "var(--text-faint, #777)", wordBreak: "break-all" }}>
                      {String(block.code || "").trim()}
                    </span>
                  </>
                ) : (
                  <>
                    <span style={{ fontWeight: 700, color: "#fbbf24" }}>
                      Still loading after 10 seconds
                    </span>
                    <span style={{ maxWidth: 420 }}>
                      The site may be slow, offline, or blocking embedded
                      previews (X-Frame-Options). It is NOT necessarily broken.
                    </span>
                    <a
                      data-testid="preview-live-open-newtab"
                      href={String(block.code || "").trim()}
                      target="_blank" rel="noopener noreferrer"
                      style={{ color: "#60a5fa", fontWeight: 600 }}
                    >
                      Open in a new tab ↗
                    </a>
                    <button
                      data-testid="preview-live-retry-btn"
                      onClick={() => setRefreshKey((k) => k + 1)}
                      className="btn-ghost"
                      style={{ fontSize: 12, padding: "4px 12px" }}
                    >
                      Retry
                    </button>
                  </>
                )}
              </div>
            )}
          </div>
          </div>
        ) : effectiveBlocks.length === 0 ? (
          // 2026-08-31 (R5b) — root-cause fix: a project with no
          // preview_url/live_url yet AND no chat-produced code block
          // fell through every branch below to an EMPTY <pre>, which
          // read as a dead blank panel. Show an honest empty state
          // instead.
          <div
            data-testid="preview-empty-state"
            style={{
              display: "flex", flexDirection: "column",
              alignItems: "center", justifyContent: "center",
              height: "100%", padding: 24, textAlign: "center",
              color: "var(--text-faint)", gap: 8,
            }}
          >
            <Eye size={22} style={{ opacity: 0.5 }} />
            <span style={{ fontSize: 13, color: "var(--text-dim)" }}>
              No preview yet
            </span>
            <span style={{ fontSize: 12, maxWidth: 320 }}>
              Ask ORA to make a change to your site, or connect your
              live site, and it will show up here.
            </span>
          </div>
        ) : viewMode === "preview" && isRenderable && !block?.isCodebase ? (
          <iframe
            key={`iframe-${activeTab}-${refreshKey}`}
            data-testid="preview-iframe"
            srcDoc={srcDoc}
            sandbox="allow-scripts"
            title="preview"
            style={{
              width: "100%", height: "100%",
              border: "none", background: "white",
            }}
          />
        ) : codebaseErr && viewMode === "code" ? (
          <div
            data-testid="preview-codebase-err"
            style={{
              padding: 24,
              color: "var(--text)",
              display: "flex",
              flexDirection: "column",
              alignItems: "flex-start",
              gap: 12,
              maxWidth: 480,
            }}
          >
            {/* Iter 212m-204 — Actionable Code-browse error state.
                Previously we just rendered the raw backend message
                which read as a dead-end.  Now we surface the specific
                token-related failures with a "Reconnect GitHub" CTA
                that opens the same NewUserWizard the user already
                knows (via the aurem:open-add-repo event Dashboard
                listens to). */}
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <span style={{
                width: 32, height: 32, borderRadius: "50%",
                background: "rgba(239,68,68,0.12)",
                border: "1px solid rgba(239,68,68,0.5)",
                display: "inline-flex", alignItems: "center", justifyContent: "center",
                fontSize: 14, color: "var(--danger, #ef4444)",
              }}>⚠</span>
              <div style={{
                fontFamily: '"JetBrains Mono", monospace',
                fontSize: 11, letterSpacing: "0.14em",
                color: "var(--danger, #ef4444)",
              }}>
                CODEBASE UNAVAILABLE
              </div>
            </div>
            <div style={{ fontSize: 13, lineHeight: 1.55, color: "var(--text)" }}>
              {/^GitHub not connected/i.test(codebaseErr) ? (
                <>This project doesn&apos;t have a GitHub PAT with <b>contents:read</b> saved.
                Reconnect it to browse the live repo files here.</>
              ) : /GitHub PAT invalid|401/i.test(codebaseErr) ? (
                <>Your GitHub PAT expired or was revoked. Reconnect it
                (with <b>contents:read &amp; write</b>) to resume the codebase browser.</>
              ) : (
                <>{codebaseErr}</>
              )}
            </div>
            <button
              type="button"
              data-testid="preview-codebase-reconnect"
              onClick={() => {
                // Fire a global event that Dashboard picks up to open
                // the AddProjectWizard.  Same code path the sidebar
                // "+ Add Repository" button uses, so behaviour stays
                // consistent whether user reconnects from the sidebar
                // or from this inline CTA.
                window.dispatchEvent(new CustomEvent("aurem:open-add-repo", {
                  detail: { source: "preview-codebase-err" },
                }));
              }}
              style={{
                padding: "8px 16px",
                background: "var(--accent, #f59e0b)",
                color: "#000",
                border: "none",
                borderRadius: 8,
                fontSize: 12,
                fontWeight: 700,
                fontFamily: '"JetBrains Mono", monospace',
                letterSpacing: "0.05em",
                cursor: "pointer",
              }}
            >
              RECONNECT GITHUB →
            </button>
          </div>
        ) : block?.isCodebase && codebaseTabState?.loading ? (
          <div
            data-testid="preview-codebase-loading"
            style={{
              display: "flex", alignItems: "center", gap: 10,
              padding: 20, color: "var(--text-faint)", fontSize: 12,
            }}
          >
            <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} />
            loading {block.label}…
          </div>
        ) : block?.isCodebase && codebaseTabState?.err ? (
          <div
            data-testid="preview-codebase-file-err"
            style={{
              padding: 20, color: "var(--danger, #ef4444)", fontSize: 12,
            }}
          >
            ⚠ {codebaseTabState.err}
          </div>
        ) : (
          <pre
            data-testid="preview-code"
            style={{
              margin: 0, padding: 16,
              overflow: "auto", height: "100%",
              fontSize: 12, lineHeight: 1.5,
              fontFamily: "'JetBrains Mono', monospace",
              color: "var(--text)",
              background: "var(--bg)",
              whiteSpace: "pre",
            }}
          >
            <code>{effectiveCode}</code>
          </pre>
        )}
      </div>

      {/* Footer */}
      {viewMode !== "deploy" && (
      <div style={{
        display: "flex", gap: 8,
        padding: "6px 12px",
        borderTop: "1px solid var(--border)",
        background: "var(--bg-elev)",
        fontSize: 11,
      }}>
        <button
          data-testid="preview-copy"
          className="btn-ghost"
          onClick={copyCode}
          style={{ padding: "4px 10px", fontSize: 11 }}
        >
          <Copy size={11} /> Copy
        </button>
        {isRenderable && (
          <button
            data-testid="preview-refresh"
            className="btn-ghost"
            onClick={() => setRefreshKey((k) => k + 1)}
            style={{ padding: "4px 10px", fontSize: 11 }}
          >
            <RefreshCw size={11} /> Refresh
          </button>
        )}
        {isLiveUrl && (
          <a
            data-testid="preview-open-newtab"
            href={block.code}
            target="_blank"
            rel="noreferrer"
            className="btn-ghost"
            style={{ padding: "4px 10px", fontSize: 11, textDecoration: "none" }}
            title="Open in new tab"
          >
            <ExternalLink size={11} /> Open
          </a>
        )}
        <span style={{
          marginLeft: "auto", color: "var(--text-faint)",
          fontFamily: "'JetBrains Mono', monospace",
        }}>
          {block?.isCodebase && codebase
            ? <>{codebase.owner}/{codebase.repo}@{codebase.branch} · {effectiveCode.length} chars{codebaseTabState?.truncated ? " (truncated)" : ""}</>
            : <>lang: {block?.lang || "—"} · {effectiveCode.length || 0} chars</>
          }
        </span>
      </div>
      )}
    </aside>
  );
}
