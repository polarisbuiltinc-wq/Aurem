#!/usr/bin/env node
/**
 * postdeploy-verify.mjs — Iter 389.1 chunk-aware post-deploy verifier
 *
 * Systemic guard against Bug L-01 (Vite lazy-chunk false-negative).
 * Documented in /app/memory/BUGS_LEDGER.md.
 *
 * What it does
 * ------------
 * 1. Fetches the deployed HTML for a target URL (default:
 *    https://auremcto.com/signup, override via first CLI arg).
 * 2. Extracts the main entry chunk from <script type="module" ...>.
 * 3. Walks the ENTIRE lazy-chunk dependency tree — fetches every
 *    `assets/*.js` referenced by the main chunk (this is where
 *    Vite hides string literals for shared modules like
 *    lib/analytics.js after code-splitting).
 * 4. Greps the concatenated chunk contents for a MANIFEST of
 *    expected sentinel strings.
 * 5. Fails loud with process.exit(1) if any sentinel is missing.
 *    Prints a detailed report of which chunk each hit was found in
 *    (for regression triage).
 *
 * Manifest
 * --------
 * The SENTINELS array below is the source of truth for "features
 * that MUST appear in production bundle". Any future analytics /
 * tracking / feature-flag change that adds a new string literal
 * or event name MUST add it here in the same PR. This makes
 * silent-drops of frontend changes IMPOSSIBLE.
 *
 * Usage
 * -----
 *   # verify prod
 *   node scripts/postdeploy-verify.mjs
 *
 *   # verify staging or a specific route
 *   node scripts/postdeploy-verify.mjs https://staging.example.com/signup
 *
 *   # exit codes:
 *   #   0 = all sentinels found (deploy verified)
 *   #   1 = one or more sentinels missing (deploy failed)
 *   #   2 = network / fetch failure (verifier could not run)
 */

const DEFAULT_TARGET = "https://auremcto.com/signup";
const MAX_CHUNK_FETCHES = 200;   // safety cap to avoid runaway walks
const FETCH_TIMEOUT_MS = 15_000;

// ----------------------------------------------------------------
// SENTINELS — strings that MUST appear somewhere in the served
// chunk dependency tree. Grouped by feature for readable failures.
//
// Rule: every future PR that adds a new client-side event/name must
// add its literal string here. Adding to this list is 30 seconds.
// Investigating a silent-drop bug in production is hours (Iter 389
// cost ~2 hours to root-cause the false negative).
// ----------------------------------------------------------------
const SENTINELS = [
  // Meta Pixel — Iter 388-ag (base + PageView)
  { feature: "meta-pixel-base",              needle: "PageView" },
  { feature: "meta-pixel-init",              needle: "1571887197933821" },

  // Meta Pixel conversions — Iter 389
  { feature: "meta-CompleteRegistration",    needle: "CompleteRegistration" },
  { feature: "meta-Lead",                    needle: "\"Lead\"" },
  { feature: "meta-Purchase",                needle: "\"Purchase\"" },

  // Google Ads — Iter 156
  { feature: "gads-account-id",              needle: "AW-18239920865" },
];

// ----------------------------------------------------------------

async function fetchWithTimeout(url) {
  const controller = new AbortController();
  const t = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const res = await fetch(url, {
      signal: controller.signal,
      headers: { "Cache-Control": "no-cache", Pragma: "no-cache" },
      redirect: "follow",
    });
    if (!res.ok) {
      throw new Error(`HTTP ${res.status} ${res.statusText}`);
    }
    return await res.text();
  } finally {
    clearTimeout(t);
  }
}

function extractMainChunkPath(html) {
  // Match: <script type="module" crossorigin src="/assets/index-XXXX.js">
  const match = html.match(
    /<script[^>]*type="module"[^>]*src="([^"]+)"/i,
  );
  if (!match) return null;
  return match[1];
}

function extractAssetRefs(chunkText) {
  // Match: assets/foo-XXXX.js (any name pattern Vite produces)
  const re = /assets\/[A-Za-z0-9_-]+-[A-Za-z0-9_-]+\.js/g;
  return Array.from(new Set(chunkText.match(re) || []));
}

function absolute(baseOrigin, path) {
  if (path.startsWith("http")) return path;
  if (path.startsWith("/")) return baseOrigin + path;
  return `${baseOrigin}/${path}`;
}

async function walkChunkTree(target) {
  const url = new URL(target);
  const origin = `${url.protocol}//${url.host}`;

  console.log(`[verify] target: ${target}`);
  console.log(`[verify] origin: ${origin}`);

  // 1. Fetch HTML.
  const html = await fetchWithTimeout(target);
  console.log(`[verify] HTML fetched: ${html.length} bytes`);

  // 2. Find main entry chunk.
  const mainPath = extractMainChunkPath(html);
  if (!mainPath) {
    throw new Error(
      "Could not locate main <script type=\"module\"> entry in HTML",
    );
  }
  console.log(`[verify] main entry chunk: ${mainPath}`);

  // 3. BFS-walk chunk references.
  const fetched = new Map(); // path -> text
  const queue = [mainPath];
  const seen = new Set([mainPath]);

  while (queue.length && fetched.size < MAX_CHUNK_FETCHES) {
    const path = queue.shift();
    const chunkUrl = absolute(origin, path);
    try {
      const text = await fetchWithTimeout(chunkUrl);
      fetched.set(path, text);
      const refs = extractAssetRefs(text);
      for (const ref of refs) {
        const norm = ref.startsWith("/") ? ref : `/${ref}`;
        if (!seen.has(norm)) {
          seen.add(norm);
          queue.push(norm);
        }
      }
    } catch (err) {
      console.error(`[verify] WARN could not fetch ${chunkUrl}: ${err.message}`);
    }
  }

  console.log(`[verify] fetched ${fetched.size} chunks total`);

  return fetched;
}

function reportSentinels(chunks) {
  const results = [];
  for (const sentinel of SENTINELS) {
    const hits = [];
    for (const [path, text] of chunks.entries()) {
      if (text.includes(sentinel.needle)) hits.push(path);
    }
    results.push({ ...sentinel, hits });
  }
  return results;
}

function printReport(target, results) {
  console.log("");
  console.log("===== POSTDEPLOY VERIFY REPORT =====");
  console.log(`target: ${target}`);
  console.log("");

  let allOk = true;
  for (const r of results) {
    const status = r.hits.length > 0 ? "OK " : "MISS";
    const marker = r.hits.length > 0 ? "\u2713" : "\u2717";
    if (r.hits.length === 0) allOk = false;
    console.log(
      `  [${status}] ${marker} ${r.feature.padEnd(30)} needle=${JSON.stringify(r.needle)}`,
    );
    if (r.hits.length > 0) {
      for (const hit of r.hits.slice(0, 3)) {
        console.log(`         found in: ${hit}`);
      }
      if (r.hits.length > 3) {
        console.log(`         (+${r.hits.length - 3} more chunks)`);
      }
    }
  }

  console.log("");
  console.log(
    allOk
      ? "===== VERIFY PASSED — all sentinels present ====="
      : "===== VERIFY FAILED — one or more sentinels missing =====",
  );
  return allOk;
}

async function main() {
  const target = process.argv[2] || DEFAULT_TARGET;
  let chunks;
  try {
    chunks = await walkChunkTree(target);
  } catch (err) {
    console.error(`[verify] FATAL: ${err.message}`);
    process.exit(2);
  }
  const results = reportSentinels(chunks);
  const ok = printReport(target, results);
  process.exit(ok ? 0 : 1);
}

main();
