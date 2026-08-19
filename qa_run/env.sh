export PROD_URL="https://auremcto.com"
export PREV_URL="https://launch-pad-237.preview.emergentagent.com"
# 2026-08-19 SECURITY FIX — real founder credentials were hardcoded
# here and committed to git (found during security audit). Removed.
# Source these from an UNTRACKED local override file instead, e.g.
# `source qa_run/env.local.sh` (add that file to .gitignore) or export
# them in your shell before running any qa_run script.
export PROD_EMAIL="${PROD_EMAIL:-}"
export PROD_PASS="${PROD_PASS:-}"
export PREV_EMAIL="test@aurem.dev"
export PREV_PASS="AuremTest2026!"
