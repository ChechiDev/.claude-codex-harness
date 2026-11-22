#!/usr/bin/env bash
# PreToolUse hook (matcher: Bash). Enforces on Claude's Bash commands:
#   1. No agent attribution in commits/merges/PRs (Co-Authored-By / "Generated with" / …).
#   2. Claude NEVER commits: the single per-subtask commit is made by the close
#      (Codex's message, committed by review_with_codex.sh after make check). Detected after parsing the command (git global
#      options, wrappers, `bash -c`, eval, git aliases), not by substring.
#      Escape valve: HARNESS_ALLOW_COMMIT=1 in the environment Claude Code was
#      started with (an inline `HARNESS_ALLOW_COMMIT=1 git commit` does not count).
#      Other commit-producing ops (commit-tree, update-ref, revert, cherry-pick, pull,
#      merge, rebase) and publication (git push, gh pr create/merge) are allowed with
#      a WARN (I8, plan 0.5); make check reports commits the close did not make.
#   3. No shell writes to protected paths (src/, tests/acceptance/, Codex verdicts):
#      redirections, tee, sed -i, cp/mv/rm, find -delete, git checkout/clean, …
#      This is best-effort; `make check` (_guard.py integrity) is the backstop.
# The decision logic lives in _guard.py.
set -uo pipefail

# Fail closed (I9): Claude Code only blocks a PreToolUse call on exit 2, so every
# other failure (python3 missing, import/syntax error, signal) is mapped to exit 2.
# settings.json also sets onFailure: "block" for failures before this script runs.
HOOK="guard_bash.sh"
if ! HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; then
  echo "BLOCKED: harness hook $HOOK cannot resolve its directory. Report it; do not work around it." >&2
  exit 2
fi
python3 "$HARNESS_DIR/_guard.py" bash
rc=$?
if [ "$rc" -ne 0 ] && [ "$rc" -ne 2 ]; then
  echo "BLOCKED: harness hook $HOOK failed (exit $rc). Report it; do not work around it." >&2
  exit 2
fi
exit "$rc"
