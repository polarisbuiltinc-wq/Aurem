/**
 * GlobalHelpFAB.jsx — Floating "Need help?" button, mounted globally.
 *
 * Shows only when:
 *   · user is logged in (JWT in localStorage), AND
 *   · current route isn't the marketing/auth surface
 *     (/, /login, /signup, /verify, /support, /demo, /bug-hunt, /admin/*)
 *
 * Clicking opens the SupportPopup which posts to /support/tickets and
 * writes into the same cto_support collection the admin Support panel
 * reads.
 */
import React, { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { SupportButton } from "./SupportPopup";
// Paths where the FAB stays hidden — marketing, auth, admin (admin has
// its own inbox and doesn't need to file tickets to itself), and pages
// that already have their own support surface.
// 2026-08-20 — exported so `OraGuideMascot` (the new fixed-position
// help entry point that replaces this FAB in App.jsx) can reuse the
// exact same "where does help even make sense" rules without forking
// the list.
export const HIDE_PREFIXES = [
  "/login", "/signup", "/verify", "/support",
  "/demo", "/bug-hunt", "/why-ora", "/both",
  "/admin",
  "/dev/",
];
export const EXACT_HIDE = new Set(["/"]);

export function shouldHide(pathname) {
  if (EXACT_HIDE.has(pathname)) return true;
  return HIDE_PREFIXES.some((p) => pathname.startsWith(p));
}

export default function GlobalHelpFAB() {
  const { pathname } = useLocation();
  const [loggedIn, setLoggedIn] = useState(
    () => !!localStorage.getItem("aurem_token"),
  );
  // Bug fix (2026-08-19): the FAB used a fixed bottom:20/right:20,
  // which sat directly on top of the chat composer's send button
  // (data-testid="chat-send") whenever a composer was on screen —
  // confirmed overlapping on mobile viewports. Instead of guessing a
  // pixel offset per breakpoint, measure the actual composer element
  // (if present on this page) and always clear its top edge by 12px.
  const [bottomOffset, setBottomOffset] = useState(20);

  useEffect(() => {
    let raf;
    const measure = () => {
      const composer = document.querySelector('[data-testid="composer-card"]');
      if (composer) {
        const rect = composer.getBoundingClientRect();
        const clearance = Math.max(20, window.innerHeight - rect.top + 12);
        setBottomOffset(clearance);
      } else {
        setBottomOffset(20);
      }
    };
    measure();
    const interval = setInterval(measure, 400);
    window.addEventListener("resize", measure);
    return () => {
      clearInterval(interval);
      window.removeEventListener("resize", measure);
      if (raf) cancelAnimationFrame(raf);
    };
  }, [pathname]);

  // Cheap re-check on route change (token can be set by login flow
  // without a page reload). Also listen for storage events so a
  // logout in another tab hides the FAB.
  useEffect(() => {
    setLoggedIn(!!localStorage.getItem("aurem_token"));
    const onStorage = (e) => {
      if (e.key === "aurem_token") setLoggedIn(!!e.newValue);
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [pathname]);

  if (!loggedIn || shouldHide(pathname)) return null;

  // Derive source label from the route so admin sees where the
  // ticket was filed from.
  const source = pathname.startsWith("/dashboard") ? "in_app_dashboard"
              : pathname.startsWith("/ora")       ? "in_app_ora"
              : pathname.startsWith("/build")     ? "in_app_build"
              : "in_app";

  return (
    <div
      data-testid="global-help-fab"
      style={{
        position: "fixed",
        bottom: bottomOffset,
        right: 20,
        zIndex: 9990,
        transition: "bottom 120ms ease",
      }}>
      <SupportButton
        source={source}
        label="Need help?"
        style={{
          background: "#141414",
          border: "1px solid #2a2a2a",
          color: "#eab308",
          padding: "10px 18px",
          fontSize: 13,
          fontWeight: 600,
          boxShadow: "0 4px 20px rgba(0,0,0,0.4)",
        }}
      />
    </div>
  );
}
