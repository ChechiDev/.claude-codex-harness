#!/usr/bin/env bash
# link-global.sh — points this machine's ~/.claude config INTO the toolkit, so
# skills and the global CLAUDE.md are a single source of truth shared with the
# team via git:
#
#   ~/.claude/skills    ->  <toolkit>/skills      (symlink)
#   ~/.claude/CLAUDE.md ->  <toolkit>/global/CLAUDE.md
#
# The toolkit folders are the REAL, versioned content. Editing them (directly or
# through the ~/.claude symlinks) and running commit + push shares them with the
# team; other machines get them with git pull + ./link-global.sh.
#
# Non-destructive: an existing real target is backed up, and machine-local extras
# inside skills/ (.atl/, .claude/) are preserved into the link target.
set -euo pipefail

TOOLKIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="${CLAUDE_HOME:-$HOME/.claude}"

backup_if_real() {  # backup_if_real <path>
  local p="$1"
  [ -L "$p" ] && return 0            # already a symlink: nothing to back up
  [ -e "$p" ] || return 0            # does not exist: nothing to back up
  local b="$p.bak.$(date +%Y%m%d%H%M%S)"
  mv "$p" "$b"
  echo "[backup] $p -> $b"
}

link() {  # link <src-in-toolkit> <dest-in-~/.claude>
  local src="$TOOLKIT/$1" dest="$CLAUDE_DIR/$2"
  [ -e "$src" ] || { echo "error: $src not found" >&2; exit 1; }
  if [ -L "$dest" ]; then
    echo "[ok] $dest already -> $(readlink "$dest")"; return 0
  fi
  mkdir -p "$(dirname "$dest")"
  backup_if_real "$dest"
  ln -sfn "$src" "$dest"
  echo "[symlink] $dest -> $src"
}

# Preserve machine-local extras (caches / local settings) before relinking skills.
if [ -d "$CLAUDE_DIR/skills" ] && [ ! -L "$CLAUDE_DIR/skills" ]; then
  for extra in .atl .claude; do
    if [ -e "$CLAUDE_DIR/skills/$extra" ] && [ ! -e "$TOOLKIT/skills/$extra" ]; then
      cp -R "$CLAUDE_DIR/skills/$extra" "$TOOLKIT/skills/$extra"
    fi
  done
fi

link skills           skills
link global/CLAUDE.md  CLAUDE.md

echo "Done. ~/.claude/skills and ~/.claude/CLAUDE.md now live in the toolkit (shared via git)."
