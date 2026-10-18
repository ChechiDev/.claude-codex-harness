#!/usr/bin/env bash
# PreToolUse hook (matcher: Bash). Blocks commits with agent attribution.
# Non-negotiable rule: no Co-Authored-By, no "Generated with", no mentions of
# Claude/Codex in commits, messages, PRs, or descriptions.
set -uo pipefail

INPUT="$(cat)"
CMD="$(printf '%s' "$INPUT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("command",""))' 2>/dev/null)"
[ -z "$CMD" ] && exit 0

# We only care about commits.
case "$CMD" in
  *"git commit"*|*"git merge"*|*"gh pr create"*|*"gh pr edit"*) ;;
  *) exit 0 ;;
esac

if printf '%s' "$CMD" | grep -qiE 'co-authored-by|generated with|🤖|claude code|codex|anthropic'; then
  echo "BLOCKED: the commit/PR contains agent attribution (Co-Authored-By / 'Generated with' / mention of Claude/Codex). Present it as the human author's work, with no attribution." >&2
  exit 2
fi

exit 0
