#!/usr/bin/env bash
# install.sh — installs the harness onto a target project (Model B).
#
#   install.sh <project-path> [--force] [--python <package-name>] [--src-dirs a,b]
#   install.sh <project-path> --migrate [--apply] [--yes]   (existing project, see below)
#   install.sh <project-path> --rollback <migration> [--apply] [--yes]
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
#
# SHARED through the harness/ symlink: the harness Make targets (lib/harness.mk,
# included by the copied Makefile) and the workflow rules (lib/WORKFLOW.md,
# imported by the copied CLAUDE.md), so a change there reaches every project.
# --migrate moves a project installed before that to this shape: it drops the
# harness targets copied into its Makefile (only when unchanged), adds the include
# and the import, and verifies the result (lib/_upgrade.py). It is a dry-run (diff
# only) unless --apply; it never creates, deletes or retargets a link (the links
# must already be right); an applied migration is all-or-nothing, backed up under
# .harness/migrations/, and undone with --rollback <migration>. Human-only.
# Idempotent; a customised harness target stops it untouched.
#
# --src-dirs writes harness.toml ([scope] src_dirs) for code outside src/.
# Inside an existing repo (history, or a monorepo subdir) nothing is committed
# and the machine-local symlinks are excluded via .git/info/exclude.
set -euo pipefail

TOOLKIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DEST="${1:-}"; shift || true
FORCE=0
MIGRATE=0
ROLLBACK=""
APPLY=()          # --apply / --yes, passed to lib/_upgrade.py
TPL_LANG="python"   # template set (python | c). Not LANG: that is the exported locale.
PYPKG=""            # --python <pkg>: scaffold a Python layer (implies lang=python)
CNAME=""            # --c <name>:     scaffold a C layer       (implies lang=c)
SRC_DIRS=""         # --src-dirs a,b: production-code dirs when they are not src/
while [ $# -gt 0 ]; do
  case "$1" in
    --force)  FORCE=1 ;;
    --migrate) MIGRATE=1 ;;
    --rollback) shift; ROLLBACK="${1:-}"
      [ -n "$ROLLBACK" ] || { echo "error: --rollback needs a migration id" >&2; exit 2; } ;;
    --apply|--yes) APPLY+=("$1") ;;
    --lang)   shift; TPL_LANG="${1:-}" ;;
    --python) shift; PYPKG="${1:-}"; TPL_LANG="python" ;;
    --c)      shift; CNAME="${1:-}"; TPL_LANG="c" ;;
    --src-dirs) shift; SRC_DIRS="${1-}"
      # Root-relative dirs only: no absolute paths, no .., no quoting tricks.
      if ! printf '%s' "$SRC_DIRS" | grep -qE '^[A-Za-z0-9_][A-Za-z0-9_./-]*(,[A-Za-z0-9_][A-Za-z0-9_./-]*)*$' ||
         printf '%s' ",$SRC_DIRS," | grep -qE '(^|[,/])\.\.([,/]|$)'; then
        echo "error: --src-dirs needs comma-separated project-relative dirs (got '$SRC_DIRS')" >&2; exit 2
      fi ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift || true
done
if [ -z "$DEST" ]; then
  echo "usage: install.sh <project-path> [--force] [--lang python|c] [--python <pkg> | --c <name>] [--src-dirs a,b]" >&2; exit 2
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

# --- Migration / rollback of an existing project: no link, copy or commit step runs ---
if [ "$MIGRATE" -eq 1 ] && [ -n "$ROLLBACK" ]; then
  echo "error: --migrate and --rollback are exclusive" >&2; exit 2
fi
if [ "${#APPLY[@]}" -gt 0 ] && [ "$MIGRATE" -eq 0 ] && [ -z "$ROLLBACK" ]; then
  echo "error: --apply/--yes only go with --migrate or --rollback" >&2; exit 2
fi
if [ "$MIGRATE" -eq 1 ]; then
  exec python3 "$TOOLKIT/lib/_upgrade.py" migrate "$DEST" "${APPLY[@]}"
fi
if [ -n "$ROLLBACK" ]; then
  exec python3 "$TOOLKIT/lib/_upgrade.py" rollback "$DEST" "$ROLLBACK" "${APPLY[@]}"
fi

. "$TOOLKIT/lib/_colors.sh"
# Output: one line per step (items collected, printed together).
LINKED=(); COPIED=(); KEPT=()

link() {  # link <target-in-toolkit> <path-in-dest>
  local target="$TOOLKIT/$1" dest="$DEST/$2"
  mkdir -p "$(dirname "$dest")"
  ln -sfn "$target" "$dest"
  LINKED+=("$2")
}

copy() {  # copy <path-in-template-set> <path-in-dest>
  local src="$TOOLKIT/$TPLSUB/$1" dest="$DEST/$2"
  mkdir -p "$(dirname "$dest")"
  if [ -e "$dest" ] && [ "$FORCE" -ne 1 ]; then KEPT+=("$2"); return; fi
  cp -R "$src" "$dest"
  COPIED+=("$2")
}

echo "Installing harness ($TPL_LANG) into $DEST"

# --- Symlinks (generic) ---
link agents        .claude/agents
link codex         .claude/codex
link skills        .claude/skills
link settings.json .claude/settings.json
link lib           harness
row "$GREEN" "[symlink]" "${LINKED[*]}"

# --- Copies (customizable) ---
copy CLAUDE.md          CLAUDE.md
copy AGENTS.md          AGENTS.md
copy Makefile           Makefile
copy CHECKPOINTS.md     CHECKPOINTS.md
copy docs               docs
copy progress           progress
# --src-dirs: the human-owned scope config (Claude's guards never let it edit it).
if [ -n "$SRC_DIRS" ]; then
  if [ -e "$DEST/harness.toml" ] && [ "$FORCE" -ne 1 ]; then
    KEPT+=("harness.toml")
  else
    toml="$(printf '%s' "$SRC_DIRS" | sed 's/,/", "/g')"
    printf '[scope]\nsrc_dirs = ["%s"]\n' "$toml" > "$DEST/harness.toml"
    COPIED+=("harness.toml")
  fi
fi
# Versions of the harness-managed copied files (lib/_templates.py, M0a).
python3 "$TOOLKIT/lib/_upgrade.py" register "$DEST" "$TPLSUB"
COPIED+=(".harness/templates.json")
[ ${#COPIED[@]} -gt 0 ] && row "$GREEN" "[copy]" "${COPIED[*]}"
[ ${#KEPT[@]} -gt 0 ] && row "$ORANGE" "[SKIP]" "${KEPT[*]} (exist; --force to overwrite)"

# --- Language layer (delegated; keeps install.sh harness-only) ---
SCAFFOLD_ARGS=(); [ "$FORCE" -eq 1 ] && SCAFFOLD_ARGS=(--force)
if [ -n "$PYPKG" ]; then
  bash "$TOOLKIT/lib/scaffold_python.sh" "$DEST" "$PYPKG" "${SCAFFOLD_ARGS[@]}"
fi
if [ -n "$CNAME" ]; then
  bash "$TOOLKIT/lib/scaffold_c.sh" "$DEST" "$CNAME" "${SCAFFOLD_ARGS[@]}"
fi

# --- Generated (derived from docs/plan/; a fresh install has an empty plan) ---
( cd "$DEST" && python3 "$TOOLKIT/lib/sync_plan.py" >/dev/null )

# --- .gitignore: secrets and build artifacts never reach a commit ---
IGNORES=(.env .harness/migrations/)  # migration backups are machine-local (M0a)
if [ "$TPL_LANG" = "c" ]; then
  IGNORES+=("*.o" "*.bin")
else
  IGNORES+=(.venv/ __pycache__/ .pytest_cache/ .mypy_cache/ .ruff_cache/ "pyproject.toml.bak.*")
fi
touch "$DEST/.gitignore"
for entry in "${IGNORES[@]}"; do
  grep -qxF -- "$entry" "$DEST/.gitignore" || printf '%s\n' "$entry" >> "$DEST/.gitignore"
done
row "$GREEN" "[gen]" "feature_list.json .gitignore"

# --- Python deps (before the root commit, so uv.lock is part of it) ---
CHECK=1
if [ "$TPL_LANG" = "python" ]; then
  if [ ! -f "$DEST/pyproject.toml" ]; then
    CHECK=0
  elif out="$(cd "$DEST" && uv sync --all-groups --quiet 2>&1)"; then
    row "$GREEN" "[uv]" "uv sync --all-groups"
  else
    printf '%s\n' "$out"
    row "$RED" "[FAIL]" "uv sync --all-groups"
    exit 1
  fi
fi

# --- Git base: root commit on main + setup branch (fresh repos only) ---
# The harness needs a base commit: the src/ scope backstop diffs against HEAD and
# every subtask branches from main. Existing history is never touched, and a
# project inside another repo (a monorepo subdir) never gets a nested one.
DEST_REAL="$(cd "$DEST" && pwd -P)"
TOP="$(git -C "$DEST" rev-parse --show-toplevel 2>/dev/null || true)"
[ -n "$TOP" ] || { git init -q -b main "$DEST"; TOP="$DEST_REAL"; }
SETUP_BRANCH="chore/project-setup"
GIT_KEPT=0
INNER=0; [ "$TOP" = "$DEST_REAL" ] || INNER=1
exclude_symlinks() {  # machine-local links: kept out of a shared repo, locally only
  local rel="${DEST_REAL#"$TOP"}" file entry
  file="$(git -C "$DEST" rev-parse --git-path info/exclude)"
  case "$file" in /*) ;; *) file="$DEST_REAL/$file" ;; esac
  mkdir -p "$(dirname "$file")"; touch "$file"
  for entry in .claude/agents .claude/codex .claude/skills .claude/settings.json harness; do
    grep -qxF -- "$rel/$entry" "$file" || printf '%s\n' "$rel/$entry" >> "$file"
  done
}
git_identity() {  # name + email from env or config; never guessed
  local name="${GIT_AUTHOR_NAME:-$(git -C "$DEST" config user.name || true)}"
  local email="${GIT_AUTHOR_EMAIL:-$(git -C "$DEST" config user.email || true)}"
  [ -n "$name" ] && [ -n "$email" ]
}
if git -C "$DEST" rev-parse -q --verify HEAD >/dev/null; then
  GIT_KEPT=1
  exclude_symlinks
  row "$ORANGE" "[git]" "existing history kept · harness symlinks excluded locally (.git/info/exclude)"
elif [ "$INNER" -eq 1 ]; then
  exclude_symlinks
  row "$ORANGE" "[git]" "inside an existing repo ($TOP) with no commits: no commit made"
elif ! git_identity; then
  row "$ORANGE" "[git]" "no git identity (user.name/user.email): root commit skipped"
else
  git -C "$DEST" symbolic-ref HEAD refs/heads/main
  git -C "$DEST" add -A
  if git -C "$DEST" commit -q -m "chore: install harness"; then
    git -C "$DEST" switch -q -c "$SETUP_BRANCH"
    row "$GREEN" "[git]" "root commit on main · on $SETUP_BRANCH"
  else
    row "$ORANGE" "[git]" "git commit failed: root commit skipped"
  fi
fi

# --- Machine-level config (idempotent; links ~/.claude into the toolkit) ---
bash "$TOOLKIT/link-global.sh" --quiet

# --- Final gate: the project must start green ---
if [ "$CHECK" -eq 0 ]; then
  row "$ORANGE" "[SKIP]" "make check: no pyproject.toml (use --python <pkg>)"
  exit 0
fi
# Captured to stay quiet when green; colors kept if it is replayed on a terminal.
REPLAY_COLOR=""; [ -t 1 ] && [ -z "${NO_COLOR:-}" ] && REPLAY_COLOR=1
if out="$(cd "$DEST" && FORCE_COLOR="$REPLAY_COLOR" make -s --no-print-directory check 2>&1)"; then
  row "$GREEN" "[OK]" "harness ready · make check green"
else
  printf '%s\n' "$out"
  hint=""; [ "$GIT_KEPT" -eq 1 ] && hint="; uncommitted install files count as changes: commit them"
  row "$RED" "[FAIL]" "harness installed but make check is red (output above)$hint"
  exit 1
fi
