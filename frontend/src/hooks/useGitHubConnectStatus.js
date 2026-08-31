/**
 * useGitHubConnectStatus.js — 2026-08 hardening (GitHub Connect: PERMANENT fix).
 *
 * ONE shared hook, used by BOTH AddProjectWizard.jsx and NewUserWizard.jsx,
 * so the connect flow is a PURE FUNCTION of one authoritative endpoint
 * (`GET /github/app/status`) instead of each wizard guessing from its own
 * copy of a postMessage listener + an "installation count went up" poll.
 *
 * That old count-based poll could never detect "user added a repo to an
 * EXISTING installation" (the common case after the first connect) — this
 * hook reads `state` (pending|connected|error) from the live-verified
 * endpoint instead, so it can't miss that case, and it can't get stuck:
 * a real ~60s timeout flips to a designed "didn't finish, try again" state
 * instead of hanging forever.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { api, API_BASE, getToken } from "../lib/api";
import { trackFunnel } from "../lib/githubFunnel";

const POLL_INTERVAL_MS = 2500;
const MAX_WAIT_MS = 60_000;
// 2026-09-01 — CONFIRMED FIX (connect-flow investigation, Bug-2/Bug-3
// shared root): once GitHub confirms the install itself
// (`installation_active`), keep a slow background refresh going for
// up to this long so a repo list that's still propagating on GitHub's
// side (confirmed in real production data to lag well past 60s) fills
// in on its own instead of leaving the picker looking permanently
// empty/broken.
const BACKGROUND_SYNC_INTERVAL_MS = 5_000;
const BACKGROUND_SYNC_MAX_MS = 2 * 60_000;

const IDLE_STATUS = {
  installation_active: false,
  installations: [],
  repos: [],
  connected_repo: null,
  state: "idle",
  error: null,
};

export default function useGitHubConnectStatus() {
  const [status, setStatus] = useState(IDLE_STATUS);
  const [connecting, setConnecting] = useState(false);
  const [timedOut, setTimedOut] = useState(false);
  const [denied, setDenied] = useState(false);
  const popupRef = useRef(null);
  const pollRef = useRef(null);
  const bgSyncRef = useRef(null);
  const startRef = useRef(0);

  const fetchStatus = useCallback(async () => {
    try {
      const r = await api.get("/github/app/status");
      setStatus(r.data);
      return r.data;
    } catch {
      return null;
    }
  }, []);

  const stopBackgroundSync = useCallback(() => {
    if (bgSyncRef.current) { clearInterval(bgSyncRef.current); bgSyncRef.current = null; }
  }, []);

  // 2026-09-01 — Bug-2/Bug-3 fix, part 2: `installation_active` means
  // GitHub confirmed the grant — the connect flow itself is DONE.
  // `state !== "connected"` at that point means only the repo LIST is
  // still propagating on GitHub's side (real-world lag observed well
  // past 60s), not that anything failed. Keep quietly re-checking so
  // the picker fills in without the user having to do anything.
  const startBackgroundRepoSync = useCallback(() => {
    if (bgSyncRef.current) return;
    const startedAt = Date.now();
    bgSyncRef.current = setInterval(async () => {
      const data = await fetchStatus();
      if ((data && data.state === "connected") || Date.now() - startedAt > BACKGROUND_SYNC_MAX_MS) {
        stopBackgroundSync();
      }
    }, BACKGROUND_SYNC_INTERVAL_MS);
  }, [fetchStatus, stopBackgroundSync]);

  // Initial check on mount — so a wizard that opens for a user who is
  // ALREADY connected (e.g. adding a 2nd project) sees that immediately,
  // no popup needed.
  useEffect(() => { fetchStatus(); }, [fetchStatus]);

  const stopPolling = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
  }, []);

  useEffect(() => () => { stopPolling(); stopBackgroundSync(); }, [stopPolling, stopBackgroundSync]);

  const startConnect = useCallback(() => {
    const token = getToken();
    if (!token) return { ok: false, reason: "no_session" };
    const url = `${API_BASE}/github/app/install?auth=${encodeURIComponent(token)}`;
    const w = 720, h = 800;
    const left = Math.max(0, window.screenX + (window.outerWidth - w) / 2);
    const top = Math.max(0, window.screenY + (window.outerHeight - h) / 2);
    popupRef.current = window.open(
      url, "aurem_github_app_install",
      `width=${w},height=${h},left=${left},top=${top}`,
    );
    // 2026-08-27 · Journey Watch Phase 0 — this is the #2 dark click
    // from the signup drop-off investigation: this button previously
    // fired ZERO client-side tracking. Fire connect_repo_click here,
    // AFTER attempting window.open, so `popup_blocked` is captured as
    // real evidence instead of looking identical to "never clicked".
    trackFunnel("connect_repo_click", "wizard", {
      popup_blocked: !popupRef.current,
    });
    if (!popupRef.current) return { ok: false, reason: "popup_blocked" };

    // 2026-08-27 · Journey Watch Phase 0 — popup actually opened, so
    // GitHub's own auth/install screen is showing. Distinct from
    // connect_repo_click (fired above even on popup_blocked) so
    // Journey Watch can tell "clicked" apart from "reached GitHub".
    trackFunnel("github_auth_started", "wizard");

    setTimedOut(false);
    setDenied(false);
    setConnecting(true);
    startRef.current = Date.now();
    stopPolling();
    stopBackgroundSync();
    pollRef.current = setInterval(async () => {
      const data = await fetchStatus();
      // 2026-09-01 — CONFIRMED FIX (connect-flow investigation,
      // Bug-2 Mike Froedge / Bug-3 Michael Pelletier — one shared
      // root, confirmed from real production state pulls): exit on
      // `installation_active`, NOT `state === "connected"`.
      // `installation_active` is GitHub confirming the grant itself;
      // `state === "connected"` ADDITIONALLY requires the repo-list
      // API to have returned a non-empty list, which real production
      // data showed can lag for well over an hour after a genuinely
      // successful install. The old code only checked `state`, so a
      // real success + a closed popup (the bridge auto-closes in
      // 400ms) during that lag fired a FALSE app_install_denied and
      // stranded the user on an empty repo picker with nothing to
      // click — indistinguishable from "nothing happened".
      if (data && data.installation_active) {
        stopPolling();
        setConnecting(false);
        try { popupRef.current?.close?.(); } catch { /* cross-origin */ }
        trackFunnel("app_install_granted", "wizard");
        if (data.state !== "connected") {
          startBackgroundRepoSync();
        }
        return;
      }
      // GitHub Apps have no real "deny" callback (unlike OAuth Apps) —
      // the popup just closing without ever reaching an active
      // installation is the only observable signal that the user
      // backed out (a real success is caught above BEFORE this runs).
      if (popupRef.current?.closed) {
        stopPolling();
        setConnecting(false);
        setDenied(true);
        trackFunnel("app_install_denied", "wizard", { reason: "popup_closed" });
        return;
      }
      if (Date.now() - startRef.current > MAX_WAIT_MS) {
        stopPolling();
        setConnecting(false);
        setTimedOut(true);
        trackFunnel("app_install_denied", "wizard", { reason: "client_timeout" });
      }
    }, POLL_INTERVAL_MS);
    return { ok: true };
  }, [fetchStatus, stopPolling, stopBackgroundSync, startBackgroundRepoSync]);

  const retry = useCallback(() => startConnect(), [startConnect]);

  // Fast-path — if the bridge page's postMessage arrives, poll right
  // away instead of waiting for the next interval tick. Not required
  // for correctness (the interval poll is authoritative either way).
  useEffect(() => {
    function onMessage(e) {
      const d = e.data;
      if (!d || d.type !== "aurem-app-installed") return;
      fetchStatus();
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [fetchStatus]);

  return { status, connecting, timedOut, denied, startConnect, retry, refresh: fetchStatus };
}
