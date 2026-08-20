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
import { Loader2, X, ArrowRight, Github } from "lucide-react";
import { api, getToken, API_BASE } from "../lib/api";
import { setActiveProjectId } from "./TabBar";
import RobotGuide, { RobotGuideKeyframes, escapeHtml, oraPulseRingStyle } from "./RobotGuide";
import useModalA11y from "../hooks/useModalA11y";

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

  // GitHub OAuth state — drives whether step 1 shows a Connect button
  // or the repo URL input + picker.
  const [ghStatus, setGhStatus] = useState("checking"); // "checking" | "connected" | "disconnected"
  const [ghLogin, setGhLogin]   = useState("");
  const [repos, setRepos]       = useState([]);
  const [reposBusy, setReposBusy] = useState(false);
  const pollRef = useRef(null);
  const popupRef = useRef(null);

  // ─── 2026-02-10 · Phase 4 · GitHub App install state ──────────────
  // `appInstalls` — list of `github_installations` rows owned by the
  //                current user (populated after they complete an App
  //                install via the wizard popup).
  // `appPickerActive` — flips to true after a successful install so
  //                the UI transitions from "Continue with GitHub App"
  //                CTA → repo picker sourced from installation.repositories.
  const [appInstalls, setAppInstalls]           = useState([]);
  const [appInstallsBusy, setAppInstallsBusy]   = useState(false);
  const [appPickerActive, setAppPickerActive]   = useState(false);
  const appPopupRef = useRef(null);

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
        const [oauthRes, installsRes] = await Promise.allSettled([
          api.get("/github/oauth/status"),
          api.get("/github/app/installations"),
        ]);
        if (cancelled) return;

        const oauthConnected = oauthRes.status === "fulfilled" && oauthRes.value.data?.connected;
        if (oauthConnected) {
          setGhLogin(oauthRes.value.data.login || "");
          fetchRepos();  // convenience dropdown for OAuth-linked accounts
        }

        const installs = (installsRes.status === "fulfilled" && installsRes.value.data?.installations) || [];
        if (installs.length > 0) {
          // Already has the App installed — go straight to its repo
          // picker, no CTA needed.
          setAppInstalls(installs);
          setAppPickerActive(true);
          maybeAutoSelectSingleRepo(installs);
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

  // 2026-08-20 — auto-select the repo when an install grants exactly
  // ONE repo — there's no real choice to make, so requiring a click
  // is pointless friction. Distinct from the earlier "no auto-fill"
  // fix, which was specifically about MULTI-repo installs (auto-
  // highlighting one of several created false confidence about which
  // repo was chosen). A single repo has no such ambiguity.
  function maybeAutoSelectSingleRepo(installs) {
    const allRepos = (installs || []).flatMap((inst) => inst.repositories || []);
    if (allRepos.length === 1) {
      setRepoUrl(`https://github.com/${allRepos[0].full_name}`);
      setBranch(allRepos[0].default_branch || "main");
    }
  }

  // ─── 2026-02-10 · Phase 4 · GitHub App install handlers ─────────
  async function fetchAppInstallations() {
    setAppInstallsBusy(true);
    try {
      const r = await api.get("/github/app/installations");
      const list = r.data?.installations || [];
      setAppInstalls(list);
      if (list.length > 0) {
        setAppPickerActive(true);
        // 2026-08-20 — deliberately NOT auto-filling repoUrl here
        // anymore when there's more than 1 repo. Auto-picking the
        // first repo of a multi-repo installation and rendering it
        // pre-highlighted created a false sense of completion right
        // after "App installed" — a user could reasonably believe
        // the connect was already done and never click the still-
        // required Continue button below. Requiring an explicit repo
        // click keeps the "done" state honest for multi-repo installs.
        // A single-repo install has no such ambiguity — auto-select it.
        maybeAutoSelectSingleRepo(list);
      }
    } catch {
      setAppInstalls([]);
    } finally {
      setAppInstallsBusy(false);
    }
  }

  function openAppInstallPopup() {
    const token = getToken();
    if (!token) {
      setErr("Session expired — please log in again.");
      return;
    }
    // Popup MUST open synchronously in the click handler or browsers
    // will block it. Auth via `?auth=<jwt>` query param since we
    // can't set Authorization headers on window.open() nav.
    const url = `${API_BASE}/github/app/install?auth=${encodeURIComponent(token)}`;
    const w = 720, h = 800;
    const left = Math.max(0, window.screenX + (window.outerWidth  - w) / 2);
    const top  = Math.max(0, window.screenY + (window.outerHeight - h) / 2);
    appPopupRef.current = window.open(
      url, "aurem_github_app_install",
      `width=${w},height=${h},left=${left},top=${top}`,
    );
    // Polling fallback in case postMessage is dropped (some browsers
    // block cross-origin messages back to opener). Every 1.5s, refetch
    // the /installations list; if it grows, we know install completed.
    const started = Date.now();
    const startCount = appInstalls.length;
    const poll = setInterval(async () => {
      if (appPopupRef.current?.closed) {
        clearInterval(poll);
        await fetchAppInstallations();
        return;
      }
      if (Date.now() - started > 180_000) {          // 3-minute timeout
        clearInterval(poll);
        return;
      }
      try {
        const r = await api.get("/github/app/installations");
        if ((r.data?.installations || []).length > startCount) {
          clearInterval(poll);
          try { appPopupRef.current?.close?.(); } catch {}
          setAppInstalls(r.data.installations);
          setAppPickerActive(true);
          // 2026-08-20 — no auto-fill here either for multi-repo
          // installs, same reasoning as fetchAppInstallations() above.
          // Single-repo installs still get auto-selected.
          maybeAutoSelectSingleRepo(r.data.installations);
        }
      } catch { /* keep polling */ }
    }, 1500);
  }


  // Listen for the postMessage handshake from /api/aurem-dev/github/app/installed.
  useEffect(() => {
    function onMessage(e) {
      const d = e.data;
      if (!d || d.type !== "aurem-app-installed") return;
      if (d.status === "success") {
        // Popup already closed itself; refresh our list of installations.
        fetchAppInstallations();
      } else if (d.status === "err") {
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
    // 2026-08-01 — funnel telemetry: cta_click for wizard entry.
    // Fire-and-forget so popup opens synchronously (avoid pop-up blockers).
    try {
      // eslint-disable-next-line @typescript-eslint/no-var-requires
      import("../lib/githubFunnel").then(({ trackFunnel, withFunnelParams }) => {
        trackFunnel("cta_click", "wizard", { has_token: true });
        const stitched = withFunnelParams(
          `${API_BASE}/github/oauth/connect?auth=${encodeURIComponent(token)}`,
          "wizard",
        );
        // Update popup location AFTER telemetry adds session params.
        if (popupRef.current) {
          try { popupRef.current.location.href = stitched; } catch {}
        }
      }).catch(() => {});
    } catch {}
    // Open the OAuth flow in a popup. The backend's /connect handler
    // accepts the JWT via `?auth=` so cookieless browsers still work.
    // NOTE: popup MUST open synchronously in the click handler or
    // browsers will block it. Funnel stitching above updates the URL
    // after the popup opens; if telemetry fails, popup still works.
    const url = `${API_BASE}/github/oauth/connect?auth=${encodeURIComponent(token)}`;
    const w = 560, h = 720;
    const left = Math.max(0, window.screenX + (window.outerWidth  - w) / 2);
    const top  = Math.max(0, window.screenY + (window.outerHeight - h) / 2);
    popupRef.current = window.open(
      url, "aurem_github_oauth",
      `width=${w},height=${h},left=${left},top=${top}`,
    );
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
              <h2 id="wizard-title" style={hStyle}>Connect your GitHub repo</h2>

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
                            disabled={appInstallsBusy}
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
                  <Footer
                    busy={busy}
                    primary="Continue"
                    onPrimary={submitRepo}
                    onSkip={close}
                  />
                </>
              )}
            </form>
          )}
        </div>
      </div>
    </div>
  );
}

function Footer({ busy, primary, onPrimary, onSkip }) {
  return (
    <div style={{ display:"flex", alignItems:"center",
                  gap: 8, padding: "16px 0 4px" }}>
      <button data-testid="wizard-skip-link" type="button" onClick={onSkip}
              style={{ background:"transparent", border:"none",
                       color:"var(--text-faint)", fontSize:11,
                       padding:"6px 4px", cursor:"pointer" }}>
        Skip for now
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
