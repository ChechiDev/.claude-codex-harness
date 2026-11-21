#!/usr/bin/env bash
# detect_harness.sh — tell py-init which steps it may run in <project-dir>.
#
#   detect_harness.sh <project-dir>
#
# Prints two lines:
#   harness=installed|absent   the claude-codex harness is installed here
#                              (role files, Makefile, commits belong to it)
#   history=yes|no             the repo already has commits (py-init never commits)
set -euo pipefail

DEST="${1:-}"
[ -n "$DEST" ] || { echo "usage: detect_harness.sh <project-dir>" >&2; exit 2; }
# Not created yet: a brand-new project (the full guided flow).
if [ ! -e "$DEST" ]; then printf 'harness=absent\nhistory=no\n'; exit 0; fi
[ -d "$DEST" ] || { echo "error: $DEST is not a directory" >&2; exit 2; }

harness=absent
if [ -e "$DEST/.harness/templates.json" ] || [ -L "$DEST/harness" ] || [ -L "$DEST/.claude/skills" ]; then
  harness=installed
fi

history=no
if git -C "$DEST" rev-parse -q --verify HEAD >/dev/null 2>&1; then
  history=yes
fi

printf 'harness=%s\nhistory=%s\n' "$harness" "$history"
