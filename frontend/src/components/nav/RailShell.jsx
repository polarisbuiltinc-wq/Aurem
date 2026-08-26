/**
 * RailShell.jsx — Iter 356 · Unified navigation shell.
 *
 * ONE nav pattern for every authenticated page: a compact 56px icon
 * rail + an expandable flyout panel with grouped, real route links.
 * Replaces the two disconnected shells (legacy "AUREM Dev" full nav
 * sidebar + the ORA dashboard sidebar). Old components stay in the
 * tree unused until the founder confirms prod stability (Phase 4).
 *
 * Usage:
 *   <RailShell>{page}</RailShell>          → rail + content column
 *   <RailShell railOnly repos={…} … />     → rail only (Dashboard owns
 *                                            its layout; passes live
 *                                            repo entries + handlers)
 *
 * Design tokens (existing site values only):
 *   bg rgb(7,8,13) · accent #ff8a2a · border rgba(255,200,120,.1)
 *   Jost for wordmark/headings · Inter for dense item labels.
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import {
  MessageSquare, Rocket, BarChart3, Settings as Cog, ShieldCheck,
  Plus, FolderGit2, Globe, Zap, Trophy, Coins, Gift,
  Receipt, User as UserIcon, Plug, Lock, KeyRound, LayoutDashboard,
  Users as UsersIcon, Lightbulb, Landmark, X, Menu, LogOut,
} from "lucide-react";
import { api, getToken, logout as apiLogout, newSessionId } from "../../lib/api";
import { setChipV2Enabled } from "../../lib/chipFlag";
import { useChatSession } from "../Shell";

const BG      = "rgb(7,8,13)";
const ACCENT  = "#ff8a2a";
const BORDER  = "rgba(255,200,120,.1)";
const LOGO_SRC = "https://customer-assets.emergentagent.com/job_launch-pad-237/artifacts/oj4581h8_Gemini_Generated_Image_sozbptsozbptsozb.png";
const ITEM_FONT = "'Inter', 'Jost', sans-serif";

const SHIP_ITEMS = [
  { to: "/deploy",      label: "Deploy",      icon: Rocket,  testid: "rail-item-deploy" },
  { to: "/domain",      label: "Domain",      icon: Globe,   testid: "rail-item-domain" },
  { to: "/automations", label: "Automations", icon: Zap,     testid: "rail-item-automations" },
  { to: "/wall",        label: "Ship Wall",   icon: Trophy,  testid: "rail-item-wall" },
];
const INSIGHT_ITEMS = [
  { to: "/analytics", label: "Analytics", icon: BarChart3, testid: "rail-item-analytics" },
  { to: "/tokens",    label: "Tokens",    icon: Coins,     testid: "rail-item-tokens" },
  { to: "/wrapped",   label: "Wrapped",   icon: Gift,      testid: "rail-item-wrapped" },
];
const SETTINGS_ITEMS = [
  { to: "/settings?tab=profile",      label: "Profile",         icon: UserIcon, testid: "rail-item-profile" },
  { to: "/settings?tab=plans",        label: "Plans & usage",   icon: Receipt,  testid: "rail-item-plans" },
  { to: "/settings?tab=integrations", label: "Integrations",    icon: Plug,     testid: "rail-item-integrations" },
  { to: "/settings?tab=vault",        label: "Vault",           icon: Lock,     testid: "rail-item-vault" },
  { to: "/integrations",              label: "IDE setup (MCP)", icon: Plug,     testid: "rail-item-mcp" },
];
import { findAdminNavItem } from "../../lib/adminNav";

// 2026-08-27 · Admin Compact M4/M5 — labels/routes now sourced from the
// single shared `ADMIN_NAV` (lib/adminNav.js) instead of a separately
// hand-maintained list; this is what fixes the "Overview" mislabel
// (M5) — it was pointing at Cockpit because this list drifted out of
// sync with the sidebar's own copy. Icons stay rail-specific.
const ADMIN_ITEMS = [
  { to: findAdminNavItem("cockpit").route,  label: findAdminNavItem("cockpit").label,  icon: LayoutDashboard, testid: "rail-item-admin-overview" },
  { to: "/admin/financials", label: "Financials",  icon: Landmark,        testid: "rail-item-admin-financials" },
  { to: findAdminNavItem("users").route,    label: "Users",       icon: UsersIcon,       testid: "rail-item-admin-users" },
  { to: findAdminNavItem("suggestions").route, label: "Suggestions", icon: Lightbulb,    testid: "rail-item-admin-suggestions" },
  { to: findAdminNavItem("api_keys").route, label: "API keys",    icon: KeyRound,        testid: "rail-item-admin-apikeys" },
];

const SECTIONS = [
  { id: "chat",     label: "Chat",     Icon: MessageSquare, match: ["/dashboard", "/projects"] },
  { id: "ship",     label: "Ship",     Icon: Rocket,        match: SHIP_ITEMS.map((i) => i.to) },
  { id: "insights", label: "Insights", Icon: BarChart3,     match: INSIGHT_ITEMS.map((i) => i.to) },
  { id: "settings", label: "Settings", Icon: Cog,           match: ["/settings", "/integrations"] },
  { id: "admin",    label: "Admin",    Icon: ShieldCheck,   match: ["/admin"], founderOnly: true },
];

function sessionStorageKey(pid) {
  return pid ? `aurem_session_proj_${pid}` : "aurem_session_home";
}

export default function RailShell({
  children,
  railOnly = false,
  repos: reposProp,
  onSelectRepo,
  onAddRepo,
}) {
  const navigate = useNavigate();
  const location = useLocation();
  const { setSessionId } = useChatSession();
  const [open, setOpen] = useState(null);          // section id | null
  const [repos, setRepos] = useState(reposProp || []);
  const [sessions, setSessions] = useState([]);
  const [isFounder, setIsFounder] = useState(false);
  const wrapRef = useRef(null);

  // Feb 2026 · Auto-hide rail on chat typing.
  // Mirrors the existing Shell.jsx `hiddenForTyping` pattern:
  // ChatPanel dispatches `aurem:chat-session-started` when the user
  // first sends a message and `aurem:chat-session-reset` when they
  // switch sessions / create a new chat. We slide the 56px rail off
  // to the left when typing so the founder gets a distraction-free
  // chat window. A floating pill button (like "Ask Advisor") on the
  // left edge is the manual way back. Founder can also toggle it
  // anytime via the little chevron pinned to the rail's edge.
  const AUTO_HIDE_KEY = "aurem_rail_autohide";
  // Feb 2026 · Founder request: "sidebar ko default hide rkho".
  // Rail now starts hidden on every mount so the chat window is the
  // hero. The bottom-left "SIDEBAR" peek tab (matching the ADVISOR
  // launcher) is the one-tap way in. When the founder toggles AUTO
  // OFF, the effect below flips `hiddenForTyping` back to false so
  // the rail re-appears — semantics preserved.
  // Iter 388i — Bug 8 fix. Rail was starting HIDDEN on every mount
  // (`useState(true)`), so any founder landing on /dashboard with an
  // already-active chat session saw the rail off-screen with
  // `pointerEvents: none`.  Clicks on the barely-visible Insights /
  // Admin icons fell through to the chat area and the founder
  // perceived "chat reloaded, nothing navigated".  Correct default is
  // VISIBLE — the rail should only hide AFTER the user actually
  // starts typing (via `aurem:chat-session-started`).  Ship section
  // worked before because founders reached it via the top-bar chip,
  // not the rail.
  const [hiddenForTyping, setHiddenForTyping] = useState(false);
  // Iter 388-af — track whether a chat session has ever started in
  // this page load. Powers the "toggle AUTO ON mid-session → collapse
  // immediately" behaviour (founder feedback 2026-02-14).
  const sessionActiveRef = useRef(false);
  const [autoHideEnabled, setAutoHideEnabled] = useState(() => {
    try {
      const v = localStorage.getItem(AUTO_HIDE_KEY);
      return v == null ? true : v === "1";
    } catch { return true; }
  });
  useEffect(() => {
    // Iter 388i — Bug 8 fix, part 2. ChatPanel dispatches
    // `chat-session-started` TWICE:
    //   1. From line 1240 with `detail.restored = true` on every page
    //      reload where an existing session has messages (i.e. any
    //      non-empty chat view). This should NOT hide the rail —
    //      the founder just landed on the page and needs to navigate.
    //   2. From line 1631 (no `restored` flag) when the founder
    //      actually types + sends the first message of a fresh
    //      session. This SHOULD hide the rail (distraction-free
    //      typing). Only respect the second event.
    //
    // Iter 388-af — mark the session as active on EITHER event (even
    // restored=true) so the `autoHideEnabled` effect below can hide
    // the rail immediately when the founder toggles AUTO ON while a
    // conversation is already in progress. Restored sessions still
    // won't auto-hide on landing — that Bug 8 behaviour is preserved
    // via the `restored` short-circuit — but they should count as
    // "active" for the AUTO-toggle-ON path.
    const onStart = (e) => {
      sessionActiveRef.current = true;
      if (!autoHideEnabled) return;
      if (e?.detail?.restored) return;
      setHiddenForTyping(true);
      setOpen(null);
    };
    const onReset = () => {
      sessionActiveRef.current = false;
      setHiddenForTyping(false);
    };
    window.addEventListener("aurem:chat-session-started", onStart);
    window.addEventListener("aurem:chat-session-reset", onReset);
    return () => {
      window.removeEventListener("aurem:chat-session-started", onStart);
      window.removeEventListener("aurem:chat-session-reset", onReset);
    };
  }, [autoHideEnabled]);
  // Iter 388-af — mirror behaviour on the AUTO toggle:
  //   - AUTO OFF  →  always re-show the rail (existing behaviour).
  //   - AUTO ON while a session is already active → collapse
  //     immediately. Without this, the founder had to send a fresh
  //     message BEFORE the rail would honour the newly-enabled AUTO
  //     preference — surprising and off-brand for a "AUTO" pill.
  useEffect(() => {
    if (!autoHideEnabled) {
      setHiddenForTyping(false);
    } else if (sessionActiveRef.current) {
      setHiddenForTyping(true);
      setOpen(null);
    }
  }, [autoHideEnabled]);
  const toggleAutoHide = useCallback(() => {
    setAutoHideEnabled((v) => {
      const next = !v;
      try { localStorage.setItem(AUTO_HIDE_KEY, next ? "1" : "0"); } catch { /* ignore */ }
      return next;
    });
  }, []);

  useEffect(() => { if (reposProp) setRepos(reposProp); }, [reposProp]);

  // Server-side founder check — the Admin section renders ONLY after
  // /auth/me confirms tier, never from cached localStorage alone.
  useEffect(() => {
    if (!getToken()) return;
    let dead = false;
    api.get("/auth/me")
      .then((r) => {
        if (dead) return;
        const me = r.data?.user || r.data || {};
        setIsFounder(me.tier === "founder" || me.is_admin === true);
        // Phase E — mirror workcard_chip_v2 for chip-rendering components.
        setChipV2Enabled(r.data?.workcard_chip_v2_enabled === true);
      })
      .catch(() => {});
    return () => { dead = true; };
  }, []);

  // Self-fetch repos when the host page didn't supply them.
  useEffect(() => {
    if (reposProp || !getToken()) return;
    let dead = false;
    api.get("/cto/projects/list")
      .then((r) => {
        if (dead) return;
        const activeId = localStorage.getItem("aurem_active_project");
        setRepos((r.data?.projects || []).map((p) => ({
          id:     p.project_id,
          owner:  p.github_owner || "",
          name:   p.github_repo || p.name,
          branch: p.branch || "main",
          active: p.project_id === activeId,
        })));
      })
      .catch(() => {});
    return () => { dead = true; };
  }, [reposProp]);

  // Recent chats for the Chat flyout (scoped to the active project).
  const loadSessions = useCallback(() => {
    if (!getToken()) return;
    const pid = localStorage.getItem("aurem_active_project");
    api.get("/chat/sessions", { params: { project_id: pid || "home" } })
      .then((r) => setSessions(r.data?.sessions || []))
      .catch(() => {});
  }, []);
  useEffect(() => { if (open === "chat") loadSessions(); }, [open, loadSessions]);

  // Close flyout on outside click / Escape / route change.
  useEffect(() => {
    const onDown = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(null);
    };
    // Code-review Feb 2026: `setMobileOpen` was never defined in
    // this component — Escape used to raise ReferenceError. RailShell
    // doesn't own a mobile-nav sheet (SidebarBound does); closing the
    // flyout via `setOpen(null)` is the only correct behaviour here.
    const onKey = (e) => { if (e.key === "Escape") setOpen(null); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, []);
  useEffect(() => { setOpen(null); }, [location.pathname, location.search]);

  // Logo click — the EXACT existing dashboard-logo behaviour
  // (Iter 212m-101, copied verbatim from SidebarBound): click clears
  // app caches (preserves auth) then hard-reloads; Cmd/Ctrl+click just
  // navigates to /dashboard without clearing.
  const onLogoClick = useCallback((e) => {
    if (e.metaKey || e.ctrlKey) { navigate("/dashboard"); return; }
    try {
      const KEEP = new Set(["aurem_token", "aurem_user", "aurem_theme", "aurem_wizard_dismissed", "aurem_projects_cache", "aurem_active_project"]);
      const drops = [];
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (k && !KEEP.has(k)) drops.push(k);
      }
      drops.forEach((k) => { try { localStorage.removeItem(k); } catch {/*noop*/} });
      try { sessionStorage.clear(); } catch {/*noop*/}
      if ("caches" in window) {
        caches.keys().then((keys) => keys.forEach((k) => caches.delete(k))).catch(() => {/*noop*/});
      }
    } catch { /* noop */ }
    const u = new URL(window.location.href);
    u.searchParams.set("_cb", Date.now().toString(36));
    window.location.replace(u.toString());
  }, [navigate]);

  const selectRepo = useCallback((repo) => {
    if (onSelectRepo) { onSelectRepo(repo); setOpen(null); return; }
    localStorage.setItem("aurem_active_project", repo.id);
    window.dispatchEvent(new CustomEvent("aurem:project-changed", { detail: { projectId: repo.id } }));
    setOpen(null);
    navigate("/dashboard");
  }, [onSelectRepo, navigate]);

  const openChat = useCallback((sid) => {
    const pid = localStorage.getItem("aurem_active_project");
    localStorage.setItem(sessionStorageKey(pid), sid);
    setSessionId?.(sid);
    setOpen(null);
    navigate("/dashboard");
  }, [navigate, setSessionId]);

  const newChat = useCallback(() => {
    const sid = newSessionId();
    const pid = localStorage.getItem("aurem_active_project");
    localStorage.setItem(sessionStorageKey(pid), sid);
    setSessionId?.(sid);
    setOpen(null);
    navigate("/dashboard");
  }, [navigate, setSessionId]);

  const sectionActive = (s) => s.match.some((m) => location.pathname.startsWith(m.split("?")[0]));
  const visibleSections = SECTIONS.filter((s) => !s.founderOnly || isFounder);

  /* ── sub-renderers ─────────────────────────────────────────────── */

  const renderLinks = (items) => items.map((it) => {
    const active = (location.pathname + location.search) === it.to
      || (it.to.indexOf("?") === -1 && location.pathname === it.to);
    const I = it.icon;
    return (
      <Link key={it.to} to={it.to} data-testid={it.testid}
        onClick={() => setOpen(null)}
        style={{
          display: "flex", alignItems: "center", gap: 10,
          padding: "8px 12px", borderRadius: 7, textDecoration: "none",
          fontFamily: ITEM_FONT, fontSize: 12.5, fontWeight: 500,
          color: active ? ACCENT : "rgba(226,232,240,0.78)",
          background: active ? "rgba(255,138,42,0.09)" : "transparent",
          transition: "background-color 140ms ease, color 140ms ease",
        }}
        onMouseEnter={(e) => { if (!active) e.currentTarget.style.background = "rgba(255,255,255,0.045)"; }}
        onMouseLeave={(e) => { if (!active) e.currentTarget.style.background = "transparent"; }}
      >
        <I size={14} style={{ flexShrink: 0, opacity: 0.85 }} />
        {it.label}
      </Link>
    );
  });

  const flyoutHeader = (txt) => (
    <div style={{
      fontFamily: ITEM_FONT, fontSize: 10, fontWeight: 700,
      letterSpacing: "0.14em", textTransform: "uppercase",
      color: "rgba(148,163,184,0.7)", padding: "4px 12px 6px",
    }}>{txt}</div>
  );

  const renderChatFlyout = () => (
    <>
      {flyoutHeader("Repositories")}
      <div style={{ display: "grid", gap: 2, marginBottom: 6 }}>
        {repos.length === 0 && (
          <div style={{ fontFamily: ITEM_FONT, fontSize: 12, color: "rgba(148,163,184,0.6)", padding: "6px 12px" }}>
            No repositories connected yet.
          </div>
        )}
        {repos.map((r) => (
          <button key={r.id} type="button"
            data-testid={`rail-repo-${r.id}`}
            onClick={() => selectRepo(r)}
            style={{
              display: "flex", alignItems: "center", gap: 10,
              padding: "7px 12px", borderRadius: 7, border: "none",
              background: r.active ? "rgba(255,138,42,0.09)" : "transparent",
              color: r.active ? ACCENT : "rgba(226,232,240,0.8)",
              fontFamily: ITEM_FONT, fontSize: 12.5, cursor: "pointer",
              textAlign: "left", width: "100%",
              transition: "background-color 140ms ease",
            }}
            onMouseEnter={(e) => { if (!r.active) e.currentTarget.style.background = "rgba(255,255,255,0.045)"; }}
            onMouseLeave={(e) => { if (!r.active) e.currentTarget.style.background = "transparent"; }}
          >
            <span style={{
              width: 7, height: 7, borderRadius: "50%", flexShrink: 0,
              background: r.active ? ACCENT : "rgba(148,163,184,0.45)",
            }} />
            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {r.owner ? `${r.owner}/` : ""}{r.name}
            </span>
          </button>
        ))}
        {onAddRepo && (
          <button type="button" data-testid="rail-add-repo"
            onClick={() => { setOpen(null); onAddRepo(); }}
            style={{
              display: "flex", alignItems: "center", gap: 10,
              padding: "7px 12px", borderRadius: 7, border: "none",
              background: "transparent", color: "rgba(148,163,184,0.85)",
              fontFamily: ITEM_FONT, fontSize: 12.5, cursor: "pointer", width: "100%",
            }}>
            <Plus size={13} /> Add repository
          </button>
        )}
        <Link to="/projects" data-testid="rail-item-projects"
          onClick={() => setOpen(null)}
          style={{
            display: "flex", alignItems: "center", gap: 10,
            padding: "7px 12px", borderRadius: 7, textDecoration: "none",
            color: "rgba(148,163,184,0.85)", fontFamily: ITEM_FONT, fontSize: 12.5,
          }}>
          <FolderGit2 size={13} /> Manage repositories
        </Link>
      </div>
      <div style={{ height: 1, background: BORDER, margin: "4px 8px 8px" }} />
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", paddingRight: 8 }}>
        {flyoutHeader("Recent chats")}
        <button type="button" data-testid="rail-new-chat" onClick={newChat}
          title="New chat"
          style={{
            border: "none", background: "transparent", color: ACCENT,
            cursor: "pointer", display: "flex", alignItems: "center",
          }}>
          <Plus size={14} />
        </button>
      </div>
      <div style={{ display: "grid", gap: 2, overflowY: "auto", minHeight: 0, flex: 1 }}>
        {sessions.length === 0 && (
          <div data-testid="rail-no-sessions" style={{ fontFamily: ITEM_FONT, fontSize: 12, color: "rgba(148,163,184,0.6)", padding: "6px 12px" }}>
            No saved chats yet.
          </div>
        )}
        {sessions.map((s) => {
          const label = (s.title && s.title.trim()) || s.last_message || "Untitled";
          return (
            <button key={s.session_id} type="button"
              data-testid={`rail-session-${s.session_id}`}
              onClick={() => openChat(s.session_id)}
              title={label}
              style={{
                display: "flex", alignItems: "center", gap: 8,
                padding: "6px 12px", borderRadius: 6, border: "none",
                background: "transparent", color: "rgba(226,232,240,0.75)",
                fontFamily: ITEM_FONT, fontSize: 12, cursor: "pointer",
                textAlign: "left", width: "100%",
              }}
              onMouseEnter={(e) => { e.currentTarget.style.background = "rgba(255,255,255,0.045)"; }}
              onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
            >
              <MessageSquare size={11} style={{ flexShrink: 0, opacity: 0.7 }} />
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {label.length > 40 ? label.slice(0, 40) + "…" : label}
              </span>
            </button>
          );
        })}
      </div>
    </>
  );

  const flyoutBody = (id) => {
    if (id === "chat")     return renderChatFlyout();
    if (id === "ship")     return <>{flyoutHeader("Ship")}{renderLinks(SHIP_ITEMS)}</>;
    if (id === "insights") return <>{flyoutHeader("Insights")}{renderLinks(INSIGHT_ITEMS)}</>;
    if (id === "settings") return <>{flyoutHeader("Settings")}{renderLinks(SETTINGS_ITEMS)}</>;
    if (id === "admin")    return <>{flyoutHeader("Admin · founder")}{renderLinks(ADMIN_ITEMS)}</>;
    return null;
  };

  const rail = (
    <div ref={wrapRef} data-testid="rail-shell"
      data-hidden-typing={hiddenForTyping ? "true" : "false"}
      style={{
        position: "relative", display: "flex",
        flexShrink: 0, zIndex: 1200,
        // Iter 388-ai (2026-02-14) — belt+suspenders hide on the OUTER
        // wrapper. Prior versions relied on the inner <nav> alone
        // doing `transform: translateX(-105%) + marginLeft: -56` to
        // collapse its own width contribution. Founder-reported prod
        // regression: `data-hidden-typing` was "true" but visually the
        // rail still rendered — the inner-nav collapse didn't happen
        // for reasons that vary by cached bundle / browser layout.
        // Adding an outer-wrapper collapse guarantees the rail vanishes
        // even if the inner transform silently fails.
        width: hiddenForTyping ? 0 : "auto",
        overflow: hiddenForTyping ? "hidden" : "visible",
        transition: "width 240ms cubic-bezier(0.4,0,0.2,1)",
      }}>
      <nav aria-label="Primary"
        data-testid="rail-nav"
        style={{
        width: 56, background: BG, borderRight: `1px solid ${BORDER}`,
        display: "flex", flexDirection: "column", alignItems: "center",
        padding: "14px 0 16px", gap: 6, height: "100vh",
        position: "sticky", top: 0,
        transform: hiddenForTyping ? "translateX(-105%)" : "translateX(0)",
        opacity: hiddenForTyping ? 0 : 1,
        pointerEvents: hiddenForTyping ? "none" : "auto",
        marginLeft: hiddenForTyping ? -56 : 0,
        transition: "transform 240ms cubic-bezier(0.4,0,0.2,1), opacity 200ms ease, margin-left 240ms cubic-bezier(0.4,0,0.2,1)",
      }}>
        <button type="button" data-testid="ds2-sidebar-logo"
          onClick={onLogoClick}
          title="Click to clear cache and reload · Cmd/Ctrl+click → open dashboard"
          style={{
            border: "none", background: "transparent", cursor: "pointer",
            padding: 0, marginBottom: 14, display: "flex",
          }}>
          <img src={LOGO_SRC} alt="AUREM"
            style={{
              width: 28, height: 28, borderRadius: "50%",
              border: `1px solid ${BORDER}`, objectFit: "cover",
            }} />
        </button>
        {visibleSections.map((s) => {
          const active = sectionActive(s) || open === s.id;
          const I = s.Icon;
          return (
            <button key={s.id} type="button"
              data-testid={`rail-icon-${s.id}`}
              aria-label={s.label}
              title={s.label}
              onClick={() => setOpen((o) => (o === s.id ? null : s.id))}
              style={{
                width: 40, height: 40, borderRadius: 10, border: "none",
                display: "flex", alignItems: "center", justifyContent: "center",
                background: active ? "rgba(255,138,42,0.12)" : "transparent",
                color: active ? ACCENT : "rgba(148,163,184,0.85)",
                cursor: "pointer",
                transition: "background-color 140ms ease, color 140ms ease",
              }}
              onMouseEnter={(e) => { if (!active) e.currentTarget.style.color = "#e2e8f0"; }}
              onMouseLeave={(e) => { if (!active) e.currentTarget.style.color = "rgba(148,163,184,0.85)"; }}
            >
              <I size={18} strokeWidth={1.9} />
            </button>
          );
        })}
        <div style={{ flex: 1 }} />
        {/* Feb 2026 · Sidebar auto-hide manual toggle. Small chevron
            pinned at the bottom of the rail so the founder can hide
            the rail on demand (matches "Ask Advisor" collapse toggle
            pattern). Persists via localStorage. */}
        <button
          type="button"
          data-testid="rail-autohide-toggle"
          aria-label={autoHideEnabled ? "Disable rail auto-hide" : "Enable rail auto-hide"}
          title={autoHideEnabled ? "Auto-hide ON — click to disable" : "Auto-hide OFF — click to enable"}
          onClick={toggleAutoHide}
          style={{
            width: 32, height: 24, borderRadius: 6,
            border: `1px solid ${BORDER}`,
            background: autoHideEnabled ? "rgba(255,138,42,0.14)" : "transparent",
            color: autoHideEnabled ? ACCENT : "rgba(148,163,184,0.65)",
            cursor: "pointer",
            display: "inline-flex", alignItems: "center", justifyContent: "center",
            fontFamily: "'JetBrains Mono', monospace", fontSize: 9, fontWeight: 700,
            letterSpacing: "0.08em",
          }}
        >
          {autoHideEnabled ? "AUTO" : "OFF"}
        </button>
        {/* Feb 2026 · Founder request: "logout add kia he nhi sidebar
            main? production pe". RailShell (Dashboard v2's 56px rail)
            had no sign-out affordance — founder had to navigate to
            Settings then hunt for it. Legacy Shell.jsx has one at
            line 941/1007; we mirror that pattern here so both shells
            expose the same sign-out target. Uses the shared
            `logout` helper from `lib/api` which clears the token +
            user cache and redirects to /login. */}
        <button
          type="button"
          data-testid="rail-logout-btn"
          aria-label="Sign out"
          title="Sign out"
          onClick={() => {
            try { apiLogout(); } catch { /* ignore */ }
          }}
          style={{
            width: 36, height: 36, borderRadius: 8,
            border: "none", background: "transparent",
            color: "rgba(148,163,184,0.75)",
            cursor: "pointer",
            display: "inline-flex", alignItems: "center", justifyContent: "center",
            marginTop: 6,
            transition: "background 140ms ease, color 140ms ease",
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = "rgba(239,68,68,0.10)";
            e.currentTarget.style.color = "#ef4444";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = "transparent";
            e.currentTarget.style.color = "rgba(148,163,184,0.75)";
          }}
        >
          <LogOut size={16} strokeWidth={1.9} />
        </button>
      </nav>

      {/* Feb 2026 · Bottom-left vertical NAV tab — mirrors the
          bottom-right "ADVISOR" launcher (AskAdvisorReal) exactly:
          same 96×26 dimensions, same bottom-6 offset, same orange
          primary tint, same vertical writing-mode label. Only visible
          when the rail has slid off after chat typing. */}
      {hiddenForTyping && (
        <button
          type="button"
          data-testid="rail-peek-pill"
          onClick={() => setHiddenForTyping(false)}
          aria-label="Open navigation rail"
          title="Open navigation rail"
          style={{
            position: "fixed",
            bottom: 24,
            left: 0,
            zIndex: 1300,
            width: 26, height: 96,
            display: "flex", flexDirection: "column",
            alignItems: "center", justifyContent: "center", gap: 6,
            background: "#0A0A0A",
            border: `1px solid ${BORDER}`,
            borderLeft: "none",
            borderRadius: "0 8px 8px 0",
            padding: "12px 6px",
            boxShadow: "0 6px 20px rgba(0,0,0,0.45)",
            color: ACCENT,
            cursor: "pointer",
            transition: "background 160ms ease",
          }}
          onMouseEnter={(e) => { e.currentTarget.style.background = "#141414"; }}
          onMouseLeave={(e) => { e.currentTarget.style.background = "#0A0A0A"; }}
        >
          <Menu size={12} strokeWidth={2.5} />
          <span style={{
            writingMode: "vertical-rl",
            fontSize: 9, fontWeight: 700,
            letterSpacing: "0.12em",
            textTransform: "uppercase",
            color: "rgba(148,163,184,0.75)",
            fontFamily: "'JetBrains Mono', monospace",
          }}>
            Sidebar
          </span>
        </button>
      )}

      {open && (
        <div data-testid="rail-flyout" role="menu"
          style={{
            position: "absolute", left: 56, top: 0, bottom: 0, width: 272,
            background: BG, borderRight: `1px solid ${BORDER}`,
            boxShadow: "12px 0 34px rgba(0,0,0,0.55)",
            padding: "16px 8px 12px", display: "flex", flexDirection: "column",
            gap: 2, overflowY: "auto", height: "100vh",
            animation: "railFlyoutIn 150ms ease-out",
          }}>
          <div style={{
            fontFamily: "'Jost', sans-serif", fontSize: 13, fontWeight: 600,
            letterSpacing: "0.02em", color: "#e2e8f0", padding: "0 12px 10px",
            display: "flex", alignItems: "center", justifyContent: "space-between",
          }}>
            {SECTIONS.find((s) => s.id === open)?.label}
            <button type="button" data-testid="rail-flyout-close"
              onClick={() => setOpen(null)}
              style={{ border: "none", background: "transparent", color: "rgba(148,163,184,0.7)", cursor: "pointer" }}>
              <X size={13} />
            </button>
          </div>
          {flyoutBody(open)}
        </div>
      )}
      <style>{`@keyframes railFlyoutIn { from { opacity: 0; transform: translateX(-6px); } to { opacity: 1; transform: translateX(0); } }`}</style>
    </div>
  );

  if (railOnly) return rail;

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "var(--bg, rgb(7,8,13))" }}>
      {rail}
      <main style={{ flex: 1, minWidth: 0, overflowY: "auto", height: "100vh", padding: "28px 32px 64px" }}>
        {children}
      </main>
    </div>
  );
}
