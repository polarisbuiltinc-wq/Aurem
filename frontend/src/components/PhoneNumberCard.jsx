/**
 * PhoneNumberCard.jsx — 2026-08-25
 * Settings → Profile tab. Add/change/clear the OPTIONAL phone number
 * after signup — mainly for GitHub/Google OAuth accounts, which never
 * saw the signup-form phone field. Never required, never blocking.
 */
import React, { useState } from "react";
import { Phone } from "lucide-react";
import { isValidPhoneNumber } from "libphonenumber-js";
import { api } from "../lib/api";

export default function PhoneNumberCard({ me, onChange }) {
  const [value, setValue] = useState(me?.phone || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [ok, setOk] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setError(null);
    setOk(false);
    const trimmed = value.trim();
    if (trimmed && !isValidPhoneNumber(trimmed)) {
      setError("That phone number doesn't look valid — include the country code (e.g. +1 415 555 2671), or leave it blank.");
      return;
    }
    setBusy(true);
    try {
      const r = await api.post("/auth/update-phone", { phone: trimmed || null });
      setOk(true);
      onChange?.(r.data?.phone ?? null);
    } catch (e) {
      setError(e?.response?.data?.detail || "Couldn't save that number. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card" data-testid="settings-phone">
      <h3 style={{ fontSize: 14, color: "var(--text)", margin: 0, marginBottom: 6, display: "flex", alignItems: "center", gap: 8 }}>
        <Phone size={14} /> Phone number
      </h3>
      <p style={{ fontSize: 12, color: "var(--text-faint)", margin: "0 0 14px" }}>
        Optional — only used if we need to follow up beyond email. Leave blank to remove it.
      </p>
      <form onSubmit={submit} style={{ display: "grid", gap: 12 }}>
        <label>
          <span className="label-mini">Phone number (optional)</span>
          <input
            data-testid="settings-phone-input"
            className="input"
            type="tel"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="+1 415 555 2671"
          />
        </label>
        {error && (
          <div data-testid="settings-phone-error" style={{
            fontSize: 12, color: "var(--danger)",
            border: "1px solid rgba(255,107,107,0.25)",
            background: "rgba(255,107,107,0.06)",
            padding: "8px 10px", borderRadius: 4,
          }}>
            {error}
          </div>
        )}
        {ok && (
          <div data-testid="settings-phone-success" style={{
            fontSize: 12, color: "var(--ok, #6dd4a1)",
            border: "1px solid rgba(109,212,161,0.25)",
            background: "rgba(109,212,161,0.06)",
            padding: "8px 10px", borderRadius: 4,
          }}>
            Saved.
          </div>
        )}
        <button
          type="submit"
          data-testid="settings-phone-submit"
          className="btn-primary"
          disabled={busy}
          style={{ justifyContent: "center", width: "fit-content" }}
        >
          {busy ? "Saving…" : "Save"}
        </button>
      </form>
    </section>
  );
}
