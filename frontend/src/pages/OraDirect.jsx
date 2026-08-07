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
import React, { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Lock, Settings, LogOut, ArrowUp, RefreshCw, Zap, Clock, Plus, Square, Copy, Play } from "lucide-react";
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

  const send = async () => {
    const text = input.trim();
    if (!text || !sessionId || sending) return;
    setInput("");
    setMessages(m => [...m, { role: "user", content: text }]);
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
        body: JSON.stringify({ session_id: sessionId, content: text }),
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
                          sending={sending} onStop={stop} large />
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
                            sending={sending} onStop={stop} />
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

function InputCard({ input, setInput, onSend, sending, onStop, large = false }) {
  // Iter 212m-246 — widened to 5 lines (was 3) so long prompts and
  // multi-line context paste-ins stay comfortably readable without
  // shrinking the message stream area. Auto-grows past 5 up to 12
  // lines. Send button becomes a Stop button while streaming.
  return (
    <form onSubmit={(e) => { e.preventDefault(); if (!sending) onSend(); }}
          style={{ background: PAL.card,
                     border: `1px solid ${PAL.border}`,
                     borderRadius: 16,
                     padding: "14px 16px",
                     boxShadow: "0 4px 20px rgba(0,0,0,0.04)",
                     display: "flex", alignItems: "flex-end", gap: 10 }}>
      <textarea
        data-testid="ora-input"
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (!sending) onSend(); } }}
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
      {sending ? (
        <button type="button" onClick={onStop} data-testid="ora-stop"
                title="Stop generating"
                style={{ width: 38, height: 38, borderRadius: 999,
                           background: "#E5E5DF", color: PAL.text,
                           border: "none", cursor: "pointer",
                           display: "flex", alignItems: "center", justifyContent: "center",
                           flexShrink: 0, transition: "background 120ms" }}
                onMouseEnter={(e) => e.currentTarget.style.background = "#D8D8D0"}
                onMouseLeave={(e) => e.currentTarget.style.background = "#E5E5DF"}>
          <Square size={14} fill={PAL.text} />
        </button>
      ) : (
        <button type="submit" data-testid="ora-send" disabled={!input.trim()}
                style={{ width: 38, height: 38, borderRadius: 999,
                           background: input.trim() ? PAL.accent : PAL.chip,
                           color: input.trim() ? "#fff" : PAL.faint,
                           border: "none", cursor: "pointer",
                           display: "flex", alignItems: "center", justifyContent: "center",
                           flexShrink: 0, transition: "background 120ms",
                           touchAction: "manipulation" }}>
          <ArrowUp size={16} />
        </button>
      )}
    </form>
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
        ) : (
          <div className="ora-md" data-testid="ora-msg-md">
            <Streamdown>{m.content || ""}</Streamdown>
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
          && m.intent !== "UNKNOWN" && (
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
