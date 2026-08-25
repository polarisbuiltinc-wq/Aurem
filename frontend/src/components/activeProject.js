/**
 * activeProject.js — localStorage-backed active-project id helpers.
 * Extracted from TabBar.jsx (2026-08-27, mechanical split — no
 * behaviour change) so both TabBar.jsx and useActiveProject.js can
 * share it without a circular import.
 */
const ACTIVE_KEY = "aurem_active_project";

export function getActiveProjectId() {
  return localStorage.getItem(ACTIVE_KEY) || null;
}

export function setActiveProjectId(id) {
  if (id) localStorage.setItem(ACTIVE_KEY, id);
  else localStorage.removeItem(ACTIVE_KEY);
  window.dispatchEvent(new Event("aurem:project-changed"));
}
