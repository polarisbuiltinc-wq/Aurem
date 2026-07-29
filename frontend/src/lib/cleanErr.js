// Iter 353 — shared error sanitizer for founder-facing admin pages.
// Prod origin hiccups (deploy rollouts, proxy 520s) can hand axios a raw
// Cloudflare/HTML body; rendering that verbatim leaks infra internals
// into the UI (founder audit: House Rules, Financials, API Keys pages).
export function cleanErr(e, fallback = "Request failed") {
  let msg = e?.response?.data?.detail
    ?? (typeof e?.response?.data === "string" ? e.response.data : null)
    ?? e?.message
    ?? fallback;
  if (typeof msg !== "string") {
    try { msg = JSON.stringify(msg); } catch { msg = String(msg); }
  }
  const looksLikeInfra =
    /<\s*(html|!doctype)|cloudflare|could not parse|bad gateway|origin web server/i.test(msg);
  if (looksLikeInfra || msg.length > 220) {
    const code = e?.response?.status;
    return `Server error${code ? ` (HTTP ${code})` : ""} — the backend returned an unreadable response (usually a transient deploy/restart). Retry in a few seconds; if it persists, check /admin/system-health.`;
  }
  return msg;
}
