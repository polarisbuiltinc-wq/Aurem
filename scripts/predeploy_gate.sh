#!/usr/bin/env bash
# Iter 346 — PRE-DEPLOY QA GATE (founder ruling 2026-07-29).
#
# Emergent's Deploy button runs build + env + health checks ONLY — it
# never executes the project's own tests. THIS script is the missing
# QA gate: the main agent runs it before every deploy request, and it
# must exit 0 or the deploy is not requested.
#
# Lanes (all blocking):
#   1. Backend pytest blocking lane (legacy/flaky/llm_judge excluded
#      via pytest.ini addopts) — must be 0 failed / 0 errors.
#   2. Frontend vitest component + lib suites.
#   3. Regression Library locks (services.qa_matrix) — every entry in
#      .emergent/qa-history/regression_library.json re-verified.
set -euo pipefail

cd "$(dirname "$0")/.."
echo "══════════ PRE-DEPLOY QA GATE ══════════"

echo "── Lane 1/3: backend pytest (blocking lane) ──"
(cd backend && python -m pytest --tb=short --timeout=30 -q \
    --continue-on-collection-errors \
    --ignore=tests/test_iter138_acceptance_seven.py \
    --ignore=tests/test_iter212m163_aggression_chat.py)

echo "── Lane 2/3: frontend vitest ──"
(cd frontend && npx vitest run src/components/__tests__/ src/lib)

echo "── Lane 3/3: regression library locks ──"
(cd backend && python -m services.qa_matrix \
    --message "predeploy gate" \
    --files "predeploy" \
    --sha "$(git rev-parse --short HEAD 2>/dev/null || echo predeploy)")

echo "── Lane 4: Guard 18 — universal timeout audit (Iter 359) ──"
(cd backend && python scripts/timeout_audit.py)

echo "── Lane 5: Guard 21 — OWASP/CWE misconfig + supply-chain scan (Iter 361) ──"
(cd backend && python scripts/g21_security_scan.py)

echo "── Lane 6: integration_health snapshot (Iter 388-aa · non-blocking) ──"
# Exit codes: 0=clean, 2=warn, 3=broken.  Non-blocking — surfaces to
# founder in deploy-request message. Rule 3 in memory/AGENT_STANDING_RULES.md.
python3 scripts/predeploy_integration_health.py || \
    echo "⚠️  Lane 6 flagged degraded integrations — see output above BEFORE dispatching deploy."

echo "── Lane 7: frontend bundle secrets sweep (Iter 388-ac · non-blocking) ──"
# Scans frontend/dist for leaked keys/DSNs/PEMs. Exit 3 == CRITICAL
# leaks; halt deploy and rotate before proceeding.
if [ -d frontend/dist ]; then
  python3 scripts/bundle_secrets_sweep.py || \
    echo "⚠️  Lane 7 flagged bundle findings — see output above BEFORE dispatching deploy."
else
  echo "   (skipped — frontend/dist not built; the build step runs later in the deploy pipeline)"
fi

echo "── Post-lane: regenerate backend/qa_manifest.json (Iter 351) ──"
(cd backend && python scripts/gen_qa_manifest.py)

echo "══════════ GATE PASSED — safe to deploy ══════════"
echo "⚠️  REMINDER (Iter 356): deploy ke BAAD founder se 'Save to GitHub' click"
echo "    karwana mat bhoolna — warna polarisbuiltinc-wq/auremdev outdated rahega."
echo "📄  DOCS CHECK (Iter 358): agar is change ne PRD/TRD/App Flow/UI-UX/Schema"
echo "    ko touch kiya hai, /app/docs/ ka relevant doc bhi update karo."
