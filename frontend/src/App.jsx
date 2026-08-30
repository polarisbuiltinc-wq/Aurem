/**
 * App.jsx — Iter 123g route-level code-splitting for first-paint speed.
 *
 * Before iter 123g: every page (Admin, AdminFinancials, BrainDump, …)
 * was eager-imported here, producing a single 607KB bundle. Landing
 * visitors paid for the admin panel they never see; admin users
 * paid for the landing animations they don't need.
 *
 * After iter 123g: only Landing + Login + Signup ship in the initial
 * bundle. Everything else is React.lazy()-loaded behind a Suspense
 * boundary, so the route's JS is fetched on-demand the moment the
 * user clicks through to it.
 *
 * Expected impact (Vite production build):
 *   • Initial JS:  607KB → ~180KB  (≈ 70% smaller)
 *   • LCP (mobile, slow-3G): 3.2s → 1.1s
 *   • Admin pages still hydrate in <300ms once clicked (cached after
 *     first hit by the browser).
 */
import { BrowserRouter, Routes, Route, Navigate, useLocation } from "react-router-dom";
import useAutoClearConsole from "./lib/useAutoClearConsole";
import RouteErrorBoundary from "./components/RouteErrorBoundary";
import { useEffect, useRef, lazy, Suspense } from "react";
import Toaster from "./components/Toast";
import FixProgressDrawer from "./components/FixProgressDrawer";
import { FixJobProvider } from "./components/FixJobContext";
import PersistentFixBar from "./components/PersistentFixBar";
import CookieConsentBanner from "./components/CookieConsentBanner";
import OraGuideMascot from "./components/OraGuideMascot";
import MaintenanceGate from "./components/MaintenanceGate";  // 2026-08
import { PrivateRoute, AdminRoute } from "./components/PrivateRoute";

// Eager — these three are the first surfaces every visitor sees and
// they share layout (AuthShell). Keeping them in the initial bundle
// avoids a Suspense flash on the highest-traffic paths.
import Landing from "./pages/Landing";
// Iter 358 — bundle diet: dev/harness/admin-QA pages don't belong in
// the first-paint entry chunk. Lazy-load them so the entry stays under
// the 350KB code-splitting target (they render behind Suspense).
const Both              = lazy(() => import("./pages/Both"));
const ResetPassword     = lazy(() => import("./pages/ResetPassword"));
const LoopLiveFeedDemo  = lazy(() => import("./pages/LoopLiveFeedDemo"));
const ShippedRowHarness = lazy(() => import("./pages/ShippedRowHarness"));
const VisualFixtures    = lazy(() => import("./pages/VisualFixtures"));
const AdminQADashboard  = lazy(() => import("./pages/AdminQADashboard"));
const AdminMaintenance  = lazy(() => import("./pages/AdminMaintenance"));  // 2026-08 — maintenance toggle + outage tracker
const Login             = lazy(() => import("./pages/Login"));
const Signup            = lazy(() => import("./pages/Signup"));
const Verify            = lazy(() => import("./pages/Verify"));  // Track 3 (item #31) — email verification landing
const Support           = lazy(() => import("./pages/Support"));  // 2026-02-12 — public token-verified support inbox
const SupportThread     = lazy(() => import("./pages/SupportThread"));  // 2026-02-13 · Iter 388u — public HMAC-verified reply-thread view
const WhyOra            = lazy(() => import("./pages/WhyOra"));
import { initTheme } from "./services/theme";

// Iter 212m-52 — apply theme as early as possible (BEFORE React
// renders) so the first paint already shows the right palette.
// Avoids the "dark flash before light mode resolves" foot-gun that
// every theme system hits if it waits for useEffect.
initTheme();

// Lazy — every other route is fetched on-demand. The browser cache
// memoises the chunks so the SECOND visit to /admin is instant.
const Dashboard         = lazy(() => import("./pages/Dashboard"));
const Deploy            = lazy(() => import("./pages/Deploy"));
const Domain            = lazy(() => import("./pages/Domain"));
const Settings          = lazy(() => import("./pages/Settings"));
const Tokens            = lazy(() => import("./pages/Tokens"));
const Analytics         = lazy(() => import("./pages/Analytics"));
const Projects          = lazy(() => import("./pages/Projects"));
const Integrations      = lazy(() => import("./pages/Integrations")); // Iter 212m-174
const Admin             = lazy(() => import("./pages/Admin"));
// Feb 2026 · AdminOverview no longer imported here — /admin/overview
// now renders inside the Admin shell via <Admin initialTab="overview" />
// so sidebar chrome is present. AdminOverview is still imported by
// Admin.jsx's renderPage() switch.
const AdminCockpit      = lazy(() => import("./pages/AdminCockpit"));
const AdminIntegrations = lazy(() => import("./pages/AdminIntegrations"));
const AdminFinancials   = lazy(() => import("./pages/AdminFinancials"));
const AdminVanguard     = lazy(() => import("./pages/AdminVanguard"));
const SystemStatsPage   = lazy(() => import("./pages/SystemStatsPage"));   // Iter 212m-153
const ToolsPage         = lazy(() => import("./pages/ToolsPage"));         // Iter 212m-158
const FeatureWindow     = lazy(() => import("./pages/FeatureWindow"));  // Iter 212m-64
const CodebaseHealth    = lazy(() => import("./pages/CodebaseHealth"));  // Iter 212m-72
const BugHunt           = lazy(() => import("./pages/BugHunt"));        // Iter 212m-75
const SidebarPreview    = lazy(() => import("./pages/SidebarPreview")); // Iter 212m-80
const DashboardPreviewV2 = lazy(() => import("./pages/DashboardPreviewV2")); // Iter 212m-81
const AdminApiKeys      = lazy(() => import("./pages/AdminApiKeys"));
const AdminSystemHealth = lazy(() => import("./pages/AdminSystemHealth"));   // Iter 212m-205
const AdminInspectLoop  = lazy(() => import("./pages/AdminInspectLoop"));    // Iter 309 · Batch-2 aftermath
const AdminInspectSpeedDiagnostic = lazy(() => import("./pages/AdminInspectSpeedDiagnostic")); // Iter 314
const AdminInspectScopeDriftAudit = lazy(() => import("./pages/AdminInspectScopeDriftAudit")); // Iter 314
const PersonalTrackAdmin = lazy(() => import("./pages/admin/PersonalTrackAdmin")); // Iter 212m-240
const OraChat            = lazy(() => import("./pages/admin/OraChat"));            // Iter 212m-238
const OraDirect          = lazy(() => import("./pages/OraDirect"));                 // Iter 212m-241 public PIN-gated
const PolicyPage        = lazy(() => import("./pages/PolicyPage"));
const Wrapped           = lazy(() => import("./pages/Wrapped"));
const ShipWall          = lazy(() => import("./pages/ShipWall"));
const BrainDump         = lazy(() => import("./pages/BrainDump"));
const OpsRecipes        = lazy(() => import("./pages/OpsRecipes"));
const Automations       = lazy(() => import("./pages/Automations"));
const OAuthFinish       = lazy(() => import("./pages/OAuthFinish"));
const MagicLogin        = lazy(() => import("./pages/MagicLogin"));   // 2026-08-20
const VsDevin           = lazy(() => import("./pages/VsDevin"));
const VsPage            = lazy(() => import("./pages/VsPage"));      // Iter 358
const CompareHub        = lazy(() => import("./pages/CompareHub"));  // Iter 358
const Pricing          = lazy(() => import("./pages/Pricing"));
const Demo             = lazy(() => import("./pages/Demo"));                  // Iter 212m-200
const NotFound         = lazy(() => import("./pages/NotFound"));              // Iter 388o · Bug 10
// Iter 212m-235 — Personal Track (Phase 6). Warm cream/terracotta
// aesthetic; distinct from Developer Track's IDE-dark shell.
// Iter 390 — ChooseTrack removed (developer-only rollout).
const BuildHome        = lazy(() => import("./pages/personal/BuildHome"));
const DraftReview      = lazy(() => import("./pages/personal/DraftReview"));
const ShipProgress     = lazy(() => import("./pages/personal/ShipProgress"));
const BuildSuccess     = lazy(() => import("./pages/personal/BuildSuccess"));

// Minimal loading state for the brief fetch window. Avoids the
// jarring "blank page" between click and hydration. Pure CSS so it
// renders before any React state.
function RouteLoader() {
  return (
    <div
      data-testid="route-loader"
      style={{
        position: "fixed",
        inset: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "var(--bg, #0a0e1a)",
        color: "var(--text-faint, #6c7280)",
        fontSize: 12,
        letterSpacing: "0.08em",
        textTransform: "uppercase",
        fontFamily: "'JetBrains Mono', monospace",
      }}
    >
      loading…
    </div>
  );
}

// Iter 212m-25 — Tiny child of <BrowserRouter> that owns the
// auto-clear-console hook. Has to live INSIDE the router because
// the hook reads `useLocation()` to fire console.clear() on every
// route change. Rendering nothing visible.
function AutoClearConsoleHost() {
  useAutoClearConsole();
  return null;
}

// Meta Pixel — SPA route-change tracking. The base code in index.html
// fires PageView once on first load; this child of <BrowserRouter>
// fires it again on EVERY client-side navigation so all pages are
// tracked (skips the initial mount to avoid double-counting).
function MetaPixelRouteTracker() {
  const location = useLocation();
  const isFirst = useRef(true);
  useEffect(() => {
    if (isFirst.current) { isFirst.current = false; return; }
    if (typeof window.fbq === "function") {
      window.fbq("track", "PageView");
    }
  }, [location.pathname, location.search]);
  return null;
}

// 2026-02-11 · Gap Register #38 — Global 401 handler.
// api.js response interceptor dispatches `aurem:session-expired` when a
// mid-session request returns 401 (JWT revoked or expired). This tiny
// child of <BrowserRouter> catches that event, shows a friendly toast,
// wipes local auth state, and SPA-navigates the user to /login with a
// preserved `?next=` so they return to where they were once signed in.
// The `firedRef` guard prevents the same session-expiry from stacking
// dozens of toasts when multiple parallel requests all 401 at once.
function SessionExpiredListener() {
  const location = useLocation();
  const firedRef = useRef(false);
  useEffect(() => {
    // Reset the guard whenever the pathname changes so a subsequent
    // login → session-expiry re-fires correctly.
    firedRef.current = false;
  }, [location.pathname]);
  useEffect(() => {
    const handler = (e) => {
      if (firedRef.current) return;
      firedRef.current = true;
      const msg = (e?.detail?.message)
        || "Your session expired. Please sign in again.";
      import("./lib/api").then(({ setToken, setUser }) => {
        try { setToken(null); } catch { /* noop */ }
        try { setUser(null);  } catch { /* noop */ }
        // Best-effort toast, then hard nav to /login preserving current
        // path so the user lands back after re-auth.
        try {
          import("sonner").then(({ toast }) => {
            try { toast.error(msg, { duration: 4000 }); } catch { /* noop */ }
          }).catch(() => {});
        } catch { /* noop */ }
        const path = window.location.pathname + window.location.search;
        const isAuthPage = /^\/(login|signup|verify|oauth-finish|magic-login)/.test(
          window.location.pathname,
        );
        const nextParam = (!isAuthPage && path && path !== "/")
          ? `?next=${encodeURIComponent(path)}` : "";
        window.location.href = `/login${nextParam}`;
      }).catch(() => {
        window.location.href = "/login";
      });
    };
    window.addEventListener("aurem:session-expired", handler);
    return () => window.removeEventListener("aurem:session-expired", handler);
  }, []);
  return null;
}

export default function App() {
  // Iter 101 — Capture `?ref=<uid>` on any landing, stash in localStorage
  // so Signup can attribute the referrer after account creation.
  // Also pings the public /referrals/track endpoint so the referrer
  // sees the click in their dashboard.
  useEffect(() => {
    try {
      const params = new URLSearchParams(window.location.search);
      const ref = params.get("ref");
      if (ref && ref.length > 0 && ref.length < 100) {
        localStorage.setItem("aurem_ref", ref);
        const backend = process.env.REACT_APP_BACKEND_URL;
        if (backend) {
          fetch(`${backend}/api/aurem-dev/referrals/track`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              ref_code: ref,
              path: window.location.pathname,
              user_agent: navigator.userAgent.slice(0, 200),
            }),
            signal: AbortSignal.timeout(10000),
          }).catch(() => {});
        }
      }
    } catch { /* no-op */ }
  }, []);

  // 2026-08-20 — Capture ad-click IDs / UTM params on first landing so
  // the admin funnel can finally show which real signups came from a
  // paid ad vs organic/referral (founder's audit: "ad-tracking not
  // joined to internal funnel"). First-touch only — never overwrites
  // an attribution already captured this browser. Read + sent once by
  // Signup.jsx / OAuthFinish.jsx right after account creation via
  // POST /ads/attribute-click, then cleared.
  useEffect(() => {
    try {
      if (localStorage.getItem("aurem_ad_attr")) return; // first-touch already captured
      const params = new URLSearchParams(window.location.search);
      const fields = ["gclid", "fbclid", "utm_source", "utm_medium", "utm_campaign"];
      const attr = {};
      for (const f of fields) {
        const v = params.get(f);
        if (v) attr[f] = v.slice(0, 200);
      }
      if (Object.keys(attr).length > 0) {
        attr.landing_path = window.location.pathname.slice(0, 120);
        localStorage.setItem("aurem_ad_attr", JSON.stringify(attr));
      }
    } catch { /* no-op */ }
  }, []);

  return (
    <BrowserRouter>
    <MaintenanceGate>
      {/* Iter 388t — Bug 25 fix.  Skip-to-content landmark link for
          keyboard-only users (WCAG 2.4.1).  Repositioned + hardened
          after prod feedback ("present but useless as-is"):
          - `position: fixed` so it sits above the sticky rail chrome
            and doesn't scroll away with the page.
          - `top: 8px, left: 8px` when focused so it appears in a
            predictable, obvious spot (was `top: 0, left: 0` which
            landed under browser chrome on some viewports).
          - `zIndex: 10000` to beat every drawer / modal in the app.
          - `onClick` programmatically focuses `#main-content` in
            addition to the fragment jump, so screen readers actually
            move the focus ring to the landmark (some browsers
            don't move focus on plain `href="#…"` links).
          Visually hidden by default; slides into view on `:focus-visible`
          so a founder who Tabs into the page can see + activate it as
          the first reachable element.

          SCOPE NOTE (Iter 388t · 2026-08-13 · founder decision):
          The Dashboard's ChatPanel composer carries `autoFocus`
          (see components/ChatPanel.jsx:4630) so keyboard focus
          lands on the textarea immediately on page load — natural
          Tab from there moves forward through the composer/toolbar,
          not backward to this skip link.  That is DELIBERATE:
            • Dashboard is a single-purpose focused-work surface;
              its rail nav is already keyboard-reachable with
              visible focus rings (Bug 24 fix).  The "skip repeated
              nav blocks" purpose of a skip link is already solved
              there by direct rail keyboard-navigation.
            • Landing / Login / Signup pages have NO autoFocus at
              first mount, so THIS link IS the first Tab stop and
              genuinely jumps to `#main-content` — verified on
              /login (screenshot proof kept in commit history).
          If a future page adds autoFocus at first mount AND wants
          the skip link to still lead the Tab order, either drop the
          autoFocus or add a keyboard shortcut (Alt+S) that focuses
          this link programmatically. */}
      <a
        href="#main-content"
        data-testid="skip-to-content-link"
        onClick={(e) => {
          // Programmatic focus so screen readers land on the landmark.
          // We still let the browser resolve the `#main-content`
          // fragment (default behaviour), we just add explicit focus.
          try {
            const target = document.getElementById("main-content");
            if (target) {
              // Small setTimeout to run after the browser's hash jump
              // so the scroll happens first and focus lands on the
              // already-in-view landmark.
              setTimeout(() => target.focus({ preventScroll: true }), 0);
            }
          } catch { /* noop */ }
        }}
        style={{
          position: "fixed",
          top: 8,
          left: 8,
          padding: "8px 14px",
          background: "var(--accent, #ff6b35)",
          color: "#fff",
          fontSize: 13,
          fontWeight: 700,
          borderRadius: 4,
          textDecoration: "none",
          zIndex: 10000,
          transform: "translateY(-140%)",
          transition: "transform 140ms ease",
        }}
        onFocus={(e) => { e.currentTarget.style.transform = "translateY(0)"; }}
        onBlur={(e) => { e.currentTarget.style.transform = "translateY(-140%)"; }}
      >
        Skip to content
      </a>
      <AutoClearConsoleHost />
      <MetaPixelRouteTracker />
      <SessionExpiredListener />
      <Toaster />
      <OraGuideMascot />
      {/* Iter 212m-148 — Global FixJob provider owns the SSE so the
          job survives panel toggles, route changes, and backdrop
          clicks.  PersistentFixBar is the always-visible 44px chrome;
          FixProgressDrawer slides up from it. */}
      <FixJobProvider>
        <FixProgressDrawer />
        <PersistentFixBar />
      <Suspense fallback={<RouteLoader />}>
        {/* Iter 356b — crashed lazy chunks / render errors show a retry
            card instead of a silent blank page. */}
        <RouteErrorBoundary>
        {/* Iter 388t — Bug 25 skip-link anchor + WCAG landmark
            (semantic `<main>` was missing from the shell entirely). */}
        <main id="main-content" tabIndex={-1}>
        <Routes>
          <Route path="/"                element={<Landing />} />
          <Route path="/both"            element={<Both />} />
          <Route path="/dev/loop-live-feed" element={<LoopLiveFeedDemo />} />
          <Route path="/dev/shipped-row-harness" element={<ShippedRowHarness />} />
          {/* Iter 302 — Frontend QA Charter Layer 2 Batch 2 —
              fixture-driven state isolation for Playwright visual
              regression. See docs/visual_regression.md. */}
          <Route path="/dev/visual"         element={<VisualFixtures />} />
          <Route path="/why-ora"         element={<WhyOra />} />
          <Route path="/demo"            element={<Demo />} />
          <Route path="/login"           element={<Login />} />
          <Route path="/signup"          element={<Signup />} />
          <Route path="/reset-password"  element={<ResetPassword />} />
          <Route path="/verify"          element={<Verify />} />
          <Route path="/support"         element={<Support />} />
          <Route path="/support/thread/:ticketId" element={<SupportThread />} />
          <Route path="/dashboard"       element={<PrivateRoute><Dashboard /></PrivateRoute>} />
          {/* Iter 212m-235 — Personal Track routes.
              Iter 390 — /choose-track removed (developer-only default). */}
          <Route path="/ora"             element={<PrivateRoute><OraDirect /></PrivateRoute>} />
          <Route path="/build"           element={<PrivateRoute><BuildHome /></PrivateRoute>} />
          <Route path="/build/:draftId"  element={<PrivateRoute><DraftReview /></PrivateRoute>} />
          <Route path="/build/:draftId/ship"    element={<PrivateRoute><ShipProgress /></PrivateRoute>} />
          <Route path="/build/:draftId/success" element={<PrivateRoute><BuildSuccess /></PrivateRoute>} />
          <Route path="/integrations"    element={<PrivateRoute><Integrations /></PrivateRoute>} />
          <Route path="/deploy"          element={<PrivateRoute><Deploy /></PrivateRoute>} />
          <Route path="/domain"          element={<PrivateRoute><Domain /></PrivateRoute>} />
          <Route path="/settings"        element={<PrivateRoute><Settings /></PrivateRoute>} />
          <Route path="/profile"         element={<PrivateRoute><Settings /></PrivateRoute>} />
          <Route path="/tokens"          element={<PrivateRoute><Tokens /></PrivateRoute>} />
          <Route path="/analytics"       element={<PrivateRoute><Analytics /></PrivateRoute>} />
          <Route path="/projects"        element={<PrivateRoute><Projects /></PrivateRoute>} />
          <Route path="/admin"           element={<AdminRoute><Admin initialTab="cockpit" /></AdminRoute>} />
          <Route path="/admin/cockpit"   element={<AdminRoute><Admin initialTab="cockpit" /></AdminRoute>} />
          <Route path="/admin/dashboard" element={<AdminRoute><Admin initialTab="dash" /></AdminRoute>} />
          <Route path="/admin/users"       element={<AdminRoute><Admin initialTab="users" /></AdminRoute>} />
          <Route path="/admin/suggestions" element={<AdminRoute><Admin initialTab="suggestions" /></AdminRoute>} />
          <Route path="/admin/overview"  element={<AdminRoute><Admin initialTab="overview" /></AdminRoute>} />
          {/* Feb 2026 · Sidebar Integrity fix — every in-shell sidebar
              item now has a real deep-linkable URL so browser Back /
              Forward and shareable links work.  The route: field in
              NAV points at the same paths (see Admin.jsx) so a
              sidebar click also updates window.location, closing the
              "internal-state-switch" back-navigation gap. */}
          <Route path="/admin/support"        element={<AdminRoute><Admin initialTab="support" /></AdminRoute>} />
          <Route path="/admin/audit"          element={<AdminRoute><Admin initialTab="audit" /></AdminRoute>} />
          <Route path="/admin/house-rules"    element={<AdminRoute><Admin initialTab="house_rules" /></AdminRoute>} />
          <Route path="/admin/robot-guide"    element={<AdminRoute><Admin initialTab="robot_guide" /></AdminRoute>} />
          <Route path="/admin/payments"       element={<AdminRoute><Admin initialTab="payments" /></AdminRoute>} />
          <Route path="/admin/token-pnl"      element={<AdminRoute><Admin initialTab="tokens" /></AdminRoute>} />
          <Route path="/admin/projects"       element={<AdminRoute><Admin initialTab="projects" /></AdminRoute>} />
          <Route path="/admin/tasks"          element={<AdminRoute><Admin initialTab="tasks" /></AdminRoute>} />
          <Route path="/admin/agent-performance" element={<AdminRoute><Admin initialTab="agent_perf" /></AdminRoute>} />
          <Route path="/admin/mcp-usage"      element={<AdminRoute><Admin initialTab="mcp" /></AdminRoute>} />
          <Route path="/admin/reliability"    element={<AdminRoute><Admin initialTab="reliability" /></AdminRoute>} />
          <Route path="/admin/settings"       element={<AdminRoute><Admin initialTab="settings" /></AdminRoute>} />
          <Route path="/admin/integrations" element={<AdminRoute><AdminIntegrations /></AdminRoute>} />
          <Route path="/admin/financials"   element={<AdminRoute><AdminFinancials /></AdminRoute>} />
          <Route path="/admin/vanguard"     element={<AdminRoute><AdminVanguard /></AdminRoute>} />
          <Route path="/admin/system-stats" element={<AdminRoute><SystemStatsPage /></AdminRoute>} />
          {/* 2026-08-27 · Admin Compact M3 — /admin/observability used to
              render an EXACT duplicate of /admin/system-stats (same
              component, same data). Redirected (not deleted) so any
              existing bookmark/deep-link to the old URL still lands on
              the real page instead of a dead route. */}
          <Route path="/admin/observability" element={<Navigate to="/admin/system-stats" replace />} />
          <Route path="/tools" element={<PrivateRoute><ToolsPage /></PrivateRoute>} />
          {/* Iter 212m-198 — Sidebar Bug Hunt bug: `/tools/bug-hunt` was
              rendering the marketing landing (BugHunt.jsx), so clicking
              Bug Hunt from the sidebar looked like it took the user to
              the homepage instead of the scanner. The public marketing
              URL `/bug-hunt` (below) still points at BugHunt.jsx so
              SEO + conversion funnel are preserved. Sidebar entry now
              lands on the CodebaseHealth scanner where the "Bug Hunt"
              category tile is one click away. */}
          <Route path="/tools/bug-hunt"    element={<PrivateRoute><CodebaseHealth /></PrivateRoute>} />
          <Route path="/tools/health-scan" element={<PrivateRoute><CodebaseHealth /></PrivateRoute>} />
          <Route path="/feature-window"     element={<PrivateRoute><FeatureWindow /></PrivateRoute>} />
          <Route path="/admin/system-map"   element={<AdminRoute><FeatureWindow /></AdminRoute>} />
          <Route path="/codebase-health"    element={<PrivateRoute><CodebaseHealth /></PrivateRoute>} />
          <Route path="/health"             element={<PrivateRoute><CodebaseHealth /></PrivateRoute>} />
          <Route path="/bug-hunt"           element={<BugHunt />} />
          <Route path="/sidebar-preview"    element={<PrivateRoute><SidebarPreview /></PrivateRoute>} />
          <Route path="/dashboard-preview-v2" element={<PrivateRoute><DashboardPreviewV2 /></PrivateRoute>} />
          <Route path="/admin/api-keys"     element={<AdminRoute><AdminApiKeys /></AdminRoute>} />
          <Route path="/admin/system-health" element={<AdminRoute><AdminSystemHealth /></AdminRoute>} />
          <Route path="/admin/inspect-loop/:loopId" element={<AdminRoute><AdminInspectLoop /></AdminRoute>} />
          {/* Iter 314 — universal admin-inspect wrappers so admin
              endpoints can be reached without hitting the JWT wall
              from direct URL navigation. */}
          <Route path="/admin/inspect-speed-diagnostic" element={<AdminRoute><AdminInspectSpeedDiagnostic /></AdminRoute>} />
          <Route path="/admin/inspect-scope-drift"      element={<AdminRoute><AdminInspectScopeDriftAudit /></AdminRoute>} />
          <Route path="/admin/qa"           element={<AdminRoute><AdminQADashboard /></AdminRoute>} />{/* Iter 303 */}
          <Route path="/admin/maintenance"  element={<AdminRoute><AdminMaintenance /></AdminRoute>} />{/* 2026-08 */}
          <Route path="/admin/personal-track" element={<AdminRoute><PersonalTrackAdmin /></AdminRoute>} />
          <Route path="/admin/ora-chat"       element={<AdminRoute><OraChat /></AdminRoute>} />
          {/* Iter 212m-171 — direct URLs for new admin sections */}
          <Route path="/admin/bin-tracker"    element={<AdminRoute><Admin initialTab="bin_tracker" /></AdminRoute>} />
          <Route path="/admin/github-bulk-revoke" element={<AdminRoute><Admin initialTab="github_bulk_revoke" /></AdminRoute>} />
          <Route path="/admin/visibility-kit" element={<AdminRoute><Admin initialTab="visibility_kit" /></AdminRoute>} />
          <Route path="/admin/feature-flags"  element={<AdminRoute><Admin initialTab="feature_flags" /></AdminRoute>} />
          <Route path="/admin/llm-credits"    element={<AdminRoute><Admin initialTab="llm_credits" /></AdminRoute>} />
          <Route path="/admin/parliament-live" element={<AdminRoute><Admin initialTab="parliament_live" /></AdminRoute>} />
          <Route path="/privacy"            element={<PolicyPage slug="privacy" />} />
          <Route path="/terms"              element={<PolicyPage slug="terms" />} />
          <Route path="/acceptable-use"     element={<PolicyPage slug="acceptable-use" />} />
          <Route path="/cookie-policy"      element={<PolicyPage slug="cookie-policy" />} />
          <Route path="/cookie-preferences" element={<PolicyPage slug="cookie-policy" />} />
          <Route path="/refund-policy"      element={<PolicyPage slug="refund-policy" />} />
          <Route path="/ai-code-processing" element={<PolicyPage slug="ai-code-processing" />} />
          <Route path="/subprocessors"      element={<PolicyPage slug="subprocessors" />} />
          <Route path="/dpa"                element={<PolicyPage slug="dpa" />} />
          <Route path="/security"           element={<PolicyPage slug="security" />} />
          <Route path="/status"             element={<PolicyPage slug="status" />} />
          <Route path="/admin/architecture" element={<AdminRoute><Admin initialTab="arch" /></AdminRoute>} />
          <Route path="/admin/ops" element={<AdminRoute><OpsRecipes /></AdminRoute>} />
          <Route path="/admin/brain/:projectId" element={<AdminRoute><BrainDump /></AdminRoute>} />
          <Route path="/wall" element={<ShipWall />} />
          <Route path="/wrapped" element={<PrivateRoute><Wrapped /></PrivateRoute>} />
          <Route path="/automations" element={<PrivateRoute><Automations /></PrivateRoute>} />
          <Route path="/oauth-finish" element={<OAuthFinish />} />
          <Route path="/magic-login" element={<MagicLogin />} />       {/* 2026-08-20 */}
          <Route path="/vs/devin"  element={<VsDevin />} />
          <Route path="/pricing" element={<Pricing />} />
          {/* Iter 358 — real comparison pages for every competitor
              (was: /vs/cursor redirect stub). Unknown slugs fall back
              to /compare inside VsPage. */}
          <Route path="/compare"  element={<CompareHub />} />
          <Route path="/vs/:slug" element={<VsPage />} />
          {/* Iter 212m-57 — Any /dashboard/<anything> subroute (e.g. the
              "/dashboard/new" URL some new-project deep-links generated)
              must redirect to /dashboard rather than getting swept up by
              the catch-all below — which sent users to "/" and read as
              "session was killed". The auth token lives in localStorage,
              not the URL, so this redirect preserves the session. */}
          <Route path="/dashboard/*" element={<Navigate to="/dashboard" replace />} />
          {/* Iter 388o — Bug 10 fix.  Previously the catch-all silently
              redirected every broken URL to `/`, so Google indexed the
              homepage under every stale link and founders couldn't
              tell links were broken.  Serve a real 404 SPA page (with
              noindex meta) instead. */}
          <Route path="*" element={<NotFound />} />
        </Routes>
        </main>
        </RouteErrorBoundary>
      </Suspense>
      </FixJobProvider>
      <CookieConsentBanner />
    </MaintenanceGate>
    </BrowserRouter>
  );
}
