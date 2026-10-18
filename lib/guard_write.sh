#!/usr/bin/env bash
# PreToolUse hook (matcher: Edit|Write). Enforces two non-bypassable rules:
#   1. tests/acceptance/ is read-only for Claude (Codex oracle).
#   2. No writing to src/ without PLAN_APPROVED for the in_progress feature.
# Blocks by returning exit 2 and a stderr message that Claude reads.
set -uo pipefail

HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
cd "$PROJECT_DIR" || exit 0

INPUT="$(cat)"
FILE="$(printf '%s' "$INPUT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("file_path",""))' 2>/dev/null)"
[ -z "$FILE" ] && exit 0

# Normalize to a project-relative path for matching.
REL="${FILE#"$PROJECT_DIR"/}"

case "$REL" in
  tests/acceptance/*|*/tests/acceptance/*)
    echo "BLOCKED: tests/acceptance/ is the independent oracle written by Codex and is READ-ONLY for Claude. If you think an acceptance test is wrong, stop and report it as a blocker in progress/current.md — do not edit it." >&2
    exit 2
    ;;
esac

case "$REL" in
  src/*|*/src/*)
    ID="$(python3 "$HARNESS_DIR/_harness.py" in-progress-id 2>/dev/null)"
    if [ -z "$ID" ]; then
      echo "BLOCKED: there is no in_progress feature in feature_list.json. The leader must pick and plan a feature before implementing." >&2
      exit 2
    fi
    REVIEW="progress/plan_review_${ID}.md"
    if [ ! -f "$REVIEW" ] || ! grep -q "PLAN_APPROVED" "$REVIEW" 2>/dev/null; then
      echo "BLOCKED: feature ${ID} has no plan approved by Codex ($REVIEW = PLAN_APPROVED). The leader must pass the plan gate before the implementer touches src/." >&2
      exit 2
    fi
    ;;
esac

exit 0
