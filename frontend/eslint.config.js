// Minimal ESLint v9 flat config (2026-08-28 · Loop N item 4).
//
// This repo's authoritative linter is `oxlint` (zero-config, already
// runs clean — see CHANGELOG). This file exists ONLY so that if
// something on the platform side invokes bare `eslint` against this
// directory, it finds a valid flat config instead of failing with
// "ESLint couldn't find an eslint.config.js file" (an engine-level
// crash, not a real lint result). Kept intentionally permissive —
// it is not meant to replace oxlint or CI's lint gate.
module.exports = [
  {
    ignores: [
      "node_modules/**",
      "dist/**",
      "build/**",
      "coverage/**",
      "playwright-report/**",
      "test-results/**",
    ],
  },
  {
    files: ["src/**/*.{js,jsx}"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
    },
    // NOTE: react/react-hooks plugins deliberately NOT registered
    // here. Installing eslint@9 + its plugins as LOCAL devDeps in
    // this repo conflicts with the existing `resolutions.brace-
    // expansion` pin in package.json (breaks @eslint/config-array's
    // bundled minimatch — "expand is not a function"). Rather than
    // touch that pin, this config stays plugin-free; a handful of
    // pre-existing `eslint-disable-next-line react-hooks/...`
    // comments will report "rule not found" if bare `eslint` is ever
    // run here — cosmetic, not a real defect. oxlint remains this
    // repo's actual zero-config linter (see CHANGELOG 2026-08-28).
    rules: {},
  },
];
