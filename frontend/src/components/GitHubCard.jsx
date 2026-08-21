/**
 * GitHubCard.jsx — Connect / Disconnect GitHub OAuth + show repos.
 *
 * 2026-08-20 — founder-reported confusion: "Disconnect" here only
 * ever clears the OAuth *login* (used for browsing/picking repos by
 * name) — it does NOT touch any GitHub App installation, which is
 * the actual mechanism granting ORA read/write access to a connected
 * project's repo. A project connected via the App keeps working
 * completely unaffected by this button, which looked like a bug
 * ("I disconnected GitHub but the repo still works everywhere") but
 * is really a missing distinction in the UI. Added: explicit scope
 * copy on the OAuth card + a real "GitHub App installations" section
 * below it, sourced from `/github/app/installations`, with its own
 * accurate status and a link to GitHub's own management page (the
 * only place that actually revokes App access).
 */
import React, { useEffect, useState, useCallback } from "react";
import { Github, ExternalLink, Plug, Unlink, Lock, FolderGit2, ShieldCheck } from "lucide-react";
import { api, API_BASE, getToken } from "../lib/api";
import { toast } from "./Toast";
import { trackFunnel, withFunnelParams } from "../lib/githubFunnel";

export default function GitHubCard() {
  const [status, setStatus] = useState(null);
  const [repos, setRepos] = useState([]);
  const [loadingRepos, setLoadingRepos] = useState(false);
  const [installs, setInstalls] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const r = await api.get("/github/oauth/status");
      setStatus(r.data);
    } catch {
      setStatus({ connected: false });
    }
  }, []);

  const refreshInstalls = useCallback(async () => {
    try {
      // 2026-08-20 — health endpoint (not the active-only list the
      // wizards use) so a suspended installation still shows up here
      // with an accurate status + reconnect CTA instead of silently
      // vanishing from Settings and looking like "never installed".
      const r = await api.get("/github/app/installations/health");
      setInstalls((r.data?.installations || []).filter((i) => i.status !== "deleted"));
    } catch {
      setInstalls([]);
    }
  }, []);

  useEffect(() => {
    refresh();
    refreshInstalls();
    // If we arrived from the OAuth callback redirect, surface a toast + clean URL
    const p = new URLSearchParams(window.location.search);
    if (p.get("github") === "connected") {
      toast({ message: `GitHub connected as @${p.get("login")}`, kind: "success" });
      window.history.replaceState({}, "", "/settings");
    } else if (p.get("github") === "error") {
      toast({ message: `GitHub connect failed: ${p.get("msg") || "unknown"}`, kind: "error" });
      window.history.replaceState({}, "", "/settings");
    }
  }, [refresh, refreshInstalls]);

  function connect() {
    const tok = getToken();
    // 2026-08-01 — funnel telemetry: cta_click on settings card.
    trackFunnel("cta_click", "settings_card", { has_token: !!tok });
    // GitHub doesn't forward arbitrary headers, so we open the connect endpoint
    // in the current window. JWT travels as an `?auth=` query param that the
    // backend reads as a fallback (see github_oauth router).
    const url = withFunnelParams(
      `${API_BASE}/github/oauth/connect?auth=${encodeURIComponent(tok || "")}`,
      "settings_card",
    );
    window.location.href = url;
  }

  async function disconnect() {
    try {
      await api.delete("/github/oauth/disconnect");
      setStatus({ connected: false });
      setRepos([]);
      toast({ message: "GitHub disconnected.", kind: "info" });
    } catch (e) {
      toast({ message: e?.response?.data?.detail || "Disconnect failed", kind: "error" });
    }
  }

  async function loadRepos() {
    setLoadingRepos(true);
    try {
      const r = await api.get("/github/oauth/repos");
      setRepos(r.data?.repos || []);
    } catch (e) {
      toast({ message: e?.response?.data?.detail || "Couldn't fetch repos", kind: "error" });
    } finally {
      setLoadingRepos(false);
    }
  }

  if (!status) return null;

  return (
    <>
    <section data-testid="settings-github-oauth" className="card" style={{ gridColumn: "1 / -1" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
        <Github size={20} style={{ color: "var(--accent-2)", flexShrink: 0 }} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="serif" style={{ fontSize: 16 }}>GitHub login</div>
          <div style={{ fontSize: 12, color: "var(--text-faint)" }}>
            {status.connected
              ? `Connected as @${status.login} — for browsing/picking repos by name. Doesn't affect any project's GitHub App access.`
              : "Connect to browse and pick your repos by name."}
          </div>
        </div>
        {status.connected ? (
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            {status.avatar_url && (
              <img
                src={status.avatar_url}
                alt={status.login}
                width={26}
                height={26}
                style={{ borderRadius: "50%", border: "1px solid var(--border-strong)" }}
              />
            )}
            <button
              data-testid="github-load-repos"
              className="btn-ghost"
              onClick={loadRepos}
              disabled={loadingRepos}
              style={{ padding: "6px 10px", fontSize: 11 }}
            >
              <FolderGit2 size={11} /> {loadingRepos ? "Loading…" : "My Repos"}
            </button>
            <button
              data-testid="github-disconnect"
              onClick={disconnect}
              className="btn-ghost"
              style={{
                padding: "6px 10px", fontSize: 11,
                borderColor: "rgba(255,107,107,0.3)", color: "var(--danger)",
              }}
            >
              <Unlink size={11} /> Disconnect
            </button>
          </div>
        ) : (
          <button
            data-testid="github-connect"
            onClick={connect}
            className="btn-primary"
            style={{ padding: "8px 14px", fontSize: 12 }}
          >
            <Plug size={13} /> Connect GitHub
          </button>
        )}
      </div>

      {repos.length > 0 && (
        <div data-testid="github-repo-list" style={{
          marginTop: 14, paddingTop: 14,
          borderTop: "1px solid var(--border)",
        }}>
          <span className="label-mini">Your repositories</span>
          <ul style={{ listStyle: "none", padding: 0, margin: "8px 0 0", display: "grid", gap: 4 }}>
            {repos.slice(0, 10).map((r) => (
              <li key={r.full_name} style={{
                display: "flex", alignItems: "center", gap: 6,
                fontSize: 12, color: "var(--text-dim)",
                padding: "6px 0", borderBottom: "1px solid var(--border)",
              }}>
                {r.private ? <Lock size={11} /> : <FolderGit2 size={11} />}
                <span style={{ flex: 1, fontFamily: "'JetBrains Mono', monospace" }}>
                  {r.full_name}
                </span>
                <a href={r.url} target="_blank" rel="noreferrer"
                   style={{ color: "var(--text-faint)" }}>
                  <ExternalLink size={11} />
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>

    {/* GitHub App installations — the ACTUAL access grant behind every
        App-connected project. Separate from the OAuth card above on
        purpose: disconnecting OAuth never touches this. */}
    <section data-testid="settings-github-app" className="card" style={{ gridColumn: "1 / -1", marginTop: 14 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
        <ShieldCheck size={20} style={{ color: "var(--accent-2)", flexShrink: 0 }} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="serif" style={{ fontSize: 16 }}>GitHub App access</div>
          <div style={{ fontSize: 12, color: "var(--text-faint)" }}>
            This is what actually grants ORA read/write access to your repos —
            separate from the login above. To fully revoke a repo, manage it here.
          </div>
        </div>
      </div>

      {installs === null ? null : installs.length === 0 ? (
        <div data-testid="settings-github-app-empty" style={{
          marginTop: 12, fontSize: 12, color: "var(--text-faint)",
        }}>
          No GitHub App installations found — your connected projects are
          using a Personal Access Token or your GitHub login instead.
        </div>
      ) : (
        <div data-testid="settings-github-app-list" style={{
          marginTop: 14, paddingTop: 14, borderTop: "1px solid var(--border)",
          display: "grid", gap: 10,
        }}>
          {installs.map((inst) => {
            const suspended = inst.status === "suspended";
            return (
              <div key={inst.installation_id} style={{
                display: "flex", alignItems: "center", gap: 10,
                fontSize: 12, color: "var(--text-dim)",
              }}>
                <span style={{
                  width: 7, height: 7, borderRadius: "50%",
                  background: suspended ? "#ef4444" : "#22c55e", flexShrink: 0,
                }} title={suspended ? "Suspended" : "Active"} />
                <span style={{ flex: 1 }}>
                  <span style={{ fontFamily: "'JetBrains Mono', monospace" }}>
                    @{inst.github_login} — {inst.repo_count}{" "}
                    repo{inst.repo_count === 1 ? "" : "s"} granted
                  </span>
                  {suspended && (
                    <div data-testid={`settings-github-app-suspended-${inst.installation_id}`}
                         style={{ color: "#fca5a5", marginTop: 2 }}>
                      Suspended — an org admin paused this installation on GitHub.
                    </div>
                  )}
                </span>
                <a
                  href={`https://github.com/settings/installations/${inst.installation_id}`}
                  target="_blank" rel="noreferrer"
                  data-testid={`settings-github-app-manage-${inst.installation_id}`}
                  style={{
                    display: "inline-flex", alignItems: "center", gap: 4,
                    fontSize: 11, fontWeight: suspended ? 600 : 400,
                    color: suspended ? "var(--accent-2, #FF8A2A)" : "var(--accent-2, #FF8A2A)",
                  }}
                >
                  {suspended ? "Reactivate on GitHub" : "Manage on GitHub"} <ExternalLink size={10} />
                </a>
              </div>
            );
          })}
        </div>
      )}
    </section>
    </>
  );
}
