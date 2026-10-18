#!/usr/bin/env bash
# PostToolUse hook (matcher: Edit|Write). Fast feedback after each write:
# lint + tests. NEVER blocks (always exit 0); only informs Claude.
set -uo pipefail
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
cd "$PROJECT_DIR" || exit 0

if command -v make >/dev/null 2>&1 && [ -f Makefile ]; then
  echo "[harness] fast lint + tests after the write:"
  make lint 2>&1 | tail -5 || true
  make test 2>&1 | tail -5 || true
fi
exit 0
