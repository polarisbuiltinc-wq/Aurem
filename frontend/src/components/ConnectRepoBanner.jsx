/**
 * components/ConnectRepoBanner.jsx — Persistent empty-state CTA.
 *
 * Renders above the chat panel when the user has no projects connected,
 * even after they dismiss the onboarding wizard.  Collapsible so it
 * doesn't dominate the screen on every reload; the collapsed state is
 * persisted to localStorage.
 *
 * Visibility contract:
 *   - Mount this only when the caller has confirmed projectCount === 0.
 *   - Polls /founder-offer/status every 60 s so the "X of 500" counter
 *     stays roughly fresh without hammering the unauthenticated route.
 *   - Hides itself when the founder offer is fully consumed (remaining
 *     === 0) — at that point there's no SEO reward to dangle, the
 *     existing wizard remains the entry point.
 *
 * The "Connect repo →" button fires `aurem:open-connect-repo` so the
 * parent (Dashboard) can show the wizard regardless of the dismiss
 * flag in localStorage.
 */
import React, { useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronUp, Loader2 } from "lucide-react";
import { api } from "../lib/api";
import { trackFunnel, getFunnelSessionId } from "../lib/githubFunnel";
import useGitHubConnectStatus from "../hooks/useGitHubConnectStatus";

const COLLAPSE_KEY = "aurem_connect_banner_collapsed";

export default function ConnectRepoBanner({ onConnect, onProjectCreated }) {
  // 2026-08-27 · Journey Watch Phase 0 — this CTA was the #1 dark
  // click identified in the signup drop-off investigation: `onConnect`
  // only ever flipped local React state, so a click here was
  // indistinguishable from never clicking at all. Fire connect_repo_click
  // FIRST (fire-and-forget, never blocks the actual UI action).
  const handleConnectClick = useCallback(() => {
    trackFunnel("connect_repo_click", "banner");
    onConnect?.();
  }, [onConnect]);
  const [status, setStatus] = useState(null);
  const [collapsed, setCollapsed] = useState(() => {
    try { return localStorage.getItem(COLLAPSE_KEY) === "1"; }
    catch { return false; }
  });

  // 2026-09-01 — CONFIRMED FIX (connect-flow investigation, Bug-3
  // Michael Pelletier): mounting this banner already GUARANTEES
  // projectCount === 0 (per the visibility contract above). If the
  // GitHub App install is ALSO already active with real repos on it,
  // the correct move is to let the user PICK one of those repos right
  // here and create the project — NOT re-run the install flow, which
  // GitHub just answers "already installed" to, producing no visible
  // change and stranding the user in a loop.
  const ghConnect = useGitHubConnectStatus();
  const installations = ghConnect?.status?.installations || [];
  const installationActive = Boolean(ghConnect?.status?.installation_active);
  const allRepos = installations.flatMap((inst) => (inst.repositories || []).map((r) => ({
    ...r, installation_id: inst.installation_id,
  })));
  const hasReposButNoProject = installationActive && allRepos.length > 0;
  const hasZeroRepos = installationActive && installations.length > 0 && allRepos.length === 0;
  const [creatingRepo, setCreatingRepo] = useState(null);
  const [createErr, setCreateErr] = useState("");
  // 2026-09-01 — connect-flow refinement: D2 (already-added redirect)
  // + D3 (short "Connected ✓" beat before landing, standard pattern).
  const [successRepo, setSuccessRepo] = useState(null);
  const [alreadyConnected, setAlreadyConnected] = useState(null); // {repo, projectId, projectName}

  const createProjectFromRepo = useCallback(async (repo) => {
    setCreateErr("");
    setAlreadyConnected(null);
    setCreatingRepo(repo.full_name);
    try {
      const name = repo.full_name.split("/").pop();
      const r = await api.post("/cto/projects/add", {
        name,
        github_url: `https://github.com/${repo.full_name}`,
        branch: repo.default_branch || "main",
        funnel_session: getFunnelSessionId(),
        installation_id: repo.installation_id,
      });
      trackFunnel("app_repo_selected", "banner", { full_name: repo.full_name });
      const newProjectId = r.data?.project_id;
      setSuccessRepo(repo.full_name);
      setTimeout(() => { onProjectCreated?.(newProjectId); }, 1000);
    } catch (e) {
      const detail = e?.response?.data?.detail;
      if (typeof detail === "object" && detail?.error === "already_connected") {
        setAlreadyConnected({
          repo: repo.full_name,
          projectId: detail.project_id,
          projectName: detail.project_name,
        });
        return;
      }
      setCreateErr(
        (typeof detail === "object" && detail?.message) ? detail.message
          : (typeof detail === "string" ? detail : "Could not connect that repo — try again."),
      );
    } finally {
      setCreatingRepo(null);
    }
  }, [onProjectCreated]);

  // ── Polling (60 s) ──────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    const refresh = () => {
      api.get("/founder-offer/status")
        .then((r) => { if (!cancelled) setStatus(r.data); })
        .catch(() => { /* best-effort */ });
    };
    refresh();
    const t = setInterval(refresh, 60_000);
    return () => { cancelled = true; clearInterval(t); };
  }, []);

  const toggleCollapsed = useCallback(() => {
    setCollapsed((c) => {
      const next = !c;
      try { localStorage.setItem(COLLAPSE_KEY, next ? "1" : "0"); }
      catch { /* ignore */ }
      return next;
    });
  }, []);

  // ── Visibility ──────────────────────────────────────────────────
  // Hide entirely once the offer sells out; the SEO incentive is gone
  // and the regular wizard is the only sensible empty-state UI.
  if (status && (status.remaining ?? 0) <= 0) return null;

  const remaining = status?.remaining;
  // Total spots come from the backend so we never hardcode the promo
  // ceiling in the UI.  Falls back to a neutral loading state until the
  // first /founder-offer/status response lands.
  const total = typeof status?.total === "number" ? status.total : null;

  return (
    <div
      data-testid="connect-repo-banner"
      style={{
        margin: "12px 18px 0",
        padding: collapsed ? "10px 16px" : "16px 18px",
        background:
          "linear-gradient(135deg, rgba(234,179,8,0.12) 0%, rgba(234,179,8,0.04) 100%)",
        border: "1px solid rgba(234,179,8,0.40)",
        borderRadius: 10,
        display: "flex", flexDirection: "column", gap: collapsed ? 0 : 12,
        boxShadow: "0 1px 3px rgba(0,0,0,0.05)",
        transition: "padding 180ms ease, gap 180ms ease",
        flexShrink: 0,
      }}
    >
      {/* Header — visible in both expanded + collapsed modes */}
      <div
        style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          gap: 12, flexWrap: "wrap",
        }}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
          <div
            data-testid="connect-repo-banner-headline"
            style={{
              fontWeight: 600, fontSize: 15,
              color: "var(--text-strong, var(--text, #fff))",
            }}
          >
            {hasReposButNoProject
              ? "You're connected — pick a repo to finish up"
              : "Connect a repo to unlock your free SEO fix"}
          </div>
          <div
            data-testid="connect-repo-banner-counter"
            style={{
              fontSize: 11,
              fontFamily: "'JetBrains Mono', monospace",
              letterSpacing: "0.04em",
              color: counterColor(remaining),
            }}
          >
            {typeof remaining === "number" && typeof total === "number"
              ? `${remaining} of ${total} founder spots remaining`
              : "Loading founder spots…"}
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexShrink: 0 }}>
          {!hasReposButNoProject && (
            <button
              type="button"
              data-testid="connect-repo-banner-cta"
              onClick={handleConnectClick}
              className="btn-primary"
              style={{
                padding: "8px 16px",
                fontSize: 13, fontWeight: 600,
                whiteSpace: "nowrap",
              }}
            >
              {hasZeroRepos ? "Select your repos →" : "Connect repo →"}
            </button>
          )}
          <button
            type="button"
            data-testid="connect-repo-banner-toggle"
            onClick={toggleCollapsed}
            title={collapsed ? "Show how the connect flow works" : "Hide details"}
            style={{
              padding: 6,
              background: "transparent",
              border: "1px solid var(--border, rgba(255,255,255,0.12))",
              borderRadius: 6,
              color: "var(--text-dim, #aaa)",
              cursor: "pointer",
              display: "inline-flex",
            }}
          >
            {collapsed ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
          </button>
        </div>
      </div>

      {/* 2026-09-01 — CONFIRMED FIX (Bug-3, Michael Pelletier): install
          is active with real repos already on it, but no project was
          ever created. Offer those repos directly instead of routing
          back through the install flow (GitHub just says "already
          installed" to a re-run, which is why the old CTA looped with
          no visible result). */}
      {hasReposButNoProject && !collapsed && (
        <div data-testid="connect-repo-banner-repo-picker" style={{
          paddingTop: 10, borderTop: "1px dashed rgba(234,179,8,0.30)",
        }}>
          {successRepo ? (
            <div data-testid="connect-repo-banner-success-beat" style={{
              display: "flex", alignItems: "center", gap: 8,
              fontSize: 12.5, color: "#22c55e", fontWeight: 600,
            }}>
              <span style={{
                display: "inline-flex", alignItems: "center", justifyContent: "center",
                width: 18, height: 18, borderRadius: "50%",
                background: "rgba(34,197,94,0.16)", fontSize: 11,
              }}>✓</span>
              Connected — taking you to {successRepo} now…
            </div>
          ) : alreadyConnected ? (
            <div data-testid="connect-repo-banner-already-connected" style={{
              display: "flex", flexDirection: "column", gap: 8,
            }}>
              <div style={{ fontSize: 12.5, color: "var(--text-dim, #b8b8b8)" }}>
                <strong>{alreadyConnected.repo}</strong> is already your project{" "}
                <strong>{alreadyConnected.projectName}</strong>.
              </div>
              <button
                type="button"
                data-testid="connect-repo-banner-open-existing-btn"
                onClick={() => onProjectCreated?.(alreadyConnected.projectId)}
                className="btn-primary"
                style={{ alignSelf: "flex-start", padding: "6px 12px", fontSize: 12, fontWeight: 600 }}
              >
                Open my {alreadyConnected.repo} project →
              </button>
            </div>
          ) : (<>
          {createErr && (
            <div data-testid="connect-repo-banner-create-error" style={{
              fontSize: 12, color: "#ef4444", marginBottom: 8,
            }}>
              {createErr}
            </div>
          )}
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {allRepos.map((repo) => (
              <button
                key={repo.full_name}
                type="button"
                data-testid={`connect-repo-banner-repo-${repo.full_name}`}
                onClick={() => createProjectFromRepo(repo)}
                disabled={creatingRepo === repo.full_name}
                style={{
                  textAlign: "left", padding: "8px 12px", fontSize: 12.5,
                  background: "rgba(234,179,8,0.08)",
                  border: "1px solid rgba(234,179,8,0.30)", borderRadius: 6,
                  color: "var(--text, #fff)", cursor: "pointer",
                  display: "flex", alignItems: "center", gap: 8,
                }}
              >
                {creatingRepo === repo.full_name
                  ? <Loader2 size={12} className="spin" />
                  : null}
                {repo.full_name}
              </button>
            ))}
          </div>
          </>)}
        </div>
      )}

      {/* 2026-02-12 · App-first flow — the wizard is the single source
          of truth for the connect UX. Copy focuses on the one-click
          GitHub App install; the wizard itself still offers a PAT
          fallback for private / legacy repos, so we don't advertise
          PAT setup here. */}
      {!collapsed && !hasReposButNoProject && (
        <div
          data-testid="connect-repo-banner-steps"
          style={{
            paddingTop: 10,
            borderTop: "1px dashed rgba(234,179,8,0.30)",
            fontSize: 12.5,
            color: "var(--text-dim, #b8b8b8)",
            lineHeight: 1.6,
          }}
        >
          {hasZeroRepos
            ? <>You connected the GitHub App but haven't selected any
                repos yet. Click <strong>Select your repos →</strong> above
                to pick which ones to grant access to.</>
            : <>Click <strong>Connect repo →</strong> above to install the{" "}
                <strong>Aurem GitHub App</strong> — one click, no tokens to
                manage. Choose which repositories to grant access to, and
                Aurem will start indexing immediately.</>}
        </div>
      )}
    </div>
  );
}


function counterColor(remaining) {
  if (typeof remaining !== "number") return "var(--text-dim, #aaa)";
  if (remaining <= 10) return "#ef4444";
  if (remaining <= 50) return "#f97316";
  return "#22c55e";
}
