/**
 * MagicLogin.jsx — 2026-08-20.
 *
 * Landing page for the "Resume your setup" links embedded in stage-
 * nudge emails (services/funnel_nudge_cron.py). Exchanges the
 * single-use `?token=` for a real session (POST /auth/magic-login/
 * exchange), then routes STRAIGHT to the exact screen the user left
 * off at — the GitHub-connect wizard if they're stuck pre-project,
 * or plain dashboard otherwise. Mirrors OAuthFinish.jsx's pattern.
 *
 * Three outcomes, matching the founder's explicit choice: no silent
 * fallback to a plain login page — always a clear message + a next
 * action.
 *   - success        → auto-login, redirect straight to the right screen
 *   - expired (410)   → "this link expired" + button to get a fresh one
 *   - used/invalid     → "already used" / "not valid" + button to /login
 */
import React, { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { setToken, setUser, api } from "../lib/api";

const SPINNER = (
  <div style={{
    width: 28, height: 28, margin: "0 auto 18px",
    border: "2px solid rgba(255,255,255,0.15)",
    borderTopColor: "#ffce7a",
    borderRadius: "50%",
    animation: "auremspin 0.9s linear infinite",
  }} />
);

function landingQueryFor(stage) {
  const qs = new URLSearchParams({ utm_source: "email", utm_campaign: "funnel_stage_nudge" });
  if (stage && stage !== "stage3_no_chat") qs.set("action", "connect-repo");
  return qs.toString();
}

export default function MagicLogin() {
  const nav = useNavigate();
  const [searchParams] = useSearchParams();
  const [phase, setPhase] = useState("loading"); // loading | expired | dead
  const [busy, setBusy] = useState(false);
  const token = searchParams.get("token") || "";

  async function finishLogin(sessionData) {
    setToken(sessionData.token);
    setUser({
      user_id: sessionData.user_id,
      email: sessionData.email,
      name: sessionData.name,
      tier: sessionData.tier,
      tokens_remaining: sessionData.tokens_remaining,
    });
    try { localStorage.setItem("aurem_just_logged_in", "1"); } catch { /* no-op */ }
    nav(`/dashboard?${landingQueryFor(sessionData.stage)}`, { replace: true });
  }

  useEffect(() => {
    if (!token) { setPhase("dead"); return; }
    (async () => {
      try {
        const r = await api.post("/auth/magic-login/exchange", { token });
        await finishLogin(r.data);
      } catch (e) {
        const status = e?.response?.status;
        const detail = e?.response?.data?.detail;
        setPhase(status === 410 && detail === "expired" ? "expired" : "dead");
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  async function getNewOne() {
    setBusy(true);
    try {
      const r = await api.post("/auth/magic-login/refresh", { token });
      await finishLogin(r.data);
    } catch {
      setBusy(false);
      setPhase("dead"); // already-used or something else went wrong — no more retries
    }
  }

  return (
    <div
      data-testid="magic-login-page"
      style={{
        minHeight: "100vh", display: "flex", alignItems: "center",
        justifyContent: "center", background: "#0a0e1a", color: "#e9edf2",
        fontFamily: "'JetBrains Mono', monospace", fontSize: 13,
        letterSpacing: "0.04em", padding: 24,
      }}
    >
      <div style={{ textAlign: "center", maxWidth: 380 }}>
        {phase === "loading" && (
          <>
            {SPINNER}
            <div data-testid="magic-login-status">Signing you in…</div>
          </>
        )}
        {phase === "expired" && (
          <>
            <div data-testid="magic-login-status" style={{ marginBottom: 16, lineHeight: 1.6 }}>
              This link has expired.<br />
              <span style={{ color: "#8a94a6" }}>No worries — links from these emails are only good for 7 days.</span>
            </div>
            <button
              data-testid="magic-login-get-new-one"
              onClick={getNewOne}
              disabled={busy}
              style={{
                background: "#ff8a2a", color: "#0a0e1a", border: "none",
                borderRadius: 6, padding: "10px 20px", fontSize: 13,
                fontFamily: "inherit", fontWeight: 600, cursor: busy ? "default" : "pointer",
                opacity: busy ? 0.6 : 1,
              }}
            >
              {busy ? "One sec…" : "Click here to get a new one →"}
            </button>
          </>
        )}
        {phase === "dead" && (
          <>
            <div data-testid="magic-login-status" style={{ marginBottom: 16, lineHeight: 1.6 }}>
              This link isn't valid — it may have already been used.<br />
              <span style={{ color: "#8a94a6" }}>Just log in normally and you'll land right back where you left off.</span>
            </div>
            <button
              data-testid="magic-login-go-to-login"
              onClick={() => nav("/login", { replace: true })}
              style={{
                background: "transparent", color: "#ffce7a",
                border: "1px solid rgba(255,206,122,0.4)",
                borderRadius: 6, padding: "10px 20px", fontSize: 13,
                fontFamily: "inherit", cursor: "pointer",
              }}
            >
              Go to login →
            </button>
          </>
        )}
        <style>{`@keyframes auremspin { to { transform: rotate(360deg); } }`}</style>
      </div>
    </div>
  );
}
