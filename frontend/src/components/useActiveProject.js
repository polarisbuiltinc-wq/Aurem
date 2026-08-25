/**
 * useActiveProject.js — hook: subscribe to active-project changes from
 * anywhere in the tree. Extracted from TabBar.jsx (2026-08-27,
 * mechanical split — no behaviour change) to keep that file under the
 * platform's file-size guard.
 */
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { getActiveProjectId, setActiveProjectId } from "./activeProject";

export function useActiveProject() {
  const [pid, setPid] = useState(() => getActiveProjectId());
  // Iter 212m-105 — Synchronously hydrate from localStorage cache so
  // downstream consumers (AskAdvisor projectId, ChatPanel activeProject,
  // TopBar breadcrumb) get a non-null value on the very first render
  // after login. Without this, the Ask Advisor's tool calls hit the
  // backend with project_id=null and the model replies "No repo
  // connected" even when the user has projects.
  const [project, setProject] = useState(() => {
    if (!pid) return null;
    try {
      const raw = localStorage.getItem("aurem_projects_cache");
      if (!raw) return null;
      const cached = JSON.parse(raw);
      return (Array.isArray(cached) && cached.find((x) => x.project_id === pid)) || null;
    } catch { return null; }
  });

  useEffect(() => {
    function onChange() { setPid(getActiveProjectId()); }
    window.addEventListener("aurem:project-changed", onChange);
    return () => window.removeEventListener("aurem:project-changed", onChange);
  }, []);

  useEffect(() => {
    if (!pid) {
      // Iter 212m-190 — even with no saved active id, still fetch the
      // list so we can AUTO-SEED the first wired project. This is the
      // reliable auto-restore path for:
      //   • fresh browsers with empty localStorage
      //   • users whose last active project was deleted while logged out
      //   • incognito sessions
      let cancelled = false;
      api.get("/cto/projects/list")
        .then((r) => {
          if (cancelled) return;
          const list = r.data?.projects || [];
          try { localStorage.setItem("aurem_projects_cache", JSON.stringify(list)); }
          catch { /* quota — ignore */ }
          if (list.length === 0) return;
          const wired = list.filter((p) => p.github_owner && p.github_repo);
          const target = wired[0] || list[0];
          if (target) {
            // setActiveProjectId dispatches `aurem:project-changed`
            // which re-runs this hook with the new pid, so we do not
            // manually call setProject here.
            setActiveProjectId(target.project_id);
          }
        })
        .catch(() => { /* offline / auth failure — silent */ });
      return () => { cancelled = true; };
    }
    let cancelled = false;
    api.get("/cto/projects/list")
      .then((r) => {
        if (cancelled) return;
        const list = r.data?.projects || [];
        // Keep the cache fresh for the next mount.
        try { localStorage.setItem("aurem_projects_cache", JSON.stringify(list)); }
        catch { /* quota — ignore */ }
        const p = list.find((x) => x.project_id === pid);
        if (p) {
          setProject(p);
          return;
        }
        // Iter 212m-190 — saved active id points to a project that no
        // longer exists (deleted while logged out, revoked, etc.).
        // Auto-heal by falling back to the first wired project instead
        // of leaving the UI stuck on a ghost.
        if (list.length > 0) {
          const wired = list.filter((x) => x.github_owner && x.github_repo);
          const target = wired[0] || list[0];
          if (target) {
            setActiveProjectId(target.project_id);   // triggers hook re-run
            return;
          }
        }
        // No projects at all — clear the stale pin so consumers render
        // the "no repo" state correctly.
        setProject(null);
        setActiveProjectId(null);
      })
      .catch(() => { /* keep cached project intact on transient failure */ });
    return () => { cancelled = true; };
  }, [pid]);

  return project;
}
