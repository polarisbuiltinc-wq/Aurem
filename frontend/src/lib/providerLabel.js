/**
 * lib/providerLabel.js — Chat UX leak-audit fix (2026-08-19).
 *
 * Every raw provider/model identifier the backend attaches to a chat
 * turn ("glm-5.2", "z-ai/glm-5.2", "deepseek-v3-rescue",
 * "claude-sonnet-4.5", "longcat", "groq-llama-3.3-70b-rescue", …) is
 * an internal routing detail, never meant for a regular user to see.
 * Any surface that shows "which model answered" to a non-admin user
 * must run the value through this helper first, so a future model
 * swap on the backend can never leak a new raw slug into the UI.
 *
 * @param {string|null|undefined} raw
 * @returns {string} "ORA" when a provider is set, "" otherwise
 */
export function brandProvider(raw) {
  return raw ? "ORA" : "";
}
