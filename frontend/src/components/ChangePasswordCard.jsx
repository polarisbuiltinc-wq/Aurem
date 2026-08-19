/**
 * ChangePasswordCard.jsx — 2026-08-19
 * Settings → Profile tab. Only rendered for password-based accounts
 * (`me.has_password`); GitHub/Google-only accounts have nothing to
 * change here.
 */
import React, { useState } from "react";
import { KeyRound } from "lucide-react";
import { api } from "../lib/api";
import PasswordStrengthMeter from "./PasswordStrengthMeter";
import PasswordInput from "./PasswordInput";

export default function ChangePasswordCard() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [ok, setOk] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setError(null);
    setOk(false);
    if (next !== confirm) {
      setError("New passwords don't match.");
      return;
    }
    if (next.length < 6) {
      setError("New password must be at least 6 characters.");
      return;
    }
    setBusy(true);
    try {
      await api.post("/auth/change-password", {
        current_password: current, new_password: next,
      });
      setOk(true);
      setCurrent(""); setNext(""); setConfirm("");
    } catch (e) {
      setError(e?.response?.data?.detail || "Could not change password.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card" data-testid="settings-change-password">
      <h3 style={{ fontSize: 14, color: "var(--text)", margin: 0, marginBottom: 14, display: "flex", alignItems: "center", gap: 8 }}>
        <KeyRound size={14} /> Change password
      </h3>
      <form onSubmit={submit} style={{ display: "grid", gap: 12 }}>
        <label>
          <span className="label-mini">Current password</span>
          <PasswordInput
            testId="change-password-current"
            required
            autoComplete="current-password"
            value={current} onChange={(e) => setCurrent(e.target.value)}
          />
        </label>
        <label>
          <span className="label-mini">New password</span>
          <PasswordInput
            testId="change-password-new"
            required
            minLength={6}
            autoComplete="new-password"
            value={next} onChange={(e) => setNext(e.target.value)}
          />
          <PasswordStrengthMeter password={next} />
        </label>
        <label>
          <span className="label-mini">Confirm new password</span>
          <PasswordInput
            testId="change-password-confirm"
            required
            minLength={6}
            autoComplete="new-password"
            value={confirm} onChange={(e) => setConfirm(e.target.value)}
          />
        </label>
        {error && (
          <div data-testid="change-password-error" style={{
            fontSize: 12, color: "var(--danger)",
            border: "1px solid rgba(255,107,107,0.25)",
            background: "rgba(255,107,107,0.06)",
            padding: "8px 10px", borderRadius: 4,
          }}>
            {error}
          </div>
        )}
        {ok && (
          <div data-testid="change-password-success" style={{
            fontSize: 12, color: "var(--ok, #6dd4a1)",
            border: "1px solid rgba(109,212,161,0.25)",
            background: "rgba(109,212,161,0.06)",
            padding: "8px 10px", borderRadius: 4,
          }}>
            Password updated.
          </div>
        )}
        <button
          type="submit"
          data-testid="change-password-submit"
          className="btn-primary"
          disabled={busy}
          style={{ justifyContent: "center", width: "fit-content" }}
        >
          {busy ? "Saving…" : "Update password"}
        </button>
      </form>
    </section>
  );
}
