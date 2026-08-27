/**
 * NewUserWizard.jsx — Onboarding wizard for fresh signups (0 projects).
 *
 *   Connect repo → POST /cto/projects/add → straight to the chat window.
 *
 * 2026-08-20 — founder's call: removed the old forced "first task" +
 * "shipping" steps (2/3). Connecting a repo is now the whole job here;
 * the user lands in the normal chat window afterward like anyone else.
 *
 * Dismiss rules:
 *   • localStorage["aurem_wizard_dismissed"] = "1" → never show again
 *   • Triggered when projects.length === 0 AND the flag is unset
 *
 * Iter 73 Task 3.
 */
import React, { useEffect, useRef, useState } from "react";
import { Loader2, X, ArrowRight, Github, RefreshCw } from "lucide-react";
import { api, getToken, API_BASE } from "../lib/api";
import { trackFunnel, withFunnelParams, getFunnelSessionId } from "../lib/githubFunnel";
import { setActiveProjectId } from "./TabBar";
import RobotGuide, { RobotGuideKeyframes, escapeHtml, oraPulseRingStyle } from "./RobotGuide";
import useModalA11y from "../hooks/useModalA11y";
import useGitHubConnectStatus from "../hooks/useGitHubConnectStatus";

const DISMISS_KEY = "aurem_wizard_dismissed";
const REPO_RX = /^(https?:\/\/)?(www\.)?github\.com\/[\w.-]+\/[\w.-]+\/?$/i;

export function isWizardDismissed() {
  try { return localStorage.getItem(DISMISS_KEY) === "1"; }
  catch { return true; }
}
export function dismissWizard() {
  try { localStorage.setItem(DISMISS_KEY, "1"); } catch { /* private mode */ }
}

export default function NewUserWizard({ onComplete }) {
  const [step]         = useState(1);
  const [repoUrl, setRepoUrl]   = useState("");
  const [branch, setBranch]     = useState("main");
  const [projectId, setProject] = useState(null);
  const [busy, setBusy]         = useState(false);
  const [err, setErr]           = useState("");

  // 2026-08-24 · Guard 22 — Phase 3.1 blueprint gap: the "start from
  // an idea" path (routers/scaffold.py — brief → real generated repo
  // + real project, fully built, Vercel auto-deploy included) had
  // ZERO frontend entry point. "connect" is the default so existing
  // muscle-memory/tests are untouched; "scaffold" is the new 3rd path.
  const [mode, setMode] = useState("connect"); // "connect" | "scaffold"
  const [ideaText, setIdeaText]         = useState("");
  const [scaffoldBusy, setScaffoldBusy] = useState(false);
  const [scaffoldErr, setScaffoldErr]   = useState("");
  const [draft, setDraft]               = useState(null); // {draft_id, files, stack_detected}
  const [materializing, setMaterializing] = useState(false);

  // GitHub OAuth state — drives whether step 1 shows a Connect button
  // or the repo URL input + picker.
  const [ghStatus, setGhStatus] = useState("checking"); // "checking" | "connected" | "disconnected"
  const [ghLogin, setGhLogin]   = useState("");
  const [repos, setRepos]       = useState([]);
  const [reposBusy, setReposBusy] = useState(false);
  const pollRef = useRef(null);
  const popupRef = useRef(null);

  // ─── 2026-08 hardening (GitHub Connect: PERMANENT fix) ───────────
  // ONE shared hook (also used by AddProjectWizard.jsx) — the wizard
  // is a pure function of the authoritative /github/app/status
  // endpoint (live-verified against GitHub), not a local
  // postMessage/count-poll guess that could never detect "repo added
  // to an existing installation" and could get stuck forever if
  // postMessage was dropped.
  const {
    status: ghConnectStatus, connecting: appConnecting,
    timedOut: appTimedOut, startConnect: startAppConnect,
    retry: retryAppConnect, refresh: refreshGhConnectStatus,
  } = useGitHubConnectStatus();
  const appInstalls = ghConnectStatus.installations;
  const appPickerActive = ghConnectStatus.installation_active;

  // Initial OAuth status check.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // 2026-08-20 — CRITICAL FIX: previously this checked ONLY legacy
        // GitHub OAuth link status. A user who signed up/logged in via
        // "Continue with GitHub" (OAuth identity link) but never
        // installed the Aurem GitHub App was landing straight on
        // ghStatus="connected" — which skips the "choosing" step
        // entirely and shows ONLY the repo dropdown, with the
        // recommended "Continue with GitHub App" CTA never surfaced
        // at all. Root cause: OAuth-linked ≠ App-installed, but the
        // old logic treated them as equivalent. Fix: always check for
        // an active App installation FIRST. Only skip straight past
        // the App CTA if one already exists.
        const [oauthRes, ghStatusData] = await Promise.allSettled([
          api.get("/github/oauth/status"),
          refreshGhConnectStatus(),
        ]);
        if (cancelled) return;

        const oauthConnected = oauthRes.status === "fulfilled" && oauthRes.value.data?.connected;
        if (oauthConnected) {
          setGhLogin(oauthRes.value.data.login || "");
          fetchRepos();  // convenience dropdown for OAuth-linked accounts
        }

        const data = ghStatusData.status === "fulfilled" ? ghStatusData.value : null;
        if (data?.installation_active) {
          // Already has the App installed — go straight to its repo
          // picker, no CTA needed.
          if (data.connected_repo) {
            setRepoUrl(`https://github.com/${data.connected_repo}`);
            const inst = (data.installations || []).find(
              (i) => (i.repositories || []).some((r) => r.full_name === data.connected_repo),
            );
            const repo = inst?.repositories?.find((r) => r.full_name === data.connected_repo);
            setBranch(repo?.default_branch || "main");
          }
          setGhStatus("choosing");
        } else {
          // No App installed yet (whether or not legacy OAuth is
          // linked) — land on "choosing" so the App-install CTA is
          // always the first thing a user without an App sees.
          setGhStatus("choosing");
        }
      } catch {
        if (!cancelled) setGhStatus("choosing");
      }
    })();
    return () => {
      cancelled = true;
      if (pollRef.current) clearInterval(pollRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Auto-select the repo the moment status resolves to exactly one —
  // whether that's on initial load or right after a connect completes.
  useEffect(() => {
    if (ghConnectStatus.connected_repo && !repoUrl) {
      setRepoUrl(`https://github.com/${ghConnectStatus.connected_repo}`);
      const repo = (ghConnectStatus.repos || [])[0];
      setBranch(repo?.default_branch || "main");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ghConnectStatus.connected_repo]);

  async function fetchRepos() {
    setReposBusy(true);
    try {
      const r = await api.get("/github/oauth/repos");
      setRepos(r.data?.repos || []);
    } catch {
      setRepos([]);
    } finally {
      setReposBusy(false);
    }
  }

  // ─── 2026-08 hardening (GitHub Connect: PERMANENT fix) — replaces
  // the old fetchAppInstallations/openAppInstallPopup/count-poll with
  // the shared hook's startAppConnect(). See useGitHubConnectStatus.js.
  function openAppInstallPopup() {
    const r = startAppConnect();
    if (!r.ok) {
      setErr(r.reason === "popup_blocked"
        ? "Popup blocked — please allow popups for this site and try again."
        : "Session expired — please log in again.");
    }
  }

  // postMessage is now just a fast-path nudge inside the hook itself;
  // this listener only surfaces the wizard-specific error/pending
  // toasts (unchanged copy), since the hook's polling of the
  // authoritative status endpoint is what actually drives correctness.
  useEffect(() => {
    function onMessage(e) {
      const d = e.data;
      if (!d || d.type !== "aurem-app-installed") return;
      if (d.status === "err") {
        setErr(
          d.err === "invalid_state"
            ? "Session expired while installing. Please try again."
            : d.err === "github_probe_failed"
              ? "GitHub couldn't verify the install. Try again."
              : "Install did not complete — please try again.",
        );
      } else if (d.status === "pending") {
        setErr(
          "Install is pending your org admin's approval. " +
          "You'll be able to continue once they accept.",
        );
      }
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  function connectGithub() {
    const token = getToken();
    if (!token) {
      setErr("Session expired — please log in again.");
      return;
    }
    // 2026-08-24 — ROOT FIX for oauth_redirect over-count: the old code
    // opened the popup with an UNSTITCHED /connect URL, then a dynamic
    // import asynchronously rewrote popup.location to the stitched URL —
    // navigating /connect TWICE (once with no `fs`, once with it), which
    // logged two server-side oauth_redirect events under two different
    // session ids. githubFunnel is now statically imported so the URL is
    // stitched synchronously and the popup navigates exactly once.
    trackFunnel("cta_click", "wizard", { has_token: true });
    const url = withFunnelParams(
      `${API_BASE}/github/oauth/connect?auth=${encodeURIComponent(token)}`,
      "wizard",
    );
    const w = 560, h = 720;
    const left = Math.max(0, window.screenX + (window.outerWidth  - w) / 2);
    const top  = Math.max(0, window.screenY + (window.outerHeight - h) / 2);
    popupRef.current = window.open(
      url, "aurem_github_oauth",
      `width=${w},height=${h},left=${left},top=${top}`,
    );
    // Rule 6 — no silent failures. Same window.open() null-return gap
    // as the App-install popup above.
    if (!popupRef.current) {
      setErr("Popup blocked — please allow popups for this site and try again.");
      return;
    }
    // Poll status every 2 s until either: connected, popup closed by user,
    // or 90 s timeout.  This is more reliable than postMessage across
    // GitHub's domain handoff.
    if (pollRef.current) clearInterval(pollRef.current);
    const started = Date.now();
    pollRef.current = setInterval(async () => {
      try {
        const r = await api.get("/github/oauth/status");
        if (r.data?.connected) {
          clearInterval(pollRef.current); pollRef.current = null;
          try { popupRef.current?.close?.(); } catch { /* xorigin */ }
          setGhStatus("connected");
          setGhLogin(r.data.login || "");
          fetchRepos();
        }
      } catch { /* keep polling */ }
      if (popupRef.current?.closed) {
        // user closed the window without finishing
        if (ghStatus !== "connected") {
          clearInterval(pollRef.current); pollRef.current = null;
        }
      }
      if (Date.now() - started > 90_000) {
        clearInterval(pollRef.current); pollRef.current = null;
      }
    }, 2000);
  }

  function close() {
    dismissWizard();
    onComplete?.();
  }

  async function submitRepo(e) {
    e?.preventDefault();
    setErr("");
    if (!REPO_RX.test(repoUrl.trim())) {
      setErr("Use a real GitHub repo URL — github.com/owner/repo");
      return;
    }
    setBusy(true);
    try {
      const parts = repoUrl.trim().replace(/\/$/, "").split("/");
      const name = parts[parts.length - 1].replace(/\.git$/, "");

      // 2026-02-10 · Phase 4 — if the App picker is active AND the
      // chosen repo belongs to one of the user's installations,
      // submit with `installation_id` so the backend uses the App-
      // install branch. Otherwise no token is sent (public-repo path).
      let installation_id = null;
      if (appPickerActive && appInstalls.length > 0) {
        const chosenFullName = repoUrl.trim()
          .replace(/^https?:\/\/(www\.)?github\.com\//i, "")
          .replace(/\/$/, "")
          .replace(/\.git$/i, "");
        for (const inst of appInstalls) {
          if ((inst.repositories || []).some(
            (r) => (r.full_name || "").toLowerCase() === chosenFullName.toLowerCase(),
          )) {
            installation_id = inst.installation_id;
            break;
          }
        }
      }

      const payload = {
        name,
        github_url: repoUrl.trim(),
        branch:     branch.trim() || "main",
        // 2026-08-24 — funnel stitching: lets the backend's server-side
        // `repo_selected` event join this browser's funnel journey.
        funnel_session: getFunnelSessionId(),
      };
      if (installation_id) {
        payload.installation_id = installation_id;
      }
      // No PAT field in the UI anymore — a repo with no installation
      // match is submitted with no github_token, i.e. a public-repo
      // clone (unauthenticated). Private repos need the GitHub App.

      const r = await api.post("/cto/projects/add", payload);
      // 2026-08-20 — founder's call: no forced "first task" step
      // inside the wizard anymore. Connecting the repo is the whole
      // job here — land straight in the normal chat window, same as
      // any returning user, instead of steps 2/3 (task + shipping).
      const newProjectId = r.data?.project_id;
      setProject(newProjectId);
      setActiveProjectId(newProjectId);
      close();
    } catch (e2) {
      const detail = e2?.response?.data?.detail;
      const msg = (typeof detail === "object" && detail?.message)
        ? detail.message
        : (typeof detail === "string" ? detail : (e2?.message || "Could not connect repo."));
      // If the server replies "GitHub not connected" mid-flow (e.g. user
      // is in manual mode for a private repo), flip the panel back to
      // the OAuth-connect view instead of asking them to leave.
      if (/github not connected/i.test(msg)) {
        setGhStatus("disconnected");
        setErr("This repo needs GitHub access. Connect once below — your manual URL will stick.");
      } else {
        setErr(msg);
      }
    } finally {
      setBusy(false);
    }
  }

  // ─── Guard 22 · Phase 3.1 — "start from an idea" (scaffold path) ──
  async function submitIdea(e) {
    e?.preventDefault();
    setScaffoldErr("");
    if (ideaText.trim().length < 10) {
      setScaffoldErr("A bit more detail please — one real sentence about what you want to build.");
      return;
    }
    setScaffoldBusy(true);
    try {
      const r = await api.post("/scaffold/new-project", { brief: ideaText.trim() });
      setDraft(r.data);
    } catch (e2) {
      const detail = e2?.response?.data?.detail;
      const msg = (typeof detail === "object" && (detail?.user_message || detail?.message))
        || (typeof detail === "string" ? detail : null);
      setScaffoldErr(msg || "Could not generate a plan — try rephrasing your idea.");
    } finally {
      setScaffoldBusy(false);
    }
  }

  async function confirmMaterialize() {
    if (!draft?.draft_id) return;
    setScaffoldErr("");
    setMaterializing(true);
    try {
      const r = await api.post(`/scaffold/${draft.draft_id}/materialize`, {});
      const newProjectId = r.data?.project_id;
      if (!newProjectId) throw new Error("No project_id returned");
      setProject(newProjectId);
      setActiveProjectId(newProjectId);
      close();
    } catch (e2) {
      const detail = e2?.response?.data?.detail;
      const msg = (typeof detail === "object" && (detail?.user_message || detail?.message))
        || (typeof detail === "string" ? detail : null);
      setScaffoldErr(msg || "Could not create the project — please try again.");
    } finally {
      setMaterializing(false);
    }
  }

  const robotMsg = buildRobotMessage({ ghStatus, busy, err, repoUrl });

  // Iter 388t · Bug 27 · Escape + focus trap.  Wizard was aria-modal
  // but had NO keyboard-close path — a keyboard-only user who hit
  // the first-time wizard could not Escape out of it if the form was
  // broken.  Now hooks in the reusable a11y trap; onClose invokes
  // the same `close()` function the "Skip for now" link uses.
  const wizardRef = useRef(null);
  useModalA11y({
    ref:     wizardRef,
    isOpen:  true,
    onClose: close,
  });

  return (
    <div
      data-testid="new-user-wizard"
      ref={wizardRef}
      role="dialog" aria-modal="true" aria-labelledby="wizard-title"
      tabIndex={-1}
      style={{
        position: "fixed", inset: 0, zIndex: 9000,
        background: "rgba(8,10,14,0.72)",
        backdropFilter: "blur(8px)", WebkitBackdropFilter: "blur(8px)",
        display: "flex", alignItems: "center", justifyContent: "center",
        padding: 16,
      }}
    >
      <RobotGuideKeyframes />
      <div style={{
        width: "min(460px, 100%)",
        maxHeight: "92vh",
        background: "#0f172a",
        border: "0.5px solid rgba(255,255,255,0.1)",
        borderRadius: 14,
        boxShadow: "0 24px 60px -16px rgba(245,158,11,0.18)",
        overflow: "hidden",
        display: "flex",
        flexDirection: "column",
      }}>
        {/* ORA brand header */}
        <header style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          padding: "14px 20px",
          borderBottom: "0.5px solid rgba(255,255,255,0.08)",
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <div style={{
              width: 24, height: 24, background: "#f59e0b",
              borderRadius: "50%", display: "flex", alignItems: "center",
              justifyContent: "center", fontSize: 11, fontWeight: 700,
              color: "#000", fontFamily: "var(--font-mono, ui-monospace, monospace)",
            }}>O</div>
            <div>
              <div style={{ fontSize: 13, fontWeight: 500, color: "#f8fafc",
                            fontFamily: "var(--font-mono, ui-monospace, monospace)" }}>
                ORA
              </div>
              <div style={{ fontSize: 10, color: "#64748b" }}>by Aurem</div>
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <button data-testid="wizard-close" onClick={close} title="Skip"
                    style={{ background:"transparent", border:"none", padding:4,
                             color:"#64748b", cursor:"pointer", display:"flex" }}>
              <X size={14} />
            </button>
          </div>
        </header>

        <div style={{ padding: "20px 20px 16px", overflowY: "auto",
                       flex: "1 1 auto", minHeight: 0 }}>
          {/* Robot Guide */}
          <RobotGuide message={robotMsg} kind={err ? "error" : "info"} testid="wizard-robot-guide" />
          {step === 1 && (
            <form onSubmit={submitRepo} data-testid="wizard-step-1">
              <h2 id="wizard-title" style={hStyle}>
                {mode === "connect" ? "Connect your GitHub repo" : "Start from an idea"}
              </h2>

              {/* 2026-08-24 · Guard 22 · Phase 3.1 — path chooser. Default
                  stays "connect" (existing behaviour untouched); this just
                  adds the previously-missing 3rd onboarding path. */}
              <div data-testid="wizard-mode-toggle" style={{
                display: "flex", gap: 6, marginBottom: 16,
                background: "var(--bg-elev, rgba(255,255,255,0.03))",
                padding: 4, borderRadius: 8,
              }}>
                <button type="button" data-testid="wizard-mode-connect"
                  onClick={() => { setMode("connect"); setScaffoldErr(""); }}
                  style={modeTabStyle(mode === "connect")}>
                  🔗 Connect a repo
                </button>
                <button type="button" data-testid="wizard-mode-scaffold"
                  onClick={() => { setMode("scaffold"); setErr(""); }}
                  style={modeTabStyle(mode === "scaffold")}>
                  💡 Start from an idea
                </button>
              </div>

              {mode === "connect" && (<>

              {ghStatus === "checking" && (
                <div data-testid="wizard-gh-checking" style={{
                  display:"flex", alignItems:"center", gap:8,
                  fontSize:12, color:"var(--text-dim)",
                  padding:"18px 0",
                }}>
                  <Loader2 size={12} style={{ animation:"spin 1s linear infinite" }} />
                  Checking GitHub connection…
                </div>
              )}

              {ghStatus === "disconnected" && (
                <div data-testid="wizard-gh-disconnected">
                  <p style={pStyle}>
                    Connect GitHub once — AUREM will use it to read your
                    repos, write commits, and open PRs. We never store the
                    token in plaintext.
                  </p>
                  <div style={{ position: "relative" }}>
                    <div data-testid="wizard-pulse-ring" style={oraPulseRingStyle} />
                    <button
                      data-testid="wizard-connect-github"
                      data-guide-target="connect-github-btn"
                      type="button"
                      onClick={connectGithub}
                      style={githubBtnStyle}
                    >
                      <Github size={16} />
                      Continue with GitHub
                    </button>
                  </div>
                  <div style={{
                    fontSize: 10.5, color: "var(--text-faint)",
                    textAlign: "center", marginTop: 10, lineHeight: 1.5,
                  }}>
                    Opens in a popup. After authorising, this wizard will
                    pick up automatically.
                  </div>
                  {err && <div data-testid="wizard-error" style={errStyle}>{err}</div>}
                  <Footer
                    busy={false}
                    primary="Skip — paste a URL"
                    onPrimary={() => setGhStatus("manual")}
                    onSkip={close}
                  />
                </div>
              )}

              {(ghStatus === "connected" || ghStatus === "manual" || ghStatus === "choosing") && (
                <>
                  {/* ── 2026-02-10 · Phase 4 · GitHub App primary CTA ── */}
                  {(ghStatus === "choosing" || (ghStatus === "manual" && appPickerActive)) && (
                    <div data-testid="wizard-app-cta-block" style={{
                      marginBottom: 16,
                      padding: 14,
                      background: "linear-gradient(135deg, rgba(255,102,8,0.08), rgba(255,102,8,0.02))",
                      border: "1px solid rgba(255,102,8,0.28)",
                      borderRadius: 6,
                    }}>
                      {!appPickerActive && (
                        <>
                          <div style={{
                            display: "flex", alignItems: "center", gap: 8,
                            marginBottom: 8,
                          }}>
                            <Github size={14} />
                            <strong style={{ fontSize: 13 }}>
                              Install Aurem for your repos
                            </strong>
                            <span style={{
                              fontSize: 10, padding: "2px 8px",
                              background: "rgba(255,102,8,0.2)",
                              color: "#ff9d5c", borderRadius: 10,
                              letterSpacing: "0.04em",
                            }}>
                              RECOMMENDED
                            </span>
                          </div>
                          {appConnecting ? (
                            <div data-testid="wizard-app-connecting" style={{
                              display: "flex", alignItems: "center", gap: 8,
                              fontSize: 12, color: "var(--text-faint)",
                            }}>
                              <Loader2 size={14} className="spin" color="#ff9d5c" />
                              Waiting for you to finish in the GitHub popup…
                            </div>
                          ) : appTimedOut ? (
                            <div data-testid="wizard-app-timeout" style={{
                              display: "flex", alignItems: "center", gap: 10,
                            }}>
                              <span style={{ fontSize: 12, color: "var(--text-faint)" }}>
                                It looks like the connection didn't finish.
                              </span>
                              <button
                                type="button"
                                data-testid="wizard-app-retry-btn"
                                onClick={retryAppConnect}
                                style={{
                                  display: "inline-flex", alignItems: "center", gap: 6,
                                  padding: "6px 12px",
                                  background: "var(--accent, #ff6608)",
                                  color: "#fff", border: "none",
                                  borderRadius: 5, fontSize: 11.5, fontWeight: 600,
                                  cursor: "pointer",
                                }}>
                                <RefreshCw size={11} /> Try again
                              </button>
                            </div>
                          ) : (
                          <>
                          <p style={{
                            fontSize: 12, color: "var(--text-faint)",
                            margin: "0 0 12px", lineHeight: 1.5,
                          }}>
                            One click — no token to manage. You pick which
                            repos Aurem can see. Revoke any time from GitHub.
                          </p>
                          <button
                            data-testid="wizard-app-install-btn"
                            data-guide-target="connect-github-btn"
                            type="button"
                            onClick={openAppInstallPopup}
                            disabled={appConnecting}
                            style={{
                              display: "inline-flex", alignItems: "center", gap: 8,
                              padding: "8px 14px",
                              background: "var(--accent, #ff6608)",
                              color: "#fff", border: "none",
                              borderRadius: 5, fontSize: 12, fontWeight: 600,
                              cursor: "pointer",
                            }}>
                            <Github size={12} />
                            Continue with GitHub App
                            <ArrowRight size={12} />
                          </button>
                          <div style={{
                            fontSize: 10.5, color: "var(--text-faint)",
                            marginTop: 8,
                          }}>
                            Opens in a popup. This wizard picks up automatically
                            once you finish installing.
                          </div>
                          {/* 2026-08-21 — founder-reported: GitHub's own
                              "Select repositories" search widget can briefly
                              show "No repositories found" right after
                              authorizing — a GitHub-side glitch, not an
                              AUREM bug. Nudge users past it instead of
                              letting them think the connection failed. */}
                          <div data-testid="wizard-app-github-glitch-hint" style={{
                            fontSize: 10.5, color: "var(--text-faint)",
                            marginTop: 6, padding: "6px 8px",
                            background: "rgba(255,255,255,0.03)",
                            border: "1px solid var(--border)",
                            borderRadius: 4, lineHeight: 1.5,
                          }}>
                            💡 If GitHub shows "No repositories found" while
                            picking repos, just type your repo's name in that
                            search box (or wait a second and reopen it) —
                            it's a GitHub-side hiccup, your repos are there.
                          </div>
                          </>
                          )}
                        </>
                      )}
                      {appPickerActive && appInstalls.length > 0 && (
                        <div data-testid="wizard-app-repo-picker">
                          <div style={{ fontSize: 12, marginBottom: 8,
                                        color: "var(--ok, #6dd4a1)",
                                        display: "flex", alignItems: "center",
                                        gap: 6 }}>
                            <Github size={11} />
                            App installed. Pick a repo below to connect.
                          </div>
                          {appInstalls.map((inst) => (
                            <div key={inst.installation_id} style={{ marginBottom: 8 }}>
                              <div style={{ fontSize: 11, color: "var(--text-faint)",
                                             marginBottom: 4 }}>
                                @{inst.github_login}
                                {" · "}
                                {(inst.repositories || []).length} repo
                                {(inst.repositories || []).length === 1 ? "" : "s"}
                              </div>
                              <div style={{
                                display: "flex", flexWrap: "wrap", gap: 6,
                              }}>
                                {(inst.repositories || []).map((r) => (
                                  <button
                                    key={r.id || r.full_name}
                                    data-testid={`wizard-app-repo-${r.full_name}`}
                                    type="button"
                                    onClick={() => {
                                      setRepoUrl(`https://github.com/${r.full_name}`);
                                      setBranch(r.default_branch || "main");
                                      trackFunnel("app_repo_selected", "wizard",
                                        { repo: r.full_name });
                                    }}
                                    style={{
                                      padding: "5px 10px", fontSize: 11,
                                      background: (repoUrl || "").endsWith(r.full_name)
                                        ? "rgba(255,102,8,0.22)"
                                        : "rgba(255,255,255,0.05)",
                                      border: (repoUrl || "").endsWith(r.full_name)
                                        ? "1px solid var(--accent, #ff6608)"
                                        : "1px solid rgba(255,255,255,0.12)",
                                      borderRadius: 4,
                                      color: "var(--text)",
                                      cursor: "pointer",
                                      fontFamily: "'JetBrains Mono', monospace",
                                    }}>
                                    {r.full_name}
                                    {r.private && (
                                      <span style={{
                                        marginLeft: 6, fontSize: 9,
                                        color: "var(--text-faint)",
                                      }}>private</span>
                                    )}
                                  </button>
                                ))}
                              </div>
                            </div>
                          ))}
                          {repoUrl && (
                            <div data-testid="wizard-app-repo-selected-cta" style={{
                              marginTop: 8, padding: "8px 12px", borderRadius: 6,
                              background: "rgba(34,197,94,0.08)",
                              border: "1px solid rgba(34,197,94,0.36)",
                              color: "#22C55E", fontSize: 11, fontWeight: 600,
                              fontFamily: "var(--font-mono, ui-monospace, monospace)",
                            }}>
                              ✓ {repoUrl.replace(/^https?:\/\/github\.com\//i, "")} selected —
                              click <strong>Continue</strong> below to connect it.
                            </div>
                          )}
                          <button
                            data-testid="wizard-app-add-more-btn"
                            type="button"
                            onClick={openAppInstallPopup}
                            style={{
                              marginTop: 6, padding: "4px 10px", fontSize: 10,
                              background: "transparent",
                              border: "1px dashed rgba(255,255,255,0.2)",
                              color: "var(--text-faint)",
                              borderRadius: 4, cursor: "pointer",
                            }}>
                            + Install on another account or add more repos
                          </button>
                        </div>
                      )}
                    </div>
                  )}

                  {(ghStatus === "connected" || (ghStatus === "choosing" && ghLogin && !appPickerActive)) && (
                    <div data-testid="wizard-gh-connected" style={{
                      display:"flex", alignItems:"center", gap:8,
                      padding:"6px 10px", marginBottom:12,
                      background:"rgba(109,212,161,0.07)",
                      border:"1px solid rgba(109,212,161,0.22)",
                      borderRadius:4, fontSize:11,
                      color:"var(--ok, #6dd4a1)",
                    }}>
                      <Github size={11} />
                      Connected as <strong>{ghLogin || "github user"}</strong>
                      {/* 2026-08-20 — reassures OAuth-linked-but-no-App
                          users that GitHub identity IS already linked;
                          the App CTA below is just for repo access. */}
                    </div>
                  )}
                  {/* 2026-02-10 · Phase 4 — hide repo URL / branch
                      while the user is still on the "choosing" landing
                      (no App picker yet). */}
                  {!(ghStatus === "choosing" && !appPickerActive) && (
                  <>
                  <p style={pStyle}>
                    {ghStatus === "connected"
                      ? "Pick a repo from your account or paste any URL — ORA will read it, write the diff, and push the commit back."
                      : appPickerActive
                        ? "Confirm the repo below (or edit if needed), then continue."
                        : "Paste any public repo URL. (You can connect GitHub later from Settings for private repos.)"}
                  </p>

                  {ghStatus === "connected" && (
                    <>
                      <label style={lStyle}>Your repositories</label>
                      <select
                        data-testid="wizard-repo-picker"
                        disabled={reposBusy}
                        onChange={(e) => {
                          const idx = parseInt(e.target.value, 10);
                          const r = repos[idx];
                          if (r) {
                            setRepoUrl(r.url || `https://github.com/${r.full_name}`);
                            setBranch(r.default_branch || "main");
                          }
                        }}
                        style={iStyle}
                        defaultValue=""
                      >
                        <option value="" disabled>
                          {reposBusy ? "Loading…" : `${repos.length} repos found — pick one`}
                        </option>
                        {repos.map((r, i) => (
                          <option key={r.full_name} value={i}>
                            {r.full_name}{r.private ? " · private" : ""}
                          </option>
                        ))}
                      </select>
                    </>
                  )}

                  <label style={lStyle}>Repository URL</label>
                  <input
                    data-testid="wizard-repo-input"
                    autoFocus={ghStatus !== "connected"}
                    value={repoUrl}
                    onChange={(e) => setRepoUrl(e.target.value)}
                    placeholder="https://github.com/owner/repo"
                    style={iStyle}
                  />
                  <label style={lStyle}>Branch</label>
                  <input
                    data-testid="wizard-branch-input"
                    value={branch}
                    onChange={(e) => setBranch(e.target.value)}
                    placeholder="main"
                    style={iStyle}
                  />
                  </>
                  )}
                  {/* end URL/branch outer conditional
                      (hidden on pure "choosing" landing) */}

                  {err && <div data-testid="wizard-error" style={errStyle}>{err}</div>}
                  {/* 2026-08-27 · Journey Watch Phase 1 — ROOT FIX for the
                      duplicate-CTA bug: on the pure "choosing" landing (no
                      App installed yet) this Footer used to render a
                      SECOND "Continue with GitHub App" button — identical
                      action to `wizard-app-install-btn` in the block
                      above. Two buttons doing the exact same thing broke
                      the "one primary CTA" rule and confused users about
                      which one to press. Skip-only footer here — the one
                      true CTA already lives in the app-cta-block above. */}
                  {(ghStatus === "choosing" && !appPickerActive) ? (
                    <div style={{ display: "flex", justifyContent: "flex-end", padding: "16px 0 4px" }}>
                      <button data-testid="wizard-skip-link" type="button" onClick={close}
                              style={{ background: "transparent", border: "none",
                                       color: "var(--text-faint)", fontSize: 11,
                                       padding: "6px 4px", cursor: "pointer" }}>
                        Skip for now
                      </button>
                    </div>
                  ) : (
                    <Footer
                      busy={busy}
                      primary="Continue"
                      onPrimary={submitRepo}
                      onSkip={close}
                    />
                  )}
                </>
              )}
              </>)}

              {mode === "scaffold" && (
                <div data-testid="wizard-scaffold-panel">
                  {!draft && (
                    <>
                      <p style={pStyle}>
                        One or two sentences is enough — AUREM will draft a
                        real project structure, then create it as a real
                        GitHub repo the moment you approve it.
                      </p>
                      <textarea
                        data-testid="wizard-idea-input"
                        autoFocus
                        value={ideaText}
                        onChange={(e) => setIdeaText(e.target.value)}
                        placeholder="e.g. A simple waitlist landing page with an email signup form and an admin list view"
                        rows={4}
                        style={{ ...iStyle, height: "auto", resize: "vertical", fontFamily: "inherit" }}
                      />
                      {scaffoldErr && <div data-testid="wizard-scaffold-error" style={errStyle}>{scaffoldErr}</div>}
                      <Footer
                        busy={scaffoldBusy}
                        primary="Draft my project"
                        onPrimary={submitIdea}
                        onSkip={close}
                      />
                    </>
                  )}
                  {draft && (
                    <>
                      <p style={pStyle}>
                        {draft.stack_detected ? `Stack: ${draft.stack_detected}. ` : ""}
                        Here's what AUREM will create ({(draft.files || []).length} files):
                      </p>
                      <div data-testid="wizard-draft-file-list" style={{
                        maxHeight: 180, overflowY: "auto",
                        background: "var(--bg-elev, rgba(255,255,255,0.03))",
                        border: "1px solid var(--border, rgba(255,255,255,0.08))",
                        borderRadius: 6, padding: "8px 12px", marginBottom: 12,
                        fontFamily: "'JetBrains Mono', monospace", fontSize: 11.5,
                      }}>
                        {(draft.files || []).map((f) => (
                          <div key={f.path} style={{ padding: "2px 0", color: "var(--text-dim)" }}>
                            {f.path}
                          </div>
                        ))}
                      </div>
                      {scaffoldErr && <div data-testid="wizard-scaffold-error" style={errStyle}>{scaffoldErr}</div>}
                      <Footer
                        busy={materializing}
                        primary="Looks good — create it"
                        onPrimary={confirmMaterialize}
                        onSkip={() => setDraft(null)}
                        skipLabel="Back"
                      />
                    </>
                  )}
                </div>
              )}
            </form>
          )}
        </div>
      </div>
    </div>
  );
}

function Footer({ busy, primary, onPrimary, onSkip, skipLabel = "Skip for now" }) {
  return (
    <div style={{ display:"flex", alignItems:"center",
                  gap: 8, padding: "16px 0 4px" }}>
      <button data-testid="wizard-skip-link" type="button" onClick={onSkip}
              style={{ background:"transparent", border:"none",
                       color:"var(--text-faint)", fontSize:11,
                       padding:"6px 4px", cursor:"pointer" }}>
        {skipLabel}
      </button>
      <div style={{ flex:1 }} />
      <button data-testid="wizard-next" data-guide-target="continue-btn" type="button" onClick={onPrimary}
              disabled={busy} style={primaryBtn}>
        {busy
          ? <><Loader2 size={12} style={{ animation:"spin 1s linear infinite" }} /> working…</>
          : <>{primary} <ArrowRight size={12} /></>}
      </button>
    </div>
  );
}

function modeTabStyle(active) {
  return {
    flex: 1, padding: "8px 10px", fontSize: 12, fontWeight: 500,
    borderRadius: 6, border: "none", cursor: "pointer",
    fontFamily: "inherit",
    background: active ? "rgba(255,102,8,0.16)" : "transparent",
    color: active ? "#ff9d5c" : "var(--text-faint)",
    transition: "background 120ms, color 120ms",
  };
}

const hStyle = { margin: 0, fontSize: 20, fontWeight: 500,
                 color: "var(--text)", letterSpacing: "-0.01em" };
const pStyle = { margin: "8px 0 14px", fontSize: 12.5, lineHeight: 1.55,
                 color: "var(--text-dim)" };
const lStyle = { display: "block", fontSize: 10, fontWeight: 600,
                 textTransform: "uppercase", letterSpacing: "0.08em",
                 color: "var(--text-faint)", margin: "10px 0 4px" };
const iStyle = { width: "100%", padding: "9px 12px", fontSize: 13,
                 background: "var(--bg-elev, #0a0c10)",
                 color: "var(--text)",
                 border: "1px solid var(--border, rgba(255,200,120,0.16))",
                 borderRadius: 5, outline: "none",
                 fontFamily: "var(--font-sans, system-ui)" };
const errStyle = { marginTop: 10, fontSize: 11, color: "var(--danger, #ff6b6b)",
                   background: "rgba(255,107,107,0.06)",
                   border: "1px solid rgba(255,107,107,0.2)",
                   padding: "8px 10px", borderRadius: 4 };
const primaryBtn = { display: "inline-flex", alignItems: "center", gap: 6,
                     padding: "10px 16px",
                     background: "#f59e0b",
                     color: "#0a0c10", border: "none",
                     borderRadius: 8, fontSize: 13, fontWeight: 600,
                     letterSpacing: "0.02em", cursor: "pointer" };

function buildRobotMessage({ ghStatus, busy, err, repoUrl }) {
  if (err) {
    return `Hmm — <strong>${escapeHtml(err)}</strong>. Try again, or skip for now.`;
  }
  if (busy) return `Working on it… <span class="ora-arrow">⏳</span>`;
  {
    if (ghStatus === "checking") return `Checking your GitHub connection…`;
    if (ghStatus === "disconnected")
      return `<strong>Fastest way:</strong> click <strong>Continue with GitHub</strong> below <span class="ora-arrow">👇</span> — connects in seconds, no PAT needed.`;
    if (ghStatus === "manual")
      return `Paste any <strong>public repo URL</strong> below. For private repos, connect GitHub from Settings later. <span class="ora-arrow">👇</span>`;
    if (ghStatus === "choosing" && !repoUrl)
      return `One click: <strong>Continue with GitHub App</strong> below — you pick which repos AUREM can see, then choose one here. <span class="ora-arrow">👇</span>`;
    if (!repoUrl) return `Your GitHub repos are loaded! <strong>Pick a repo</strong> from the dropdown — or paste a URL. <span class="ora-arrow">👇</span>`;
    return `Nice — <strong>${escapeHtml(repoUrl.replace(/^https?:\/\/github\.com\//, ""))}</strong> looks good. Click <strong>Continue</strong> to connect it. <span class="ora-arrow">👇</span>`;
  }
  return "";
}

const githubBtnStyle = {
  width: "100%", padding: "13px", background: "#24292e",
  color: "#fff", border: "2px solid #f59e0b", borderRadius: 10,
  fontSize: 14, fontWeight: 500, cursor: "pointer",
  display: "flex", alignItems: "center", justifyContent: "center",
  gap: 10, marginBottom: 4, transition: "all .2s",
  position: "relative", zIndex: 1,
};
