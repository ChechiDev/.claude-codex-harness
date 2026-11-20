#!/usr/bin/env bash
# Stop hook — ADVISORY ONLY (issue #55). Claude Code fires it at the end of EVERY
# assistant turn, not only when the session closes, so it must stay cheap:
#   - it computes the working-tree fingerprint (_guard.py fingerprint, ~1 s) and
#     compares it with the last green full-check record (.git/harness-check.ok);
#   - it prints whether that record matches the exact current state, and
#     "unknown" when the fingerprint cannot be computed (never green);
#   - it NEVER runs `make check`/check.sh and NEVER blocks (always exit 0).
# The real full `make check` runs at the gates, at close and before a PR; those
# are the only writers of the green record and the PR path requires it to match.
set -uo pipefail
# Inherited exported functions could stand in for any command this script runs
# (issue #31): drop them all before anything else.
for _f in $(builtin compgen -A function); do builtin unset -f "$_f"; done; builtin unset _f
if [ -n "$(builtin compgen -A function)" ]; then
  echo "[harness] inherited shell functions remain: green full check unknown" >&2; exit 0
fi
HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
cd "$PROJECT_DIR" || exit 0
cat >/dev/null  # the payload is not needed: the hook decides nothing from it

WHERE="the full make check runs at the gates, at close and before a PR"
FP="$(python3 "$HARNESS_DIR/_guard.py" fingerprint 2>/dev/null)"
if [ -z "$FP" ]; then
  echo "[harness] green full check: unknown (no fingerprint for this tree) — $WHERE."
  exit 0
fi
RECORD="$(git rev-parse --git-path harness-check.ok 2>/dev/null)"
if [ -n "$RECORD" ] && [ -f "$RECORD" ] && [ "$(cat "$RECORD")" = "$FP" ]; then
  echo "[harness] last green full check matches this exact state."
  exit 0
fi
echo "[harness] no green full check for this state — $WHERE."
exit 0
