#!/usr/bin/env bash
# Stop hook. Before closing the session, force a full verification:
#   - make check  (verify + invariants + gate consistency + integrity backstop)
# If it fails, block the close (exit 2) with the detail for Claude to see.
#
# Loop protection: when the stop was already blocked once (stop_hook_active),
# a still-red check is reported but no longer blocks, so the session cannot spin.
# Cache: a green result is remembered per working-tree fingerprint (git state +
# untracked contents), so an unchanged tree does not re-run the whole suite.
set -uo pipefail
# Inherited exported functions could stand in for any command this script runs
# (issue #31): drop them all before anything else.
for _f in $(builtin compgen -A function); do builtin unset -f "$_f"; done; builtin unset _f
if [ -n "$(builtin compgen -A function)" ]; then
  echo "[FAIL] inherited shell functions remain: refusing to run" >&2; exit 1
fi
HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
cd "$PROJECT_DIR" || exit 0

INPUT="$(cat)"
ACTIVE="$(printf '%s' "$INPUT" | python3 -c '
import json, sys
try:
    print(str(json.load(sys.stdin).get("stop_hook_active", False)).lower())
except ValueError:
    print("false")' 2>/dev/null)"

FP="$(python3 "$HARNESS_DIR/_guard.py" fingerprint 2>/dev/null)"
CACHE="$(git rev-parse --git-path harness-check.ok 2>/dev/null)"
if [ -n "$FP" ] && [ -n "$CACHE" ] && [ -f "$CACHE" ] && [ "$(cat "$CACHE")" = "$FP" ]; then
  echo "[harness] make check already green for this exact tree — session ready to close."
  exit 0
fi

LOG="$(mktemp)"
# Never under the gate's counterfactual assumption (issue #27).
. "$HARNESS_DIR/_clean_env.sh"
if "${CLEAN_ENV[@]}" make check >"$LOG" 2>&1; then
  echo "[harness] make check OK — session ready to close."
  rm -f "$LOG"
  if [ -n "$FP" ] && [ -n "$CACHE" ]; then printf '%s' "$FP" >"$CACHE"; fi
  exit 0
fi

if [ "$ACTIVE" = "true" ]; then
  echo "[harness] make check is still red after a blocked stop; not blocking again (avoids a loop). Resolve it before the next session:" >&2
  tail -30 "$LOG" >&2
  rm -f "$LOG"
  exit 0
fi

echo "[harness] make check FAILED — do not close the session until resolved:" >&2
tail -30 "$LOG" >&2
rm -f "$LOG"
exit 2
