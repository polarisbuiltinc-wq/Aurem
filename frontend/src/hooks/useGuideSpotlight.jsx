/**
 * useGuideSpotlight.js — pulses a brand-orange glow ring around
 * whichever on-screen element carries `data-guide-target="<key>"`.
 *
 * Targets live in a totally different component tree (wizard modals),
 * so this uses `document.querySelector` + a class toggle — the same
 * pattern GlobalHelpFAB already used to measure the composer card.
 * Polls while active because the target may mount *after* the guide
 * bubble opens (e.g. the wizard modal opens a beat later).
 *
 * Usage:
 *   useGuideSpotlight(open ? "continue-btn" : null);
 */
import { useEffect } from "react";

const SPOTLIGHT_CLASS = "ora-guide-spotlight";
const POLL_MS = 400;

export function useGuideSpotlight(targetKey) {
  useEffect(() => {
    if (!targetKey) return undefined;
    let current = null;
    const apply = () => {
      const el = document.querySelector(`[data-guide-target="${targetKey}"]`);
      if (el === current) return;
      if (current) current.classList.remove(SPOTLIGHT_CLASS);
      if (el) el.classList.add(SPOTLIGHT_CLASS);
      current = el;
    };
    apply();
    const interval = setInterval(apply, POLL_MS);
    return () => {
      clearInterval(interval);
      if (current) current.classList.remove(SPOTLIGHT_CLASS);
    };
  }, [targetKey]);
}

/** Mount once near the root of any tree that uses the spotlight. */
export function GuideSpotlightStyle() {
  return (
    <style>{`
      .${SPOTLIGHT_CLASS} {
        position: relative;
        animation: oraGuideSpotlightPulse 1.6s ease-in-out infinite;
        border-radius: 6px;
      }
      @keyframes oraGuideSpotlightPulse {
        0%, 100% { box-shadow: 0 0 0 0 rgba(255,102,8,0.55), 0 0 0 0 rgba(255,102,8,0.0); }
        50%      { box-shadow: 0 0 0 3px rgba(255,102,8,0.45), 0 0 14px 4px rgba(255,102,8,0.35); }
      }
    `}</style>
  );
}
