#!/usr/bin/env bash
# scripts/cleanup_aurem_fix_branches.sh
#
# Safely delete stale `aurem/fix-*` remote branches from the GitHub
# repo, applying the Step-2 "think twice" verification the founder
# spec'd. DRY-RUN by default — pass --for-real to actually delete.
#
# Prereqs (on the machine you run this from — NOT the Emergent pod):
#   - `gh` CLI installed and authenticated (`gh auth status` OK)
#   - Working directory is a local clone of the target repo, with
#     `origin` pointing at the GitHub remote you want cleaned
#   - `origin/main` up-to-date (`git fetch origin --prune` recently)
#
# Safety guarantees (verify in code below):
#   - Only touches branches matching `aurem/fix-*` on `origin`
#   - Never touches `main`, `master`, `develop`, or any branch not
#     matching the auto-generated Vanguard pattern
#   - For MERGED PRs   → verifies `merge-base --is-ancestor` before
#                        delete (main truly contains the branch tip)
#   - For CLOSED (unmerged/draft) PRs → verifies branch is NOT
#                        ancestor of main (expected for abandoned
#                        drafts) then deletes; diff remains visible
#                        on the closed PR page
#   - Any branch with an OPEN PR, ambiguous PR state, or no PR at
#     all → SKIP + report (do not touch)
#   - Prints main HEAD SHA at start and end for tamper-check
#
# Flags:
#   --for-real    Actually delete (default is dry-run)
#   --pattern P   Override branch glob (default `aurem/fix-*`)
#   --limit N     Cap the number of deletes per run (default 200)

set -euo pipefail

DRY_RUN=1
PATTERN="aurem/fix-*"
LIMIT=200

while [[ $# -gt 0 ]]; do
  case "$1" in
    --for-real) DRY_RUN=0 ;;
    --pattern)  PATTERN="$2"; shift ;;
    --limit)    LIMIT="$2"; shift ;;
    -h|--help)
      grep -E "^# " "$0" | sed 's/^# //'
      exit 0
      ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
  shift
done

if ! command -v gh >/dev/null 2>&1; then
  echo "❌ gh CLI not found. Install: https://cli.github.com/" >&2
  exit 2
fi
if ! gh auth status >/dev/null 2>&1; then
  echo "❌ gh not authenticated. Run: gh auth login" >&2
  exit 2
fi

# ── Sanity: repo + main HEAD ──────────────────────────────────────
git fetch origin --prune --quiet
REPO_SLUG=$(gh repo view --json nameWithOwner -q .nameWithOwner)
MAIN_HEAD_START=$(git rev-parse origin/main)
echo "Repo:       $REPO_SLUG"
echo "main HEAD:  $MAIN_HEAD_START"
echo "Pattern:    $PATTERN"
echo "Mode:       $([ $DRY_RUN -eq 1 ] && echo 'DRY-RUN' || echo 'FOR-REAL (deletes will happen)')"
echo

# ── Step 1: enumerate candidates ──────────────────────────────────
mapfile -t BRANCHES < <(
  git for-each-ref --format='%(refname:short)' "refs/remotes/origin/${PATTERN}" \
    | sed 's|^origin/||' \
    | grep -v '^HEAD$' \
    || true
)

TOTAL=${#BRANCHES[@]}
echo "Step 1 — found $TOTAL remote branches matching $PATTERN"
if [[ $TOTAL -eq 0 ]]; then
  echo "Nothing to do."
  exit 0
fi

# ── Step 2: classify each branch ──────────────────────────────────
SAFE_MERGED=()
SAFE_ABANDONED=()
SKIP_OPEN_PR=()
SKIP_NO_PR=()
SKIP_AMBIGUOUS=()
SKIP_MERGED_NOT_ANCESTOR=()

echo
echo "Step 2 — classifying (this hits gh API — takes ~1s per branch)…"

for br in "${BRANCHES[@]}"; do
  # Guard: never touch these names even if they match the pattern
  case "$br" in
    main|master|develop|release/*)
      SKIP_AMBIGUOUS+=("$br"); continue ;;
  esac

  # Fetch PR info for this branch (head:branch filter)
  pr_json=$(gh pr list \
    --repo "$REPO_SLUG" \
    --head "$br" \
    --state all \
    --json number,state,merged,mergeCommit,title \
    --limit 3 2>/dev/null || echo "[]")

  pr_count=$(echo "$pr_json" | jq 'length')

  if [[ "$pr_count" == "0" ]]; then
    SKIP_NO_PR+=("$br")
    continue
  fi

  # Any OPEN PR against this branch? → skip
  has_open=$(echo "$pr_json" | jq '[.[] | select(.state=="OPEN")] | length')
  if [[ "$has_open" -gt 0 ]]; then
    SKIP_OPEN_PR+=("$br")
    continue
  fi

  # Latest closed PR — use the most recent one
  merged=$(echo "$pr_json" | jq -r '.[0].merged')
  br_sha=$(git rev-parse "origin/$br" 2>/dev/null || echo "")

  if [[ -z "$br_sha" ]]; then
    SKIP_AMBIGUOUS+=("$br (no local ref)"); continue
  fi

  if [[ "$merged" == "true" ]]; then
    # Verify main truly contains this branch's history
    if git merge-base --is-ancestor "$br_sha" "$MAIN_HEAD_START" 2>/dev/null; then
      SAFE_MERGED+=("$br")
    else
      # PR says merged but SHA not in main — could be squash-merge
      # (branch tip differs from merge commit). Cross-check via
      # mergeCommit sha.
      merge_sha=$(echo "$pr_json" | jq -r '.[0].mergeCommit.oid // empty')
      if [[ -n "$merge_sha" ]] && git merge-base --is-ancestor "$merge_sha" "$MAIN_HEAD_START" 2>/dev/null; then
        SAFE_MERGED+=("$br")  # squash-merge — commit landed in main under a new SHA
      else
        SKIP_MERGED_NOT_ANCESTOR+=("$br")
      fi
    fi
  else
    # Closed-without-merge (abandoned draft) — expected pattern
    # for Vanguard Iter-348 auto-generated fixes. Verify branch
    # is NOT already in main (defensive):
    if git merge-base --is-ancestor "$br_sha" "$MAIN_HEAD_START" 2>/dev/null; then
      # Weird: closed unmerged but tip is in main → treat as merged
      SAFE_MERGED+=("$br")
    else
      SAFE_ABANDONED+=("$br")
    fi
  fi
done

# ── Report classification ─────────────────────────────────────────
echo
echo "──────────────────────────────────────────"
echo "  Classification Summary"
echo "──────────────────────────────────────────"
echo "  Safe · merged (in main):        ${#SAFE_MERGED[@]}"
echo "  Safe · abandoned (closed draft):${#SAFE_ABANDONED[@]}"
echo "  Skip · has OPEN PR:              ${#SKIP_OPEN_PR[@]}"
echo "  Skip · no PR found:              ${#SKIP_NO_PR[@]}"
echo "  Skip · merged but NOT ancestor:  ${#SKIP_MERGED_NOT_ANCESTOR[@]}"
echo "  Skip · ambiguous / protected:    ${#SKIP_AMBIGUOUS[@]}"
echo "──────────────────────────────────────────"

if [[ ${#SKIP_OPEN_PR[@]} -gt 0 ]]; then
  echo
  echo "⚠️  Branches with OPEN PRs (not deleted):"
  printf '  - %s\n' "${SKIP_OPEN_PR[@]}"
fi
if [[ ${#SKIP_MERGED_NOT_ANCESTOR[@]} -gt 0 ]]; then
  echo
  echo "⚠️  Branches marked merged but tip NOT in main (needs eyeball):"
  printf '  - %s\n' "${SKIP_MERGED_NOT_ANCESTOR[@]}"
fi

# ── Step 3: delete (dry-run vs for-real) ──────────────────────────
DELETE_LIST=("${SAFE_MERGED[@]}" "${SAFE_ABANDONED[@]}")
DELETE_COUNT=${#DELETE_LIST[@]}

if [[ $DELETE_COUNT -gt $LIMIT ]]; then
  echo
  echo "⚠️  Delete count ($DELETE_COUNT) exceeds --limit ($LIMIT). "
  echo "    Capping to first $LIMIT. Re-run to continue."
  DELETE_LIST=("${DELETE_LIST[@]:0:$LIMIT}")
fi

echo
echo "Step 3 — ${#DELETE_LIST[@]} branch(es) to delete"

DELETED_MERGED=0
DELETED_ABANDONED=0
FAILED=()

for br in "${DELETE_LIST[@]}"; do
  # Was this in SAFE_MERGED?
  is_merged=0
  for m in "${SAFE_MERGED[@]}"; do
    if [[ "$m" == "$br" ]]; then is_merged=1; break; fi
  done

  tag="[abandoned]"
  [[ $is_merged -eq 1 ]] && tag="[merged   ]"

  if [[ $DRY_RUN -eq 1 ]]; then
    echo "  $tag  would delete: $br"
  else
    if git push origin --delete "$br" 2>&1 | tail -1; then
      echo "  $tag  DELETED:      $br"
      [[ $is_merged -eq 1 ]] && DELETED_MERGED=$((DELETED_MERGED+1)) || DELETED_ABANDONED=$((DELETED_ABANDONED+1))
    else
      FAILED+=("$br")
      echo "  $tag  ❌ FAILED:    $br"
    fi
  fi
done

# ── Step 4: final report ──────────────────────────────────────────
MAIN_HEAD_END=$(git rev-parse origin/main 2>/dev/null || echo "?")
echo
echo "══════════════════════════════════════════"
echo "  Final Report"
echo "══════════════════════════════════════════"
echo "  Total matching branches:  $TOTAL"
if [[ $DRY_RUN -eq 1 ]]; then
  echo "  DRY-RUN — nothing was deleted."
  echo "  Re-run with --for-real once the classification looks right."
else
  echo "  Deleted · merged:         $DELETED_MERGED"
  echo "  Deleted · abandoned:      $DELETED_ABANDONED"
  echo "  Failed to delete:         ${#FAILED[@]}"
  [[ ${#FAILED[@]} -gt 0 ]] && printf '    - %s\n' "${FAILED[@]}"
fi
echo
echo "  main HEAD before:  $MAIN_HEAD_START"
echo "  main HEAD after:   $MAIN_HEAD_END"
if [[ "$MAIN_HEAD_START" == "$MAIN_HEAD_END" ]]; then
  echo "  ✓ main untouched"
else
  echo "  ⚠ main HEAD changed during run — investigate"
fi
