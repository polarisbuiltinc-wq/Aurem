/**
 * hooks/useFriendlyStatusPhrase.js
 *
 * 2026-08-22 — masks how long ORA is actually taking behind a
 * reassuring, progressing narrative instead of raw numbers/labels
 * like "Slow response · 35s silent · auto-retry in 55s" or a ticking
 * "· 205.0s" counter. Cycles forward through a fixed sequence every
 * `intervalMs` while `active` is true; holds on the last phrase for
 * very long waits instead of looping back to "Bear with me…" (which
 * would look like it restarted). Resets to the first phrase the next
 * time it goes active again.
 */
import { useState, useEffect, useRef } from "react";

export const FRIENDLY_STATUS_PHRASES = [
  "Bear with me — our agents are working on it…",
  "Thinking through your request…",
  "Deciding on the best approach…",
  "Parallel agents are collaborating…",
  "Council is reviewing the result…",
  "Finalizing the answer…",
];

export function useFriendlyStatusPhrase(active, intervalMs = 4500) {
  const [idx, setIdx] = useState(0);
  const timerRef = useRef(null);

  useEffect(() => {
    if (!active) {
      setIdx(0);
      if (timerRef.current) clearInterval(timerRef.current);
      return;
    }
    timerRef.current = setInterval(() => {
      setIdx((i) => (i + 1 < FRIENDLY_STATUS_PHRASES.length ? i + 1 : i));
    }, intervalMs);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [active, intervalMs]);

  return FRIENDLY_STATUS_PHRASES[idx];
}
