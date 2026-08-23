/**
 * vitest.config.js — Iter 289 (Track 1 Lane A), widened Iter arch-2b
 * (2026-08-22)
 *
 * Frontend coverage instrumentation. Originally scoped to 3 curated
 * files (see git history) so the traceability matrix's numbers
 * stayed provably honest while the suite was small. That scope is
 * now the single biggest blocker to safe refactoring: ~80 existing
 * test files exercise components across the whole app, but coverage
 * only ever measured 3 of them — meaning every other tested file
 * (including the largest, riskiest ones like ChatPanel.jsx) reported
 * as having ZERO safety net when in fact some of them DO have tests.
 * Widened to the whole `src/` tree so the number reflects reality.
 */
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/__tests__/setup.js"],
    include: ["src/**/*.test.{js,jsx,ts,tsx}"],
    coverage: {
      provider: "v8",
      reporter: ["text", "json", "json-summary"],
      reportsDirectory: "coverage",
      // Iter arch-2b — avoid brace-expansion `{js,jsx,ts,tsx}` syntax
      // here specifically: the installed nested glob@10.5.0's own
      // minimatch@9.0.9 (pulled in by @vitest/coverage-v8's
      // test-exclude dependency) needs brace-expansion@^2.0.2, but
      // this repo's blanket `resolutions.brace-expansion: ^5.0.8`
      // (package.json, pinned for an unrelated reason) forces v5
      // everywhere, and v9's minimatch calls the v5 API in a way
      // that throws `braceExpand is not a function`. A scoped yarn
      // `resolutions` path override was attempted and did not take
      // (yarn classic kept collapsing it back to the blanket v5 pin)
      // — tracked as a real infra fix, not silently patched here.
      // Plain per-extension globs avoid triggering brace parsing at
      // all for OUR patterns, but v8's `all: true` mode still walks
      // every include-matched file via that same broken glob/
      // minimatch chain to report untested files at 0% — so `all`
      // is set to false below and the true "of every src file, how
      // many have zero coverage" number is computed separately (see
      // scripts/coverage_baseline.py) using Node's own `fs` glob
      // instead of the broken vitest-internal one.
      include: [
        "src/**/*.js", "src/**/*.jsx",
        "src/**/*.ts", "src/**/*.tsx",
      ],
      exclude: [
        "src/**/*.test.js", "src/**/*.test.jsx",
        "src/**/*.test.ts", "src/**/*.test.tsx",
        "src/**/__tests__/**",
        "src/__tests__/**",
      ],
      all: false,
      // Never lie about coverage — leave the threshold soft here so
      // the numbers are real; the QA MCP tool inspects the raw
      // percent and reports gaps, it does NOT gate on this file.
      thresholds: undefined,
    },
  },
});
