/**
 * lib/adminNav.js — single source of truth for admin navigation.
 *
 * 2026-08-27 · Admin Compact M4 — the founder-only "Admin" flyout in
 * the global RailShell and the in-shell sidebar inside Admin.jsx used
 * to be two separately hand-maintained lists (5 items vs 23 items),
 * which is exactly how the Rail's "Overview" link ended up silently
 * pointing at Cockpit (M5) — nobody updates two lists in lockstep.
 * Both surfaces now read id/label/route from this ONE array; each
 * surface still owns its own icon choice + layout (flyout vs grouped
 * sidebar), only the navigation data itself is unified.
 *
 * M2 note: "payments" and the rail's "financials" both resolve to the
 * same merged AdminFinancials page now — kept as two entries on
 * purpose (they live in two different navigation *systems*: the
 * always-visible global rail vs. the in-context admin sidebar), not a
 * true duplicate the way the /admin/observability route was.
 */
export const ADMIN_NAV = [
  { id: "cockpit",         label: "Cockpit",              route: "/admin/cockpit",       group: null },
  { id: "overview",        label: "Overview",             route: "/admin/overview",       group: "MONITOR" },
  { id: "llm_credits",     label: "LLM Credits",          route: "/admin/llm-credits",    group: "MONITOR" },
  { id: "parliament_live", label: "Parliament Live",      route: "/admin/parliament-live",group: "MONITOR" },
  { id: "qa_health",       label: "QA Health",            route: "/admin/qa",             group: "MONITOR" },
  { id: "maintenance",     label: "Maintenance",          route: "/admin/maintenance",    group: "MONITOR" },
  { id: "arch",            label: "Architecture",         route: "/admin/architecture",   group: "MONITOR" },
  { id: "bin_tracker",     label: "BIN Tracker",          route: "/admin/bin-tracker",    group: "USERS" },
  { id: "users",           label: "Users (Legacy)",       route: "/admin/users",          group: "USERS" },
  { id: "support",         label: "Support",              route: "/admin/support",        group: "USERS" },
  { id: "suggestions",     label: "Suggestions",          route: "/admin/suggestions",    group: "USERS" },
  { id: "audit",           label: "Audit Log",            route: "/admin/audit",          group: "USERS" },
  { id: "feature_flags",   label: "Feature Flags",        route: "/admin/feature-flags",  group: "CONFIG" },
  { id: "house_rules",     label: "House Rules V2",       route: "/admin/house-rules",    group: "CONFIG" },
  { id: "robot_guide",     label: "Robot Guide",          route: "/admin/robot-guide",    group: "CONFIG" },
  // Folded in from the rail's rail-only ADMIN_ITEMS — previously
  // unreachable from inside the Admin sidebar (M4).
  { id: "api_keys",        label: "API Keys",             route: "/admin/api-keys",       group: "CONFIG" },
  { id: "payments",        label: "Payments & Revenue",   route: "/admin/payments",       group: "BUSINESS" },
  { id: "tokens",          label: "Token P&L",            route: "/admin/token-pnl",      group: "BUSINESS" },
  { id: "dash",            label: "Dashboard",            route: "/admin/dashboard",      group: "SYSTEM" },
  { id: "projects",        label: "Projects",             route: "/admin/projects",       group: "SYSTEM" },
  { id: "tasks",           label: "Tasks",                route: "/admin/tasks",          group: "SYSTEM" },
  { id: "agent_perf",      label: "Agent Performance",    route: "/admin/agent-performance", group: "SYSTEM" },
  { id: "mcp",             label: "MCP Usage",            route: "/admin/mcp-usage",      group: "SYSTEM" },
  { id: "reliability",     label: "Reliability",          route: "/admin/reliability",    group: "SYSTEM" },
  { id: "settings",        label: "Settings",             route: "/admin/settings",       group: "SYSTEM" },
];

export function findAdminNavItem(id) {
  return ADMIN_NAV.find((n) => n.id === id) || null;
}

/** Rebuild the sidebar's grouped shape (with `{group: "X"}` separator
 * rows) from the flat ADMIN_NAV list, attaching each surface's own
 * Icon component by id. */
export function buildGroupedAdminNav(iconMap) {
  const out = [];
  let lastGroup;
  for (const item of ADMIN_NAV) {
    if (item.group && item.group !== lastGroup) {
      out.push({ group: item.group });
    }
    lastGroup = item.group;
    out.push({ id: item.id, label: item.label, route: item.route, Icon: iconMap[item.id] });
  }
  return out;
}
