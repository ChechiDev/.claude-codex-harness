#!/usr/bin/env bash
# Verifies gate consistency: every feature marked 'done' must have its Codex
# review APPROVED. Used by check.sh and the Stop hook.
# exit 0 = consistent; exit 1 = a 'done' without approval, or a helper failed.
set -uo pipefail

HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
. "$HARNESS_DIR/_colors.sh"
cd "$PROJECT_DIR" || { fail "cannot enter the project directory $PROJECT_DIR"; exit 1; }
# The done leaves, or FAIL: a helper that cannot answer must not empty the list (I10).
# No temp file: the helper's own error goes straight to stderr, above this FAIL.
if ! IDS="$(python3 "$HARNESS_DIR/_harness.py" done-ids)"; then
  fail "cannot read the done leaves (the helper's error is above)"
  exit 1
fi

rc=0
while read -r ID; do
  [ -z "$ID" ] && continue
  REVIEW="progress/review_${ID}.md"
  # Exact verdict line only: "Not APPROVED yet" or PLAN_APPROVED must not pass.
  if ! VERDICT="$(python3 "$HARNESS_DIR/_harness.py" verdict "$REVIEW")"; then
    fail "cannot read the verdict of ${ID} ($REVIEW)"
    rc=1
  elif [ "$VERDICT" != "APPROVED" ]; then
    fail "feature ${ID} is 'done' but has no Codex review APPROVED ($REVIEW)"
    rc=1
  fi
done <<< "$IDS"

[ $rc -eq 0 ] && ok "gates consistent (every 'done' has APPROVED)"
exit $rc
