/**
 * ModeLoopPill.jsx — 2026-08-21.
 *
 * Swift / Pro / Maxx mode picker + the Prompt-vs-Loop sub-choice for
 * Pro/Maxx, extracted from TopBar.jsx and moved into the chat
 * composer (founder request — same spot the old standalone
 * "LOOP OFF/ON" toggle used to occupy).
 *
 * Flow:
 *   • Click "Swift" → selects immediately, execMode forced to Prompt
 *     (Swift never runs Loop).
 *   • Click "Pro" / "Maxx" → shows a Prompt/Loop sub-choice; picking
 *     one finalises BOTH mode + execMode together. Loop is locked
 *     (dashed lock icon) for non-Pro/Team users — clicking it fires
 *     the existing `aurem:loop-coming-soon` toast instead of toggling.
 *   • Reopening while already on Pro/Maxx jumps straight to the
 *     Prompt/Loop step (mode is already decided) with a back arrow
 *     to reach the Swift/Pro/Maxx row again.
 *   • Collapsed pill shows ONLY the mode name (no "· Loop" suffix).
 */
import React, { useState, useEffect } from "react";
import { Zap, Gauge, Crown, ChevronDown, ChevronLeft, RefreshCw, Lock } from "lucide-react";
import { EXEC_MODES } from "./LoopModeToggle";
import { isLoopUnlockedSync, getUnlockedModesSync } from "../utils/chatTextUtils";
import { UpgradePopup } from "./ModeSelector";
import { api } from "../lib/api";

const MODES = [
  { id: "swift", label: "Swift", icon: Zap },
  { id: "pro",   label: "Pro",   icon: Gauge },
  { id: "maxx",  label: "Maxx",  icon: Crown },
];
const LOOP_ELIGIBLE = new Set(["pro", "maxx"]);
// Fallback copy while /chat/modes/available hasn't resolved yet — kept
// in sync with routers/chat/misc.py::available_modes()'s catalog.
const MODE_FALLBACK_DATA = {
  pro:  { label: "Pro",  min_tier: "pro",  price: "$19", desc: "DeepSeek + Claude review every answer. Higher quality." },
  maxx: { label: "Maxx", min_tier: "team", price: "$49", desc: "Claude writes your code directly. Best for critical work." },
};

export default function ModeLoopPill({ mode, onModeChange, execMode, onExecModeChange }) {
  const [modesOpen, setModesOpen] = useState(false);
  const [step, setStep] = useState("mode");
  const [pendingMode, setPendingMode] = useState(null);
  const [upsellMode, setUpsellMode] = useState(null);
  const [modesCatalog, setModesCatalog] = useState(null);
  const activeMode = MODES.find((m) => m.id === mode) || MODES[0];
  const ActiveModeIcon = activeMode.icon;
  const loopUnlocked = isLoopUnlockedSync();
  // D2 (2026-09) — free/starter users can only actually RUN Swift
  // (backend silently clamps any other req_mode down to swift — see
  // services/mode_routing.py). Gate the pill itself so a locked user
  // gets an honest "Unlock Pro" upsell instead of the pill visually
  // "selecting" a mode the backend then silently downgrades.
  const unlockedModes = getUnlockedModesSync();

  useEffect(() => {
    let cancelled = false;
    api.get("/chat/modes/available")
      .then((r) => { if (!cancelled) setModesCatalog(r.data?.modes || null); })
      .catch(() => { /* silent — falls back to MODE_FALLBACK_DATA */ });
    return () => { cancelled = true; };
  }, []);

  function openPill() {
    if (LOOP_ELIGIBLE.has(mode)) {
      setPendingMode(mode);
      setStep("exec");
    } else {
      setStep("mode");
      setPendingMode(null);
    }
    setModesOpen(true);
  }

  function pickMode(id) {
    if (id === "swift") {
      onModeChange(id);
      onExecModeChange?.(EXEC_MODES.PROMPT);
      setModesOpen(false);
      return;
    }
    if (!unlockedModes.includes(id)) {
      setModesOpen(false);
      setUpsellMode(id);
      return;
    }
    setPendingMode(id);
    setStep("exec");
  }

  function pickExec(target) {
    if (target === EXEC_MODES.LOOP && !loopUnlocked) {
      try {
        window.dispatchEvent(new CustomEvent("aurem:loop-coming-soon"));
      } catch { /* ignore */ }
      return;
    }
    onModeChange(pendingMode);
    onExecModeChange?.(target);
    setModesOpen(false);
  }

  const execActive = pendingMode === mode ? execMode : null;

  return (
    <div
      data-testid="ds2-mode-pill"
      data-modes-open={modesOpen ? "true" : "false"}
      data-mode-step={step}
      style={{
        display: "inline-flex", alignItems: "center", gap: 2,
        borderRadius: 999, border: "1px solid var(--border, rgba(255,255,255,0.12))",
        background: "#111111", padding: 3,
      }}
    >
      {modesOpen && step === "mode" && (
        MODES.map(({ id, label, icon: Icon }) => {
          const locked = id !== "swift" && !unlockedModes.includes(id);
          return (
          <button key={id} type="button"
            onClick={() => pickMode(id)}
            data-testid={`ds2-mode-${id}`}
            data-locked={locked ? "1" : "0"}
            title={locked ? `${label} unlocks on a paid plan` : ""}
            style={{
              display: "inline-flex", alignItems: "center", gap: 6,
              borderRadius: 999, padding: "5px 12px",
              fontSize: 11, fontWeight: 600, fontFamily: "'JetBrains Mono', monospace",
              border: "none", cursor: "pointer",
              background: mode === id ? "var(--accent, #FF6608)" : "transparent",
              color: locked ? "rgba(255,255,255,0.3)" : mode === id ? "#0A0A0A" : "rgba(255,255,255,0.6)",
            }}>
            {locked ? <Lock size={10} strokeWidth={2.5} /> : <Icon size={11} strokeWidth={2.5} />}
            {label}
          </button>
          );
        })
      )}
      {modesOpen && step === "exec" && (
        <>
          <button type="button"
            onClick={() => { setStep("mode"); setPendingMode(null); }}
            data-testid="ds2-mode-back"
            title="Change mode"
            style={{
              display: "inline-flex", alignItems: "center", justifyContent: "center",
              borderRadius: 999, padding: "5px 6px", border: "none", background: "transparent",
              color: "rgba(255,255,255,0.5)", cursor: "pointer",
            }}>
            <ChevronLeft size={12} strokeWidth={2.5} />
          </button>
          <button type="button"
            onClick={() => pickExec(EXEC_MODES.PROMPT)}
            data-testid="ds2-exec-prompt"
            style={{
              display: "inline-flex", alignItems: "center", gap: 6,
              borderRadius: 999, padding: "5px 12px",
              fontSize: 11, fontWeight: 600, fontFamily: "'JetBrains Mono', monospace",
              border: "none", cursor: "pointer",
              background: execActive === EXEC_MODES.PROMPT ? "var(--accent, #FF6608)" : "transparent",
              color: execActive === EXEC_MODES.PROMPT ? "#0A0A0A" : "rgba(255,255,255,0.6)",
            }}>
            Prompt
          </button>
          <button type="button"
            onClick={() => pickExec(EXEC_MODES.LOOP)}
            data-testid="ds2-exec-loop"
            data-locked={loopUnlocked ? "0" : "1"}
            title={loopUnlocked ? "" : "Loop unlocks on Pro/Team plans"}
            style={{
              display: "inline-flex", alignItems: "center", gap: 6,
              borderRadius: 999, padding: "5px 12px",
              fontSize: 11, fontWeight: 600, fontFamily: "'JetBrains Mono', monospace",
              border: "none",
              cursor: loopUnlocked ? "pointer" : "not-allowed",
              background: !loopUnlocked
                ? "transparent"
                : execActive === EXEC_MODES.LOOP ? "var(--accent, #FF6608)" : "transparent",
              color: !loopUnlocked
                ? "rgba(255,255,255,0.3)"
                : execActive === EXEC_MODES.LOOP ? "#0A0A0A" : "rgba(255,255,255,0.6)",
            }}>
            {loopUnlocked ? <RefreshCw size={11} strokeWidth={2.5} /> : <Lock size={10} strokeWidth={2.5} />}
            Loop
          </button>
        </>
      )}
      {!modesOpen && (
        <button type="button"
          onClick={openPill}
          data-testid="ds2-mode-collapsed"
          title="Change mode"
          className="chip chip-md chip-interactive"
          style={{
            gap: 6,
            border: "none",
            background: "var(--accent, #FF6608)", color: "#0A0A0A",
          }}>
          <ActiveModeIcon size={11} strokeWidth={2.5} />
          {activeMode.label}
          <ChevronDown size={10} strokeWidth={2.5} style={{ opacity: 0.8 }} />
        </button>
      )}
      {upsellMode && (
        <UpgradePopup
          mode={upsellMode}
          data={(modesCatalog && modesCatalog[upsellMode]) || MODE_FALLBACK_DATA[upsellMode]}
          onClose={() => setUpsellMode(null)}
        />
      )}
    </div>
  );
}
