#!/usr/bin/env bash
# install_archify.sh — install the Archify skill into <project-dir>/.agents/skills/
# and nowhere else.
#
#   install_archify.sh <project-dir>
#
# `npx skills add` also creates .claude/skills/archify. In a harness project
# .claude/skills is a link to the shared toolkit, so running it in the project
# would write into every project. It runs in a throwaway directory instead and
# only .agents/skills/archify is copied over. Fails (non-zero) if the skill was
# not produced or a copy already exists (delete it first to upgrade).
set -euo pipefail

DEST="${1:-}"
[ -n "$DEST" ] && [ -d "$DEST" ] || { echo "usage: install_archify.sh <project-dir>" >&2; exit 2; }
DEST="$(cd "$DEST" && pwd)"
TARGET="$DEST/.agents/skills/archify"

command -v npx >/dev/null 2>&1 || { echo "error: npx not found (install node)" >&2; exit 1; }
if [ -e "$TARGET" ]; then
  echo "error: $TARGET already exists; remove it to reinstall" >&2; exit 1
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
( cd "$WORK" && npx skills add tt-a1i/archify ) || { echo "error: npx skills add failed" >&2; exit 1; }

SRC="$WORK/.agents/skills/archify"
[ -f "$SRC/SKILL.md" ] || { echo "error: archify skill not produced ($SRC/SKILL.md missing)" >&2; exit 1; }
mkdir -p "$DEST/.agents/skills"
cp -R "$SRC" "$TARGET"
[ -f "$TARGET/SKILL.md" ] || { echo "error: copy failed: $TARGET/SKILL.md" >&2; exit 1; }
echo "archify installed: .agents/skills/archify (not linked into .claude/skills)"
