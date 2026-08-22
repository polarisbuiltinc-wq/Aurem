/**
 * RevokedRepoBanner.jsx — 2026-08-20
 *
 * Persistent in-chat banner: polls the SAME `/connection-status`
 * endpoint the sidebar uses (backend caches it 8s, safe to double-poll),
 * filtered to just the active project. When GitHub access has been
 * revoked/rejected, shows a red banner with a one-click "Reconnect
 * GitHub App" button so the user isn't stuck hunting through Settings
 * or getting a confusing chat-side misdiagnosis.
 *
 * Defense-in-depth: shows regardless of whether the sidebar dot or
 * cleanup banner also caught it — founder's explicit ask.
 */
import React, { useEffect, useRef, useState } from "react";
import { AlertTriangle, Github, Loader2 } from "lucide-react";
import { api, getToken, API_BASE } from "../lib/api";

const REASON_LABEL = {
  github_rejected:        "token rejected — access revoked or expired",
  no_token:               "no GitHub credential on file",
  repo_not_found:         "repo not found — renamed, deleted, or access removed",
  installation_suspended: "GitHub App installation suspended — an org admin paused it",
  installation_deleted:   "GitHub App installation removed — reinstall to reconnect",
  // 2026-06 PAT-removal — App-only typed codes:
  app_installation_missing: "not connected via the GitHub App — reconnect to continue",
  app_installation_revoked: "GitHub App installation revoked — reinstall to reconnect",
};

// App-level states fixed on GitHub's OWN settings page (unsuspend) or
// via a fresh install — the wizard's per-repo "Reconnect GitHub App"
// popup can't fix these, so they get a distinct CTA below.
const APP_LEVEL_REASONS = new Set(["installation_suspended", "installation_deleted"]);

export default function RevokedRepoBanner({ activeProject }) {
  const [status, setStatus]     = useState(null); // null | "connected" | "disconnected"
  const [reason, setReason]     = useState(null);
  const [installationId, setInstallationId] = useState(null);
  const [reconnecting, setReconnecting] = useState(false);
  const popupRef = useRef(null);

  const projectId = activeProject?.project_id;

  useEffect(() => {
    if (!projectId) { setStatus(null); return undefined; }
    let cancelled = false;
    const check = async () => {
      try {
        const token = getToken();
        const res = await fetch(`${API_BASE}/cto/projects/connection-status`, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
          signal: AbortSignal.timeout(15000),
        });
        if (!res.ok || cancelled) return;
        const j = await res.json();
        const mine = (j.statuses || []).find((s) => s.project_id === projectId);
        if (!cancelled && mine) {
          // 2026-06 — "unreachable" (network) is a TEMPORARY state and
          // must NEVER render as revoked. Tracked as its own status.
          setStatus(mine.status);
          setReason(mine.error || null);
          setInstallationId(mine.installation_id || null);
        }
      } catch { /* leave last known state on a transient blip */ }
    };
    check();
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") check();
    }, 30000);
    return () => { cancelled = true; clearInterval(timer); };
  }, [projectId]);

  async function reconnect() {
    const token = getToken();
    if (!token) return;
    setReconnecting(true);
    const url = `${API_BASE}/github/app/install?auth=${encodeURIComponent(token)}`;
    const w = 720, h = 800;
    const left = Math.max(0, window.screenX + (window.outerWidth  - w) / 2);
    const top  = Math.max(0, window.screenY + (window.outerHeight - h) / 2);
    popupRef.current = window.open(
      url, "aurem_github_app_reconnect",
      `width=${w},height=${h},left=${left},top=${top}`,
    );

    const finishIfCovered = async () => {
      try {
        const r = await api.get("/github/app/installations");
        const owner = (activeProject.github_owner || "").toLowerCase();
        const repo  = (activeProject.github_repo  || "").toLowerCase();
        for (const inst of (r.data?.installations || [])) {
          const hit = (inst.repositories || []).find(
            (x) => (x.full_name || "").toLowerCase() === `${owner}/${repo}`,
          );
          if (hit) {
            await api.patch(`/cto/projects/${projectId}`, {
              installation_id: inst.installation_id,
            });
            setStatus("connected");
            setReason(null);
            return true;
          }
        }
      } catch { /* keep polling */ }
      return false;
    };

    const started = Date.now();
    const poll = setInterval(async () => {
      if (popupRef.current?.closed) {
        clearInterval(poll);
        await finishIfCovered();
        setReconnecting(false);
        return;
      }
      if (Date.now() - started > 180_000) {
        clearInterval(poll);
        setReconnecting(false);
        return;
      }
      if (await finishIfCovered()) {
        clearInterval(poll);
        try { popupRef.current?.close?.(); } catch { /* xorigin */ }
        setReconnecting(false);
      }
    }, 1500);
  }

  // 2026-06 — network blips get a calm amber note, never the red
  // "revoked" banner (a timeout is not a revocation).
  if (status === "unreachable") {
    return (
      <div
        data-testid="github-unreachable-banner"
        style={{
          display: "flex", alignItems: "center", gap: 10,
          padding: "8px 14px", margin: "8px 0", borderRadius: 10,
          background: "rgba(245, 158, 11, 0.08)",
          border: "1px solid rgba(245, 158, 11, 0.35)",
          color: "#fbbf24", fontSize: 13,
        }}
      >
        <Loader2 size={15} className="animate-spin" />
        GitHub is unreachable right now (temporary network issue). Your
        repo connection is fine — retrying automatically.
      </div>
    );
  }

  if (status !== "disconnected") return null;

  const appLevel = APP_LEVEL_REASONS.has(reason);
  const suspended = reason === "installation_suspended";

  return (
    <div
      data-testid="revoked-repo-banner"
      style={{
        display: "flex", alignItems: "center", gap: 10,
        margin: "0 0 10px", padding: "10px 14px",
        background: "rgba(239,68,68,0.10)",
        border: "1px solid rgba(239,68,68,0.4)",
        borderRadius: 8, fontSize: 13, color: "#fca5a5",
      }}
    >
      <AlertTriangle size={16} style={{ flexShrink: 0, color: "#ef4444" }} />
      <span style={{ flex: 1, lineHeight: 1.4 }}>
        <strong style={{ color: "#fff" }}>
          {suspended
            ? "GitHub App access paused"
            : appLevel
              ? "GitHub App installation removed"
              : "GitHub access revoked"}
        </strong>{" "}
        for{" "}
        <code>{activeProject?.github_owner}/{activeProject?.github_repo}</code>
        {reason ? ` (${REASON_LABEL[reason] || reason})` : ""} — reconnect to keep chatting.
      </span>
      {suspended && installationId ? (
        <a
          data-testid="revoked-repo-reconnect-suspended-link"
          href={`https://github.com/settings/installations/${installationId}`}
          target="_blank"
          rel="noreferrer"
          style={{
            display: "inline-flex", alignItems: "center", gap: 6,
            padding: "6px 12px", fontSize: 12, fontWeight: 600,
            background: "var(--accent-2, #FF8A2A)", color: "#fff",
            borderRadius: 6, textDecoration: "none", whiteSpace: "nowrap",
          }}
        >
          <Github size={12} /> Reactivate on GitHub
        </a>
      ) : (
        <button
          type="button"
          data-testid="revoked-repo-reconnect-btn"
          onClick={reconnect}
          disabled={reconnecting}
          style={{
            display: "inline-flex", alignItems: "center", gap: 6,
            padding: "6px 12px", fontSize: 12, fontWeight: 600,
            background: reconnecting ? "rgba(255,255,255,0.08)" : "var(--accent-2, #FF8A2A)",
            color: reconnecting ? "#94a3b8" : "#fff",
            border: "none", borderRadius: 6,
            cursor: reconnecting ? "default" : "pointer",
            whiteSpace: "nowrap",
          }}
        >
          {reconnecting
            ? <><Loader2 size={12} className="animate-spin" /> Reconnecting…</>
            : <><Github size={12} /> Reconnect GitHub App</>}
        </button>
      )}
    </div>
  );
}
