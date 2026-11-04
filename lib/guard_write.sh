#!/usr/bin/env bash
# PreToolUse hook (matcher: Edit|Write|MultiEdit|NotebookEdit). Rules:
#   1. tests/acceptance/ is read-only for Claude (Codex oracle).
#   2. Codex-owned files (gate verdicts, acceptance seals) are read-only for Claude.
#   3. No writing to src/ without PLAN_APPROVED for the in_progress subtask, and
#      only to the files declared in its `files:` (micro-task scope).
# Paths are resolved (symlinks, .., relative spellings) before matching.
# Blocks by returning exit 2 and a stderr message that Claude reads.
# The decision logic lives in _guard.py (shared with the Bash guard and check.sh).
set -uo pipefail

# Fail closed (I9): Claude Code only blocks a PreToolUse call on exit 2, so every
# other failure (python3 missing, import/syntax error, signal) is mapped to exit 2.
# settings.json also sets onFailure: "block" for failures before this script runs.
HOOK="guard_write.sh"
if ! HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; then
  echo "BLOCKED: harness hook $HOOK cannot resolve its directory. Report it; do not work around it." >&2
  exit 2
fi
python3 "$HARNESS_DIR/_guard.py" write
rc=$?
if [ "$rc" -ne 0 ] && [ "$rc" -ne 2 ]; then
  echo "BLOCKED: harness hook $HOOK failed (exit $rc). Report it; do not work around it." >&2
  exit 2
fi
exit "$rc"
