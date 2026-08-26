/**
 * lib/chipFlag.js — Phase E · workcard_chip_v2 flag mirror (2026-08-27)
 *
 * Module-singleton mirror of `workcard_chip_v2_enabled` from /auth/me,
 * same cross-component pattern as the existing activeProject signal.
 * Default false (off) until /auth/me resolves and the user is on the
 * allowlist — matches every other WorkCard flag's fail-closed default.
 */
let _enabled = false;

export function setChipV2Enabled(value) {
  _enabled = value === true;
}

export function isChipV2Enabled() {
  return _enabled;
}
