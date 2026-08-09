/**
 * pages/OraDirect.jsx — Iter 212m-242
 *
 * Light-cream themed public ORA Chat at /ora — matches BuildHome
 * aesthetic (cream background, white cards, warm accent).
 *
 * Interaction model:
 *   - Empty state → hero heading + big centered input card + suggestion pills
 *   - After first message → input slides to bottom-center; messages
 *     stream in the scrollable area above
 *
 * Auth: PIN pad (4 digits) → mints 7-day admin JWT via
 * /api/aurem-dev/ora-chat/pin-login. Reuses existing session /
 * streaming / budget / house-rules backend.
 */
import React, { useEffect, useRef, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { Lock, Settings, LogOut, ArrowUp, RefreshCw, Zap, Clock, Plus, Square, Copy, Play, Paperclip, X, FileText, Image as ImageIcon, Loader2 } from "lucide-react";
import { api, setToken, getToken } from "../lib/api";
import OraChatHouseRulesPanel from "../components/OraChatHouseRulesPanel";
// Feb 2026 · Phase 1 (Streamdown) — see OraChatDrawer.jsx for the full
// rationale. XSS-safe defaults, streaming caret, GFM/LaTeX/Mermaid.
import { Streamdown } from "streamdown";
// Feb 2026 · Phase 2 — Security-hardened live preview drawer for
// HTML/JSX/JS code blocks. See OraPreviewPanel.jsx for the full
// security contract (sandbox=allow-scripts only, strict CSP,
// Vanguard-gated, 300ms debounce, 16MB cap).
import OraPreviewPanel from "../components/OraPreviewPanel";

// Iter 212m-264 · Feb 2026 — Renderable code-fence detector.
// Returns the FIRST renderable ``` fence found in a markdown string
// (`{ lang, code }`) or null.  We only surface a "Preview" affordance
// for langs the sandboxed iframe can execute — HTML/JSX/TSX/JS.
const _RENDERABLE_LANGS = new Set([
  "html", "htm", "jsx", "tsx", "js", "javascript",
]);
function findRenderableBlock(md) {
  if (!md || typeof md !== "string") return null;
  const re = /```([a-zA-Z0-9_+-]+)\n([\s\S]*?)```/g;
  let m;
  while ((m = re.exec(md)) !== null) {
    const lang = (m[1] || "").toLowerCase();
    if (_RENDERABLE_LANGS.has(lang)) {
      return { lang, code: m[2] || "" };
    }
  }
  return null;
}

const BASE = `${process.env.REACT_APP_BACKEND_URL}/api/aurem-dev/ora-chat`;
const PIN_LENGTH = 4;

// Iter 212m-263 · Feb 2026 — Claude-style layout: single readable
// column, generous white-space, no hard-edged card around assistant
// replies. Width matches Claude.ai chat body (~760px on desktop),
// gracefully collapses on tablet/mobile.
function useContainerWidth() {
  const [w, setW] = useState(() =>
    typeof window !== "undefined" ? window.innerWidth : 1440);
  useEffect(() => {
    const on = () => setW(window.innerWidth);
    window.addEventListener("resize", on);
    return () => window.removeEventListener("resize", on);
  }, []);
  if (w < 768)   return { pct: "100%", maxW: "100%" };
  if (w < 1024)  return { pct: "92%",  maxW: "760px" };
  return             { pct: "780px", maxW: "780px" };
}

// Iter 212m-263 · Feb 2026 — dev-mode gate. Internal routing/config
// metadata (route name, temperature, per-message cost pill) is hidden
// from the default chat view — same policy as the earlier "(via
// /loop/active fallback)" leak we cleaned up. Add `?debug=1` to the
// URL to bring them back for founder QA sessions.
function useDebugMode() {
  if (typeof window === "undefined") return false;
  try {
    const p = new URLSearchParams(window.location.search);
    return p.get("debug") === "1";
  } catch { return false; }
}

// ── Palette (matches /app/frontend/src/pages/personal/BuildHome.jsx) ──
const PAL = {
  bg:         "#FAFAF5",
  card:       "#FFFFFF",
  text:       "#1C1C19",
  muted:      "#6B6B63",
  faint:      "#8B8B7D",
  chip:       "#F4F3EE",
  chipHover:  "#EDECE5",
  border:     "#E5E5DF",
  accent:     "#D56A4F",
  accentBg:   "rgba(224,122,95,0.10)",
  bubbleUser: "#EDECE5",
  bubbleAsst: "#FFFFFF",
};

// Iter 212m-250 — warm plaster-textured background photograph for
// both the PIN gate + the chat surface. Kept as a module-level const
// so the image URL is co-located with the palette. Soft cream tint
// on top (linear-gradient) preserves text readability without
// killing the texture.
const ORA_BG_IMG =
  "https://customer-assets-39nsmqrw.emergentagent.net/job_launch-pad-237/artifacts/bu3orhz1_Screenshot%202026-07-17%20002657.png";
const ORA_BG_STYLE = {
  backgroundImage:
    "linear-gradient(rgba(250,250,245,0.55), rgba(250,250,245,0.72)), " +
    `url("${ORA_BG_IMG}")`,
  backgroundSize: "cover",
  backgroundPosition: "center",
  backgroundRepeat: "no-repeat",
  backgroundAttachment: "fixed",
};

export default function OraDirect() {
  const [authorized, setAuthed] = useState(!!getToken());

  useEffect(() => {
    if (getToken()) {
      api.get("/auth/me").then(() => setAuthed(true))
                          .catch(() => setToken(null));
    }
  }, []);

  return authorized ? <ChatShell onLogout={() => { setToken(null); setAuthed(false); }} />
                     : <PinPad onSuccess={() => setAuthed(true)} />;
}

// ────────────────────────────────────────────────────────────────────
// PIN pad (light theme)
// ────────────────────────────────────────────────────────────────────
function PinPad({ onSuccess }) {
  const [pin, setPin]   = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr]   = useState(null);

  const submit = async (p) => {
    if (p.length !== PIN_LENGTH || busy) return;
    setBusy(true); setErr(null);
    try {
      const r = await api.post("/ora-chat/pin-login", { pin: p });
      setToken(r.data.token);
      onSuccess();
    } catch (e) {
      const d = e?.response?.data?.detail || e?.response?.data;
      if (d?.error === "too_many_attempts") setErr(d.message);
      else if (d?.error === "invalid_pin") setErr(`Wrong PIN. ${d.attempts_remaining} left.`);
      else setErr("Login failed. Check connection.");
      setPin("");
    } finally { setBusy(false); }
  };
  const press = (d) => { if (pin.length >= PIN_LENGTH || busy) return;
                          const n = pin + d; setPin(n);
                          if (n.length === PIN_LENGTH) submit(n); };

  return (
    <div data-testid="ora-direct-pin"
         style={{ minHeight: "100dvh", background: PAL.bg, color: PAL.text,
                    ...ORA_BG_STYLE,
                    display: "flex", alignItems: "center", justifyContent: "center",
                    padding: "20px 20px 40px",
                    // iOS Safari cuts off content behind the bottom nav bar
                    // — dvh + safe-area padding keeps the pad fully tappable.
                    paddingBottom: "max(40px, env(safe-area-inset-bottom))",
                    WebkitTapHighlightColor: "transparent",
                    fontFamily: "-apple-system, BlinkMacSystemFont, 'Inter', sans-serif" }}>
      <div style={{ maxWidth: 380, width: "100%", textAlign: "center" }}>
        <div style={{ width: 56, height: 56, borderRadius: 14, background: PAL.accentBg,
                        border: `1px solid ${PAL.accent}33`, margin: "0 auto 22px",
                        display: "flex", alignItems: "center", justifyContent: "center" }}>
          <Lock size={22} color={PAL.accent} strokeWidth={1.8} />
        </div>
        <div style={{ fontSize: 22, fontWeight: 700, marginBottom: 6,
                        letterSpacing: -0.5 }}>ORA Chat</div>
        <div style={{ fontSize: 13, color: PAL.muted, marginBottom: 32 }}>
          Enter your 4-digit PIN
        </div>
        <div style={{ display: "flex", justifyContent: "center", gap: 14, marginBottom: 30 }}>
          {[0,1,2,3].map(i => (
            <div key={i} data-testid={`pin-dot-${i}`}
                 style={{ width: 14, height: 14, borderRadius: "50%",
                            background: pin.length > i ? PAL.accent : "transparent",
                            border: `2px solid ${pin.length > i ? PAL.accent : PAL.border}`,
                            transition: "all 0.1s" }} />
          ))}
        </div>
        {err && (
          <div data-testid="pin-error" style={{ fontSize: 12, color: "#c94a37",
                                                   marginBottom: 20, minHeight: 16 }}>{err}</div>
        )}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 10,
                       maxWidth: 280, margin: "0 auto" }}>
          {["1","2","3","4","5","6","7","8","9"].map(d => (
            <PadKey key={d} label={d} testId={`pin-key-${d}`}
                     onClick={() => press(d)} disabled={busy} />
          ))}
          <PadKey label="C" testId="pin-key-clear" onClick={() => setPin("")} disabled={busy} muted />
          <PadKey label="0" testId="pin-key-0" onClick={() => press("0")} disabled={busy} />
          <PadKey label="⌫" testId="pin-key-back"
                   onClick={() => setPin(pin.slice(0, -1))} disabled={busy} muted />
        </div>
        <div style={{ marginTop: 26, fontSize: 10, color: PAL.faint }}>
          5 wrong attempts per hour · Session lasts 7 days
        </div>
      </div>
    </div>
  );
}
function PadKey({ label, onClick, disabled, muted, testId }) {
  // Iter 212m-244 — Mobile PIN reliability fix. Two problems seen on
  // iOS Safari + Chrome Android:
  //   (a) 300ms tap delay before click fires — touchAction:manipulation
  //       eliminates it.
  //   (b) The onMouseDown transform interfered with the click event
  //       being registered on some touch devices — switched to :active
  //       via inline CSS and dropped the mouse-only handlers.
  return (
    <button type="button" data-testid={testId} onClick={onClick} disabled={disabled}
            style={{ aspectRatio: "1 / 1", fontSize: 22,
                       fontWeight: 500, color: muted ? PAL.muted : PAL.text,
                       background: PAL.card,
                       border: `1px solid ${PAL.border}`, borderRadius: 12,
                       cursor: disabled ? "not-allowed" : "pointer",
                       transition: "background 120ms",
                       touchAction: "manipulation",
                       WebkitTapHighlightColor: "transparent",
                       userSelect: "none",
                       minHeight: 56,   // hits Apple/Google 44px+ tap-target guidance
                       minWidth: 56 }}
            onPointerDown={(e) => e.currentTarget.style.background = PAL.chip}
            onPointerUp={(e)   => e.currentTarget.style.background = PAL.card}
            onPointerLeave={(e)=> e.currentTarget.style.background = PAL.card}>
      {label}
    </button>
  );
}

// ────────────────────────────────────────────────────────────────────
// Chat shell (post-auth) — BuildHome-inspired aesthetic
// ────────────────────────────────────────────────────────────────────
const SUGGESTIONS = [
  "Aaj ka top AI news",
  "Explain quantum computing simply",
  "Draft a launch tweet for AUREM",
  "/users-today",
];

function ChatShell({ onLogout }) {
  const containerW = useContainerWidth();
  const debug = useDebugMode();
  const [previewBlock, setPreviewBlock] = useState(null); // {code, lang} | null
  // Iter 212m-266 · Feb 2026 · Phase 4 — file attachments.  Uploaded
  // via POST /ora-chat/upload which runs the vision LLM (images) or
  // MarkItDown (docs) server-side and returns markdown the LLM can
  // read.  Each attachment carries its own state so the founder sees
  // per-file progress + errors instead of one blob spinner.
  //   Shape: { id, file, kind: 'image'|'doc', status: 'uploading'|'ready'|'error',
  //            markdown, error, filename, size }
  const [attachments, setAttachments] = useState([]);
  const [tierError, setTierError] = useState(null);

  const uploadOne = async (file) => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    setAttachments(a => [...a, {
      id, filename: file.name, size: file.size,
      status: "uploading", markdown: "", kind: "doc",
    }]);
    const fd = new FormData();
    fd.append("file", file);
    try {
      // Guard 18 · Feb 2026 — 60s AbortSignal.timeout so a hung
      // /upload (Cloudflare edge stall, backend R2 upload deadlock,
      // etc.) can't leave the attachment stuck in "uploading" forever.
      // 60s is comfortable for the largest file we accept.
      const r = await fetch(`${BASE}/upload`, {
        method: "POST",
        headers: { "Authorization": `Bearer ${getToken()}` },
        body: fd,
        signal: AbortSignal.timeout(60_000),
      });
      if (r.status === 402) {
        const d = await r.json().catch(() => ({}));
        const detail = d?.detail || d;
        setAttachments(a => a.filter(x => x.id !== id));
        setTierError({
          kind: "tier_locked",
          message: detail?.message
            || "File attachments are a Pro / Team feature.",
          upgrade_url: detail?.upgrade_url || "/pricing",
        });
        return;
      }
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        const detail = d?.detail || d;
        setAttachments(a => a.map(x => x.id === id
          ? { ...x, status: "error",
              error: detail?.message || `HTTP ${r.status}` }
          : x));
        return;
      }
      const j = await r.json();
      setAttachments(a => a.map(x => x.id === id
        ? { ...x, status: "ready", markdown: j.markdown,
            kind: j.kind, filename: j.filename }
        : x));
    } catch (e) {
      // Guard 18 · surface AbortError (timeout) with a distinct
      // user-facing message so the founder knows to retry vs a
      // generic "upload_failed". Any error still marks the attachment
      // as error so it doesn't sit in "uploading" state forever.
      const msg = e?.name === "TimeoutError" || e?.name === "AbortError"
        ? "Upload timed out after 60s — try again or use a smaller file"
        : (e?.message || "upload_failed");
      setAttachments(a => a.map(x => x.id === id
        ? { ...x, status: "error", error: msg }
        : x));
    }
  };
  const removeAttachment = (id) =>
    setAttachments(a => a.filter(x => x.id !== id));
  const clearAttachments = () => setAttachments([]);
  const [sessionId, setSessionId] = useState(null);
  const [messages, setMessages]   = useState([]);
  const [input, setInput]         = useState("");
  const [sending, setSending]     = useState(false);
  const [budget, setBudget]       = useState(null);
  const [stream, setStream]       = useState({ buf: "", route: null, model: null });
  const [showRules, setShowRules] = useState(false);
  const [showPicker, setShowPicker] = useState(false);
  const [recentSessions, setRecent] = useState([]);
  const listRef = useRef(null);
  const abortRef = useRef(null);

  // Bootstrap
  useEffect(() => {
    (async () => {
      try {
        const r = await api.get("/ora-chat/sessions");
        const rows = r.data.sessions || [];
        setRecent(rows);
        // Iter 212m-262 — Always auto-create a fresh session on load
        // so the founder can start typing immediately. Older chats
        // stay accessible via the "Continue last chat →" link in the
        // hero AND via the Clock icon in the header. No pop-up modal
        // on entry.
        const c = await api.post("/ora-chat/sessions", { title: "Quick chat" });
        setSessionId(c.data.session.session_id);
      } catch { /* token invalid — fall through */ }
    })();
  }, []);

  const refreshBudget = async () => {
    try { const r = await api.get("/ora-chat/usage"); setBudget(r.data.budget); }
    catch { /* ignore */ }
  };
  useEffect(() => { refreshBudget(); }, []);

  useEffect(() => {
    if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [messages, stream.buf]);

  const startNewSession = async () => {
    try {
      const c = await api.post("/ora-chat/sessions", { title: "Quick chat" });
      setSessionId(c.data.session.session_id);
      setMessages([]); setShowPicker(false);
    } catch { /* ignore */ }
  };
  const openSession = async (sid) => {
    try {
      const r = await api.get(`/ora-chat/sessions/${sid}`);
      const s = r.data.session;
      setMessages((s.messages || []).map(m => ({
        role: m.role, content: m.content, route: m.route, model: m.model,
        ungrounded: m.ungrounded,
        review_caveats: m.review?.caveats,
      })));
      setSessionId(sid); setShowPicker(false);
    } catch { /* ignore */ }
  };

  // Iter 386 · Session 2.7 · Fix A — shared image-slash runner.
  //
  // The typed `/image <prompt>` intercept AND the tap-to-run buttons
  // rendered by `OraSlashCmdButtons` both call this. Keeping the fetch
  // + result-shaping in ONE place means the two entry points can NEVER
  // drift apart (a bug in one could otherwise leak stale caches or a
  // stale prompt into the LLM context).
  const _runImageSlashPrompt = useCallback(
    async (prompt, fromTypedCommand = false, userTypedText = null) => {
      if (!prompt || !sessionId || sending) return;
      const displayed = userTypedText
        || (fromTypedCommand ? `/image ${prompt}` : `/image ${prompt}`);
      setMessages(m => [...m, { role: "user", content: displayed }]);
      setSending(true);
      try {
        // Guard 18 · Feb 2026 — 90s AbortSignal.timeout for image
        // generation. gpt-image-1 typically responds in 5-20s but
        // long prompts + retries can stretch to 60s legitimately;
        // 90s guards against a fully-hung LLM call while giving
        // real requests headroom. `finally: setSending(false)`
        // below ensures the send button re-enables even on timeout.
        const r = await fetch(`${BASE}/image-generate`, {
          method: "POST",
          headers: { "Content-Type": "application/json",
                     "Authorization": `Bearer ${getToken()}` },
          body: JSON.stringify({ prompt }),
          signal: AbortSignal.timeout(90_000),
        });
        const j = await r.json().catch(() => ({}));
        if (!r.ok) {
          const detail = j?.detail || j;
          const kind   = detail?.error || `HTTP_${r.status}`;
          const msg    = detail?.message
            || `Image generation failed (${r.status}).`;
          setMessages(m => [...m, {
            role: "assistant",
            content: `**Image generation blocked** · \`${kind}\`\n\n${msg}`,
            isError: true,
          }]);
        } else {
          const dataUrl =
            `data:${j.mime || "image/png"};base64,${j.image_base64}`;
          const quotaLine = j.user_month_status
            ? `_${j.user_month_status.used}/${j.user_month_status.cap} images used this month · $${(j.daily_status?.spent_usd || 0).toFixed(3)} / $${j.daily_status?.cap_usd?.toFixed?.(2) || "3.00"} today._`
            : "";
          setMessages(m => [...m, {
            role: "assistant",
            content:
              `![${prompt.slice(0, 80)}](${dataUrl})\n\n${quotaLine}`,
            imageGen: true,
          }]);
        }
      } catch (e) {
        // Guard 18 · surface AbortError (timeout) with a distinct
        // user-facing message. `finally` below still re-enables
        // the send button so the UI is never stuck on a hang.
        const isTimeout = e?.name === "TimeoutError" || e?.name === "AbortError";
        const msg = isTimeout
          ? "Image generation timed out after 90s — please try again"
          : (e?.message || "network error");
        setMessages(m => [...m, {
          role: "assistant",
          content: `**Image generation failed** — ${msg}`,
          isError: true,
        }]);
      } finally {
        setSending(false);
      }
    },
    [BASE, sessionId, sending],
  );

  // Expose the runner via a stable window bridge so the Bubble-level
  // `OraSlashCmdButtons` can invoke it without having to thread the
  // callback through Bubble's props (which are shared across every
  // message and would otherwise force a re-render whenever `sending`
  // toggles).
  useEffect(() => {
    window.__oraRunImageSlash = _runImageSlashPrompt;
    return () => { delete window.__oraRunImageSlash; };
  }, [_runImageSlashPrompt]);

  const send = async () => {
    const text = input.trim();
    // Only "ready" attachments count towards the send.  Uploading /
    // errored ones stay behind so the founder can retry / remove.
    const readyAttachments = attachments.filter(a => a.status === "ready");
    if ((!text && readyAttachments.length === 0) || !sessionId || sending) return;

    // ── Phase 5 · Feb 2026 — client-side /image slash-command ─────
    //   `/image <prompt>` short-circuits to the JSON image-generate
    //   endpoint (founder-only, gpt-image-1 low, $3/day + 10/mo caps).
    //   We handle it in the client so the SSE pipeline stays a pure
    //   text stream — no binary blobs threaded through delta events.
    //
    //   Iter 386 · Session 2.7 · Fix A — factored the fetch into a
    //   closure `_runImageSlashPrompt` so the OraSlashCmdButtons
    //   component can share the exact same code path when a user
    //   taps ORA's inline `/image ...` recommendation. Zero duplicate
    //   logic; button and typed-command produce identical results.
    const _imageSlash = text.match(/^\/image(?:-gen)?\s+([\s\S]+)$/i);
    if (_imageSlash) {
      const prompt = _imageSlash[1].trim();
      setInput("");
      await _runImageSlashPrompt(prompt, /*fromTypedCommand=*/ true, text);
      return;
    }

    // Iter 212m-266 · Feb 2026 · Phase 4 — prepend each attachment's
    // markdown as a fenced ATTACHMENT block so the LLM can distinguish
    // "user typed this" from "here's the doc/image content".  The
    // visible user turn still shows the raw text they typed.
    let outbound = text;
    if (readyAttachments.length) {
      const blocks = readyAttachments.map(a => {
        const label = a.kind === "image"
          ? `IMAGE ATTACHMENT — ${a.filename}`
          : `DOCUMENT ATTACHMENT — ${a.filename}`;
        return `--- ${label} ---\n${a.markdown}\n--- end ${label} ---`;
      }).join("\n\n");
      outbound = text ? `${blocks}\n\n${text}` : blocks;
    }
    setInput("");
    // Show the user their raw text + a compact attachment summary.
    const userDisplay = readyAttachments.length
      ? `${text}${text ? "\n\n" : ""}📎 ${readyAttachments.map(a => a.filename).join(", ")}`
      : text;
    setMessages(m => [...m, { role: "user", content: userDisplay }]);
    clearAttachments();
    setStream({ buf: "", route: null, model: null });
    setSending(true);
    // Iter 212m-246 — AbortController lets the Stop button interrupt
    // an in-flight SSE stream cleanly.
    const controller = new AbortController();
    abortRef.current = controller;
    let clientTz = "";
    try { clientTz = Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch { /* */ }
    try {
      const res = await fetch(`${BASE}/message`, {
        method: "POST",
        signal: controller.signal,
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${getToken()}`,
          "Accept": "text/event-stream",
          ...(clientTz ? { "X-Client-TZ": clientTz } : {}),
        },
        body: JSON.stringify({ session_id: sessionId, content: outbound }),
      });
      if (res.status === 402) {
        const b = await res.json().catch(() => ({}));
        setMessages(m => [...m, { role: "assistant",
                                     content: b?.detail?.message || "Budget paused.",
                                     isError: true }]);
        return;
      }
      if (!res.ok || !res.body) {
        setMessages(m => [...m, { role: "assistant",
                                     content: `Error ${res.status}. Try again.`,
                                     isError: true }]);
        return;
      }
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buffer = "", buf = "", routeMeta = {};
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += dec.decode(value, { stream: true });
        const parts = buffer.split(/\r?\n\r?\n/);
        buffer = parts.pop() || "";
        for (const chunk of parts) {
          let evtType = "message", dataStr = "";
          for (const ln of chunk.split(/\r?\n/)) {
            if (ln.startsWith("event:")) evtType = ln.slice(6).trim();
            else if (ln.startsWith("data:")) dataStr += ln.slice(5).trim();
          }
          if (!dataStr) continue;
          let obj = {}; try { obj = JSON.parse(dataStr); } catch { continue; }
          if (evtType === "route" || obj.type === "route") {
            // Iter 212m-265 · Feb 2026 — spread the previous
            // routeMeta so a second `route` event (deep-research
            // emits one at start AND one after tool orchestration
            // with the final `sources` list) does NOT wipe fields
            // set by earlier events, most importantly the Phase 3
            // `intent` verdict which sits between the two routes.
            routeMeta = { ...routeMeta,
                           route: obj.route, model: obj.model, temperature: obj.temperature,
                           sources: obj.sources, sources_fired: obj.sources_fired,
                           downgraded: obj.downgraded };
            setStream(s => ({ ...s, ...routeMeta }));
          } else if (evtType === "delta" || obj.type === "delta") {
            buf += obj.content || "";
            setStream(s => ({ ...s, buf }));
          } else if (evtType === "slash_result" || obj.type === "slash_result") {
            // Iter 212m-263 · Feb 2026 — slash-command results whose
            // `value` is already a preformatted string (e.g. /repo-tree
            // returning a tree with REAL "\n" newlines) must NOT be
            // JSON.stringify'd — that escapes every newline into a
            // literal "\\n" glyph and produces one unreadable wall
            // of text. Render strings verbatim inside a fenced code
            // block so Streamdown preserves the whitespace; only
            // objects/arrays fall through to JSON.stringify.
            const _v = obj.result?.value;
            if (typeof _v === "string") {
              buf += `\n\`\`\`\n${_v}\n\`\`\`\n`;
            } else {
              buf += `\n\`\`\`json\n${JSON.stringify(_v, null, 2)}\n\`\`\`\n`;
            }
            setStream(s => ({ ...s, buf }));
          } else if (evtType === "review_status" || obj.type === "review_status") {
            // Iter 268 — HIGH_STAKES turn being buffered + reviewed.
            setStream(s => ({ ...s, reviewing: true }));
          } else if (evtType === "intent" || obj.type === "intent") {
            // Iter 212m-265 · Feb 2026 · Phase 3 — two-layer intent
            // verdict from the backend router.  Store on the route
            // meta so it persists onto the final assistant turn.
            routeMeta = {
              ...routeMeta,
              intent: obj.intent,
              intent_source: obj.source,
              intent_matches: obj.matches,
              intent_meta: obj.meta,
            };
            setStream(s => ({ ...s, intent: obj.intent,
                                     intent_source: obj.source }));
          } else if (evtType === "review_caveat" || obj.type === "review_caveat") {
            routeMeta = { ...routeMeta, review_caveats: obj.quotes || [] };
          } else if (evtType === "grounding_warning" || obj.type === "grounding_warning") {
            // Iter 264 Fix A4 — deterministic validator flagged
            // fabricated citations in this reply.
            routeMeta = { ...routeMeta, ungrounded: obj.ungrounded || [] };
            setStream(s => ({ ...s, ungrounded: obj.ungrounded || [] }));
          } else if (evtType === "final" || obj.type === "final") {
            setMessages(m => [...m, { role: "assistant", content: buf,
                                         ...routeMeta,
                                         cost_usd: obj.cost_usd }]);
            setStream({ buf: "", route: null, model: null });
          }
        }
      }
    } catch (e) {
      if (e.name === "AbortError") {
        // User pressed Stop — preserve partial buffer as an
        // interrupted assistant turn instead of erroring.
        setMessages(m => (stream.buf
          ? [...m, { role: "assistant", content: stream.buf,
                       interrupted: true, ...stream }]
          : m));
      } else {
        setMessages(m => [...m, { role: "assistant",
                                     content: `Network error: ${e.message}`,
                                     isError: true }]);
      }
    } finally { setSending(false); abortRef.current = null; refreshBudget(); }
  };

  const stop = () => {
    if (abortRef.current) {
      abortRef.current.abort();
    }
  };

  const hasChat = messages.length > 0 || stream.buf;

  return (
    <div style={{ minHeight: "100vh", height: "100vh",
                    background: PAL.bg, color: PAL.text,
                    ...ORA_BG_STYLE,
                    display: "flex", flexDirection: "column",
                    fontFamily: "-apple-system, BlinkMacSystemFont, 'Inter', sans-serif" }}>
      {/* Header */}
      <header style={{ display: "flex", alignItems: "center",
                          padding: "16px 32px",
                          borderBottom: `1px solid ${PAL.border}`,
                          background: PAL.card }}>
        <div style={{ fontWeight: 700, letterSpacing: 2, fontSize: 15 }}>AUREM</div>
        <div style={{ flex: 1 }} />
        {budget && debug && (
          <div data-testid="ora-budget-pill"
               title={`Today: $${budget.day_spent_usd} · Month: $${budget.month_spent_usd}`}
               style={{ fontSize: 11, padding: "4px 10px", borderRadius: 999,
                          background: PAL.chip, color: PAL.muted,
                          fontFamily: "ui-monospace, monospace", marginRight: 12 }}>
            ${(budget.day_spent_usd || 0).toFixed(4)} / ${budget.day_cap_usd}
          </div>
        )}
        <IconBtn testId="ora-history" onClick={() => setShowPicker(true)}><Clock size={16} /></IconBtn>
        <IconBtn testId="ora-settings" onClick={() => setShowRules(true)}><Settings size={16} /></IconBtn>
        <IconBtn testId="ora-logout" onClick={onLogout}><LogOut size={16} /></IconBtn>
      </header>

      {/* Main area */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column",
                     overflow: "hidden", position: "relative" }}>
        {!hasChat && (
          // ── EMPTY STATE — centered hero ─────────────────────────
          <div style={{ flex: 1, display: "flex", flexDirection: "column",
                          alignItems: "center", justifyContent: "center",
                          padding: "20px", overflow: "auto" }}>
            <div style={{ maxWidth: containerW.maxW, width: containerW.pct,
                            textAlign: "center" }}>
              <div style={{ display: "inline-block", padding: "5px 12px",
                              background: PAL.accentBg, color: PAL.accent,
                              borderRadius: 999, fontSize: 12, fontWeight: 500,
                              marginBottom: 24 }}>
                <Zap size={12} style={{ display: "inline", marginRight: 4, verticalAlign: -1 }} />
                ORA Chat
              </div>
              <h1 style={{ fontSize: "clamp(32px, 5vw, 52px)",
                             fontWeight: 700, lineHeight: 1.1,
                             margin: "0 0 20px", letterSpacing: -1 }}>
                How can I help?
              </h1>
              <p style={{ fontSize: 15, color: PAL.muted, marginBottom: 40,
                            maxWidth: 480, marginLeft: "auto", marginRight: "auto" }}>
                General chat, deep research, or type a <code style={{ color: PAL.accent }}>/</code> for
                deterministic slash-commands.
              </p>
              <InputCard input={input} setInput={setInput} onSend={send}
                          sending={sending} onStop={stop} large
                          attachments={attachments}
                          onFilesPicked={(fs) => fs.forEach(uploadOne)}
                          onRemoveAttachment={removeAttachment}
                          tierError={tierError}
                          onDismissTierError={() => setTierError(null)} />
              <div style={{ marginTop: 32, display: "flex", flexWrap: "wrap",
                              gap: 10, justifyContent: "center" }}>
                {SUGGESTIONS.map(s => (
                  <button key={s} data-testid={`suggestion-${s}`}
                          onClick={() => { setInput(s); }}
                          style={{ padding: "8px 16px", borderRadius: 999,
                                     background: PAL.chip, border: "none",
                                     color: PAL.muted, fontSize: 13,
                                     cursor: "pointer", fontFamily: "inherit" }}
                          onMouseEnter={(e) => e.currentTarget.style.background = PAL.chipHover}
                          onMouseLeave={(e) => e.currentTarget.style.background = PAL.chip}>
                    {s}
                  </button>
                ))}
              </div>
              {/* Iter 212m-262 — Subtle single-line "Continue last chat" link.
                  Only rendered when a recent session exists AND we're in a
                  brand-new (empty) session. Founder-friendly: one click to
                  resume, no popup. Full history stays behind the Clock
                  icon in the header. */}
              {recentSessions.length > 0 && (
                <div style={{ marginTop: 20, textAlign: "center" }}>
                  <button type="button"
                          data-testid="ora-continue-last"
                          onClick={() => openSession(recentSessions[0].session_id)}
                          style={{ padding: "6px 12px", background: "transparent",
                                     border: "none", color: PAL.muted, fontSize: 13,
                                     cursor: "pointer", fontFamily: "inherit",
                                     textDecoration: "underline",
                                     textUnderlineOffset: 3,
                                     textDecorationColor: PAL.border,
                                     transition: "color 120ms" }}
                          onMouseEnter={(e) => e.currentTarget.style.color = PAL.text}
                          onMouseLeave={(e) => e.currentTarget.style.color = PAL.muted}>
                    Continue last chat →
                  </button>
                </div>
              )}
            </div>
          </div>
        )}

        {hasChat && (
          // ── CHAT STATE — messages scroll, input pinned bottom ────
          <>
            <div ref={listRef} data-testid="ora-messages"
                 style={{ flex: 1, overflow: "auto",
                            // Iter 212m-247 — bottom pad must clear the
                            // widened 5-row input (form ~120 px + wrapper
                            // ~40 px + gradient fade + safe-area). Was
                            // 140 px for the old 3-row input which hid
                            // ~60 px of the latest reply behind the
                            // input panel.
                            padding: "24px 20px min(240px, 32vh)" }}>
              <div style={{ maxWidth: containerW.maxW, width: containerW.pct,
                              margin: "0 auto",
                              display: "flex", flexDirection: "column", gap: 28 }}>
                {messages.map((m, i) => <Bubble key={i} m={m} debug={debug}
                                                       onOpenPreview={setPreviewBlock} />)}
                {sending && !stream.buf && <ThinkingDots label={stream.reviewing ? "verifying high-stakes response…" : undefined} />}
                {stream.buf && (
                  <Bubble m={{ role: "assistant", content: stream.buf,
                                 ...stream, streaming: true }} debug={debug}
                          onOpenPreview={setPreviewBlock} />
                )}
              </div>
            </div>
            <div style={{ position: "absolute", bottom: 0, left: 0, right: 0,
                            padding: "16px 20px 20px",
                            background: `linear-gradient(to top, ${PAL.bg} 60%, transparent)` }}>
              <div style={{ maxWidth: containerW.maxW, width: containerW.pct,
                              margin: "0 auto" }}>
                <InputCard input={input} setInput={setInput} onSend={send}
                            sending={sending} onStop={stop}
                            attachments={attachments}
                            onFilesPicked={(fs) => fs.forEach(uploadOne)}
                            onRemoveAttachment={removeAttachment}
                            tierError={tierError}
                            onDismissTierError={() => setTierError(null)} />
              </div>
            </div>
          </>
        )}
      </div>

      {/* Sessions picker overlay */}
      {showPicker && (
        <PickerModal recent={recentSessions} onClose={() => setShowPicker(false)}
                     onNew={startNewSession} onOpen={openSession} />
      )}
      {/* House rules panel */}
      {showRules && (
        <OraChatHouseRulesPanel onClose={() => setShowRules(false)} />
      )}

      {/* Iter 212m-264 · Feb 2026 · Phase 2 — Security-hardened live
          preview drawer.  Slides in from the right when the user
          clicks "Preview" on an assistant message that contains a
          renderable code block. See OraPreviewPanel.jsx for the
          full security contract. */}
      {previewBlock && (
        <OraPreviewPanel code={previewBlock.code}
                          lang={previewBlock.lang}
                          onClose={() => setPreviewBlock(null)} />
      )}
    </div>
  );
}

function IconBtn({ children, onClick, testId }) {
  return (
    <button type="button" onClick={onClick} data-testid={testId}
            style={{ padding: 8, marginLeft: 4, background: "transparent",
                       border: "none", color: PAL.muted, cursor: "pointer",
                       borderRadius: 8, display: "flex", alignItems: "center" }}
            onMouseEnter={(e) => e.currentTarget.style.background = PAL.chip}
            onMouseLeave={(e) => e.currentTarget.style.background = "transparent"}>
      {children}
    </button>
  );
}


// Iter 212m-246 — colored 3-dot pulse shown between "user just sent"
// and "first delta arrives" so the UI never feels frozen. Each dot
// uses a different accent color and staggers its bounce so the row
// reads as a purposeful "thinking" cue, not a spinner.
function ThinkingDots({ label }) {
  return (
    <div data-testid="ora-thinking-dots"
         style={{ alignSelf: "flex-start",
                    padding: "12px 16px",
                    borderRadius: 14,
                    background: PAL.bubbleAsst,
                    border: `1px solid ${PAL.border}`,
                    display: "flex", gap: 6, alignItems: "center" }}>
      <style>{`
        @keyframes ora-pulse {
          0%, 80%, 100% { transform: translateY(0);   opacity: 0.35; }
          40%           { transform: translateY(-4px); opacity: 1; }
        }
      `}</style>
      {[
        { c: "#D56A4F", d: "0ms"   }, // terracotta
        { c: "#81B29A", d: "160ms" }, // moss
        { c: "#3B82F6", d: "320ms" }, // blue
      ].map((dot, i) => (
        <span key={i}
              style={{ width: 8, height: 8, borderRadius: 999,
                         background: dot.c, display: "inline-block",
                         animation: `ora-pulse 1.2s ease-in-out ${dot.d} infinite` }} />
      ))}
      <span style={{ fontSize: 11, color: PAL.faint, marginLeft: 4,
                       fontStyle: "italic" }}>{label || "thinking…"}</span>
    </div>
  );
}

function InputCard({ input, setInput, onSend, sending, onStop, large = false,
                       attachments = [], onFilesPicked, onRemoveAttachment,
                       tierError, onDismissTierError }) {
  // Iter 212m-266 · Feb 2026 · Phase 4 — drag-drop composer.
  //   · Paperclip button + hidden <input type=file multiple> for click
  //     uploads.
  //   · Drop zone: outer <form> catches dragover/drop for drag-and-drop.
  //   · Attachment pills stack above the textarea with per-file status
  //     (uploading spinner / ready checkmark + filename / error) and
  //     an X to remove. Ready-only attachments are sent.
  //   · 402 tier_locked errors surface as an inline upgrade banner
  //     above the composer — non-dismissible except via the X so the
  //     founder can't miss the paywall.
  const fileInputRef = useRef(null);
  const [dragActive, setDragActive] = useState(false);
  const hasReady = attachments.some(a => a.status === "ready");
  const canSend = !!input.trim() || hasReady;

  const openPicker = () => fileInputRef.current?.click();
  const onPick = (e) => {
    const files = Array.from(e.target.files || []);
    if (files.length && onFilesPicked) onFilesPicked(files);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };
  const onDragOver = (e) => {
    if (e.dataTransfer?.types?.includes("Files")) {
      e.preventDefault();
      setDragActive(true);
    }
  };
  const onDragLeave = () => setDragActive(false);
  const onDrop = (e) => {
    e.preventDefault();
    setDragActive(false);
    const files = Array.from(e.dataTransfer?.files || []);
    if (files.length && onFilesPicked) onFilesPicked(files);
  };

  return (
    <div>
      {/* Tier-lock upgrade banner — non-dismissible clue except X */}
      {tierError && (
        <div data-testid="ora-attach-tier-locked"
             style={{ marginBottom: 10, padding: "10px 14px",
                        borderRadius: 12, background: "#FBF1DC",
                        border: "1px solid #E4C26B",
                        color: "#7A5A0F", fontSize: 12.5,
                        display: "flex", gap: 10, alignItems: "center" }}>
          <Paperclip size={14} />
          <span style={{ flex: 1 }}>{tierError.message}</span>
          <a href={tierError.upgrade_url || "/pricing"}
             data-testid="ora-attach-upgrade-link"
             style={{ padding: "4px 10px", borderRadius: 999,
                        background: "#7A5A0F", color: "#fff",
                        textDecoration: "none", fontSize: 11, fontWeight: 600 }}>
            Upgrade
          </a>
          <button type="button" onClick={onDismissTierError}
                  data-testid="ora-attach-tier-dismiss"
                  style={{ background: "transparent", border: "none",
                             color: "#7A5A0F", cursor: "pointer",
                             display: "flex", alignItems: "center" }}>
            <X size={14} />
          </button>
        </div>
      )}
      <form onSubmit={(e) => { e.preventDefault(); if (!sending && canSend) onSend(); }}
            onDragOver={onDragOver}
            onDragLeave={onDragLeave}
            onDrop={onDrop}
            data-testid="ora-input-form"
            style={{ background: PAL.card,
                       border: `1px solid ${dragActive ? PAL.accent : PAL.border}`,
                       borderRadius: 16,
                       padding: "14px 16px",
                       boxShadow: dragActive
                         ? "0 0 0 3px rgba(213,106,79,0.15)"
                         : "0 4px 20px rgba(0,0,0,0.04)",
                       transition: "border-color 120ms, box-shadow 120ms",
                       position: "relative" }}>
        {/* Attachment pills row */}
        {attachments.length > 0 && (
          <div data-testid="ora-attachment-list"
               style={{ display: "flex", flexWrap: "wrap", gap: 6,
                          marginBottom: 8 }}>
            {attachments.map(a => (
              <AttachmentPill key={a.id} a={a}
                              onRemove={() => onRemoveAttachment(a.id)} />
            ))}
          </div>
        )}
        {/* Drag-active hint (visible only when actively dragging) */}
        {dragActive && (
          <div data-testid="ora-drop-hint"
               style={{ position: "absolute", inset: 6, pointerEvents: "none",
                          borderRadius: 12, background: "rgba(213,106,79,0.06)",
                          border: `2px dashed ${PAL.accent}`,
                          display: "flex", alignItems: "center",
                          justifyContent: "center",
                          color: PAL.accent, fontSize: 13, fontWeight: 500,
                          zIndex: 2 }}>
            Drop files to attach
          </div>
        )}
        <div style={{ display: "flex", alignItems: "flex-end", gap: 10 }}>
          <textarea
            data-testid="ora-input"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (!sending && canSend) onSend(); } }}
            placeholder={large ? "Ask ORA... (or /repo-tree, /find, /read)" : "Message ORA..."}
            rows={5}
            style={{ flex: 1, border: "none", outline: "none",
                       fontFamily: "inherit", fontSize: 15,
                       color: PAL.text, background: "transparent",
                       resize: "none", padding: "4px 4px",
                       lineHeight: 1.55,
                       minHeight: `calc(1.55em * 5)`,
                       maxHeight: `calc(1.55em * 12)`,
                       overflowY: "auto",
                       opacity: sending ? 0.65 : 1 }}
            disabled={sending}
          />
          <div style={{ display: "flex", flexDirection: "column", gap: 6,
                          alignItems: "center", flexShrink: 0 }}>
            <button type="button" onClick={openPicker}
                    data-testid="ora-attach-btn"
                    title="Attach a file (Pro / Team)"
                    disabled={sending}
                    style={{ width: 38, height: 38, borderRadius: 999,
                               background: "transparent",
                               border: `1px solid ${PAL.border}`,
                               color: PAL.muted, cursor: sending ? "not-allowed" : "pointer",
                               display: "flex", alignItems: "center",
                               justifyContent: "center",
                               transition: "background 120ms" }}
                    onMouseEnter={(e) => { if (!sending) e.currentTarget.style.background = PAL.chip; }}
                    onMouseLeave={(e) => e.currentTarget.style.background = "transparent"}>
              <Paperclip size={15} />
            </button>
            <input ref={fileInputRef} type="file" multiple
                   data-testid="ora-file-input"
                   onChange={onPick}
                   style={{ display: "none" }}
                   /* Iter 212m-266b · Feb 2026 — founder-tightened
                      Phase 4 whitelist: exactly PNG, JPG, WEBP, PDF.
                      Server enforces the same set with a 415 on any
                      mismatch, so this `accept` is a native-UI hint,
                      not a security boundary. */
                   accept=".png,.jpg,.jpeg,.webp,.pdf,image/png,image/jpeg,image/webp,application/pdf" />
            {sending ? (
              <button type="button" onClick={onStop} data-testid="ora-stop"
                      title="Stop generating"
                      style={{ width: 38, height: 38, borderRadius: 999,
                                 background: "#E5E5DF", color: PAL.text,
                                 border: "none", cursor: "pointer",
                                 display: "flex", alignItems: "center", justifyContent: "center" }}>
                <Square size={14} fill={PAL.text} />
              </button>
            ) : (
              <button type="submit" data-testid="ora-send" disabled={!canSend}
                      style={{ width: 38, height: 38, borderRadius: 999,
                                 background: canSend ? PAL.accent : PAL.chip,
                                 color: canSend ? "#fff" : PAL.faint,
                                 border: "none",
                                 cursor: canSend ? "pointer" : "not-allowed",
                                 display: "flex", alignItems: "center", justifyContent: "center",
                                 transition: "background 120ms",
                                 touchAction: "manipulation" }}>
                <ArrowUp size={16} />
              </button>
            )}
          </div>
        </div>
      </form>
    </div>
  );
}

// Iter 386 · Session 2.7 · Fix A — Slash-command button extractor.
//
// ORA's response (per the CORE_SAFETY_RULES capability-discipline
// clause) writes recommended slash-commands as an inline-code block
// like `` `/image <prompt>` ``. This helper regex-matches those,
// dedupes, and returns them as an array so the Bubble component
// can render a "Run" button per suggestion — turning ORA's advice
// into a real one-click action, matching the Claude/Gemini pattern.
//
// Scoped to `/image` for now (Phase 5's client-side intercept).
// Extending to `/read /find /defs /repo-tree /loop-stats` follows
// the same shape but they're already tap-typable via /help so lower
// urgency. See PRD.md for the roadmap.
const _ORA_IMAGE_CMD_RE = /`\s*\/image(?:-gen)?\s+([^`\n]{3,200}?)\s*`/gi;

function _extractImageSlashPrompts(content) {
  if (!content) return [];
  const out = [];
  const seen = new Set();
  let m;
  // Reset lastIndex — regex is /g, module-scoped, so mid-stream calls
  // could otherwise skip matches on the same content.
  _ORA_IMAGE_CMD_RE.lastIndex = 0;
  while ((m = _ORA_IMAGE_CMD_RE.exec(content)) !== null) {
    const p = (m[1] || "").trim();
    if (p && !seen.has(p)) {
      seen.add(p);
      out.push(p);
    }
    if (out.length >= 3) break;      // cap: at most 3 buttons per turn
  }
  return out;
}

function OraSlashCmdButtons({ content, onRun }) {
  const prompts = _extractImageSlashPrompts(content);
  if (prompts.length === 0) return null;
  return (
    <div data-testid="ora-slash-cmd-buttons"
         style={{ marginTop: 10, display: "flex", flexDirection: "column",
                    gap: 6, alignItems: "flex-start" }}>
      {prompts.map((p, i) => (
        <button
          key={i}
          type="button"
          data-testid={`ora-run-image-slash-${i}`}
          onClick={() => onRun?.(p)}
          style={{
            padding: "6px 12px", borderRadius: 8, border: "1px solid #D0D5DD",
            background: "#F9FAFB", color: "#1D2939",
            fontSize: 12, fontWeight: 500, cursor: "pointer",
            fontFamily: "ui-sans-serif, system-ui",
            display: "inline-flex", alignItems: "center", gap: 6,
          }}
          title={`Generate: ${p}`}
        >
          <span style={{ fontFamily: "ui-monospace, monospace",
                         fontSize: 11, color: "#667085" }}>▸</span>
          Generate image: <em style={{ color: "#475467",
                                        fontWeight: 400,
                                        maxWidth: 320,
                                        overflow: "hidden",
                                        textOverflow: "ellipsis",
                                        whiteSpace: "nowrap" }}>
            {p.length > 60 ? p.slice(0, 60) + "…" : p}
          </em>
        </button>
      ))}
    </div>
  );
}


// Iter 212m-267 · Feb 2026 · Phase 5 — render an image-gen assistant
// message: parse the first `![alt](data:…)` line into a real <img>,
// keep the remaining italic quota-line rendered by Streamdown so it
// still looks like the rest of the chat.
function ImageGenBubbleContent({ content }) {
  // Iter 212m-267b · Feb 2026 — Defense-in-depth on the Streamdown
  // bypass.  The security envelope is:
  //   ① `m.imageGen === true` is set ONLY in the /image-generate
  //      success branch (OraDirect.jsx ~line 415);
  //   ② the data URL is constructed client-side from `j.mime`, which
  //      the backend hardcodes to "image/png" (image_gen.py);
  //   ③ this regex — the LAST line of defence — refuses anything
  //      that isn't `data:image/(png|jpeg|jpg|webp);base64,…`.
  //   The three checks together mean: even if a future refactor
  //   accidentally sets imageGen:true on a malicious message, or the
  //   backend returns a bad mime, this component still renders
  //   NOTHING outside the tight image allow-list — falling back to
  //   Streamdown's full harden for any mismatch.
  const IMG_DATA_URI_RE =
    /^!\[([^\]]*)\]\((data:image\/(?:png|jpe?g|webp);base64,[A-Za-z0-9+/=\r\n]+)\)\n\n?([\s\S]*)$/;
  const m = (content || "").match(IMG_DATA_URI_RE);
  if (!m) {
    // Any content that doesn't cleanly match `data:image/*;base64,…`
    // falls back to normal Streamdown-hardened rendering.
    return (
      <div className="ora-md" data-testid="ora-msg-md">
        <Streamdown>{content || ""}</Streamdown>
      </div>
    );
  }
  const [, alt, dataUrl, tail] = m;
  return (
    <div data-testid="ora-msg-md" className="ora-md">
      <img src={dataUrl} alt={alt} data-testid="ora-gen-image"
           style={{ maxWidth: "100%", borderRadius: 12,
                      border: "1px solid #E5E5DF",
                      display: "block" }} />
      {tail && (
        <div style={{ marginTop: 8, fontSize: 11,
                        color: "#8B8B7D", fontStyle: "italic" }}>
          <Streamdown>{tail}</Streamdown>
        </div>
      )}
    </div>
  );
}

// Iter 212m-266 · Feb 2026 · Phase 4 — per-attachment pill.  Icon
// swaps by kind (image vs doc), a small spinner shows while
// uploading, error banner replaces filename when the upload fails.
function AttachmentPill({ a, onRemove }) {
  const Icon = a.kind === "image" ? ImageIcon : FileText;
  const isErr = a.status === "error";
  const bg = isErr ? "#fdeeea" : PAL.chip;
  const fg = isErr ? "#8C2E1C" : PAL.text;
  return (
    <div data-testid={`ora-attachment-pill-${a.status}`}
         style={{ display: "inline-flex", alignItems: "center", gap: 6,
                    padding: "5px 10px", borderRadius: 999,
                    background: bg, color: fg, fontSize: 12,
                    border: `1px solid ${isErr ? "#E4C26B" : PAL.border}`,
                    maxWidth: 320 }}>
      {a.status === "uploading"
        ? <Loader2 size={12} className="animate-spin" />
        : <Icon size={12} />}
      <span style={{ overflow: "hidden", textOverflow: "ellipsis",
                       whiteSpace: "nowrap", maxWidth: 200 }}>
        {isErr ? `Failed: ${a.error || "upload_failed"}` : a.filename}
      </span>
      {a.status === "ready" && (
        <span style={{ fontSize: 10, color: PAL.faint }}>
          ({(a.size / 1024).toFixed(0)} KB)
        </span>
      )}
      <button type="button" onClick={onRemove}
              data-testid={`ora-attachment-remove-${a.id}`}
              style={{ background: "transparent", border: "none",
                         color: PAL.faint, cursor: "pointer",
                         display: "flex", alignItems: "center", padding: 0 }}>
        <X size={12} />
      </button>
    </div>
  );
}

function Bubble({ m, debug = false, onOpenPreview }) {
  const isUser = m.role === "user";
  // Iter 212m-258 — copy toggle on bottom-outer of every bubble.
  // 2s "Copied!" flash confirms the clipboard write, then reverts.
  const [copied, setCopied] = useState(false);
  const doCopy = async () => {
    try {
      await navigator.clipboard.writeText(m.content || "");
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Legacy fallback for older browsers / non-secure contexts.
      const ta = document.createElement("textarea");
      ta.value = m.content || "";
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand("copy"); setCopied(true);
             setTimeout(() => setCopied(false), 1500); } catch { /* ignore */ }
      document.body.removeChild(ta);
    }
  };
  const showCopy = !m.streaming && (m.content || "").trim().length > 0;

  // Iter 212m-263 · Feb 2026 — Claude-style bubble treatment:
  //  · Assistant messages: no border, no card background — text
  //    flows directly on the page. Full column width. Generous
  //    vertical padding + line-height for readable prose density.
  //  · User messages: keep the subtle warm-chip background so the
  //    two roles remain distinguishable, but drop to ~75% width so
  //    long user paragraphs align right without dominating.
  //  · Error bubbles retain a tinted background for signal.
  const bubbleStyle = isUser
    ? {
        padding: "12px 16px",
        borderRadius: 14,
        background: m.isError ? "#fdeeea" : PAL.bubbleUser,
        border: "none",
        color: PAL.text,
        fontSize: 15,
        lineHeight: 1.65,
        wordBreak: "break-word",
      }
    : {
        padding: "4px 0",
        borderRadius: 0,
        background: m.isError ? "#fdeeea" : "transparent",
        border: "none",
        color: PAL.text,
        fontSize: 15.5,
        lineHeight: 1.75,
        wordBreak: "break-word",
      };

  return (
    <div style={{ alignSelf: isUser ? "flex-end" : "stretch",
                    maxWidth: isUser ? "75%" : "100%",
                    width: isUser ? "auto" : "100%",
                    display: "flex",
                    flexDirection: "column",
                    alignItems: isUser ? "flex-end" : "flex-start",
                    marginBottom: 6 }}>
      <div data-testid={`ora-msg-${m.role}`} style={bubbleStyle}>
        {/* Phase 1 · Streamdown — user turn stays plain text so the
            user's raw prompt is never interpreted as HTML. Assistant
            turn renders markdown (GFM + code + LaTeX + inline images)
            with XSS-safe defaults from Streamdown. */}
        {isUser ? (
          <span style={{ whiteSpace: "pre-wrap" }}>{m.content}</span>
        ) : m.imageGen ? (
          // Iter 212m-267 · Feb 2026 · Phase 5 — image-generation
          // messages carry a `data:image/png;base64,…` URL that
          // Streamdown's harden step refuses to render (its default
          // `linkSafety` treats data: as suspicious even with
          // allowDataImages:true).  We bypass Streamdown for this
          // one message type and drop the raw <img> directly — the
          // URL is server-generated, never user-typed, so the
          // safety envelope stays intact.
          <ImageGenBubbleContent content={m.content} />
        ) : (
          <div className="ora-md" data-testid="ora-msg-md">
            <Streamdown>{m.content || ""}</Streamdown>
            {/* ── Iter 386 · Session 2.7 · Fix A — buttonify /image ─
                When ORA's reply contains an inline-code `/image <prompt>`
                block, render a tap-to-run button that fires the same
                image-generate endpoint used by the client-side slash
                intercept. This turns ORA's recommendation into a real,
                one-click action — the founder no longer has to copy
                the command back into the composer. Pattern-matched on
                the shipped markdown so this works with any LLM that
                follows the CORE_SAFETY_RULES capability-discipline
                clause (Session 2.7). */}
            {!m.streaming && (
              <OraSlashCmdButtons
                content={m.content || ""}
                onRun={(prompt) => window.__oraRunImageSlash?.(prompt)}
              />
            )}
          </div>
        )}
        {/* Iter 212m-265 · Feb 2026 · Phase 3 — intent verdict from
            the two-layer router.  Renders a small subtle chip so the
            founder sees at a glance what ORA thinks the message was
            asking for.  CODE_CHANGE also surfaces a CTA hint about
            starting a loop run — Phase 3 doesn't kick off the loop
            automatically (that lives in Phase 4 wiring), it just
            makes the affordance discoverable. */}
        {!isUser && !m.streaming && m.intent
          && m.intent !== "UNKNOWN"
          && m.intent !== "CASUAL_CHAT" && (
          <div data-testid={`ora-intent-${m.intent}`}
               style={{ marginTop: 10, display: "flex", gap: 8,
                          alignItems: "center", flexWrap: "wrap" }}>
            <span style={{ padding: "2px 8px", borderRadius: 999,
                             background: m.intent === "CODE_CHANGE"
                               ? "#EEF0F6" : PAL.chip,
                             color: m.intent === "CODE_CHANGE"
                               ? "#4A5878" : PAL.muted,
                             fontSize: 10, fontWeight: 600,
                             fontFamily: "ui-monospace, monospace",
                             letterSpacing: 0.5 }}>
              {m.intent === "CODE_CHANGE" ? "code change" : "preview only"}
              {debug && m.intent_source ? ` · ${m.intent_source}` : ""}
            </span>
            {m.intent === "CODE_CHANGE" && (
              <span data-testid="ora-code-change-hint"
                    style={{ fontSize: 11, color: PAL.faint,
                               fontStyle: "italic" }}>
                Want ORA to actually make this change? Start a loop
                run from the dashboard.
              </span>
            )}
          </div>
        )}
        {/* Iter 212m-264 · Feb 2026 · Phase 2 — Preview affordance.
            When the assistant message contains a renderable code
            block (html/jsx/tsx/js), surface a "Preview" chip that
            opens the sandboxed drawer. Streaming replies hide it
            until final so we don't scan a half-built payload. */}
        {!isUser && !m.streaming && onOpenPreview && (() => {
          const _blk = findRenderableBlock(m.content || "");
          if (!_blk) return null;
          return (
            <div style={{ marginTop: 10 }}>
              <button type="button"
                      data-testid="ora-open-preview"
                      onClick={() => onOpenPreview(_blk)}
                      style={{ display: "inline-flex", alignItems: "center", gap: 6,
                                 padding: "6px 12px", borderRadius: 999,
                                 background: PAL.accentBg,
                                 border: `1px solid ${PAL.accent}33`,
                                 color: PAL.accent, fontSize: 12, fontWeight: 500,
                                 cursor: "pointer", fontFamily: "inherit",
                                 transition: "background 120ms" }}
                      onMouseEnter={(e) => e.currentTarget.style.background = "rgba(224,122,95,0.18)"}
                      onMouseLeave={(e) => e.currentTarget.style.background = PAL.accentBg}>
                <Play size={11} />
                Preview {_blk.lang.toUpperCase()}
              </button>
            </div>
          );
        })()}
        {!isUser && Array.isArray(m.ungrounded) && m.ungrounded.length > 0 && (
          <div data-testid="ora-grounding-warning"
               style={{ marginTop: 10, padding: "8px 12px", borderRadius: 8,
                          background: "#FBF1DC", border: "1px solid #E4C26B",
                          color: "#8A6512", fontSize: 12, lineHeight: 1.5 }}>
            ⚠️ Unverified citations: <span style={{ fontFamily: "ui-monospace, monospace" }}>
            {m.ungrounded.join(", ")}</span> — ye paths repo mein exist nahi karte.
          </div>
        )}
        {!isUser && Array.isArray(m.review_caveats) && m.review_caveats.length > 0 && (
          <div data-testid="ora-review-caveat"
               style={{ marginTop: 10, padding: "8px 12px", borderRadius: 8,
                          background: "#EEF0F6", border: "1px solid #B9C2D8",
                          color: "#4A5878", fontSize: 12, lineHeight: 1.5 }}>
            ⚠︎ Review-flagged as unverified: {m.review_caveats.join(" · ")}
          </div>
        )}
        {/* Iter 212m-263 · Feb 2026 — internal route/temperature/model
            metadata was leaking into the user-facing chat (same
            category as the earlier "(via /loop/active fallback)" bug).
            Gated behind ?debug=1 URL query so founder QA can still
            inspect routing when needed, but default UX is Claude-clean. */}
        {debug && (m.route || m.streaming) && (
          <div style={{ marginTop: 8, fontSize: 10, color: PAL.faint,
                          display: "flex", gap: 6, alignItems: "center" }}>
            {m.route && (
              <span data-testid="ora-route-badge" style={{ padding: "1px 6px", borderRadius: 4,
                               background: PAL.chip,
                               fontFamily: "ui-monospace, monospace" }}>
                {m.route}
                {m.route === "deep" && m.sources && ` · ${m.sources}`}
                {m.downgraded && ` · downgraded`}
                {m.temperature !== undefined && ` · t=${m.temperature}`}
              </span>
            )}
            {m.streaming && <span>streaming…</span>}
          </div>
        )}
      </div>
      {showCopy && (
        <button type="button" onClick={doCopy}
                data-testid={`ora-copy-${m.role}`}
                title={copied ? "Copied!" : "Copy message"}
                aria-label={copied ? "Copied" : "Copy message"}
                style={{ marginTop: 4, padding: "4px 6px",
                           // Iter 212m-259 — light-sky-blue confirmation
                           // state. Iter 212m-260 — icon-only (no label
                           // text) — cleaner outer chrome, easier to
                           // scan long threads.
                           background: copied ? "#DBEEFB" : "transparent",
                           border: `1px solid ${copied ? "#7DB9E8" : PAL.border}`,
                           borderRadius: 999,
                           color: copied ? "#1F6FB2" : PAL.faint,
                           cursor: "pointer",
                           display: "flex", alignItems: "center",
                           justifyContent: "center",
                           lineHeight: 0,
                           transition: "color 120ms, background 120ms, border-color 120ms" }}
                onMouseEnter={(e) => { if (!copied) e.currentTarget.style.color = PAL.muted; }}
                onMouseLeave={(e) => { if (!copied) e.currentTarget.style.color = PAL.faint; }}>
          {/* Iter 212m-261 — Icon stays SAME (Copy) always.
              Only the color + background flashes sky-blue for 1.5 s
              on click as confirmation. No tick swap. */}
          <Copy size={11} />
        </button>
      )}
    </div>
  );
}

function PickerModal({ recent, onClose, onNew, onOpen }) {
  // Iter 212m-262 — group sessions by date bucket so browsing older
  // chats stays scannable at any list length.
  const now = Date.now() / 1000;
  const DAY = 86400;
  const buckets = { "Today": [], "Yesterday": [], "This week": [], "Older": [] };
  for (const s of recent) {
    const age = now - (s.updated_at || 0);
    if      (age < DAY)     buckets["Today"].push(s);
    else if (age < 2 * DAY) buckets["Yesterday"].push(s);
    else if (age < 7 * DAY) buckets["This week"].push(s);
    else                    buckets["Older"].push(s);
  }
  return (
    <div data-testid="ora-picker"
         onClick={onClose}
         style={{ position: "fixed", inset: 0, zIndex: 200,
                    background: "rgba(28,28,25,0.4)",
                    display: "flex", alignItems: "center", justifyContent: "center",
                    padding: 20 }}>
      <div onClick={(e) => e.stopPropagation()}
           style={{ background: PAL.card, borderRadius: 16,
                      border: `1px solid ${PAL.border}`,
                      maxWidth: 460, width: "100%", maxHeight: "80vh",
                      overflow: "auto", padding: 20 }}>
        <div style={{ display: "flex", justifyContent: "space-between",
                        alignItems: "center", marginBottom: 16 }}>
          <div style={{ fontSize: 15, fontWeight: 600 }}>Chat history</div>
          <button data-testid="ora-picker-new" onClick={onNew}
                  style={{ background: PAL.accent, color: "#fff",
                             border: "none", borderRadius: 8,
                             padding: "8px 14px", fontSize: 12, fontWeight: 600,
                             cursor: "pointer", display: "flex", gap: 4,
                             alignItems: "center" }}>
            <Plus size={12} /> New chat
          </button>
        </div>
        {recent.length === 0 && (
          <div style={{ fontSize: 13, color: PAL.muted }}>No previous sessions.</div>
        )}
        {["Today", "Yesterday", "This week", "Older"].map(bucket => (
          buckets[bucket].length > 0 && (
            <div key={bucket} style={{ marginBottom: 14 }}>
              <div style={{ fontSize: 10, color: PAL.faint,
                              textTransform: "uppercase",
                              letterSpacing: 1, marginBottom: 6,
                              fontWeight: 600 }}>
                {bucket}
              </div>
              {buckets[bucket].map(s => (
                <button key={s.session_id} data-testid={`ora-picker-${s.session_id}`}
                        onClick={() => onOpen(s.session_id)}
                        style={{ display: "block", width: "100%", textAlign: "left",
                                   padding: "10px 12px", marginBottom: 6,
                                   background: PAL.chip,
                                   border: `1px solid ${PAL.border}`,
                                   borderRadius: 8, cursor: "pointer",
                                   color: PAL.text, fontFamily: "inherit" }}>
                  <div style={{ fontSize: 13, fontWeight: 500 }}>
                    {s.title || "Untitled"}
                  </div>
                  <div style={{ fontSize: 10, color: PAL.faint, marginTop: 2 }}>
                    {s.message_count || 0} messages · {new Date((s.updated_at || 0) * 1000).toLocaleString()}
                  </div>
                </button>
              ))}
            </div>
          )
        ))}
      </div>
    </div>
  );
}
