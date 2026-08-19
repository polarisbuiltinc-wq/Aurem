/**
 * ResetPassword.jsx — 2026-08-19
 * Consumes the token from /auth/forgot-password's emailed link and
 * sets a new password. No auth required (the token itself is the
 * proof of ownership).
 */
import React, { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { KeyRound } from "lucide-react";
import AuthShell from "../components/AuthShell";
import usePageMeta from "../lib/usePageMeta";
import { api } from "../lib/api";
import PasswordStrengthMeter from "../components/PasswordStrengthMeter";
import PasswordInput from "../components/PasswordInput";

export default function ResetPassword() {
  usePageMeta({
    title: "Reset password · ORA by Aurem",
    description: "Set a new password for your AUREM account.",
  });
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token") || "";
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [done, setDone] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setError(null);
    if (password !== confirm) {
      setError("Passwords don't match.");
      return;
    }
    if (password.length < 6) {
      setError("Password must be at least 6 characters.");
      return;
    }
    setBusy(true);
    try {
      await api.post("/auth/reset-password", { token, new_password: password });
      setDone(true);
    } catch (e) {
      setError(e?.response?.data?.detail || "Reset link invalid or expired.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthShell>
      <section style={{ maxWidth: 440, margin: "20px auto" }}>
        <div style={{ textAlign: "center", marginBottom: 28 }}>
          <span className="eyebrow">reset password</span>
          <h1 className="serif" style={{ fontSize: 32, marginTop: 10 }}>
            Set a new password
          </h1>
        </div>

        <div className="card" data-testid="reset-password-card" style={{
          background: "rgba(20, 20, 28, 0.55)",
          backdropFilter: "blur(10px)",
          border: "1px solid rgba(255,255,255,0.06)",
        }}>
          {!token && (
            <div data-testid="reset-password-no-token" style={{
              fontSize: 13, color: "var(--danger)",
            }}>
              This link is missing its reset token. Request a new one from
              the <Link to="/login">sign-in page</Link>.
            </div>
          )}

          {token && done && (
            <div data-testid="reset-password-success" style={{ fontSize: 13, color: "var(--text-dim)" }}>
              Your password has been updated.{" "}
              <Link to="/login" data-testid="reset-password-to-login">Sign in →</Link>
            </div>
          )}

          {token && !done && (
            <form onSubmit={submit} style={{ display: "grid", gap: 16 }}>
              <label>
                <span className="label-mini">New password</span>
                <PasswordInput
                  testId="reset-password-input"
                  required
                  minLength={6}
                  autoComplete="new-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
                <PasswordStrengthMeter password={password} />
              </label>
              <label>
                <span className="label-mini">Confirm new password</span>
                <PasswordInput
                  testId="reset-password-confirm-input"
                  required
                  minLength={6}
                  autoComplete="new-password"
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                />
                {confirm && password !== confirm && (
                  <span
                    data-testid="reset-password-mismatch"
                    style={{
                      fontSize: 11,
                      color: "var(--danger, #ff6b6b)",
                      marginTop: 6,
                      display: "block",
                    }}
                  >
                    Passwords do not match
                  </span>
                )}
              </label>
              {error && (
                <div data-testid="reset-password-error" style={{
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
                data-testid="reset-password-submit"
                className="btn-primary"
                disabled={busy}
                style={{ justifyContent: "center" }}
              >
                <KeyRound size={15} /> {busy ? "Saving…" : "Set new password"}
              </button>
            </form>
          )}
        </div>
      </section>
    </AuthShell>
  );
}
