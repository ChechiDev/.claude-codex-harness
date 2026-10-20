#!/usr/bin/env bash
# install.sh — installs the harness onto a target project (Model B).
#
#   install.sh <project-path> [--force] [--python <package-name>]
#
# --python delegates to lib/scaffold_python.sh (minimal Python layer) so the
# project runs `make check` green without manual pyproject editing. install.sh
# itself stays harness-only; the Python logic lives in its own script.
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

DEST="${1:-}"; shift || true
FORCE=0
TPL_LANG="python"   # template set (python | c). Not LANG: that is the exported locale.
PYPKG=""            # --python <pkg>: scaffold a Python layer (implies lang=python)
CNAME=""            # --c <name>:     scaffold a C layer       (implies lang=c)
while [ $# -gt 0 ]; do
  case "$1" in
    --force)  FORCE=1 ;;
    --lang)   shift; TPL_LANG="${1:-}" ;;
    --python) shift; PYPKG="${1:-}"; TPL_LANG="python" ;;
    --c)      shift; CNAME="${1:-}"; TPL_LANG="c" ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift || true
done
if [ -z "$DEST" ]; then
  echo "usage: install.sh <project-path> [--force] [--lang python|c] [--python <pkg> | --c <name>]" >&2; exit 2
fi
case "$TPL_LANG" in python|c) ;; *) echo "error: --lang must be python or c" >&2; exit 2 ;; esac
for v in "$PYPKG" "$CNAME"; do
  case "$v" in --*) echo "error: --python/--c needs a name argument" >&2; exit 2 ;; esac
done
# Template set: Python uses templates/, C uses templates-c/.
TPLSUB="templates"; [ "$TPL_LANG" = "c" ] && TPLSUB="templates-c"
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

copy() {  # copy <path-in-template-set> <path-in-dest>
  local src="$TOOLKIT/$TPLSUB/$1" dest="$DEST/$2"
  mkdir -p "$(dirname "$dest")"
  if [ -e "$dest" ] && [ "$FORCE" -ne 1 ]; then skipped "$2"; return; fi
  cp -R "$src" "$dest"
  copied "$2"
}

echo "Installing harness into: $DEST"
echo "From toolkit:            $TOOLKIT"
echo "Language / templates:    $TPL_LANG ($TPLSUB/)"
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

# --- Language layer (delegated; keeps install.sh harness-only) ---
SCAFFOLD_ARGS=(); [ "$FORCE" -eq 1 ] && SCAFFOLD_ARGS=(--force)
if [ -n "$PYPKG" ]; then
  echo ""
  bash "$TOOLKIT/lib/scaffold_python.sh" "$DEST" "$PYPKG" "${SCAFFOLD_ARGS[@]}"
fi
if [ -n "$CNAME" ]; then
  echo ""
  bash "$TOOLKIT/lib/scaffold_c.sh" "$DEST" "$CNAME" "${SCAFFOLD_ARGS[@]}"
fi

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
