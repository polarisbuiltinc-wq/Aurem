/**
 * Login.jsx — Developer sign-in.
 */
import React, { useState, useEffect } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { LogIn, Github } from "lucide-react";
import GoogleIcon from "../components/GoogleIcon";
import AuthShell from "../components/AuthShell";
import usePageMeta from "../lib/usePageMeta";
import { api, setToken, setUser } from "../lib/api";
import RobotGuide, { RobotGuideKeyframes, escapeHtml } from "../components/RobotGuide";
import PasswordInput from "../components/PasswordInput";

export default function Login() {
  usePageMeta({
    title: "Sign in · ORA by Aurem",
    description: "Sign in to your AUREM account and continue shipping features to your GitHub repo with an autonomous AI engineer.",
    canonical: (typeof window !== "undefined" ? window.location.origin : "") + "/login",
  });
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  // Honour ?next=… for safe in-app paths only (must start with /, not //)
  const rawNext = searchParams.get("next") || "";
  const next = rawNext.startsWith("/") && !rawNext.startsWith("//") ? rawNext : "/dashboard";
  // Iter 113 — friendly banner when user cancelled the GitHub OAuth flow
  // and was sent back here.
  const cancelled = searchParams.get("github") === "cancelled";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  // Iter 388t · GDPR self-delete flow lands here with ?deleted=1.
  // Surface a one-shot success banner (auto-dismisses on next Tab).
  const [deletedBanner, setDeletedBanner] = useState(
    () => searchParams.get("deleted") === "1",
  );
  // Iter 212m-187 — admin-editable welcome message
  const [welcomeMsg, setWelcomeMsg] = useState("");
  useEffect(() => {
    api.get("/auth/robot-guide")
      .then((r) => setWelcomeMsg(r.data?.login_message || ""))
      .catch(() => {});
  }, []);
  // Iter 212m-20 — Admin 2FA challenge state. `null` = normal email/pw
  // form. `{ mfa_token, email }` = the password was correct but the
  // account has TOTP enabled, so we now collect the 6-digit code.
  const [mfaState, setMfaState] = useState(null);
  const [mfaCode,  setMfaCode]  = useState("");
  const [useBackup, setUseBackup] = useState(false);
  // 2026-08-19 — self-service forgot-password toggle.
  const [forgotMode, setForgotMode] = useState(false);
  const [forgotBusy, setForgotBusy] = useState(false);
  const [forgotDone, setForgotDone] = useState(false);

  async function submitForgot(e) {
    e.preventDefault();
    setForgotBusy(true);
    setError(null);
    try {
      await api.post("/auth/forgot-password", { email: email.trim() });
      setForgotDone(true);
    } catch (e) {
      setError(e?.response?.data?.detail || "Could not send reset link. Try again.");
    } finally {
      setForgotBusy(false);
    }
  }

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api.post("/auth/login", { email: email.trim(), password });
      // Iter 212m-20 — admin 2FA gate. Switch to the 2FA step instead
      // of issuing a session.
      if (r.data?.mfa_required && r.data?.mfa_token) {
        setMfaState({ mfa_token: r.data.mfa_token, email: r.data.email || email });
        setBusy(false);
        return;
      }
      setToken(r.data.token);
      setUser({
        user_id: r.data.user_id,
        email: r.data.email,
        name: r.data.name,
        tier: r.data.tier,
        tokens_remaining: r.data.tokens_remaining,
      });
      try { localStorage.setItem("aurem_just_logged_in", "1"); } catch { /* ignore */ }
      // Iter 212m-235 — Track-based routing on LOGIN (there is no
      // signup-time prompt anymore; /choose-track was removed in
      // Iter 390). Fetch the user's persisted track from /auth/me
      // and route accordingly. If ?next=… was set explicitly, that
      // always wins. Existing users on track="personal" still land
      // on /build so their workspace is preserved.
      if (next !== "/dashboard") {
        navigate(next, { replace: true });
      } else {
        try {
          const me = await api.get("/auth/me");
          const track = me.data?.user?.track;
          navigate(track === "personal" ? "/build" : "/dashboard", { replace: true });
        } catch {
          navigate("/dashboard", { replace: true });
        }
      }
    } catch (e) {
      setError(e?.response?.data?.detail || "Sign in failed. Try again.");
    } finally {
      setBusy(false);
    }
  }

  // Iter 212m-20 — submit the second-leg verify with the 6-digit code
  // (or a backup recovery code) in exchange for the real session JWT.
  async function submitMfa(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const payload = { mfa_token: mfaState.mfa_token };
    if (useBackup) payload.backup_code = mfaCode.trim();
    else           payload.code        = mfaCode.replace(/\D/g, "");
    try {
      const r = await api.post("/auth/login/2fa-verify", payload);
      setToken(r.data.token);
      setUser({
        user_id: r.data.user_id,
        email: r.data.email,
        name: r.data.name,
        tier: r.data.tier,
        tokens_remaining: r.data.tokens_remaining,
      });
      try { localStorage.setItem("aurem_just_logged_in", "1"); } catch { /* ignore */ }
      // Iter 212m-235 — same track-based routing as the primary
      // login handler above.
      if (next !== "/dashboard") {
        navigate(next, { replace: true });
      } else {
        try {
          const me = await api.get("/auth/me");
          const track = me.data?.user?.track;
          navigate(track === "personal" ? "/build" : "/dashboard", { replace: true });
        } catch {
          navigate("/dashboard", { replace: true });
        }
      }
    } catch (e) {
      setError(e?.response?.data?.detail || "Invalid 2FA code. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthShell
      secondaryCta={
        <Link to="/signup" data-testid="auth-nav-signup" className="btn-primary" style={{ fontSize: 12 }}>
          Get started
        </Link>
      }
    >
      <section style={{ maxWidth: 440, margin: "20px auto" }}>
        {deletedBanner && (
          <div
            data-testid="login-deleted-banner"
            role="status"
            style={{
              background: "rgba(74, 222, 128, 0.10)",
              border: "1px solid rgba(74, 222, 128, 0.35)",
              color: "rgb(74, 222, 128)",
              padding: "10px 14px",
              borderRadius: 6,
              marginBottom: 20,
              fontSize: 13,
              lineHeight: 1.5,
            }}
          >
            <strong>Your account has been permanently deleted.</strong>
            <br />
            <span style={{ opacity: 0.85 }}>
              All associated data has been purged from our systems.
              You can create a new account at any time.
            </span>
            <button
              type="button"
              onClick={() => setDeletedBanner(false)}
              aria-label="Dismiss deletion notice"
              style={{
                float: "right", background: "transparent", border: "none",
                color: "inherit", cursor: "pointer", fontSize: 16,
                lineHeight: 1, opacity: 0.7, marginLeft: 8,
              }}
            >×</button>
          </div>
        )}
        <div style={{ textAlign: "center", marginBottom: 28 }}>
          <span className="eyebrow">sign in</span>
          <h1 className="serif" style={{ fontSize: 32, marginTop: 10 }}>Welcome back, builder.</h1>
          <p style={{ fontSize: 13, color: "var(--text-dim)" }}>
            Sign in to your <strong>AUREM</strong> account
            <span style={{ color: "var(--text-dim)", opacity: 0.7 }}> — for developers</span>.
          </p>
        </div>

        <div className="card" data-testid="login-card" style={{
          background: "rgba(20, 20, 28, 0.55)",
          backdropFilter: "blur(10px)",
          border: "1px solid rgba(255,255,255,0.06)",
        }}>
          <RobotGuideKeyframes />
          <RobotGuide
            testid="login-robot-guide"
            kind={error ? "error" : "info"}
            message={
              error
                ? `Hmm — <strong>${escapeHtml(error)}</strong>. Try again, or use GitHub above.`
                : cancelled
                  ? `No worries — GitHub sign-in was cancelled. Try again, or sign in with email below. <span class="ora-arrow">👇</span>`
                  : email && password.length >= 6
                    ? `Looks good — hit <strong>Sign in</strong> when you&rsquo;re ready. <span class="ora-arrow">👇</span>`
                    : email
                      ? `Now enter your <strong>password</strong> and sign in. <span class="ora-arrow">👇</span>`
                      : (welcomeMsg || `<strong>Fastest way:</strong> click <strong>Continue with GitHub</strong> below <span class="ora-arrow">👇</span> — one tap, no password.`)
            }
          />
          {/* Iter 113 — friendly banner when GitHub OAuth was cancelled */}
          {cancelled && (
            <div data-testid="login-github-cancelled" style={{
              padding: "10px 12px", marginBottom: 12,
              borderRadius: 4,
              background: "rgba(255,138,42,0.08)",
              border: "1px solid rgba(255,138,42,0.35)",
              color: "#ff8a2a", fontSize: 12, lineHeight: 1.5,
            }}>
              GitHub sign-in cancelled. You can try again or use email below.
            </div>
          )}
          {/* Iter 212m-183 — Google OAuth (Emergent-managed) one-click */}
          {/* REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR
              REDIRECT URLS, THIS BREAKS THE AUTH */}
          <button
            type="button"
            data-testid="login-google-oauth"
            onClick={() => {
              const redirectUrl = window.location.origin + "/oauth-finish";
              window.location.href =
                `https://auth.emergentagent.com/?redirect=${encodeURIComponent(redirectUrl)}`;
            }}
            style={{
              padding: "12px 14px", marginBottom: 12,
              borderRadius: 4, cursor: "pointer",
              background: "#fff", color: "#1f1f1f",
              border: "1px solid #dadce0",
              fontWeight: 600, fontSize: 13,
              display: "flex", alignItems: "center", gap: 8,
              justifyContent: "center", width: "100%",
              fontFamily: "'JetBrains Mono', monospace",
              letterSpacing: "0.04em",
            }}
          >
            <GoogleIcon size={16} /> Continue with Google
          </button>
          {/* Iter 50 — GitHub OAuth-first CTA (signup-killer removed) */}
          <button
            type="button"
            data-testid="login-github-oauth"
            onClick={async () => {
              // 2026-08-01 — funnel telemetry: cta_click. Fires before
              // the redirect; uses fetch keepalive so it survives.
              try {
                const { trackFunnel, withFunnelParams } = await import("../lib/githubFunnel");
                await trackFunnel("cta_click", "login", { intent: "login" });
                // Use the live origin so the OAuth callback returns to
                // whichever domain the user actually loaded the app from
                // (preview pod, auremcto.com, custom domain). Reading
                // REACT_APP_BACKEND_URL here would lock us to the build-
                // time value and break across environments.
                const base = window.location.origin;
                // Iter 113 — pass intent=login so backend redirects to
                // /login (not /signup) if the user clicks Cancel on
                // GitHub's consent screen.
                const url = withFunnelParams(
                  `${base}/api/aurem-dev/github/oauth/connect?signup=1&intent=login`,
                  "login",
                );
                window.location.href = url;
              } catch {
                // Telemetry lib failure MUST NOT block the OAuth flow.
                const base = window.location.origin;
                window.location.href = `${base}/api/aurem-dev/github/oauth/connect?signup=1&intent=login`;
              }
            }}
            style={{
              padding: "12px 14px", marginBottom: 16,
              borderRadius: 4, cursor: "pointer",
              background: "#0d1117", color: "#fff",
              border: "1px solid #30363d",
              fontWeight: 600, fontSize: 13,
              display: "flex", alignItems: "center", gap: 8,
              justifyContent: "center", width: "100%",
              fontFamily: "'JetBrains Mono', monospace",
              letterSpacing: "0.04em",
            }}
          >
            <Github size={14} /> Continue with GitHub
          </button>
          <div style={{
            display: "flex", alignItems: "center", gap: 10,
            margin: "0 0 16px 0", color: "var(--text-faint)",
            fontSize: 10, letterSpacing: "0.1em",
          }}>
            <div style={{ flex: 1, height: 1, background: "rgba(255,255,255,0.08)" }} />
            <span>OR EMAIL</span>
            <div style={{ flex: 1, height: 1, background: "rgba(255,255,255,0.08)" }} />
          </div>
          <form onSubmit={submit} style={{ display: mfaState || forgotMode ? "none" : "grid", gap: 16 }}>
            <label>
              <span className="label-mini">Email</span>
              <input
                data-testid="login-email"
                className="input"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@company.com"
              />
            </label>

            <label>
              <span className="label-mini">Password</span>
              <PasswordInput
                testId="login-password"
                required
                minLength={6}
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>

            <button
              type="button"
              data-testid="login-forgot-password-link"
              onClick={() => { setForgotMode(true); setError(null); setForgotDone(false); }}
              style={{
                background: "transparent", border: "none", padding: 0,
                color: "var(--accent-2)", cursor: "pointer",
                fontSize: 12, textAlign: "right", justifySelf: "end",
              }}
            >
              Forgot password?
            </button>

            {error && (
              <div data-testid="login-error" style={{
                fontSize: 12, color: "var(--danger)",
                border: "1px solid rgba(255,107,107,0.25)",
                background: "rgba(255,107,107,0.06)",
                padding: "10px 12px", borderRadius: 4,
              }}>
                {error}
              </div>
            )}

            <button
              type="submit"
              data-testid="login-submit"
              className="btn-primary"
              disabled={busy}
              style={{ justifyContent: "center" }}
            >
              <LogIn size={15} /> {busy ? "Signing in…" : "Sign in"}
            </button>
          </form>

          {/* 2026-08-19 — forgot-password mini-flow, same card. */}
          {forgotMode && !mfaState && (
            <form
              data-testid="login-forgot-password-form"
              onSubmit={submitForgot}
              style={{ display: "grid", gap: 16 }}
            >
              {forgotDone ? (
                <div data-testid="login-forgot-password-done" style={{
                  fontSize: 13, color: "var(--text-dim)",
                  padding: "10px 12px", borderRadius: 4,
                  background: "rgba(109,212,161,0.06)",
                  border: "1px solid rgba(109,212,161,0.25)",
                }}>
                  If that email exists, a reset link has been sent. Check your inbox.
                </div>
              ) : (
                <>
                  <label>
                    <span className="label-mini">Email</span>
                    <input
                      data-testid="login-forgot-password-email"
                      className="input"
                      type="email"
                      required
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      placeholder="you@company.com"
                    />
                  </label>
                  {error && (
                    <div data-testid="login-forgot-password-error" style={{
                      fontSize: 12, color: "var(--danger)",
                      border: "1px solid rgba(255,107,107,0.25)",
                      background: "rgba(255,107,107,0.06)",
                      padding: "10px 12px", borderRadius: 4,
                    }}>
                      {error}
                    </div>
                  )}
                  <button
                    type="submit"
                    data-testid="login-forgot-password-submit"
                    className="btn-primary"
                    disabled={forgotBusy}
                    style={{ justifyContent: "center" }}
                  >
                    {forgotBusy ? "Sending…" : "Send reset link"}
                  </button>
                </>
              )}
              <button
                type="button"
                data-testid="login-forgot-password-cancel"
                onClick={() => { setForgotMode(false); setForgotDone(false); setError(null); }}
                style={{
                  background: "transparent", border: "none",
                  color: "var(--text-faint)", cursor: "pointer",
                  padding: 0, fontSize: 11, justifySelf: "start",
                }}
              >
                ← Back to sign in
              </button>
            </form>
          )}

          {/* Iter 212m-20 — second leg of admin 2FA login. Shown only
              after the password step returned `mfa_required:true`. */}
          {mfaState && (
            <form
              data-testid="login-2fa-form"
              onSubmit={submitMfa}
              style={{ display: "grid", gap: 16 }}
            >
              <div style={{
                fontSize: 12, color: "var(--text-dim)",
                padding: "10px 12px", borderRadius: 4,
                background: "rgba(255,197,96,0.06)",
                border: "1px solid rgba(255,197,96,0.25)",
              }}>
                🔐 Two-factor required for <strong>{mfaState.email}</strong>.
                {useBackup
                  ? " Enter a backup code from when you enrolled."
                  : " Enter the 6-digit code from your authenticator app."}
              </div>
              <label>
                <span className="label-mini">
                  {useBackup ? "Backup code" : "Authenticator code"}
                </span>
                <input
                  data-testid={useBackup ? "login-2fa-backup-input" : "login-2fa-code-input"}
                  className="input"
                  type="text"
                  required
                  autoFocus
                  value={mfaCode}
                  onChange={(e) => setMfaCode(e.target.value)}
                  placeholder={useBackup ? "XXXX-XXXX-XXXX" : "000000"}
                  inputMode={useBackup ? "text" : "numeric"}
                  pattern={useBackup ? undefined : "[0-9]{6}"}
                  maxLength={useBackup ? 14 : 6}
                  style={{
                    fontFamily: "'JetBrains Mono', monospace",
                    letterSpacing: useBackup ? "0.12em" : "0.4em",
                    fontSize: 18,
                    textAlign: "center",
                  }}
                />
              </label>
              {error && (
                <div data-testid="login-2fa-error" style={{
                  fontSize: 12, color: "var(--danger)",
                  border: "1px solid rgba(255,107,107,0.25)",
                  background: "rgba(255,107,107,0.06)",
                  padding: "10px 12px", borderRadius: 4,
                }}>
                  {error}
                </div>
              )}
              <button
                type="submit"
                data-testid="login-2fa-submit"
                className="btn-primary"
                disabled={busy || mfaCode.length < (useBackup ? 12 : 6)}
                style={{ justifyContent: "center" }}
              >
                <LogIn size={15} /> {busy ? "Verifying…" : "Verify & sign in"}
              </button>
              <div style={{
                display: "flex", justifyContent: "space-between",
                fontSize: 11, color: "var(--text-dim)",
              }}>
                <button
                  type="button"
                  data-testid="login-2fa-toggle-backup"
                  onClick={() => { setUseBackup((v) => !v); setMfaCode(""); setError(null); }}
                  style={{
                    background: "transparent", border: "none",
                    color: "var(--accent-2)", cursor: "pointer",
                    padding: 0, fontSize: 11,
                  }}
                >
                  {useBackup
                    ? "← Use authenticator code instead"
                    : "Use a backup code →"}
                </button>
                <button
                  type="button"
                  data-testid="login-2fa-cancel"
                  onClick={() => {
                    setMfaState(null); setMfaCode(""); setUseBackup(false);
                    setError(null);
                  }}
                  style={{
                    background: "transparent", border: "none",
                    color: "var(--text-faint)", cursor: "pointer",
                    padding: 0, fontSize: 11,
                  }}
                >
                  Cancel
                </button>
              </div>
            </form>
          )}

          <div style={{
            marginTop: 22, paddingTop: 18,
            borderTop: "1px solid var(--border)",
            textAlign: "center", fontSize: 13, color: "var(--text-dim)",
          }}>
            No account yet?{" "}
            <Link to="/signup" data-testid="login-to-signup">Claim 1000 tokens →</Link>
          </div>
        </div>
      </section>
    </AuthShell>
  );
}
