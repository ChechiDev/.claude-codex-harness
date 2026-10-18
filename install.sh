#!/usr/bin/env bash
# install.sh — installs the harness onto a target project (Model B).
#
#   install.sh <project-path> [--force]
#
# SYMLINK (generic, updated via `git pull` in the toolkit):
#   <target>/.claude/agents        -> <toolkit>/agents
#   <target>/.claude/codex         -> <toolkit>/codex
#   <target>/.claude/settings.json -> <toolkit>/settings.json
#   <target>/harness               -> <toolkit>/lib
#
# COPY (customizable per project, NOT overwritten unless --force):
#   CLAUDE.md AGENTS.md Makefile CHECKPOINTS.md feature_list.json
#   docs/ progress/
set -euo pipefail

TOOLKIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DEST="${1:-}"
FORCE=0
[ "${2:-}" = "--force" ] && FORCE=1
if [ -z "$DEST" ]; then
  echo "usage: install.sh <project-path> [--force]" >&2; exit 2
fi
mkdir -p "$DEST"
DEST="$(cd "$DEST" && pwd)"

if [ "$TOOLKIT" = "$DEST" ]; then
  echo "error: the target cannot be the toolkit itself" >&2; exit 2
fi

GREEN='\033[0;32m'; YELLOW='\033[0;33m'; NC='\033[0m'
linked() { printf "${GREEN}[symlink]${NC} %s -> %s\n" "$1" "$2"; }
copied() { printf "${GREEN}[copy]${NC}    %s\n" "$1"; }
skipped(){ printf "${YELLOW}[skip]${NC}    %s (already exists; use --force to overwrite)\n" "$1"; }

link() {  # link <target-in-toolkit> <path-in-dest>
  local target="$TOOLKIT/$1" dest="$DEST/$2"
  mkdir -p "$(dirname "$dest")"
  ln -sfn "$target" "$dest"
  linked "$2" "$target"
}

copy() {  # copy <path-in-templates> <path-in-dest>
  local src="$TOOLKIT/templates/$1" dest="$DEST/$2"
  mkdir -p "$(dirname "$dest")"
  if [ -e "$dest" ] && [ "$FORCE" -ne 1 ]; then skipped "$2"; return; fi
  cp -R "$src" "$dest"
  copied "$2"
}

echo "Installing harness into: $DEST"
echo "From toolkit:            $TOOLKIT"
echo ""

# --- Symlinks (generic) ---
link agents        .claude/agents
link codex         .claude/codex
link settings.json .claude/settings.json
link lib           harness

echo ""
# --- Copies (customizable) ---
copy CLAUDE.md          CLAUDE.md
copy AGENTS.md          AGENTS.md
copy Makefile           Makefile
copy CHECKPOINTS.md     CHECKPOINTS.md
copy feature_list.json  feature_list.json
copy docs               docs
copy progress           progress

echo ""
echo "Done. Next steps:"
echo "  1. cd $DEST && fill in the ‹…› in CLAUDE.md, feature_list.json and docs/."
echo "  2. Make sure you have a pyproject.toml with ruff/mypy/pytest (uv)."
echo "  3. make check   # must finish green"
echo "  4. (optional) /codex:setup --enable-review-gate"
