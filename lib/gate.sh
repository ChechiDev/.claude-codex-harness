#!/usr/bin/env bash
# Verifies gate consistency: every feature marked 'done' must have its Codex
# review APPROVED. Used by check.sh and the Stop hook.
# exit 0 = consistent; exit 1 = there is a 'done' without approval.
set -uo pipefail

HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
. "$HARNESS_DIR/_colors.sh"
cd "$PROJECT_DIR" || exit 0

rc=0
while read -r ID; do
  [ -z "$ID" ] && continue
  REVIEW="progress/review_${ID}.md"
  # Exact verdict line only: "Not APPROVED yet" or PLAN_APPROVED must not pass.
  if [ "$(python3 "$HARNESS_DIR/_harness.py" verdict "$REVIEW")" != "APPROVED" ]; then
    fail "feature ${ID} is 'done' but has no Codex review APPROVED ($REVIEW)"
    rc=1
  fi
done < <(python3 "$HARNESS_DIR/_harness.py" done-ids 2>/dev/null)

[ $rc -eq 0 ] && ok "gates consistent (every 'done' has APPROVED)"
exit $rc
