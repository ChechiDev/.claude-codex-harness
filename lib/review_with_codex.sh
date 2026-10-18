#!/usr/bin/env bash
# The single entry point through which Claude (leader) invokes Codex.
#   review_with_codex.sh <plan|code|doc> <feature-id>
#
# Reads the model and effort from the role prompt header (codex/*.md) and passes
# them to the codex CLI. Execution mode via HARNESS_CODEX_MODE:
#   auto   (default) → runs codex
#   manual           → prints the prompt for you to launch it yourself
set -uo pipefail

HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
CODEX_DIR="$PROJECT_DIR/.claude/codex"
MODE_EXEC="${HARNESS_CODEX_MODE:-auto}"
cd "$PROJECT_DIR" || exit 1

PHASE="${1:-}"; ID="${2:-}"
if [ -z "$PHASE" ] || [ -z "$ID" ]; then
  echo "usage: review_with_codex.sh <plan|code|doc> <feature-id>" >&2; exit 2
fi

case "$PHASE" in
  plan|code) ROLE="$CODEX_DIR/reviewer.md"; CODEX_PHASE="$PHASE" ;;
  doc)       ROLE="$CODEX_DIR/documenter.md"; CODEX_PHASE="doc" ;;
  *) echo "invalid phase: $PHASE (plan|code|doc)" >&2; exit 2 ;;
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

# --- Model and effort for the codex CLI (verified with codex exec --help). -----
# Effort is not a dedicated flag: it is passed as a config override.
CODEX_ARGS=(--model "$MODEL")
case "$EFFORT" in
  low|medium|high|xhigh) CODEX_ARGS+=(-c "model_reasoning_effort=$EFFORT") ;;
esac

if [ "$MODE_EXEC" = "manual" ]; then
  echo "── MANUAL MODE: launch it yourself with Codex ──────────"
  echo "codex ${CODEX_ARGS[*]}"
  echo "--- prompt ---"
  echo "$PROMPT"
  exit 0
fi

if ! command -v codex >/dev/null 2>&1; then
  echo "[FAIL]  codex is not in PATH. Use HARNESS_CODEX_MODE=manual or install Codex." >&2
  exit 1
fi

# Non-interactive execution via `codex exec`.
printf '%s\n' "$PROMPT" | codex exec "${CODEX_ARGS[@]}"
