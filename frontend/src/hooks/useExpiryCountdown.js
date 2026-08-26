/**
 * useExpiryCountdown.js — BUILD PROMPT v4 · Phase C (D4).
 *
 * Ticks a live "seconds remaining" countdown from a server-sourced
 * ISO `expiresAt` string. Shared by PlanApprovalCard, ShipPendingCard,
 * and UserActionCard so all three gate cards render the SAME real
 * countdown sourced from `GET /loop/{id}/status`'s `expires_at` field
 * (the one sanctioned additive response field) — never a client-side
 * guess at the timeout window.
 *
 * Returns `null` when there's nothing to count down (no expiresAt yet,
 * or it already passed) so callers can hide the countdown cleanly.
 */
import { useEffect, useState } from "react";

export function useExpiryCountdown(expiresAt) {
  const [secondsLeft, setSecondsLeft] = useState(null);

  useEffect(() => {
    if (!expiresAt) {
      setSecondsLeft(null);
      return undefined;
    }
    const target = new Date(expiresAt).getTime();
    if (Number.isNaN(target)) {
      setSecondsLeft(null);
      return undefined;
    }
    const tick = () => {
      const left = Math.round((target - Date.now()) / 1000);
      setSecondsLeft(left > 0 ? left : 0);
    };
    tick();
    const t = setInterval(tick, 1000);
    return () => clearInterval(t);
  }, [expiresAt]);

  return secondsLeft;
}

export function formatCountdown(secondsLeft) {
  if (secondsLeft == null) return null;
  const m = Math.floor(secondsLeft / 60);
  const s = secondsLeft % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}
