/**
 * PrivateRoute.jsx — 2026-02-11 · Gap Register item #42
 *
 * Router-level auth guard. Redirects to /login when no JWT is present
 * BEFORE the underlying page component mounts. Runs alongside (not
 * instead of) each page's own <Shell requireAuth> gate — defense in
 * depth: this cuts the "flash of broken page then redirect" UX gap.
 *
 * `<AdminRoute>` additionally checks the admin/founder flag before
 * rendering. Non-admins get redirected to /dashboard rather than
 * /login (they're logged in — they just don't have the right tier).
 *
 * Both guards preserve the current URL in a `?next=` param so the
 * user lands back where they intended after re-auth.
 *
 * IMPORTANT: this is a UX guard only. Backend `/api/aurem-dev/*`
 * endpoints still enforce their own JWT + role checks via
 * `current_dev()` / `require_admin()`. Editing the JS bundle to
 * bypass this component does NOT grant server-side access.
 */
import React from "react";
import { Navigate, useLocation } from "react-router-dom";
import { getToken, isAdminOrFounder } from "../lib/api";

export function PrivateRoute({ children }) {
  const location = useLocation();
  const token = getToken();
  if (!token) {
    const path = location.pathname + location.search;
    const next = (path && path !== "/") ? `?next=${encodeURIComponent(path)}` : "";
    return <Navigate to={`/login${next}`} replace />;
  }
  return children;
}

export function AdminRoute({ children }) {
  const location = useLocation();
  const token = getToken();
  if (!token) {
    const path = location.pathname + location.search;
    const next = (path && path !== "/") ? `?next=${encodeURIComponent(path)}` : "";
    return <Navigate to={`/login${next}`} replace />;
  }
  if (!isAdminOrFounder()) {
    return <Navigate to="/dashboard" replace />;
  }
  return children;
}

export default PrivateRoute;
