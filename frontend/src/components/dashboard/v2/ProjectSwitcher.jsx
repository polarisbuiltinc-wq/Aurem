/**
 * ProjectSwitcher.jsx — R3 (Repo Quick-Switch).
 *
 * Sits directly beside the TopBar breadcrumb (owner/repo · branch) —
 * the ONE place founders already look for "what am I looking at right
 * now" (founder spec, do NOT duplicate this as a second picker in the
 * sidebar — RailShell's Chat flyout repo list already exists and stays
 * as-is).
 *
 * Persistence reuses the SAME localStorage key + `aurem:project-changed`
 * event the sidebar/RailShell already use (see activeProject.js /
 * useActiveProject.js) so both pickers always agree.
 *
 * Revoked/unreachable detection reuses the existing
 * `GET /cto/projects/connection-status` endpoint (same one
 * RevokedRepoBanner polls) — no backend schema change.
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ChevronDown, AlertTriangle, Check } from "lucide-react";
import { api } from "../../../lib/api";
import { toast } from "../../Toast";

const UNREACHABLE = new Set(["disconnected", "unreachable"]);

export function ProjectSwitcher({ projects = [], activeProjectId, onSelect }) {
  const [open, setOpen] = useState(false);
  const [coords, setCoords] = useState({ top: 0, left: 0 });
  const [statuses, setStatuses] = useState({}); // project_id -> status
  const rootRef = useRef(null);
  const btnRef = useRef(null);
  const panelRef = useRef(null);
  const healedRef = useRef(false);

  const refreshStatuses = useCallback(() => {
    api.get("/cto/projects/connection-status")
      .then((r) => {
        const map = {};
        for (const s of (r.data?.statuses || [])) map[s.project_id] = s.status;
        setStatuses(map);
      })
      .catch(() => { /* keep last-known statuses on a transient blip */ });
  }, []);

  useEffect(() => {
    refreshStatuses();
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") refreshStatuses();
    }, 30000);
    return () => clearInterval(timer);
  }, [refreshStatuses]);

  // H1 (X1/W1 hardening, 2026-08-30, overnight-loop-2 P0) — NEVER
  // silently switch the active project. This effect used to call
  // onSelect() automatically whenever the active project's
  // connection-status read "unreachable" OR "disconnected" — but
  // "unreachable" is an explicitly TEMPORARY network blip (see
  // repo_status.py: "a network failure is NOT a revocation"), so a
  // transient timeout (e.g. during a burst of GitHub API calls) could
  // silently aim the user — and any live loop started right after —
  // at a completely different repo with zero action from them. This
  // is the confirmed root cause of the reported "active project
  // silently switched mid-session with no user action" incident
  // (see REPORT-x1-crossproject.md §W1). Fix: the active project may
  // now change ONLY via an explicit click on an item below (or a
  // deep-link/account load elsewhere) — this effect only ever shows a
  // one-line, non-navigating notice for a REAL ("disconnected")
  // revocation, and says nothing at all for a transient one.
  useEffect(() => {
    if (healedRef.current) return;
    if (!activeProjectId || projects.length === 0) return;
    if (Object.keys(statuses).length === 0) return;
    const current = projects.find((p) => p.project_id === activeProjectId);
    if (!current || statuses[activeProjectId] !== "disconnected") return;
    healedRef.current = true;
    const oldLabel = current.github_repo || current.name || "project";
    toast({
      message: `${oldLabel} looks disconnected from GitHub — use the switcher above to pick another project, or reconnect it in Settings.`,
      kind: "info",
    });
  }, [activeProjectId, projects, statuses]);

  const toggle = useCallback(() => {
    setOpen((o) => {
      const next = !o;
      if (next) {
        refreshStatuses();
        const rect = btnRef.current?.getBoundingClientRect();
        if (rect) setCoords({ top: rect.bottom + 6, left: rect.left });
      }
      return next;
    });
  }, [refreshStatuses]);

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e) => {
      if (
        rootRef.current && !rootRef.current.contains(e.target) &&
        panelRef.current && !panelRef.current.contains(e.target)
      ) setOpen(false);
    };
    const onKey = (e) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={rootRef} style={{ position: "relative", display: "inline-flex" }}>
      <button
        ref={btnRef}
        onClick={toggle}
        data-testid="project-switcher-trigger"
        title="Switch project"
        className="flex items-center justify-center rounded-sm text-muted-foreground hover:text-foreground transition-colors"
        style={{ width: 16, height: 16 }}
      >
        <ChevronDown className="size-3" strokeWidth={2.5} />
      </button>

      {open && createPortal(
        <div
          ref={panelRef}
          data-testid="project-switcher-panel"
          style={{ position: "fixed", top: coords.top, left: coords.left }}
          className="z-[999] w-[300px] max-h-[360px] overflow-y-auto rounded-lg border border-border bg-[#0A0A0A] shadow-xl"
        >
          <div className="px-3 py-2 border-b border-border text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
            Switch project
          </div>
          {projects.length === 0 ? (
            <p data-testid="project-switcher-empty" className="px-3 py-4 text-[12px] text-muted-foreground/70">
              No connected projects yet.
            </p>
          ) : (
            projects.map((p) => {
              const active = p.project_id === activeProjectId;
              const unreachable = UNREACHABLE.has(statuses[p.project_id]);
              const repoLabel = p.github_owner ? `${p.github_owner}/${p.github_repo || p.name}` : (p.github_repo || "untitled");
              const projectName = p.name && p.name !== p.github_repo ? p.name : null;
              return (
                <div
                  key={p.project_id}
                  data-testid={`project-switcher-item-${p.project_id}`}
                  role="button"
                  aria-disabled={unreachable}
                  data-disabled={unreachable ? "true" : "false"}
                  onClick={() => {
                    if (unreachable) return;
                    onSelect?.(p.project_id);
                    setOpen(false);
                  }}
                  className={
                    "flex items-center gap-2 px-3 py-2 border-l-2 " +
                    (unreachable
                      ? "opacity-45 cursor-not-allowed border-transparent text-muted-foreground"
                      : active
                        ? "cursor-pointer bg-primary/10 border-primary text-foreground"
                        : "cursor-pointer border-transparent text-muted-foreground hover:bg-white/[0.03] hover:text-foreground")
                  }
                >
                  <div className="flex-1 min-w-0">
                    {/* R7 — project NAME shown above owner/repo so two
                        projects pointing at the same repo are still
                        distinguishable in the list. */}
                    {projectName && (
                      <div
                        data-testid={`project-switcher-item-name-${p.project_id}`}
                        className="truncate text-[12px] font-medium"
                        title={projectName}
                      >
                        {projectName}
                      </div>
                    )}
                    <div className="truncate text-[11px] font-mono text-muted-foreground/80" title={repoLabel}>{repoLabel}</div>
                    {unreachable && (
                      <div className="flex items-center gap-1 text-[10px] text-amber-500 mt-[2px]">
                        <AlertTriangle className="size-2.5" strokeWidth={2.5} /> repo unreachable
                      </div>
                    )}
                  </div>
                  {active && !unreachable && <Check className="size-3 shrink-0 text-primary" strokeWidth={2.5} />}
                </div>
              );
            })
          )}
        </div>,
        document.body,
      )}
    </div>
  );
}
