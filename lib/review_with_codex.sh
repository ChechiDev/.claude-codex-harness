#!/usr/bin/env bash
# The single entry point through which Claude (leader) invokes Codex.
#   review_with_codex.sh roadmap              Codex reviews PLAN.md + every phase (scope)
#   review_with_codex.sh phase <NN>           Codex reviews the breakdown of phase NN
#   review_with_codex.sh <plan|code|doc> <N.M.K>   per-subtask gates and close
#   review_with_codex.sh close <N.M.K>        check + commit a close (after manual `doc`)
#   review_with_codex.sh seal <N.M.K|roadmap|phase NN>   (human, manual mode: seal)
#
# Each gate checks its preconditions first (roadmap approved before a phase gate,
# phase approved before a subtask plan gate) and, when Codex approves, seals what
# it approved so `make check` catches any later silent change.
#
# Reads the model and effort from the role prompt header (codex/*.md) and passes
# them to the codex CLI. Execution mode via HARNESS_CODEX_MODE:
#   auto   (default) → runs codex
#   manual           → prints the command and prompt for you to launch it yourself
#
# Sandbox (least privilege by default):
#   HARNESS_CODEX_SANDBOX  workspace-write (default) | read-only | danger-full-access
#   HARNESS_CODEX_NETWORK  1 → allow network inside workspace-write (default: off)
# Under workspace-write Codex can write the project but not .git; the only extra
# writable root is the uv cache.
#
# The test suite runs HERE, outside the sandbox: with the network off the sandbox
# also blocks local sockets, so web-framework test clients hang inside it. Before
# every gate the harness runs HARNESS_CHECK_CMD (default `make check`) and hands
# Codex the exit code and the log. The close (`doc`) ends with Codex writing its
# commit message to progress/close_<N.M.K>.msg; the harness then runs the check
# again and, if green, makes the commit itself (`close`).
set -uo pipefail

HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HARNESS_DIR/_colors.sh"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
CODEX_DIR="$PROJECT_DIR/.claude/codex"
MODE_EXEC="${HARNESS_CODEX_MODE:-auto}"
cd "$PROJECT_DIR" || exit 1

usage() {
  echo "usage: review_with_codex.sh roadmap | phase <NN> | <plan|code|doc> <N.M.K>" \
       "| close <N.M.K> | seal <N.M.K|roadmap|phase NN>" >&2
  exit 2
}
PHASE="${1:-}"; shift || true
HARNESS_PY="$HARNESS_DIR/_harness.py"
CHECK_CMD="${HARNESS_CHECK_CMD:-make check}"

# Run the project check outside the sandbox; $1 = log file. Returns its exit code.
run_check() { bash -c "$CHECK_CMD" >"$1" 2>&1 </dev/null; }
new_log() { mktemp "${TMPDIR:-/tmp}/harness-check-$1.XXXXXX"; }

# Commit a close: the documenter's message, only with the check green.
close_commit() {
  local id="$1" msg="progress/close_$1.msg" log text
  python3 "$HARNESS_DIR/_guard.py" commit-message "$msg" "$id" || return 1
  log="$(new_log close)"
  if ! run_check "$log"; then
    fail "$CHECK_CMD is red after the close of $id: nothing committed (log: $log)" >&2
    tail -n 30 "$log" >&2
    return 1
  fi
  text="$(cat "$msg")"
  rm -f "$msg"
  git add -A && printf '%s\n' "$text" | git commit -q -F - || return 1
  ok "closed -> $id ; commit $(git rev-parse --short HEAD)"
}

case "$PHASE" in
  seal)    exec python3 "$HARNESS_DIR/_guard.py" seal "$@" ;;
  close)   [ -n "${1:-}" ] || usage; close_commit "$1"; exit $? ;;
  roadmap) ID="roadmap"; ROLE="$CODEX_DIR/reviewer.md" ;;
  phase|plan|code) ID="${1:-}"; ROLE="$CODEX_DIR/reviewer.md" ;;
  doc)     ID="${1:-}"; ROLE="$CODEX_DIR/documenter.md" ;;
  *) usage ;;
esac
[ -n "$ID" ] || usage
CODEX_PHASE="$PHASE"
[ -f "$ROLE" ] || { echo "role prompt not found: $ROLE" >&2; exit 1; }

# Preconditions: never review a step whose prerequisite is not approved yet.
case "$PHASE" in
  roadmap) python3 "$HARNESS_PY" roadmap-ready || exit 1 ;;
  phase)   python3 "$HARNESS_PY" phase-ready "$ID" || exit 1 ;;
  plan)    python3 "$HARNESS_PY" subtask-ready "$ID" || exit 1 ;;
esac

# Read config from the role prompt's YAML header.
read_cfg() { grep -E "^$1:" "$ROLE" | head -1 | sed -E "s/^$1:[[:space:]]*//"; }
MODEL="$(read_cfg codex_model)"
EFFORT="$(read_cfg codex_effort)"
: "${MODEL:=default}"
: "${EFFORT:=medium}"

# What this gate reviews, and where its verdict goes.
case "$PHASE" in
  roadmap)
    TARGET="the roadmap: docs/plan/PLAN.md and the title + goal of every docs/plan/phase-*.md"
    VERDICT_FILE="progress/roadmap_review.md"; SEAL_ARGS=(roadmap); SEAL_ON="ROADMAP_APPROVED" ;;
  phase)
    PHASE_FILE="$(ls docs/plan/phase-"$ID"-*.md 2>/dev/null | head -1)"
    TARGET="the breakdown of phase $ID: ${PHASE_FILE:-docs/plan/phase-$ID-<slug>.md} (tasks) and every subtask spec in ${PHASE_FILE%.md}/"
    VERDICT_FILE="progress/phase_review_$ID.md"; SEAL_ARGS=(phase "$ID"); SEAL_ON="PHASE_APPROVED" ;;
  *)
    TARGET="subtask $ID, spec: $(python3 "$HARNESS_PY" spec "$ID" 2>/dev/null)"
    VERDICT_FILE="progress/plan_review_$ID.md"; SEAL_ARGS=("$ID"); SEAL_ON="PLAN_APPROVED"
    [ "$PHASE" = "plan" ] || SEAL_ON="" ;;
esac
FEATURE_NAME="$(python3 "$HARNESS_PY" feature-name "$ID" 2>/dev/null)"

CHECK_LOG="$(new_log "$PHASE")"
run_check "$CHECK_LOG"
CHECK_RC=$?
CLOSE_STEP=""
if [ "$PHASE" = "doc" ]; then
  CLOSE_STEP="
Close: do not commit. Write the commit message to progress/close_$ID.msg and
stop; the harness then runs \`$CHECK_CMD\` and makes the commit."
fi

# Build the concrete instruction for Codex.
PROMPT="$(cat <<EOF
Act strictly according to your role defined in: $ROLE
Phase: $CODEX_PHASE
Feature: id=$ID name=$FEATURE_NAME
Review: $TARGET
Project root: $PROJECT_DIR

Context you must read: AGENTS.md, docs/architecture.md, docs/conventions.md,
CHECKPOINTS.md, feature_list.json, and progress/current.md.

Test suite: this sandbox cannot run it (local sockets are blocked).
Do NOT run \`make check\`, \`make verify\`, the tests or anything that runs them.
The harness ran \`$CHECK_CMD\` outside the sandbox just before this session:
exit code $CHECK_RC, full output in $CHECK_LOG.
That result is the one AGENTS.md step 1 asks for.$CLOSE_STEP

Respect your role's output contract (write the verdict/result to the indicated
progress/ file and return a single reference line).
Do not edit production code. Do not add agent attribution.
EOF
)"

# --- Model, effort and sandbox for the codex CLI (verified with codex exec --help).
# Effort is not a dedicated flag: it is passed as a config override.
# Sandbox: the reviewer must WRITE (acceptance tests + verdict) and may run static
# tools through uv, so read-only fails. workspace-write covers the project; it
# keeps .git read-only and blocks the network (verified with `codex sandbox`).
SANDBOX="${HARNESS_CODEX_SANDBOX:-workspace-write}"
CODEX_ARGS=(--model "$MODEL" --sandbox "$SANDBOX")
case "$EFFORT" in
  low|medium|high|xhigh) CODEX_ARGS+=(-c "model_reasoning_effort=$EFFORT") ;;
esac
if [ "$SANDBOX" = "workspace-write" ]; then
  ROOTS=("$(uv cache dir 2>/dev/null || echo "${UV_CACHE_DIR:-$HOME/.cache/uv}")")
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
  echo "--- verdict file: $VERDICT_FILE ---"
  if [ -n "$SEAL_ON" ]; then
    echo "--- after $SEAL_ON, seal what was approved (human only) ---"
    echo "./harness/review_with_codex.sh seal ${SEAL_ARGS[*]}"
  fi
  if [ "$PHASE" = "doc" ]; then
    echo "--- after Codex writes progress/close_$ID.msg, check and commit ---"
    echo "./harness/review_with_codex.sh close $ID"
  fi
  exit 0
fi

if ! command -v codex >/dev/null 2>&1; then
  fail "codex is not in PATH. Use HARNESS_CODEX_MODE=manual or install Codex." >&2
  exit 1
fi

# Non-interactive execution via `codex exec`.
printf '%s\n' "$PROMPT" | codex exec "${CODEX_ARGS[@]}"
RC=$?

if [ "$PHASE" = "doc" ]; then
  [ $RC -eq 0 ] || exit $RC
  close_commit "$ID"
  exit $?
fi

# Seal what Codex just approved (roadmap, phase breakdown, or the subtask's
# acceptance tests + spec): check.sh then fails if it changes afterwards,
# whichever tool changed it.
if [ -n "$SEAL_ON" ] && [ $RC -eq 0 ] &&
   [ "$(python3 "$HARNESS_PY" verdict "$VERDICT_FILE")" = "$SEAL_ON" ]; then
  python3 "$HARNESS_DIR/_guard.py" seal "${SEAL_ARGS[@]}" || RC=1
fi
exit $RC
