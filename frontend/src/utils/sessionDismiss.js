/**
 * sessionDismiss.js — 2026-08-21.
 *
 * "Dismiss until next login" helper for nudge/status pills (the
 * thinking-hint upsell strip, the active-project scope chip, etc).
 *
 * Plain localStorage would survive forever (never comes back) and
 * sessionStorage clears on every tab close (comes back too often —
 * even within the SAME login). Neither matches "stay dismissed for
 * this login, reappear only after a fresh login" — so we scope the
 * dismissal to the current auth token itself: a new login always
 * issues a new token, so the stored marker naturally stops matching
 * and the pill reappears, while closing/reopening the tab (same
 * token, same login) keeps it dismissed.
 */
import { getToken } from "../lib/api";

export function isDismissedForSession(key) {
  try {
    const token = getToken();
    if (!token) return false;
    return localStorage.getItem(key) === token;
  } catch {
    return false;
  }
}

export function dismissForSession(key) {
  try {
    const token = getToken();
    if (token) localStorage.setItem(key, token);
  } catch { /* ignore */ }
}
