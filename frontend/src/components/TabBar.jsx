/**
 * TabBar.jsx — Emergent-style header tabs.
 *
 *   [Home] [project_a ✕] [project_b ✕] [+]
 *
 * - "Home" is the default chat scope (no project context)
 * - Each project tab pins the chat to that project (project_id stored
 *   in localStorage as `aurem_active_project`)
 * - "+" button opens the Projects page to add/connect a new repo
 */
import React, { useEffect, useState, useCallback } from "react";
import { X, Plus, FolderGit2 } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { getActiveProjectId, setActiveProjectId } from "./activeProject";

export { getActiveProjectId, setActiveProjectId } from "./activeProject";
export { useActiveProject } from "./useActiveProject";

export default function TabBar() {
  const [projects, setProjects] = useState([]);
  const [active, setActive] = useState(() => getActiveProjectId());
  const navigate = useNavigate();

  const refresh = useCallback(async () => {
    try {
      const r = await api.get("/cto/projects/list");
      const list = r.data?.projects || [];
      setProjects(list);
      // Keep the cache fresh for hooks that hydrate synchronously
      // on next mount (useActiveProject reads this on first render).
      try { localStorage.setItem("aurem_projects_cache", JSON.stringify(list)); }
      catch { /* quota — ignore */ }

      // Iter 212m-190 — Active-project auto-restore / auto-heal.
      // Priority order:
      //   1. If saved active id still exists in the list → keep it.
      //   2. If saved id was deleted (or never set) AND at least one
      //      *wired* (has github_owner + github_repo) project exists →
      //      auto-activate the first wired one. This covers:
      //         a) fresh browsers with no localStorage seed
      //         b) users whose active project was deleted while they
      //            were logged out
      //         c) the pre-existing "exactly one wired project" case
      //   3. If nothing wired but there are projects → activate first
      //      so the chat isn't stuck on null forever.
      const savedId = getActiveProjectId();
      const savedStillExists = savedId && list.some((p) => p.project_id === savedId);
      const wired = list.filter((p) => p.github_owner && p.github_repo);

      if (savedStillExists) {
        // Nothing to do — user's last project is intact.
        return;
      }
      // Saved id is either missing or points to a deleted project.
      if (wired.length > 0) {
        setActiveProjectId(wired[0].project_id);
      } else if (list.length > 0) {
        setActiveProjectId(list[0].project_id);
      } else if (savedId) {
        // No projects at all but a stale id was pinned — clear it so
        // downstream consumers correctly render the "no repo" state
        // instead of pointing at a ghost project.
        setActiveProjectId(null);
      }
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    refresh();
    const onChange = () => {
      setActive(getActiveProjectId());
      // Iter 212m-5 — also refresh the project list when active project
      // changes. This catches deletion (sets active to null) so the
      // deleted tab disappears immediately instead of waiting for
      // window focus.
      refresh();
    };
    window.addEventListener("aurem:project-changed", onChange);
    window.addEventListener("focus", refresh);
    return () => {
      window.removeEventListener("aurem:project-changed", onChange);
      window.removeEventListener("focus", refresh);
    };
  }, [refresh]);

  function switchTo(id) {
    setActiveProjectId(id);
    setActive(id);
  }

  function closeTab(e, id) {
    e.stopPropagation();
    if (active === id) {
      setActiveProjectId(null);
      setActive(null);
    }
    // Note: this only "closes" the tab from the active set — project remains in DB.
    // The user can re-open by going to /projects and clicking the row.
  }

  return (
    <div
      data-testid="tabbar"
      style={{
        display: "flex", alignItems: "center", gap: 2,
        padding: "8px 12px 0",
        background: "transparent",
        borderBottom: "1px solid var(--border)",
        overflowX: "auto", minHeight: 42,
      }}
    >
      {/* Iter 212m-20 — Home tab removed per founder request.
          The chat panel always operates inside a project scope; the
          "no project" state is reachable via /projects sidebar.
          Removing the Home pill cleans up the tab strip so customers
          don't accidentally drop out of their active project. */}
      {projects.map((p) => (
        <Tab
          key={p.project_id}
          testid={`tab-${p.project_id}`}
          // Iter 212m-15 — also expose a name-slug testid so Playwright
          // scripts can drive `getByTestId('project-tab-dogfood')`
          // instead of needing the opaque project_id.
          nameTestid={`project-tab-${(p.name || '').toLowerCase().replace(/[^a-z0-9-]+/g, '-')}`}
          label={p.name}
          Icon={FolderGit2}
          active={active === p.project_id}
          onClick={() => switchTo(p.project_id)}
          onClose={(e) => closeTab(e, p.project_id)}
        />
      ))}
      <button
        data-testid="tab-add"
        onClick={() => navigate("/projects?add=1")}
        title="Add a new project"
        style={{
          marginLeft: 4,
          width: 28, height: 28, borderRadius: 4,
          background: "transparent",
          border: "1px solid var(--border)",
          color: "var(--text-faint)",
          cursor: "pointer",
          display: "flex", alignItems: "center", justifyContent: "center",
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.color = "var(--accent-2)";
          e.currentTarget.style.borderColor = "var(--border-strong)";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.color = "var(--text-faint)";
          e.currentTarget.style.borderColor = "var(--border)";
        }}
      >
        <Plus size={13} />
      </button>
    </div>
  );
}

function Tab({ testid, nameTestid, label, Icon, active, onClick, onClose }) {
  return (
    <div
      data-testid={testid}
      data-name-testid={nameTestid}
      onClick={onClick}
      role="button"
      style={{
        display: "flex", alignItems: "center", gap: 8,
        padding: "7px 12px 7px 10px",
        borderTopLeftRadius: 6, borderTopRightRadius: 6,
        border: `1px solid ${active ? "var(--accent)" : "var(--border)"}`,
        borderBottom: active ? "1px solid var(--panel)" : "1px solid var(--border)",
        background: active ? "var(--panel)" : "transparent",
        color: active ? "var(--accent-2)" : "var(--text-dim)",
        cursor: "pointer",
        fontSize: 12,
        maxWidth: 200,
        marginBottom: -1,
        position: "relative",
        zIndex: active ? 2 : 1,
      }}
      onMouseEnter={(e) => {
        if (!active) e.currentTarget.style.color = "var(--text)";
      }}
      onMouseLeave={(e) => {
        if (!active) e.currentTarget.style.color = "var(--text-dim)";
      }}
    >
      <Icon size={12} style={{ flexShrink: 0 }} />
      <span
        style={{
          whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
          fontWeight: active ? 600 : 400,
        }}
      >
        {label}
      </span>
      {onClose && (
        <button
          onClick={onClose}
          aria-label="Close tab"
          style={{
            background: "none", border: "none", padding: 0,
            color: "var(--text-faint)", cursor: "pointer",
            display: "inline-flex", marginLeft: 2,
          }}
          onMouseEnter={(e) => (e.currentTarget.style.color = "var(--danger)")}
          onMouseLeave={(e) => (e.currentTarget.style.color = "var(--text-faint)")}
        >
          <X size={11} />
        </button>
      )}
    </div>
  );
}


