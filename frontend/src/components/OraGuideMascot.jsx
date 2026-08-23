/**
 * OraGuideMascot.jsx — fixed-position ORA guide + help entry point.
 *
 * 2026-08-20 — replaces GlobalHelpFAB (the old "Need help?" pill) at
 * the exact same bottom-right spot. One floating element, not two.
 *
 * Lives permanently once logged in, NEVER moves toward other UI —
 * guidance to a specific button happens via `useGuideSpotlight`
 * (a glow ring on the real element), not by the mascot relocating.
 *
 * Stage-aware auto-open: polls GET /auth/me/funnel-stage (live,
 * no 24h gate — see services/funnel_nudge_cron.py::current_stage_for_user)
 * and opens ONCE per stage-group per browser session (sessionStorage,
 * NOT the email nudge system's permanent Mongo dedup — those are
 * different semantics: "once this session" vs "once ever").
 * Auto-closes if the user completes the step before dismissing.
 *
 * Clicking the mascot any other time opens a general "How can I
 * help?" state with a bridge to the real Advisor panel (kept
 * separate from Advisor by design — Advisor is desktop-only and
 * lives in the top bar; merging would either break mobile help or
 * reintroduce the composer-overlap bug Advisor was moved to avoid).
 */
import React, { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { Sparkles, Bug, CheckCircle2, Zap, ShieldCheck } from "lucide-react";
import { api } from "../lib/api";
import { useGuideSpotlight, GuideSpotlightStyle } from "../hooks/useGuideSpotlight.jsx";
import { shouldHide } from "./GlobalHelpFAB";
import { useActiveProject } from "./TabBar";
import { Z_FLOATING_GUIDE } from "../lib/zIndex";
import { toast } from "./Toast";

// 2026-08-23 — founder direction: the "ORA GUIDE" suggestions card that
// used to live pinned in the Ask Advisor sidebar now lives HERE instead.
// Once onboarding is done (no pending stage bubble) and a project is
// active, this mascot shows project-scoped suggestions/issues instead
// of the generic "How can I help?" menu — same underlying data source
// PromptStarterPanel used (`/findings/starter-suggestions`).
const SUGGESTION_ICONS = { sparkles: Sparkles, bug: Bug, check: CheckCircle2, zap: Zap, shield: ShieldCheck };
const FALLBACK_SUGGESTIONS = [
  { slug: "build-something-new", icon_hint: "sparkles", label: "Build something new", example: "I want a contact form on my website" },
  { slug: "somethings-broken", icon_hint: "bug", label: "Something's broken", example: "The login button doesn't work, please fix it" },
  { slug: "check-everything-working", icon_hint: "check", label: "Check everything is working", example: "Check my whole website for any bugs" },
  { slug: "security-check", icon_hint: "shield", label: "Security check", example: "Check my code for any security problems" },
];

const STAGE_COPY = {
  // stage1_github_started and stage4_fully_inactive collapse into the
  // same on-screen message — there's nothing different to show a user
  // who never clicked vs one who clicked and didn't finish; same wizard
  // screen either way.
  connect_github: {
    message: "Connect your repo to get started — click Connect GitHub above.",
    target: "connect-github-btn",
  },
  project_pending: {
    message: "One step left — pick a repo above, then click Continue to finish setup.",
    target: "continue-btn",
  },
  no_chat: {
    message: "Your project's ready — try asking ORA to fix a bug or add a feature. A few ideas below.",
    target: null,
  },
};

function stageToGroup(stage) {
  if (stage === "stage1_github_started" || stage === "stage4_fully_inactive") return "connect_github";
  if (stage === "stage2_project_pending") return "project_pending";
  if (stage === "stage3_no_chat") return "no_chat";
  return null;
}

const SEEN_KEY_PREFIX = "ora_guide_seen_v1_";
function alreadyShownThisSession(group) {
  try { return sessionStorage.getItem(SEEN_KEY_PREFIX + group) === "1"; } catch { return false; }
}
function markShownThisSession(group) {
  try { sessionStorage.setItem(SEEN_KEY_PREFIX + group, "1"); } catch { /* ignore */ }
}

const POLL_MS = 15000;

export default function OraGuideMascot() {
  const { pathname } = useLocation();
  const [loggedIn, setLoggedIn] = useState(() => !!localStorage.getItem("aurem_token"));
  const [open, setOpen] = useState(false);
  const [autoGroup, setAutoGroup] = useState(null);  // stage-triggered bubble
  const [panelMode, setPanelMode] = useState("stage"); // "stage" | "general" | "suggestions" | "escalating" | "escalated"
  const pollRef = useRef(null);
  const activeProject = useActiveProject();
  const effectiveProjectId = activeProject?.project_id || null;
  const [projectSuggestions, setProjectSuggestions] = useState(FALLBACK_SUGGESTIONS);

  useEffect(() => {
    setLoggedIn(!!localStorage.getItem("aurem_token"));
    const onStorage = (e) => { if (e.key === "aurem_token") setLoggedIn(!!e.newValue); };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [pathname]);

  const hidden = !loggedIn || shouldHide(pathname);

  // Poll the live stage. Fires the auto-bubble the first time a
  // stage-group is seen this session; auto-closes if the user
  // finishes the step while an auto-bubble is showing.
  useEffect(() => {
    if (hidden) return undefined;
    let cancelled = false;
    async function tick() {
      try {
        const r = await api.get("/auth/me/funnel-stage");
        if (cancelled) return;
        const group = stageToGroup(r.data?.stage);
        if (group && !alreadyShownThisSession(group)) {
          markShownThisSession(group);
          setAutoGroup(group);
          setPanelMode("stage");
          setOpen(true);
        } else if (autoGroup && group !== autoGroup) {
          // User moved past the stage the open bubble was about —
          // close it automatically, no stale advice lingering.
          setOpen(false);
          setAutoGroup(null);
        }
      } catch { /* best-effort — never blocks the UI */ }
    }
    tick();
    const intervalId = setInterval(tick, POLL_MS);
    pollRef.current = intervalId;
    return () => { cancelled = true; clearInterval(intervalId); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hidden, pathname]);

  const stageInfo = autoGroup ? STAGE_COPY[autoGroup] : null;
  useGuideSpotlight(open && panelMode === "stage" ? stageInfo?.target : null);

  // Once onboarding no longer has a pending stage bubble, and there's a
  // real project to talk about, pull the same personalized
  // findings-based prompts PromptStarterPanel used to show in the
  // sidebar. Falls back to the generic pool on error/empty/no-project.
  useEffect(() => {
    let cancelled = false;
    if (!effectiveProjectId) { setProjectSuggestions(FALLBACK_SUGGESTIONS); return undefined; }
    api.get(`/findings/starter-suggestions?project_id=${encodeURIComponent(effectiveProjectId)}&limit=4`)
      .then((res) => {
        if (cancelled) return;
        const s = res?.data?.suggestions;
        setProjectSuggestions(Array.isArray(s) && s.length >= 3 ? s.slice(0, 4) : FALLBACK_SUGGESTIONS);
      })
      .catch(() => { if (!cancelled) setProjectSuggestions(FALLBACK_SUGGESTIONS); });
    return () => { cancelled = true; };
  }, [effectiveProjectId]);

  if (hidden) return null;

  function toggleOpen() {
    if (open) { setOpen(false); return; }
    // Manual click shows the onboarding stage bubble if one's still
    // pending; otherwise, once a project is active, shows project
    // suggestions/issues; otherwise the generic help menu.
    setPanelMode(autoGroup ? "stage" : (effectiveProjectId ? "suggestions" : "general"));
    setOpen(true);
  }

  function dismiss() {
    setOpen(false);
    setAutoGroup(null);
  }

  function openAdvisor() {
    window.dispatchEvent(new Event("aurem:ora-open"));
    dismiss();
  }

  // Same handoff PromptStarterPanel used — prefills the main chat
  // composer (ChatPanel listens for this event), then closes the bubble
  // so the user sees what they picked.
  function pickSuggestion(example) {
    try {
      window.dispatchEvent(new CustomEvent("aurem:starter-pick", { detail: { prompt: example } }));
    } catch { /* noop */ }
    dismiss();
  }

  async function reportSomethingWrong() {
    setPanelMode("escalating");
    try {
      const stageLabel = autoGroup ? STAGE_COPY[autoGroup].message : "(general help)";
      const body = [
        "[Auto-filed via ORA Guide]",
        `Stage: ${autoGroup || "none"} — "${stageLabel}"`,
        `Page: ${pathname}`,
        `Time: ${new Date().toISOString()}`,
      ].join("\n");
      await api.post("/support/tickets", {
        subject: `ORA Guide — stuck at: ${autoGroup || "general"}`,
        body,
        source: "in_app_guide",
      });
      setPanelMode("escalated");
      toast({ message: "Thanks, we'll take a look.", kind: "success", duration: 3500 });
      setTimeout(() => {
        setOpen((wasOpen) => wasOpen ? false : wasOpen);
        setPanelMode((m) => m === "escalated" ? "stage" : m);
      }, 1400);
    } catch {
      toast({ message: "Couldn't send that just now — try again in a moment.", kind: "error" });
      setPanelMode(autoGroup ? "stage" : "general");
    }
  }

  return (
    <div
      data-testid="ora-guide-mascot-root"
      // 2026-08-20 — was `bottom: 20`, sat directly on top of the chat
      // composer's send button on the chat page (both anchor to the
      // bottom-right corner). Raised clear of the composer footer row.
      style={{ position: "fixed", bottom: 92, right: 20, zIndex: Z_FLOATING_GUIDE /* 2026-06 — must sit ABOVE CookieConsentBanner; at 9990 the banner intercepted every click → founder-reported dead icon */ }}
    >
      <GuideSpotlightStyle />
      <style>{MASCOT_KEYFRAMES}</style>

      {open && (
        <div
          data-testid="ora-guide-bubble"
          style={{
            position: "absolute", bottom: 52, right: 0,
            width: panelMode === "suggestions" ? 300 : 280, background: "#141414",
            border: "1px solid rgba(255,102,8,0.28)",
            borderRadius: 12, padding: "14px 16px",
            boxShadow: "0 12px 40px rgba(0,0,0,0.5)",
            animation: "oraGuideBubbleIn 180ms ease-out",
          }}
        >
          <div style={{
            fontSize: 10, color: "#ff9d5c",
            fontFamily: "var(--font-mono, ui-monospace, monospace)",
            letterSpacing: "0.08em", marginBottom: 8,
          }}>
            ORA GUIDE
          </div>

          {panelMode === "stage" && stageInfo && (
            <>
              <div data-testid="ora-guide-message"
                   style={{ fontSize: 13, color: "#f8fafc", lineHeight: 1.55, marginBottom: 12 }}>
                {stageInfo.message}
              </div>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                <button data-testid="ora-guide-got-it" type="button" onClick={dismiss} style={primaryBtnStyle}>
                  Got it
                </button>
                <button data-testid="ora-guide-something-wrong" type="button"
                        onClick={reportSomethingWrong} style={linkBtnStyle}>
                  Something's wrong
                </button>
              </div>
            </>
          )}

          {panelMode === "general" && (
            <>
              <div style={{ fontSize: 13, color: "#f8fafc", lineHeight: 1.55, marginBottom: 12 }}>
                How can I help?
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <button data-testid="ora-guide-open-advisor" type="button" onClick={openAdvisor} style={menuBtnStyle}>
                  Open Advisor
                </button>
                <button data-testid="ora-guide-contact-support" type="button"
                        onClick={reportSomethingWrong} style={menuBtnStyle}>
                  Contact support
                </button>
                <button data-testid="ora-guide-close" type="button" onClick={dismiss} style={linkBtnStyle}>
                  Close
                </button>
              </div>
            </>
          )}

          {panelMode === "suggestions" && (
            <>
              <div data-testid="ora-guide-suggestions-intro"
                   style={{ fontSize: 12, color: "#f8fafc", lineHeight: 1.5, marginBottom: 10 }}>
                {projectSuggestions.some((s) => s.personalized)
                  ? "Here's what I found in your repo — pick one, or type your own:"
                  : "Not sure what to ask? Pick one, or describe what you want:"}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 10 }}>
                {projectSuggestions.map(({ slug, icon_hint, label, example, personalized }, idx) => {
                  const Icon = SUGGESTION_ICONS[icon_hint] || Sparkles;
                  // Backend finding slugs can contain spaces/punctuation —
                  // keep data-testid CSS-selector-safe regardless of source.
                  const safeSlug = String(slug || idx).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "") || `item-${idx}`;
                  return (
                    <button
                      key={`${slug}-${idx}`}
                      type="button"
                      data-testid={`ora-guide-suggestion-${safeSlug}`}
                      onClick={() => pickSuggestion(example)}
                      title={`Click to try: "${example}"`}
                      style={suggestionBtnStyle}
                    >
                      <Icon size={12} strokeWidth={2.2} style={{ flexShrink: 0, marginTop: 2, color: "#ffb37a" }} />
                      <span style={{ minWidth: 0, flex: 1 }}>
                        <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                          <span style={{ fontSize: 12, fontWeight: 600, color: "#f8fafc" }}>{label}</span>
                          {personalized && (
                            <span style={{ fontSize: 8, fontWeight: 700, letterSpacing: "0.04em",
                                           color: "#4ade80", background: "rgba(74,222,128,0.12)",
                                           borderRadius: 4, padding: "1px 4px" }}>
                              FROM YOUR REPO
                            </span>
                          )}
                        </span>
                        <span style={{ display: "block", fontSize: 11, color: "#9aa0aa", fontStyle: "italic",
                                       overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          &ldquo;{example}&rdquo;
                        </span>
                      </span>
                    </button>
                  );
                })}
              </div>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                <button data-testid="ora-guide-open-advisor" type="button" onClick={openAdvisor} style={linkBtnStyle}>
                  Open Advisor
                </button>
                <button data-testid="ora-guide-close" type="button" onClick={dismiss} style={linkBtnStyle}>
                  Close
                </button>
              </div>
            </>
          )}

          {panelMode === "escalating" && (
            <div style={{ fontSize: 12, color: "var(--text-faint, #999)" }}>Sending…</div>
          )}

          {panelMode === "escalated" && (
            <div data-testid="ora-guide-escalated-confirm" style={{ fontSize: 13, color: "#22c55e", fontWeight: 600 }}>
              ✓ Thanks, we'll take a look.
            </div>
          )}
        </div>
      )}

      <button
        data-testid="ora-guide-mascot-avatar"
        type="button"
        aria-label="ORA Guide"
        onClick={toggleOpen}
        style={{
          width: 38, height: 38, borderRadius: "50%",
          background: "linear-gradient(135deg, #ff6608, #ff9d5c)",
          border: "none", cursor: "pointer", position: "relative",
          boxShadow: autoGroup && !open
            ? "0 4px 20px rgba(255,102,8,0.5), 0 0 0 3px rgba(255,102,8,0.25)"
            : "0 4px 16px rgba(0,0,0,0.4)",
          animation: "oraGuideFloat 3s ease-in-out infinite",
        }}
      >
        <span style={{ position: "absolute", top: 12, left: 9, width: 6, height: 6,
                       background: "#0b0b0b", borderRadius: "50%", animation: "oraGuideBlink 4.5s infinite" }} />
        <span style={{ position: "absolute", top: 12, right: 9, width: 6, height: 6,
                       background: "#0b0b0b", borderRadius: "50%", animation: "oraGuideBlink 4.5s infinite 0.08s" }} />
        <span style={{ position: "absolute", bottom: 10, left: "50%", transform: "translateX(-50%)",
                       width: 12, height: 3, background: "#0b0b0b", borderRadius: 2 }} />
      </button>
    </div>
  );
}

const primaryBtnStyle = {
  background: "var(--accent, #ff6608)", color: "#0b0b0b",
  border: "none", borderRadius: 6, padding: "6px 14px",
  fontSize: 12, fontWeight: 600, cursor: "pointer",
};
const menuBtnStyle = {
  background: "rgba(255,102,8,0.08)", color: "#f8fafc",
  border: "1px solid rgba(255,102,8,0.22)", borderRadius: 6,
  padding: "8px 12px", fontSize: 12, fontWeight: 500,
  cursor: "pointer", textAlign: "left",
};
const linkBtnStyle = {
  background: "transparent", color: "var(--text-faint, #999)",
  border: "none", fontSize: 11, cursor: "pointer", padding: 4,
};
const suggestionBtnStyle = {
  display: "flex", gap: 8, alignItems: "flex-start", textAlign: "left",
  padding: "8px 10px", borderRadius: 8, width: "100%",
  background: "rgba(255,102,8,0.08)", border: "1px solid rgba(255,102,8,0.22)",
  cursor: "pointer",
};

const MASCOT_KEYFRAMES = `
@keyframes oraGuideFloat {
  0%, 100% { transform: translateY(0); }
  50%      { transform: translateY(-3px); }
}
@keyframes oraGuideBlink {
  0%, 90%, 100% { transform: scaleY(1); }
  95%           { transform: scaleY(0.1); }
}
@keyframes oraGuideBubbleIn {
  from { opacity: 0; transform: translateY(6px); }
  to   { opacity: 1; transform: translateY(0); }
}
`;
