/**
 * githubFunnel.js — CTA drop-off telemetry for GitHub Connect flow
 * (2026-08-01, follow-up to Session 6+7).
 *
 * 5 stages we track (see routers/github_funnel.py for canonical list):
 *   1. cta_click        — user clicks a "Connect GitHub" button
 *   2. oauth_redirect   — backend logs this (server-side)
 *   3. callback_received — backend logs this (server-side)
 *   4. linked           — backend logs this (server-side)
 *   5. repo_selected    — user picks a repo (client-side)
 *
 * The client-side helper below fires stages 1 & 5. Backend fires 2/3/4
 * via `_funnel_track` in `routers/github_oauth.py`. A stable
 * `session_id` is stored in localStorage so all 5 events for one user
 * journey are stitchable.
 *
 * DESIGN: silent-fail. Telemetry MUST NEVER block the OAuth click.
 * If the POST fails, we swallow the error and proceed with the
 * navigation — funnel data is best-effort, user flow is sacred.
 */
import { API_BASE } from "./api";

const SESSION_KEY = "aurem_gh_funnel_session";

/** Get-or-create a stable session_id for the current browser. */
export function getFunnelSessionId() {
  try {
    let sid = localStorage.getItem(SESSION_KEY);
    if (!sid || sid.length < 16) {
      // Use crypto if available, else fall back to Math.random.
      const rnd =
        (typeof crypto !== "undefined" && crypto.randomUUID)
          ? crypto.randomUUID().replace(/-/g, "")
          : Math.random().toString(36).slice(2) +
            Math.random().toString(36).slice(2);
      sid = `c_${rnd.slice(0, 24)}`;
      localStorage.setItem(SESSION_KEY, sid);
    }
    return sid;
  } catch {
    // localStorage disabled — return a per-page ephemeral id.
    return `c_ephemeral_${Date.now().toString(36)}`;
  }
}

/**
 * Fire a funnel event. Best-effort, never throws.
 * @param {string} stage   — one of the 5 canonical stages
 * @param {string} source  — "login" / "signup" / "settings_card" / "wizard" / "projects"
 * @param {object} [meta]  — extra small dict (source-specific)
 */
export async function trackFunnel(stage, source, meta) {
  try {
    const session_id = getFunnelSessionId();
    // Use `fetch` with keepalive so the beacon survives page navigation
    // (critical for cta_click which is followed by window.location.href).
    const body = JSON.stringify({
      stage,
      source: source || "unknown",
      session_id,
      meta: meta || {},
    });
    await fetch(`${API_BASE}/funnel/github/event`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body,
      keepalive: true,       // survive navigation
      credentials: "omit",   // anonymous ingestion; no cookie needed
      // Guard 18 — every outbound fetch must have a timeout signal
      // so a stuck server can never wedge the caller. 5s is generous
      // for a fire-and-forget telemetry POST; the .catch() below
      // swallows the AbortError silently on timeout.
      signal: AbortSignal.timeout(5000),
    }).catch(() => {});
  } catch {
    // Silent — telemetry never blocks flow.
  }
}

/** Convenience: stitch the funnel session_id onto a GitHub-connect URL. */
export function withFunnelParams(url, source) {
  const sid = getFunnelSessionId();
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}fs=${encodeURIComponent(sid)}&fsrc=${encodeURIComponent(source || "unknown")}`;
}
