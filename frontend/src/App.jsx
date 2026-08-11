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
import { PrivateRoute, AdminRoute } from "./components/PrivateRoute";

// Eager — these three are the first surfaces every visitor sees and
// they share layout (AuthShell). Keeping them in the initial bundle
// avoids a Suspense flash on the highest-traffic paths.
import Landing from "./pages/Landing";
// Iter 358 — bundle diet: dev/harness/admin-QA pages don't belong in
// the first-paint entry chunk. Lazy-load them so the entry stays under
// the 350KB code-splitting target (they render behind Suspense).
const Both              = lazy(() => import("./pages/Both"));
const LoopLiveFeedDemo  = lazy(() => import("./pages/LoopLiveFeedDemo"));
const ShippedRowHarness = lazy(() => import("./pages/ShippedRowHarness"));
const VisualFixtures    = lazy(() => import("./pages/VisualFixtures"));
const AdminQADashboard  = lazy(() => import("./pages/AdminQADashboard"));
const Login             = lazy(() => import("./pages/Login"));
const Signup            = lazy(() => import("./pages/Signup"));
const Verify            = lazy(() => import("./pages/Verify"));  // Track 3 (item #31) — email verification landing
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
const VsDevin           = lazy(() => import("./pages/VsDevin"));
const VsPage            = lazy(() => import("./pages/VsPage"));      // Iter 358
const CompareHub        = lazy(() => import("./pages/CompareHub"));  // Iter 358
const Pricing          = lazy(() => import("./pages/Pricing"));
const Demo             = lazy(() => import("./pages/Demo"));                  // Iter 212m-200
// Iter 212m-235 — Personal Track (Phase 6). Warm cream/terracotta
// aesthetic; distinct from Developer Track's IDE-dark shell.
const ChooseTrack      = lazy(() => import("./pages/personal/ChooseTrack"));
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
        const isAuthPage = /^\/(login|signup|verify|oauth-finish)/.test(
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

  return (
    <BrowserRouter>
      <AutoClearConsoleHost />
      <MetaPixelRouteTracker />
      <SessionExpiredListener />
      <Toaster />
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
          <Route path="/verify"          element={<Verify />} />
          <Route path="/dashboard"       element={<PrivateRoute><Dashboard /></PrivateRoute>} />
          {/* Iter 212m-235 — Personal Track routes */}
          <Route path="/choose-track"    element={<PrivateRoute><ChooseTrack /></PrivateRoute>} />
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
          <Route path="/admin/observability" element={<AdminRoute><SystemStatsPage /></AdminRoute>} />
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
          <Route path="/admin/personal-track" element={<AdminRoute><PersonalTrackAdmin /></AdminRoute>} />
          <Route path="/admin/ora-chat"       element={<AdminRoute><OraChat /></AdminRoute>} />
          {/* Iter 212m-171 — direct URLs for new admin sections */}
          <Route path="/admin/bin-tracker"    element={<AdminRoute><Admin initialTab="bin_tracker" /></AdminRoute>} />
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
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
        </RouteErrorBoundary>
      </Suspense>
      </FixJobProvider>
      <CookieConsentBanner />
    </BrowserRouter>
  );
}
