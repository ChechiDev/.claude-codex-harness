#!/usr/bin/env bash
# PreToolUse hook (matcher: Bash). Enforces on Claude's Bash commands:
#   1. No agent attribution in commits/merges/PRs (Co-Authored-By / "Generated with" / …).
#   2. Claude NEVER commits: the single per-subtask commit is made by Codex at
#      close, outside these hooks. Detected after parsing the command (git global
#      options, wrappers, `bash -c`, eval, git aliases), not by substring.
#      Escape valve: HARNESS_ALLOW_COMMIT=1 in the environment Claude Code was
#      started with (an inline `HARNESS_ALLOW_COMMIT=1 git commit` does not count).
#   3. No shell writes to protected paths (src/, tests/acceptance/, Codex verdicts):
#      redirections, tee, sed -i, cp/mv/rm, find -delete, git checkout/clean, …
#      This is best-effort; `make check` (_guard.py integrity) is the backstop.
# The decision logic lives in _guard.py.
set -uo pipefail

HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$HARNESS_DIR/_guard.py" bash
