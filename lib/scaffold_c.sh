#!/usr/bin/env bash
# scaffold_c.sh — minimal C layer so a harness C project runs green.
# Single responsibility: the C scaffold. The harness layer is install.sh; this
# script knows nothing about it and can be run on its own.
#
#   scaffold_c.sh <project-dir> <name> [--force]
#
# Creates the layout: src/, include/<name>/ and tests/unit/ (no placeholder code).
# The build/test contract lives in the Makefile (installed by install.sh).
# Idempotent and non-destructive: existing files are kept unless --force.
set -euo pipefail

DEST="${1:-}"; NAME="${2:-}"; FORCE=0
[ "${3:-}" = "--force" ] && FORCE=1
if [ -z "$DEST" ] || [ -z "$NAME" ]; then
  echo "usage: scaffold_c.sh <project-dir> <name> [--force]" >&2; exit 2
fi
if ! printf '%s' "$NAME" | grep -qE '^[a-z_][a-z0-9_]*$'; then
  echo "error: name '$NAME' must be a valid C identifier (snake_case)" >&2; exit 2
fi
mkdir -p "$DEST"; DEST="$(cd "$DEST" && pwd)"

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_colors.sh"
# Output: one line for what was created, one for what was kept.
DONE=(); KEPT=()
did()  { DONE+=("$1"); }
skip() { KEPT+=("$1"); }

# git repo (parity with `uv init` for Python; the Codex close needs a repo).
# An enclosing work tree (monorepo subdir) is used as is: never a nested repo.
if TOP="$(git -C "$DEST" rev-parse --show-toplevel 2>/dev/null)"; then
  [ "$TOP" = "$(cd "$DEST" && pwd -P)" ] || PARENT_REPO="$TOP"
else
  git init -b main "$DEST" >/dev/null 2>&1 && did "git init"
fi

# Layout only: tests/ holds real project tests (the toolchain probe lives in
# the harness: harness/probes/smoke.c, run by `make env-check`).
for dir in src "include/$NAME" tests/unit; do
  [ -d "$DEST/$dir" ] || { mkdir -p "$DEST/$dir"; did "$dir/"; }
done

[ ${#DONE[@]} -gt 0 ] && row "$GREEN" "[c]" "${DONE[*]}"
[ -n "${PARENT_REPO:-}" ] && row "$ORANGE" "[git]" "kept (parent repo $PARENT_REPO)"
[ ${#KEPT[@]} -gt 0 ] && row "$ORANGE" "[SKIP]" "${KEPT[*]} (exist; --force to rewrite)"
exit 0
