#!/usr/bin/env bash
# Stop hook. Before closing the session, force a full verification:
#   - make check  (verify + invariants + gate consistency)
# If anything fails, block the close (exit 2) with the detail for Claude to see.
set -uo pipefail
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
cd "$PROJECT_DIR" || exit 0

LOG="$(mktemp)"
if make check >"$LOG" 2>&1; then
  echo "[harness] make check OK — session ready to close."
  rm -f "$LOG"
  exit 0
fi

echo "[harness] make check FAILED — do not close the session until resolved:" >&2
tail -30 "$LOG" >&2
rm -f "$LOG"
exit 2
