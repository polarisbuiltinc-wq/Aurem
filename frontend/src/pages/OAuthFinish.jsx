/**
 * OAuthFinish.jsx — final hop after GitHub OAuth signup/sign-in.
 *
 * Backend redirects here with the JWT in the URL fragment (#token=…
 * &login=…) so the token never lands in server access logs or Referer
 * headers. We stash it in localStorage and route to /dashboard.
 *
 * Failure modes (no token, bad fragment) bounce the user back to /login
 * with a friendly error.
 */
import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { setToken, setUser, api } from "../lib/api";
import { trackSignup, metaCompleteRegistration } from "../lib/analytics";

export default function OAuthFinish() {
  const nav = useNavigate();
  const [status, setStatus] = useState("Signing you in…");
  // 2026-08-25 — root-cause fix for a founder-reported bug: URL bar
  // showed /dashboard, then unexpectedly bounced to
  // /login?github=missing_token. Root cause: if this effect ever
  // runs a second time (React double-invoke, remount, fast
  // back/forward nav) AFTER the first run already succeeded, the
  // first run's `history.replaceState(..., "/oauth-finish")` (below)
  // has already cleared the URL hash — so the second run reads an
  // EMPTY hash, finds no token, and incorrectly redirects to
  // /login?...missing_token even though sign-in already succeeded.
  // Guard: only the FIRST invocation is allowed to act.
  const hasRun = React.useRef(false);

  useEffect(() => {
    async function run() {
      if (hasRun.current) return;
      hasRun.current = true;
      try {
        // Read fragment: "#token=eyJ...&login=octocat" (GitHub) OR
        // "#session_id=..." (Google via Emergent-managed OAuth).
        const raw = (window.location.hash || "").replace(/^#/, "");
        const parts = new URLSearchParams(raw);

        // ── Google path (Emergent-managed OAuth) ──────────────────
        // REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR
        // REDIRECT URLS, THIS BREAKS THE AUTH
        const sessionId = parts.get("session_id");
        if (sessionId) {
          let d;
          try {
            const resp = await api.post("/auth/google/session", {
              session_id: sessionId,
            });
            d = resp.data || {};
          } catch (e) {
            setStatus("Google sign-in failed. Sending you back…");
            setTimeout(() => nav("/login?google=error", { replace: true }), 1000);
            return;
          }
          if (!d.token) {
            setStatus("No sign-in token returned. Redirecting…");
            setTimeout(() => nav("/login?google=missing_token", { replace: true }), 800);
            return;
          }
          setToken(d.token);
          setUser({
            user_id:          d.user_id,
            email:            d.email,
            name:             d.name || d.email,
            tier:             d.tier,
            tokens_remaining: d.tokens_remaining,
          });
          // 2026-08-25 — root-cause fix: same class of bug fixed below
          // in the GitHub/direct-Google branch — these were `await`ed
          // sequentially before navigating, each eligible for the full
          // 60s axios timeout, which could leave the user stuck on a
          // blank page for minutes on a slow network. Fire in the
          // background instead; neither is needed for the redirect.
          try {
            const ref = localStorage.getItem("aurem_ref");
            if (ref && ref !== d.user_id) {
              api.post("/referrals/attribute", { ref_code: ref }).catch(() => {});
              localStorage.removeItem("aurem_ref");
            }
          } catch { /* non-blocking */ }
          // 2026-08-20 — Attribute ad click captured on landing.
          try {
            const raw = localStorage.getItem("aurem_ad_attr");
            if (raw) {
              api.post("/ads/attribute-click", JSON.parse(raw)).catch(() => {});
              localStorage.removeItem("aurem_ad_attr");
            }
          } catch { /* non-blocking */ }
          try { localStorage.setItem("aurem_just_logged_in", "1"); } catch {}
          if (d.new) {
            trackSignup();
            metaCompleteRegistration("google");
          }
          try { window.history.replaceState(null, "", "/oauth-finish"); } catch {}
          setStatus("Signed in. Redirecting to your dashboard…");
          nav("/dashboard", { replace: true });
          return;
        }

        // ── GitHub path (backend redirect with #token=…) ──────────
        const token = parts.get("token");
        const login = parts.get("login") || "";
        // 2026-08-25 — the direct-Google-OAuth callback reuses this
        // SAME branch (also redirects with #token=), so a real
        // provider label is needed for an honest error redirect
        // instead of always saying "github".
        const provider = parts.get("provider") === "google" ? "google" : "github";
        // Iter 156 — `new=1` is set by the backend only when this
        // OAuth callback minted a brand-new account row. We fire
        // the Google Ads signup conversion at most once per session.
        const isNewAccount = parts.get("new") === "1";
        if (!token) {
          setStatus("No sign-in token returned. Redirecting…");
          setTimeout(() => nav(`/login?${provider}=missing_token`, { replace: true }), 800);
          return;
        }
        setToken(token);
        // 2026-08-25 — root-cause fix: this used to `await` /usage/me
        // and the ad-attribution POST sequentially BEFORE navigating,
        // each with the shared 60s axios timeout. Any real slowness on
        // either call (cold backend, slow network) left the user
        // staring at a blank "Signing you in…" page for up to 2 minutes
        // combined — exactly the "stuck on /oauth-finish" symptom
        // reported live. Shell.jsx already refetches /usage/me right
        // after landing on /dashboard anyway, so none of this needs to
        // block the redirect — set a minimal user from the URL params
        // we already have, fire the rest in the background, and
        // navigate immediately.
        setUser({ name: login || "developer" });
        api.get("/usage/me").then((me) => {
          if (me?.data?.user) {
            setUser({
              user_id:          me.data.user.user_id,
              email:            me.data.user.email,
              name:             me.data.user.name || login || me.data.user.email,
              tier:             me.data.user.tier,
              tokens_remaining: me.data.user.tokens_remaining,
            });
          }
        }).catch(() => { /* non-fatal — Dashboard/Shell will refetch */ });
        // Flag for the PWA install popup so the Dashboard can prompt.
        try { localStorage.setItem("aurem_just_logged_in", "1"); } catch {}
        // 2026-08-20 — Attribute ad click captured on landing. Fire in
        // the background too — same reasoning as above.
        try {
          const raw = localStorage.getItem("aurem_ad_attr");
          if (raw) {
            api.post("/ads/attribute-click", JSON.parse(raw)).catch(() => {});
            localStorage.removeItem("aurem_ad_attr");
          }
        } catch { /* non-blocking */ }
        // Iter 156 — Google Ads signup conversion for GitHub OAuth
        // path. Only fires when backend flagged this hop as a new
        // account; return-logins are a no-op.
        if (isNewAccount) {
          trackSignup();
          metaCompleteRegistration("github");
        }
        // Clear the fragment so a refresh doesn't replay it.
        try {
          window.history.replaceState(null, "", "/oauth-finish");
        } catch {}
        setStatus("Signed in. Redirecting to your dashboard…");
        nav("/dashboard", { replace: true });
      } catch (e) {
        setStatus("Sign-in failed. Sending you back to login…");
        setTimeout(() => nav("/login?github=error", { replace: true }), 1200);
      }
    }
    run();
  }, [nav]);

  return (
    <div
      data-testid="oauth-finish"
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "#0a0e1a",
        color: "#e9edf2",
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 13,
        letterSpacing: "0.04em",
      }}
    >
      <div style={{ textAlign: "center" }}>
        <div
          style={{
            width: 28, height: 28, margin: "0 auto 18px",
            border: "2px solid rgba(255,255,255,0.15)",
            borderTopColor: "#ffce7a",
            borderRadius: "50%",
            animation: "auremspin 0.9s linear infinite",
          }}
        />
        <div data-testid="oauth-finish-status">{status}</div>
        <style>{`@keyframes auremspin { to { transform: rotate(360deg); } }`}</style>
      </div>
    </div>
  );
}
