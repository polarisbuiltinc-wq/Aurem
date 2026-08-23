/**
 * zIndex.js — single source of truth for cross-component stacking.
 * 2026-08-24 — the floating ORA mascot was dead in production because
 * its hardcoded 9990 sat UNDER the cookie banner's hardcoded 10000.
 * Shared constants make that class of silent regression impossible:
 * change one layer here and every consumer moves together.
 */
export const Z_COOKIE_BANNER = 10000;
export const Z_FLOATING_GUIDE = 10001; // must stay above the banner
// 2026-08-23 — the collapsed "ADVISOR" re-open tab (AskAdvisorReal.jsx)
// is `fixed`, vertically-centered, anchored to the right edge — the
// EXACT same screen region LiveTaskPopup.jsx's live ship/task progress
// panel occupies (fixed, right:16, top:50%, hardcoded zIndex:7500).
// A real user got their Advisor toggle completely covered/unclickable
// by the live-ship popup mid-task (right when a GitHub auth hiccup
// also hit), with no way to bring the sidebar back until the popup
// cleared. Must always render above ANY transient floating panel.
export const Z_ADVISOR_TOGGLE = 10002;
