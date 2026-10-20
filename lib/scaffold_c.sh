#!/usr/bin/env bash
# scaffold_c.sh — minimal C layer so a harness C project runs green.
# Single responsibility: the C scaffold. The harness layer is install.sh; this
# script knows nothing about it and can be run on its own.
#
#   scaffold_c.sh <project-dir> <name> [--force]
#
# Creates: include/<name>/version.h, a src/ dir, and tests/unit/test_smoke.c.
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

GREEN='\033[0;32m'; YELLOW='\033[0;33m'; NC='\033[0m'
did()  { printf "${GREEN}[c]${NC}      %s\n" "$1"; }
skip() { printf "${YELLOW}[c]${NC}      %s\n" "$1"; }

UPPER="$(printf '%s' "$NAME" | tr '[:lower:]' '[:upper:]')"

# git repo (parity with `uv init` for Python; the Codex close needs a repo).
if [ ! -d "$DEST/.git" ]; then
  git init -b main "$DEST" >/dev/null 2>&1 && did "git init (branch main)"
fi

mkdir -p "$DEST/src" "$DEST/include/$NAME" "$DEST/tests/unit"
[ -f "$DEST/src/.gitkeep" ] || touch "$DEST/src/.gitkeep"

# ── include/<name>/version.h ─────────────────────────────────────────────────
VER="$DEST/include/$NAME/version.h"
if [ -f "$VER" ] && [ "$FORCE" -ne 1 ]; then
  skip "include/$NAME/version.h exists"
else
  cat > "$VER" <<EOF
#ifndef ${UPPER}_VERSION_H
#define ${UPPER}_VERSION_H

#define ${UPPER}_VERSION "0.1.0"

#endif /* ${UPPER}_VERSION_H */
EOF
  did "include/$NAME/version.h"
fi

# ── tests/unit/test_smoke.c ──────────────────────────────────────────────────
SMOKE="$DEST/tests/unit/test_smoke.c"
if [ -f "$SMOKE" ]; then
  skip "tests/unit/test_smoke.c exists"
else
  cat > "$SMOKE" <<EOF
#include <assert.h>
#include <string.h>

#include "$NAME/version.h"

int main(void)
{
    /* Smoke test: the version macro is defined and non-empty. */
    assert(strlen(${UPPER}_VERSION) > 0);
    return 0;
}
EOF
  did "tests/unit/test_smoke.c"
fi

echo "C layer ready. Next: make verify"
