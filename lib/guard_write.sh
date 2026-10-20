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

HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$HARNESS_DIR/_guard.py" write
