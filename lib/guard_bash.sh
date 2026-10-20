#!/usr/bin/env bash
# PreToolUse hook (matcher: Bash). Enforces two rules on Claude's Bash commands:
#   1. No agent attribution in commits/PRs (Co-Authored-By / "Generated with" / …).
#   2. Claude NEVER commits: the single per-subtask commit is made by Codex at
#      close, outside these hooks. Escape valve: HARNESS_ALLOW_COMMIT=1.
set -uo pipefail

INPUT="$(cat)"
CMD="$(printf '%s' "$INPUT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("command",""))' 2>/dev/null)"
[ -z "$CMD" ] && exit 0

# We only care about commits / merges / PRs.
case "$CMD" in
  *"git commit"*|*"git merge"*|*"gh pr create"*|*"gh pr edit"*) ;;
  *) exit 0 ;;
esac

# Rule 1: attribution is always forbidden, even under the escape valve.
if printf '%s' "$CMD" | grep -qiE 'co-authored-by|generated with|🤖|claude code|codex|anthropic'; then
  echo "BLOCKED: the commit/PR contains agent attribution (Co-Authored-By / 'Generated with' / mention of Claude/Codex). Present it as the human author's work, with no attribution." >&2
  exit 2
fi

# Rule 2: Claude must not commit. Only git commit is blocked (merges/PRs pass
# once they are attribution-clean). Codex closes outside these hooks.
case "$CMD" in
  *"git commit"*)
    if [ "${HARNESS_ALLOW_COMMIT:-0}" != "1" ]; then
      echo "BLOCKED: Claude does not commit in the harness. The single per-subtask commit is made by Codex at close (./harness/review_with_codex.sh doc <N.M.K>). If you really need a manual commit outside the flow, re-run with HARNESS_ALLOW_COMMIT=1 set in the environment." >&2
      exit 2
    fi
    ;;
esac

exit 0
