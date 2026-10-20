#!/usr/bin/env bash
# The single entry point through which Claude (leader) invokes Codex.
#   review_with_codex.sh <plan|code|doc> <feature-id>
#   review_with_codex.sh seal <feature-id>   (human, manual mode: seal the oracle)
#
# Reads the model and effort from the role prompt header (codex/*.md) and passes
# them to the codex CLI. Execution mode via HARNESS_CODEX_MODE:
#   auto   (default) → runs codex
#   manual           → prints the command and prompt for you to launch it yourself
#
# Sandbox (least privilege by default):
#   HARNESS_CODEX_SANDBOX  workspace-write (default) | read-only | danger-full-access
#   HARNESS_CODEX_NETWORK  1 → allow network inside workspace-write (default: off)
# Under workspace-write Codex can write the project but not .git, so the extra
# writable roots are: the uv cache (always) and the git dir (doc phase only, to commit).
set -uo pipefail

HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
CODEX_DIR="$PROJECT_DIR/.claude/codex"
MODE_EXEC="${HARNESS_CODEX_MODE:-auto}"
cd "$PROJECT_DIR" || exit 1

PHASE="${1:-}"; ID="${2:-}"
if [ -z "$PHASE" ] || [ -z "$ID" ]; then
  echo "usage: review_with_codex.sh <plan|code|doc|seal> <feature-id>" >&2; exit 2
fi

case "$PHASE" in
  seal) exec python3 "$HARNESS_DIR/_guard.py" seal "$ID" ;;
  plan|code) ROLE="$CODEX_DIR/reviewer.md"; CODEX_PHASE="$PHASE" ;;
  doc)       ROLE="$CODEX_DIR/documenter.md"; CODEX_PHASE="doc" ;;
  *) echo "invalid phase: $PHASE (plan|code|doc|seal)" >&2; exit 2 ;;
esac
[ -f "$ROLE" ] || { echo "role prompt not found: $ROLE" >&2; exit 1; }

# Read config from the role prompt's YAML header.
read_cfg() { grep -E "^$1:" "$ROLE" | head -1 | sed -E "s/^$1:[[:space:]]*//"; }
MODEL="$(read_cfg codex_model)"
EFFORT="$(read_cfg codex_effort)"
: "${MODEL:=default}"
: "${EFFORT:=medium}"

FEATURE_NAME="$(python3 "$HARNESS_DIR/_harness.py" feature-name "$ID" 2>/dev/null)"

# Build the concrete instruction for Codex.
PROMPT="$(cat <<EOF
Act strictly according to your role defined in: $ROLE
Phase: $CODEX_PHASE
Feature: id=$ID name=$FEATURE_NAME
Project root: $PROJECT_DIR

Context you must read: AGENTS.md, docs/architecture.md, docs/conventions.md,
CHECKPOINTS.md, feature_list.json, and progress/current.md.

Respect your role's output contract (write the verdict/result to the indicated
progress/ file and return a single reference line).
Do not edit production code. Do not add agent attribution.
EOF
)"

# --- Model, effort and sandbox for the codex CLI (verified with codex exec --help).
# Effort is not a dedicated flag: it is passed as a config override.
# Sandbox: the reviewer must WRITE (acceptance tests + verdict) and RUN the uv
# toolchain, so read-only fails. workspace-write covers the project; it keeps
# .git read-only and blocks the network, so grant only what each phase needs
# (verified with `codex sandbox`): the uv cache, plus the git dir for `doc`.
SANDBOX="${HARNESS_CODEX_SANDBOX:-workspace-write}"
CODEX_ARGS=(--model "$MODEL" --sandbox "$SANDBOX")
case "$EFFORT" in
  low|medium|high|xhigh) CODEX_ARGS+=(-c "model_reasoning_effort=$EFFORT") ;;
esac
if [ "$SANDBOX" = "workspace-write" ]; then
  ROOTS=("$(uv cache dir 2>/dev/null || echo "${UV_CACHE_DIR:-$HOME/.cache/uv}")")
  if [ "$PHASE" = "doc" ]; then
    ROOTS+=("$(git rev-parse --absolute-git-dir 2>/dev/null)")
    ROOTS+=("$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)")
  fi
  # A JSON array of strings is valid TOML for the -c override.
  ROOTS_TOML="$(python3 -c 'import json,sys; print(json.dumps(sorted({p for p in sys.argv[1:] if p})))' "${ROOTS[@]}")"
  CODEX_ARGS+=(-c "sandbox_workspace_write.writable_roots=$ROOTS_TOML")
  if [ "${HARNESS_CODEX_NETWORK:-0}" = "1" ]; then
    CODEX_ARGS+=(-c "sandbox_workspace_write.network_access=true")
  fi
fi

if [ "$MODE_EXEC" = "manual" ]; then
  echo "── MANUAL MODE: launch it yourself with Codex ──────────"
  printf 'codex exec'; printf ' %q' "${CODEX_ARGS[@]}"; echo
  echo "--- prompt ---"
  echo "$PROMPT"
  if [ "$PHASE" = "plan" ]; then
    echo "--- after PLAN_APPROVED, seal the acceptance tests (human only) ---"
    echo "./harness/review_with_codex.sh seal $ID"
  fi
  exit 0
fi

if ! command -v codex >/dev/null 2>&1; then
  echo "[FAIL]  codex is not in PATH. Use HARNESS_CODEX_MODE=manual or install Codex." >&2
  exit 1
fi

# Non-interactive execution via `codex exec`.
printf '%s\n' "$PROMPT" | codex exec "${CODEX_ARGS[@]}"
RC=$?

# Seal the oracle right after an approved plan gate: check.sh then fails if the
# acceptance tests change afterwards, whichever tool changed them.
if [ "$PHASE" = "plan" ] && [ $RC -eq 0 ] &&
   [ "$(python3 "$HARNESS_DIR/_harness.py" verdict "progress/plan_review_${ID}.md")" = "PLAN_APPROVED" ]; then
  python3 "$HARNESS_DIR/_guard.py" seal "$ID" || RC=1
fi
exit $RC
