#!/usr/bin/env bash
# install.sh — installs the harness onto a target project (Model B).
#
#   install.sh <project-path> [--force]
#
# SYMLINK (generic, updated via `git pull` in the toolkit):
#   <target>/.claude/agents        -> <toolkit>/agents
#   <target>/.claude/codex         -> <toolkit>/codex
#   <target>/.claude/skills        -> <toolkit>/skills
#   <target>/.claude/settings.json -> <toolkit>/settings.json
#   <target>/harness               -> <toolkit>/lib
#
# COPY (customizable per project, NOT overwritten unless --force):
#   CLAUDE.md AGENTS.md Makefile CHECKPOINTS.md docs/ progress/
#
# GENERATED (never copied): feature_list.json is produced from docs/plan/.
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
genned() { printf "${GREEN}[gen]${NC}     %s\n" "$1"; }
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
link skills        .claude/skills
link settings.json .claude/settings.json
link lib           harness

echo ""
# --- Copies (customizable) ---
copy CLAUDE.md          CLAUDE.md
copy AGENTS.md          AGENTS.md
copy Makefile           Makefile
copy CHECKPOINTS.md     CHECKPOINTS.md
copy docs               docs
copy progress           progress

echo ""
# --- Generated (derived from docs/plan/) ---
( cd "$DEST" && python3 "$TOOLKIT/lib/sync_plan.py" >/dev/null ) && genned "feature_list.json (from docs/plan/)"

echo ""
# --- Machine-level config (idempotent; links ~/.claude into the toolkit) ---
bash "$TOOLKIT/link-global.sh"

echo ""
echo "Done. Next steps:"
echo "  1. cd $DEST && fill in docs/plan/ (rename phase-01-example.md, assign N.M.K ids)."
echo "  2. make plan        # regenerate feature_list.json after editing docs/plan/"
echo "  3. Make sure you have a pyproject.toml with ruff/mypy/pytest (uv)."
echo "  4. make check       # must finish green"
echo "  5. (optional) /codex:setup --enable-review-gate"
