/**
 * AddProjectWizard.jsx — Iter 212m-5 — 3-step "Add Project" flow.
 *
 *   Step 1  Repo identify  (free-form owner/repo or full URL)
 *   Step 2  Install/select via the GitHub App
 *   Step 3  Confirm summary + project name → save → land in chat
 *
 * 2026-08-20 — founder's call: GitHub App is now the ONLY visible way
 * to connect a repo. The old PAT (Personal Access Token) fallback UI
 * was removed entirely from this flow. The backend still accepts a
 * github_token on /cto/projects/add (untouched, existing PAT-connected
 * projects keep working) — there's just no way to reach it from here
 * anymore.
 *
 * After successful save:
 *   - localStorage active project is set to the new project_id
 *   - parent's onAdded(projectId) is called (refreshes list / closes modal)
 *   - user is navigated to /dashboard so they can start chatting immediately
 */
import React, { useEffect, useRef, useState } from "react";
import {
  Github,
  Check,
  Loader2,
  ArrowRight,
  ArrowLeft,
  X as XIcon,
  ShieldCheck,
} from "lucide-react";
import { useNavigate } from "react-router-dom";
import { api, API_BASE, getToken } from "../lib/api";
import { toast } from "./Toast";
import { setActiveProjectId } from "./TabBar";
import { metaLead } from "../lib/analytics";

// ── Helpers ───────────────────────────────────────────────────────────

/** Parse a free-form `owner/repo` or `https://github.com/owner/repo` into
 *  canonical `{owner, repo, full_name, url}`. Returns null on invalid. */
export function parseRepoInput(raw) {
  const s = (raw || "")
    .trim()
    .replace(/^https?:\/\/github\.com\//i, "")
    .replace(/\.git$/i, "")
    .replace(/\/+$/, "");
  const parts = s.split("/").filter(Boolean);
  if (parts.length < 2) return null;
  return {
    owner:     parts[0],
    repo:      parts[1],
    full_name: `${parts[0]}/${parts[1]}`,
    url:       `https://github.com/${parts[0]}/${parts[1]}`,
  };
}

// ── Component ─────────────────────────────────────────────────────────

export default function AddProjectWizard({ onClose, onAdded }) {
  const navigate = useNavigate();
  const [step, setStep] = useState(1);

  // Step-1 state
  const [repoInput, setRepoInput] = useState("");
  const repo = parseRepoInput(repoInput);

  // Step-3 state
  const [projectName, setProjectName] = useState("");
  const [saving, setSaving] = useState(false);

  // ─── 2026-02-10 · Phase 4 · GitHub App install (additive) ────────
  const [appInstalls, setAppInstalls]         = useState([]);
  const appPopupRef = useRef(null);

  // The specific installation (if any) that covers the current `repo`.
  // Non-null → App-install branch is available for this repo; the
  // Save button uses installation_id.
  const installationForRepo = React.useMemo(() => {
    if (!repo) return null;
    for (const inst of (appInstalls || [])) {
      const has = (inst.repositories || []).some(
        (r) => (r.full_name || "").toLowerCase() === repo.full_name.toLowerCase(),
      );
      if (has) return inst;
    }
    return null;
  }, [repo, appInstalls]);

  async function fetchAppInstallations() {
    try {
      const r = await api.get("/github/app/installations");
      setAppInstalls(r.data?.installations || []);
    } catch { /* silent */ }
  }
  useEffect(() => { fetchAppInstallations(); }, []);

  function openAppInstallPopup() {
    const token = getToken();
    if (!token) {
      toast({ message: "Session expired — please log in again.", kind: "error" });
      return;
    }
    const url = `${API_BASE}/github/app/install?auth=${encodeURIComponent(token)}`;
    const w = 720, h = 800;
    const left = Math.max(0, window.screenX + (window.outerWidth  - w) / 2);
    const top  = Math.max(0, window.screenY + (window.outerHeight - h) / 2);
    appPopupRef.current = window.open(
      url, "aurem_github_app_install",
      `width=${w},height=${h},left=${left},top=${top}`,
    );
    // Polling fallback in case postMessage is dropped.
    const started = Date.now();
    const startCount = appInstalls.length;
    const poll = setInterval(async () => {
      if (appPopupRef.current?.closed) {
        clearInterval(poll);
        await fetchAppInstallations();
        return;
      }
      if (Date.now() - started > 180_000) { clearInterval(poll); return; }
      try {
        const r = await api.get("/github/app/installations");
        if ((r.data?.installations || []).length > startCount) {
          clearInterval(poll);
          try { appPopupRef.current?.close?.(); } catch { /* xorigin */ }
          setAppInstalls(r.data.installations);
        }
      } catch { /* keep polling */ }
    }, 1500);
  }

  useEffect(() => {
    function onMessage(e) {
      const d = e.data;
      if (!d || d.type !== "aurem-app-installed") return;
      if (d.status === "success") {
        fetchAppInstallations();
      } else if (d.status === "err") {
        toast({
          message: d.err === "invalid_state"
            ? "Session expired during install — please try again."
            : d.err === "github_probe_failed"
              ? "GitHub couldn't verify the install. Try again."
              : "Install did not complete — please try again.",
          kind: "warn",
        });
      }
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  // ── Auto-fill project name from repo name when entering step 3 ───
  useEffect(() => {
    if (step === 3 && !projectName.trim() && repo) {
      setProjectName(repo.repo);
    }
  }, [step, repo, projectName]);

  // ── Step-3 save ──────────────────────────────────────────────────
  async function handleSave() {
    if (!repo || !projectName.trim()) {
      toast({ message: "Fill in the project name.", kind: "warn" });
      return;
    }
    if (!installationForRepo) {
      toast({ message: "Install the GitHub App and grant access to this repo above first.", kind: "warn" });
      return;
    }
    setSaving(true);
    try {
      const payload = {
        name:            projectName.trim(),
        github_url:      repo.url,
        branch:          "main",
        installation_id: installationForRepo.installation_id,
      };
      const r = await api.post("/cto/projects/add", payload);
      const newProjectId = r.data?.project?.project_id || r.data?.project_id;
      // Iter 389 — Meta Pixel Lead. Fires only after backend confirms
      // the project was created (real intent signal, not a click).
      metaLead("project_added");
      toast({
        message: `Connected ${repo.full_name} — opening chat…`,
        kind:    "success",
      });
      if (newProjectId) {
        setActiveProjectId(newProjectId);
      }
      try { onAdded?.(newProjectId); } catch { /* noop */ }
      // Land directly in chat with this project active.
      navigate("/dashboard");
    } catch (e) {
      const d = e?.response?.data?.detail;
      const msg = typeof d === "object" && d?.message
        ? d.message
        : (typeof d === "string" ? d : "Connect failed");
      toast({ message: msg, kind: "error" });
    } finally {
      setSaving(false);
    }
  }

  // ── Render ───────────────────────────────────────────────────────

  return (
    <div
      onClick={onClose}
      data-testid="add-project-wizard-backdrop"
      style={{
        position: "fixed", inset: 0, zIndex: 9000,
        background: "rgba(0,0,0,0.68)",
        display: "flex", alignItems: "center", justifyContent: "center",
        padding: 24,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        data-testid="add-project-wizard"
        style={{
          width: "100%", maxWidth: 580,
          background: "var(--panel)",
          border: "1px solid var(--border-strong)",
          borderRadius: 12,
          padding: 24,
        }}
      >
        {/* ── Header + step indicator ── */}
        <div style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          marginBottom: 18,
        }}>
          <div>
            <div style={{
              fontSize: 10, color: "var(--accent-2, #FF8A2A)",
              letterSpacing: "0.14em", textTransform: "uppercase",
              fontFamily: "'JetBrains Mono', monospace", marginBottom: 4,
            }}>
              Add Project · Step {step} of 3
            </div>
            <h3 style={{ margin: 0, fontSize: 19, color: "var(--text)" }}>
              {step === 1 && "Which GitHub repo?"}
              {step === 2 && "Connect via GitHub App"}
              {step === 3 && "Confirm and start"}
            </h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            data-testid="add-project-close"
            aria-label="Close"
            style={{
              background: "none", border: "none",
              color: "var(--text-faint)", cursor: "pointer", padding: 4,
            }}
          >
            <XIcon size={16} />
          </button>
        </div>

        {/* ── Stepper dots ── */}
        <div style={{
          display: "flex", alignItems: "center", gap: 6, marginBottom: 22,
        }}>
          {[1, 2, 3].map((n) => (
            <div
              key={n}
              data-testid={`step-dot-${n}`}
              style={{
                height: 4, flex: 1, borderRadius: 2,
                background: n <= step
                  ? "var(--accent-2, #FF8A2A)"
                  : "rgba(255,255,255,0.08)",
                transition: "background 200ms",
              }}
            />
          ))}
        </div>

        {/* ──────────────── Step 1 — Repo input ──────────────── */}
        {step === 1 && (
          <div data-testid="wizard-step-1">
            <p style={{
              fontSize: 13, color: "var(--text-dim)",
              margin: "0 0 16px", lineHeight: 1.6,
            }}>
              Type the repo as <code style={codeChip}>owner/repo</code>{" "}
              or paste the full GitHub URL.
            </p>
            <input
              data-testid="repo-input"
              autoFocus
              type="text"
              spellCheck={false}
              autoComplete="off"
              value={repoInput}
              onChange={(e) => setRepoInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && repo) setStep(2);
              }}
              placeholder="e.g. facebook/react   •   or https://github.com/owner/repo"
              style={{
                width: "100%", padding: "14px 16px",
                background: "rgba(0,0,0,0.32)",
                color: "var(--text)",
                fontFamily: "'JetBrains Mono', monospace", fontSize: 14,
                border: `1.5px solid ${
                  repoInput && !repo ? "rgba(239,68,68,0.5)" :
                  repo               ? "rgba(34,197,94,0.5)"  :
                  "rgba(245,158,11,0.4)"
                }`,
                borderRadius: 8, outline: "none",
              }}
            />
            {repoInput && !repo && (
              <div style={{ fontSize: 11, color: "#ef4444", marginTop: 8 }}>
                Use the format <code style={codeChip}>owner/repo</code> (e.g.{" "}
                <code style={codeChip}>octocat/Hello-World</code>).
              </div>
            )}
            {repo && (
              <div
                data-testid="repo-parsed"
                style={{
                  marginTop: 10, fontSize: 12, color: "#22c55e",
                  fontFamily: "'JetBrains Mono', monospace",
                  display: "inline-flex", alignItems: "center", gap: 6,
                }}
              >
                <Check size={12} /> Repo set — github.com/{repo.full_name}
              </div>
            )}

            <div style={navRowStyle}>
              <button
                type="button"
                onClick={onClose}
                data-testid="wizard-cancel-1"
                className="btn-ghost"
                style={{ padding: "10px 18px", fontSize: 12 }}
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => setStep(2)}
                disabled={!repo}
                data-testid="wizard-next-1"
                style={cta(!!repo)}
              >
                Next <ArrowRight size={14} />
              </button>
            </div>
          </div>
        )}

        {/* ──────────────── Step 2 — Connect via GitHub App ──────────────── */}
        {step === 2 && repo && (
          <div data-testid="wizard-step-2">
            {/* 2026-02-10 · Phase 4 · GitHub App primary CTA ─────── */}
            {installationForRepo ? (
              /* Already have an installation covering this repo — skip
                 to Step 3 straight away with a one-line confirmation. */
              <div
                data-testid="add-wizard-app-installed-banner"
                style={{
                  display: "flex", alignItems: "center", gap: 10,
                  padding: 12, marginBottom: 14,
                  background: "rgba(109,212,161,0.08)",
                  border: "1px solid rgba(109,212,161,0.32)",
                  borderRadius: 8,
                }}>
                <Check size={16} color="#6dd4a1" />
                <div style={{ fontSize: 13, lineHeight: 1.4 }}>
                  <strong>@{installationForRepo.github_login}</strong>{" "}
                  installed via GitHub App — no token needed.
                  <div style={{ fontSize: 11, color: "var(--text-faint)",
                                 marginTop: 3 }}>
                    Click Continue to name and save this project.
                  </div>
                </div>
              </div>
            ) : (
              <div
                data-testid="add-wizard-app-cta"
                style={{
                  padding: 14, marginBottom: 14,
                  background: "linear-gradient(135deg, rgba(255,138,42,0.08), rgba(255,138,42,0.02))",
                  border: "1px solid rgba(255,138,42,0.32)",
                  borderRadius: 8,
                }}>
                <div style={{
                  display: "flex", alignItems: "center", gap: 8,
                  marginBottom: 8,
                }}>
                  <Github size={14} />
                  <strong style={{ fontSize: 13 }}>
                    Install Aurem for {repo.full_name}
                  </strong>
                  <span style={{
                    fontSize: 10, padding: "2px 8px",
                    background: "rgba(255,138,42,0.24)",
                    color: "#ffb27a", borderRadius: 10,
                    letterSpacing: "0.04em",
                  }}>
                    RECOMMENDED
                  </span>
                </div>
                <p style={{
                  fontSize: 12, color: "var(--text-faint)",
                  margin: "0 0 10px", lineHeight: 1.5,
                }}>
                  One click — no token to manage or rotate.
                  You pick which repos Aurem can see. Revoke any time from GitHub.
                </p>
                <button
                  type="button"
                  data-testid="add-wizard-app-install-btn"
                  data-guide-target="connect-github-btn"
                  onClick={openAppInstallPopup}
                  style={{
                    display: "inline-flex", alignItems: "center", gap: 8,
                    padding: "8px 14px",
                    background: "var(--accent-2, #FF8A2A)",
                    color: "#fff", border: "none",
                    borderRadius: 6, fontSize: 13, fontWeight: 600,
                    cursor: "pointer",
                  }}>
                  <Github size={13} />
                  Continue with GitHub App
                  <ArrowRight size={13} />
                </button>
                <div style={{
                  fontSize: 10.5, color: "var(--text-faint)",
                  marginTop: 8,
                }}>
                  Opens in a popup. This wizard picks up automatically
                  after you finish selecting {repo.repo}.
                </div>
                {/* 2026-08-21 — founder-reported: GitHub's own "Select
                    repositories" search widget can briefly show "No
                    repositories found" right after authorizing — a
                    GitHub-side glitch, not an AUREM bug. */}
                <div data-testid="add-wizard-github-glitch-hint" style={{
                  fontSize: 10.5, color: "var(--text-faint)",
                  marginTop: 6, padding: "6px 8px",
                  background: "rgba(255,255,255,0.03)",
                  border: "1px solid var(--border)",
                  borderRadius: 4, lineHeight: 1.5,
                }}>
                  💡 If GitHub shows "No repositories found" while picking
                  repos, just type your repo's name in that search box (or
                  wait a second and reopen it) — it's a GitHub-side hiccup,
                  your repos are there.
                </div>
              </div>
            )}

            <div style={navRowStyle}>
              <button
                type="button"
                onClick={() => setStep(1)}
                data-testid="wizard-back-2"
                className="btn-ghost"
                style={{ padding: "10px 18px", fontSize: 12 }}
              >
                <ArrowLeft size={12} /> Back
              </button>
              <button
                type="button"
                onClick={() => setStep(3)}
                disabled={!installationForRepo}
                data-testid="wizard-next-2"
                style={cta(!!installationForRepo)}
              >
                Next <ArrowRight size={14} />
              </button>
            </div>
          </div>
        )}

        {/* ──────────────── Step 3 — Confirm + save ──────────────── */}
        {step === 3 && repo && installationForRepo && (
          <div data-testid="wizard-step-3">
            <p style={{
              fontSize: 13, color: "var(--text-dim)",
              margin: "0 0 14px", lineHeight: 1.6,
            }}>
              Everything checks out. Confirm the project name and we&apos;ll
              take you to the chat.
            </p>

            {/* Summary card */}
            <div
              data-testid="confirm-summary"
              style={{
                padding: 14, marginBottom: 14,
                background: "rgba(34,197,94,0.06)",
                border: "1px solid rgba(34,197,94,0.32)",
                borderRadius: 10,
                display: "flex", flexDirection: "column", gap: 8,
                fontSize: 12, color: "var(--text)",
                fontFamily: "'JetBrains Mono', monospace",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <ShieldCheck size={14} style={{ color: "#22c55e" }} />
                <span>Access to <strong>{repo.full_name}</strong> verified</span>
              </div>
              <div style={{
                fontSize: 11, color: "var(--text-dim)", paddingLeft: 22,
              }}>
                Connected via GitHub App · @{installationForRepo.github_login}
              </div>
            </div>

            <label style={{ display: "grid", gap: 6, marginBottom: 4 }}>
              <span style={{
                fontSize: 11, color: "var(--text-dim)",
                fontFamily: "'JetBrains Mono', monospace",
                letterSpacing: "0.06em", textTransform: "uppercase",
              }}>
                Project name
              </span>
              <input
                data-testid="project-name-input"
                type="text"
                value={projectName}
                onChange={(e) => setProjectName(e.target.value)}
                placeholder={repo.repo}
                className="input"
                style={{
                  fontFamily: "'JetBrains Mono', monospace", fontSize: 13,
                }}
              />
            </label>

            <div style={navRowStyle}>
              <button
                type="button"
                onClick={() => setStep(2)}
                data-testid="wizard-back-3"
                className="btn-ghost"
                disabled={saving}
                style={{ padding: "10px 18px", fontSize: 12 }}
              >
                <ArrowLeft size={12} /> Back
              </button>
              <button
                type="button"
                onClick={handleSave}
                disabled={saving || !projectName.trim()}
                data-testid="wizard-save"
                data-guide-target="continue-btn"
                style={cta(!saving && !!projectName.trim())}
              >
                {saving ? <Loader2 size={14} className="animate-spin" /> : null}
                {saving ? "Saving…" : "Save & Open Chat"}
                {!saving && <ArrowRight size={14} />}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Style atoms ───────────────────────────────────────────────────────

const codeChip = {
  background: "rgba(255,138,42,0.10)",
  color: "var(--accent-2, #FF8A2A)",
  padding: "1px 6px",
  borderRadius: 4,
  fontFamily: "'JetBrains Mono', monospace",
  fontSize: 11,
};

const navRowStyle = {
  display: "flex", justifyContent: "space-between", alignItems: "center",
  gap: 10, paddingTop: 18, marginTop: 18,
  borderTop: "0.5px solid var(--border, rgba(255,255,255,0.08))",
};

function cta(enabled) {
  return {
    display: "inline-flex", alignItems: "center", gap: 8,
    padding: "10px 22px",
    background: enabled ? "var(--accent-2, #FF8A2A)" : "rgba(255,255,255,0.06)",
    color:      enabled ? "#0a0e1a"               : "var(--text-faint)",
    border: "none", borderRadius: 8,
    fontSize: 13, fontWeight: 600,
    fontFamily: "'JetBrains Mono', monospace",
    cursor: enabled ? "pointer" : "not-allowed",
  };
}

