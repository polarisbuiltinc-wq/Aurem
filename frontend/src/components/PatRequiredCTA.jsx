/**
 * PatRequiredCTA.jsx — Inline "Connect GitHub App" button inside chat
 * bubbles.
 *
 * 2026-08-24 · PAT-removal sweep — PATs are no longer an auth method
 * anywhere in AUREM. When ORA (or any assistant/tool) reports a GitHub
 * auth failure, this CTA deep-links to /projects?app=<active-project-id>
 * where Projects.jsx opens the per-project GitHub App connect modal.
 *
 * Detection is intentionally conservative — we only fire when the
 * message contains multiple auth-failure signals so we never show the
 * CTA on incidental mentions of "GitHub" or "token".
 */
import React from "react";
import { useNavigate } from "react-router-dom";
import { Github, ArrowRight } from "lucide-react";
import { getActiveProjectId, useActiveProject } from "./TabBar";

const AUTH_SIGNALS = [
  /\b401\b.*\b(github|bad credentials|authentication|unauthor)/i,
  /\b(bad credentials|invalid credentials)\b/i,
  /\bgithub app\b.*\b(revoked|missing|reinstall|reconnect|not installed)/i,
  /\binstallation\b.*\b(revoked|missing|suspended|reinstall)/i,
  /\bcredentials aren'?t available\b/i,
  // Legacy PAT phrasing can still appear in OLD persisted chat turns —
  // detect it so those historical bubbles get the modern App CTA too.
  /\bpersonal access token\b/i,
  /\bgithub pat\b/i,
  /\bfine[- ]grained pat\b/i,
  /\bcontents:\s*read/i,
];

function needsAppConnect(text) {
  if (!text || typeof text !== "string") return false;
  let hits = 0;
  for (const re of AUTH_SIGNALS) if (re.test(text)) hits++;
  // Require ≥2 distinct signals to avoid false positives on casual mentions.
  return hits >= 2;
}

export default function PatRequiredCTA({ text }) {
  const navigate = useNavigate();
  const activeProject = useActiveProject();

  // Healthy App-authed project → any auth chatter is commentary, not an
  // actionable setup signal.
  if (activeProject?.auth_method === "github_app" && activeProject?.installation_active) return null;

  if (!needsAppConnect(text)) return null;

  const projectId = getActiveProjectId();
  const target = projectId
    ? `/projects?app=${encodeURIComponent(projectId)}`
    : `/projects?add=1`;

  return (
    <div data-testid="chat-pat-cta" style={{
      marginTop: 12, padding: "10px 12px",
      background: "rgba(245,158,11,0.08)",
      border: "1px solid rgba(245,158,11,0.3)",
      borderRadius: 8,
      display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
    }}>
      <Github size={14} style={{ color: "#f59e0b", flexShrink: 0 }} />
      <span style={{ fontSize: 12, color: "var(--text-dim, #94a3b8)", flex: 1, minWidth: 0 }}>
        GitHub access for this repo is unavailable. Connect it via the AUREM
        GitHub App — one popup, no tokens to manage.
      </span>
      <button
        type="button"
        data-testid="chat-pat-cta-btn"
        onClick={() => navigate(target)}
        style={{
          padding: "6px 12px",
          background: "#f59e0b", color: "#0a0c10",
          border: "none", borderRadius: 6,
          fontSize: 12, fontWeight: 600, cursor: "pointer",
          display: "inline-flex", alignItems: "center", gap: 5,
          fontFamily: "'JetBrains Mono', monospace", letterSpacing: "0.04em",
          flexShrink: 0,
        }}
      >
        Connect GitHub App <ArrowRight size={11} />
      </button>
    </div>
  );
}
