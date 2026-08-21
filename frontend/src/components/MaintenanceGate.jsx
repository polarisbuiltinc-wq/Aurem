/**
 * MaintenanceGate.jsx — 2026-08.
 *
 * Wraps the whole app. Polls the public, unauthenticated
 * /maintenance/status endpoint:
 *   - manual_enabled=true  → show the maintenance screen immediately
 *     (admin/founder still bypass, so they can verify a fresh deploy).
 *   - fetch fails 2x in a row (~6s grace) → treat as a real outage
 *     and show the "brief hiccup, retrying" screen instead of a blank
 *     page / raw network error.
 * Keeps polling while blocked so it self-clears the moment the
 * backend/manual-flag recovers — no reload needed.
 */
import React, { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { getUser, isAdminOrFounder } from "../lib/api";
import MaintenanceScreen from "./MaintenanceScreen";

const BACKEND = process.env.REACT_APP_BACKEND_URL;
const POLL_MS = 3000;
const FAILS_BEFORE_BLOCK = 2;

// Routes an admin MUST be able to reach even during manual maintenance
// mode — otherwise a fresh/logged-out admin session could never sign
// in to turn maintenance off. These paths don't represent the "app"
// experience a maintenance screen protects.
const EXEMPT_PREFIXES = ["/login", "/signup", "/admin", "/magic-login", "/oauth-finish", "/reset-password", "/verify"];

export default function MaintenanceGate({ children }) {
  const location = useLocation();
  const pathExempt = EXEMPT_PREFIXES.some((p) => location.pathname.startsWith(p));
  const bypass = pathExempt || isAdminOrFounder(getUser());
  const [state, setState] = useState({ blocked: false, manual: false, message: "", window: "" });
  const failCountRef = useRef(0);
  const cancelledRef = useRef(false);

  useEffect(() => {
    if (bypass || !BACKEND) return;
    cancelledRef.current = false;

    async function poll() {
      try {
        const res = await fetch(`${BACKEND}/api/aurem-dev/maintenance/status`, {
          cache: "no-store",
          signal: AbortSignal.timeout(5000),
        });
        if (!res.ok) throw new Error(`status ${res.status}`);
        const data = await res.json();
        if (cancelledRef.current) return;
        failCountRef.current = 0;
        setState({
          blocked: !!data.manual_enabled,
          manual: !!data.manual_enabled,
          message: data.message || "",
          window: data.window || "",
        });
      } catch {
        if (cancelledRef.current) return;
        failCountRef.current += 1;
        if (failCountRef.current >= FAILS_BEFORE_BLOCK) {
          setState((s) => (s.manual ? s : { blocked: true, manual: false, message: "", window: "" }));
        }
      }
    }

    poll();
    const iv = setInterval(poll, POLL_MS);
    return () => { cancelledRef.current = true; clearInterval(iv); };
  }, [bypass]);

  if (bypass) return children;
  if (state.blocked) {
    return (
      <MaintenanceScreen manual={state.manual} message={state.message} window={state.window} />
    );
  }
  return children;
}
