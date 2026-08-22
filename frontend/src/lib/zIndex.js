/**
 * zIndex.js — single source of truth for cross-component stacking.
 * 2026-08-24 — the floating ORA mascot was dead in production because
 * its hardcoded 9990 sat UNDER the cookie banner's hardcoded 10000.
 * Shared constants make that class of silent regression impossible:
 * change one layer here and every consumer moves together.
 */
export const Z_COOKIE_BANNER = 10000;
export const Z_FLOATING_GUIDE = 10001; // must stay above the banner
