#!/usr/bin/env bash
# The single entry point through which Claude (leader) invokes Codex.
#   review_with_codex.sh roadmap              Codex reviews PLAN.md + every phase (scope)
#   review_with_codex.sh phase <NN>           Codex reviews the breakdown of phase NN
#   review_with_codex.sh issue <ISS-n>        Codex reviews the breakdown of issue ISS-n
#   review_with_codex.sh issue-draft          Codex reviews progress/issue_draft.md
#   review_with_codex.sh <plan|code|doc> <N.M.K|ISS-n.k>   per-subtask gates and close
#   review_with_codex.sh close <N.M.K>        check + commit a close (after manual `doc`)
#   review_with_codex.sh pr <N.M|ISS-n>       push the task/issue branch and open its PR
#   review_with_codex.sh seal <N.M.K|roadmap|phase NN|issue ISS-n|draft>
#                                             (human, manual mode: seal)
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
#
# Every gate verdict is appended to progress/review_history/, so rejected rounds
# survive. When a close finishes the last subtask of a task, Codex has also
# written progress/pr_<N.M>.md and the harness pushes the task branch and opens
# one PR for the task with `gh` (harness.toml [git] auto_pr; HARNESS_AUTO_PR=0
# turns it off; HARNESS_GH overrides the gh binary). A failed push or PR never
# undoes the close: it warns and prints the command to retry.
set -uo pipefail

HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HARNESS_DIR/_colors.sh"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
CODEX_DIR="$PROJECT_DIR/.claude/codex"
MODE_EXEC="${HARNESS_CODEX_MODE:-auto}"
cd "$PROJECT_DIR" || exit 1

usage() {
  echo "usage: review_with_codex.sh roadmap | phase <NN> | issue <ISS-n> | issue-draft" \
       "| <plan|code|doc> <N.M.K|ISS-n.k> | close <N.M.K> | pr <N.M> | pr <ISS-n>" \
       "| seal <N.M.K|roadmap|phase NN|issue ISS-n|draft>" >&2
  exit 2
}
PHASE="${1:-}"; shift || true
HARNESS_PY="$HARNESS_DIR/_harness.py"
PR_PY="$HARNESS_DIR/_pr.py"
GH="${HARNESS_GH:-gh}"
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

# An issue (ISS-n) is claimed with a tag until its PR is open: drop the claim then.
# A failure only warns: retrying `pr ISS-n` runs it again.
cleanup_claim() {
  case "$1" in ISS-*) ;; *) return 0 ;; esac
  HARNESS_GH="$GH" python3 "$HARNESS_DIR/_issue.py" cleanup "$1" ||
    warn "could not drop the claim of $1; $2" >&2
}

# Push the current (task) branch and open the PR of task $1. Never fails the
# caller: the close is already committed, so problems become a warning.
open_pr() {
  local tid="$1" branch base title body retry
  branch="$(git rev-parse --abbrev-ref HEAD)"
  base="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null)"
  base="${base#origin/}"; : "${base:=main}"
  retry="retry with: ./harness/review_with_codex.sh pr $tid"
  if ! command -v "$GH" >/dev/null 2>&1; then
    warn "gh not found: the PR for task $tid was not opened; $retry" >&2
    return 0
  fi
  if ! git push -q -u origin "$branch"; then
    warn "could not push $branch: the PR for task $tid was not opened; $retry" >&2
    return 0
  fi
  if "$GH" pr view "$branch" --json url >/dev/null 2>&1; then
    ok "PR already open for $branch (pushed the new commits)"
    cleanup_claim "$tid" "$retry"
    return 0
  fi
  title="$(python3 "$PR_PY" title "$tid")"
  body="$(mktemp "${TMPDIR:-/tmp}/harness-pr.XXXXXX")"
  if ! python3 "$PR_PY" body "$tid" "$base" >"$body"; then
    warn "could not build the PR body for task $tid; $retry" >&2
    return 0
  fi
  if "$GH" pr create --head "$branch" --base "$base" --title "$title" --body-file "$body"; then
    ok "PR opened for task $tid ($branch -> $base)"
    cleanup_claim "$tid" "$retry"
  else
    warn "gh pr create failed for task $tid; $retry" >&2
  fi
  rm -f "$body"
  return 0
}

# After a close: open the task PR when that close finished the task.
open_pr_if_due() {
  local tid
  tid="$(python3 "$PR_PY" pr-due "$1")" || return 0
  open_pr "$tid"
}

case "$PHASE" in
  seal)    exec python3 "$HARNESS_DIR/_guard.py" seal "$@" ;;
  close)   [ -n "${1:-}" ] || usage; close_commit "$1" || exit 1; open_pr_if_due "$1"; exit 0 ;;
  pr)      [ -n "${1:-}" ] || usage; open_pr "$1"; exit 0 ;;
  roadmap) ID="roadmap"; ROLE="$CODEX_DIR/reviewer.md" ;;
  phase|plan|code) ID="${1:-}"; ROLE="$CODEX_DIR/reviewer.md" ;;
  issue)   ID="${1:-}"; [[ "$ID" =~ ^[0-9]+$ ]] && ID="ISS-$ID"; ROLE="$CODEX_DIR/reviewer.md" ;;
  issue-draft) ID="draft"; ROLE="$CODEX_DIR/reviewer.md" ;;
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
  issue)   python3 "$HARNESS_PY" issue-ready "$ID" || exit 1 ;;
  plan)    python3 "$HARNESS_PY" subtask-ready "$ID" || exit 1
           python3 "$HARNESS_DIR/_guard.py" migrates-ready "$ID" || exit 1 ;;
  issue-draft)
    [ -f progress/issue_draft.md ] || {
      fail "no progress/issue_draft.md: the leader writes the draft first" >&2; exit 1; } ;;
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
  issue)
    ISSUE_FILE="$(ls docs/plan/issues/"$ID"-*.md 2>/dev/null | head -1)"
    TARGET="the breakdown of issue $ID: ${ISSUE_FILE:-docs/plan/issues/$ID-<slug>.md} (header) and every subtask spec in ${ISSUE_FILE%.md}/"
    VERDICT_FILE="progress/issue_review_$ID.md"; SEAL_ARGS=(issue "$ID"); SEAL_ON="ISSUE_APPROVED" ;;
  issue-draft)
    TARGET="the draft of a new GitHub issue: progress/issue_draft.md (front matter + body)"
    VERDICT_FILE="progress/issue_draft_review.md"; SEAL_ARGS=(draft); SEAL_ON="DRAFT_APPROVED" ;;
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
  # The close that finishes a task also writes the narrative of the task's PR.
  if LAST_TASK="$(python3 "$PR_PY" last-of-task "$ID" 2>/dev/null)"; then
    CLOSE_STEP="$CLOSE_STEP
Task PR: this close finishes task $LAST_TASK. Before the commit message, also
write progress/pr_$LAST_TASK.md as your role's \"Task PR narrative\" section
describes. Sources: the specs and docs/reference/ notes of every subtask of
task $LAST_TASK, progress/review_history/ (its subtasks, its phase and the
roadmap) and \`git log\` of this branch. The harness opens the PR after the commit."
  fi
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
    if [ -n "${LAST_TASK:-}" ]; then
      echo "--- this close finishes task $LAST_TASK: the close opens its PR (or retry) ---"
      echo "./harness/review_with_codex.sh pr $LAST_TASK"
    fi
  else
    echo "--- after Codex writes the verdict, keep it in the review history ---"
    echo "python3 harness/_pr.py record $PHASE $ID $VERDICT_FILE"
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
  close_commit "$ID" || exit 1
  open_pr_if_due "$ID"
  exit 0
fi

# Keep every round, rejected ones included: the next verdict overwrites the file.
if [ $RC -eq 0 ] && [ -f "$VERDICT_FILE" ]; then
  python3 "$PR_PY" record "$PHASE" "$ID" "$VERDICT_FILE" ||
    warn "could not record $VERDICT_FILE in progress/review_history/" >&2
fi

# Seal what Codex just approved (roadmap, phase breakdown, or the subtask's
# acceptance tests + spec): check.sh then fails if it changes afterwards,
# whichever tool changed it.
if [ -n "$SEAL_ON" ] && [ $RC -eq 0 ] &&
   [ "$(python3 "$HARNESS_PY" verdict "$VERDICT_FILE")" = "$SEAL_ON" ]; then
  python3 "$HARNESS_DIR/_guard.py" seal "${SEAL_ARGS[@]}" || RC=1
fi
exit $RC
