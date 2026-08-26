/**
 * githubConnectErrors.js — 2026-08 hardening (F4).
 *
 * Cost/failure audit found GitHub PAT/App preflight failures are the
 * #3 most common loop_errors category (~9.3%) — and today the user
 * just sees the raw backend string, e.g.
 *   "GitHub App auth failed (app_installation_revoked): ..."
 * This maps the 3 confirmed common cases to a plain-language,
 * actionable message. Frontend-only (loop_engine.py is a protected
 * file for this task) — parses the error code already embedded in
 * parentheses in the existing `reason` text.
 */
const PATTERNS = [
  {
    test: /GitHub App auth failed \(app_installation_revoked\)/,
    title: "GitHub connection expired",
    message: "Your GitHub connection expired or was revoked. Reconnect to keep shipping.",
    actionLabel: "Reconnect GitHub",
  },
  {
    test: /GitHub App auth failed \(app_installation_missing\)/,
    title: "GitHub not connected",
    message: "This project isn't connected to GitHub yet. Connect it to start shipping code.",
    actionLabel: "Connect GitHub",
  },
  {
    test: /GitHub (?:App auth|auth preflight) failed \((?:github_rejected_401|github_rejected_403)\)/,
    title: "GitHub connection expired",
    message: "GitHub rejected the connection (access lost or revoked). Reconnect to keep shipping.",
    actionLabel: "Reconnect GitHub",
  },
  {
    test: /GitHub auth preflight failed \(repo_not_found_or_no_access\)/,
    title: "Repo not found or not accessible",
    message: "GitHub can't find this repo — it may have been renamed, deleted, or access removed. Check the repo name and the GitHub App's access, then retry.",
    actionLabel: "Check GitHub connection",
  },
];

export function translateGithubConnectError(reason) {
  if (typeof reason !== "string" || !reason) return null;
  for (const p of PATTERNS) {
    if (p.test.test(reason)) {
      return { title: p.title, message: p.message, actionLabel: p.actionLabel };
    }
  }
  return null;
}
