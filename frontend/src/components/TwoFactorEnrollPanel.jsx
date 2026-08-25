/**
 * TwoFactorEnrollPanel.jsx — QR + secret + backup codes + confirm form
 * for 2FA enrollment. Extracted from TwoFactorCard.jsx (2026-08-27,
 * mechanical split — no behaviour change) to keep that file under the
 * platform's file-size guard.
 */
import React from "react";
import { Copy as CopyIcon } from "lucide-react";
import { toast } from "./Toast";

export function EnrollPanel({ enroll, code, onCode, onConfirm, onCancel, busy }) {
  const copy = async (text, label) => {
    try { await navigator.clipboard.writeText(text); toast({ message: `${label} copied`, kind: "info" }); }
    catch { /* ignore */ }
  };
  return (
    <div data-testid="admin-2fa-enroll-panel" style={{ display: "grid", gap: 16, marginTop: 8 }}>
      <div style={{
        padding: 14, borderRadius: 6,
        border: "1px solid rgba(255,200,120,0.20)",
        background: "rgba(255,200,120,0.04)",
        color: "#FFD58A", fontSize: 11, lineHeight: 1.5,
      }}>
        <strong>Step 1.</strong> Scan the QR with Google Authenticator / 1Password / Authy.
        <br />
        <strong>Step 2.</strong> Save the backup codes below — they&apos;re shown ONCE.
        <br />
        <strong>Step 3.</strong> Type the 6-digit code your app shows to activate.
      </div>

      <div style={{ display: "flex", gap: 16, alignItems: "flex-start", flexWrap: "wrap" }}>
        <img
          data-testid="admin-2fa-qr"
          src={enroll.qr_png}
          alt="2FA QR code"
          style={{ width: 180, height: 180, borderRadius: 6, background: "white", padding: 6 }}
        />
        <div style={{ flex: 1, minWidth: 220 }}>
          <span className="label-mini">Manual entry secret</span>
          <div
            data-testid="admin-2fa-secret"
            onClick={() => copy(enroll.secret, "Secret")}
            style={{
              fontFamily: "'JetBrains Mono', monospace", fontSize: 11,
              padding: "6px 8px", marginTop: 4, marginBottom: 12,
              background: "rgba(255,255,255,0.04)",
              border: "1px solid rgba(255,255,255,0.08)",
              borderRadius: 4, cursor: "pointer",
              display: "flex", alignItems: "center", gap: 6,
              wordBreak: "break-all",
            }}
            title="Click to copy"
          >
            <CopyIcon size={11} />
            <span>{enroll.secret}</span>
          </div>

          <span className="label-mini">Backup codes (save these!)</span>
          <div
            data-testid="admin-2fa-backup-codes"
            style={{
              fontFamily: "'JetBrains Mono', monospace", fontSize: 11,
              padding: "8px 10px", marginTop: 4,
              background: "rgba(255,255,255,0.04)",
              border: "1px solid rgba(255,138,42,0.30)",
              borderRadius: 4,
              display: "grid", gridTemplateColumns: "1fr 1fr", gap: 4,
            }}
          >
            {(enroll.backup_codes || []).map((c) => (
              <span key={c} style={{ color: "var(--text)" }}>{c}</span>
            ))}
          </div>
          <button
            type="button"
            data-testid="admin-2fa-copy-backups"
            onClick={() => copy((enroll.backup_codes || []).join("\n"), "Backup codes")}
            className="btn-ghost"
            style={{ fontSize: 10, marginTop: 6 }}
          >
            <CopyIcon size={10} style={{ marginRight: 4 }} /> Copy all
          </button>
        </div>
      </div>

      <form onSubmit={onConfirm} style={{ display: "grid", gap: 8, maxWidth: 320 }}>
        <span className="label-mini">Enter the 6-digit code from your authenticator</span>
        <input
          data-testid="admin-2fa-confirm-input"
          className="input"
          value={code}
          onChange={(e) => onCode(e.target.value)}
          placeholder="000000"
          inputMode="numeric"
          pattern="[0-9]{6}"
          maxLength={6}
          autoFocus
          style={{
            fontFamily: "'JetBrains Mono', monospace",
            letterSpacing: "0.4em", fontSize: 18, textAlign: "center",
          }}
        />
        <div style={{ display: "flex", gap: 8 }}>
          <button
            data-testid="admin-2fa-confirm-submit"
            type="submit" disabled={busy || code.replace(/\D/g, "").length < 6}
            className="btn-primary" style={{ flex: 1, justifyContent: "center" }}
          >
            {busy ? "Activating…" : "Activate 2FA"}
          </button>
          <button
            type="button"
            data-testid="admin-2fa-enroll-cancel"
            onClick={onCancel}
            className="btn-ghost" style={{ flex: 1, justifyContent: "center" }}
          >
            Cancel
          </button>
        </div>
      </form>
    </div>
  );
}
