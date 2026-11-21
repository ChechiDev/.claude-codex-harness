#!/usr/bin/env bash
# The single entry point through which Claude (leader) invokes Codex.
#   review_with_codex.sh roadmap              Codex reviews PLAN.md + every phase (scope)
#   review_with_codex.sh phase <NN>           Codex reviews the breakdown of phase NN (tasks)
#   review_with_codex.sh task <N.M>           Codex reviews the subtask specs of task N.M
#   review_with_codex.sh task-close <N.M>     Codex checks a finished task against its goal
#   review_with_codex.sh phase-close <NN>     Codex checks a finished phase against PLAN.md
#   review_with_codex.sh issue <ISS-n>        Codex reviews the breakdown of issue ISS-n
#   review_with_codex.sh issue-draft          Codex reviews progress/issue_draft.md
#   review_with_codex.sh <plan|code|doc> <N.M.K|ISS-n.k>   per-subtask gates and close
#   review_with_codex.sh close <N.M.K>        check + commit a close (after manual `doc`)
#   review_with_codex.sh pr <N.M|ISS-n>       push the task/issue branch and open its PR
#   review_with_codex.sh seal <N.M.K|roadmap|phase NN|task N.M|issue ISS-n|draft>
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
# Inherited exported functions could stand in for any command this script runs
# (issue #31): drop them all before anything else.
for _f in $(builtin compgen -A function); do builtin unset -f "$_f"; done; builtin unset _f
if [ -n "$(builtin compgen -A function)" ]; then
  echo "[FAIL] inherited shell functions remain: refusing to run" >&2; exit 1
fi

HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HARNESS_DIR/_colors.sh"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
CODEX_DIR="$PROJECT_DIR/.claude/codex"
MODE_EXEC="${HARNESS_CODEX_MODE:-auto}"
cd "$PROJECT_DIR" || exit 1

usage() {
  echo "usage: review_with_codex.sh roadmap | phase <NN> | task <N.M> | task-close <N.M>" \
       "| phase-close <NN> | issue <ISS-n> | issue-draft" \
       "| <plan|code|doc> <N.M.K|ISS-n.k> | close <N.M.K> | pr <N.M> | pr <ISS-n>" \
       "| seal <N.M.K|roadmap|phase NN|task N.M|issue ISS-n|draft> | contract" >&2
  exit 2
}
PHASE="${1:-}"; shift || true
HARNESS_PY="$HARNESS_DIR/_harness.py"
PR_PY="$HARNESS_DIR/_pr.py"
GH="${HARNESS_GH:-gh}"
CHECK_CMD="${HARNESS_CHECK_CMD:-make check}"

# Every decision run goes through the trusted launcher harness/run_check.sh (issue
# #64): it validates the effective Makefile, runs the check in a controlled
# environment (the _clean_env.sh drops of issues #27/#31 included) and warns on
# anything it does not trust (WARN rollout: its exit code is the check's own).
RUN_CHECK="$HARNESS_DIR/run_check.sh"
# $1 = log file. Returns the check's exit code.
run_check() { bash "$RUN_CHECK" --decision --log "$1"; }

# Green record (issue #55): .git/harness-check.ok holds the `_guard.py fingerprint`
# of the last state a real, clean `make check` passed on (the format the Stop hook
# reads). Only the launcher writes it (run_check.sh --record): the default command
# (no HARNESS_CHECK_CMD override), green, and the same fingerprint before and after
# the run. The counterfactual and third runs never record. Any other outcome removes
# the record, so it never outlives a red or unverifiable run of the same state.
. "$HARNESS_DIR/_clean_env.sh"   # CLEAN_ENV for the issue gate's third run
GREEN_RECORD="$(git rev-parse --git-path harness-check.ok 2>/dev/null)"
fingerprint() { python3 "$HARNESS_DIR/_guard.py" fingerprint 2>/dev/null; }
# Exit 0 when no tracked file is marked assume-unchanged or skip-worktree: those
# flags hide edits from git status and the fingerprint, so the check would read
# content the record does not describe. An unreadable index counts as flagged.
index_flags_clear() {
  local flags
  flags="$(git ls-files -v 2>&1)" || return 1
  ! printf '%s\n' "$flags" | grep -E '^([a-z]|S)' >/dev/null
}
# $1 = log file. Returns the check's exit code.
run_check_recorded() { bash "$RUN_CHECK" --decision --record --log "$1"; }
# PR barrier: exit 0 only when the green record matches the exact current state.
green_record_current() {
  local fp
  fp="$(fingerprint)" || return 1
  [ -n "$fp" ] && [ -n "$GREEN_RECORD" ] && [ -f "$GREEN_RECORD" ] &&
    [ "$(cat "$GREEN_RECORD")" = "$fp" ]
}
# $1 = log file, $2 = target (roadmap | phase:NN | issue:ISS-n).
run_check_assuming() { bash "$RUN_CHECK" --assume "$2" --log "$1"; }
# The C template's test list: "<origin of TESTS>" then one file per line.
read_tests_var() {
  local mk rc
  mk="$(mktemp "${TMPDIR:-/tmp}/harness-tests.XXXXXX")"
  printf '%s\n' '__harness_print_tests:' "	@echo '\$(origin TESTS)'" \
    "	@printf '%s\\n' \$(TESTS)" >"$mk"
  "${CLEAN_ENV[@]}" make -s --no-print-directory -f Makefile -f "$mk" __harness_print_tests \
    2>/dev/null </dev/null
  rc=$?; rm -f "$mk"; return $rc
}
# Issue gate, third run (issue #31): `make check` assuming the issue approved, with the
# test files of T (one per line in $3) excluded by code: pytest --ignore for Python,
# a `TESTS=` override on the command line for a C Makefile that assigns TESTS.
# $1 = log, $2 = target. Returns 99 when the exclusion cannot be applied.
run_check_tolerating() {
  local log="$1" target="$2" files="$3" label out origin keep=() t f skip args=()
  # The build and test configuration must be what the last approved close committed
  # (the leader cannot commit outside it): a changed or new Makefile, pyproject.toml,
  # setup.cfg, tox.ini, pytest.ini or conftest.py (git-ignored ones too) means no
  # third run.
  local cfg
  cfg="$(env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE git status --porcelain \
    --ignored --untracked-files=all -- Makefile GNUmakefile makefile pyproject.toml setup.cfg \
    tox.ini pytest.ini ':(glob)**/conftest.py' ':(exclude,glob)**/.venv/**' \
    ':(exclude,glob)**/venv/**' ':(exclude,glob)**/node_modules/**' 2>&1)" || return 99
  [ -z "$cfg" ] || return 99
  # Index flags (assume-unchanged, skip-worktree) hide changes from git status:
  # compare each tracked configuration file's content with its blob in HEAD too.
  local rel want have
  while IFS= read -r -d '' rel; do
    want="$(git rev-parse -q --verify "HEAD:./$rel" 2>/dev/null)" || return 99
    have="$(git hash-object -- "$rel" 2>/dev/null)" || return 99
    [ "$want" = "$have" ] || return 99
  done < <(env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE git ls-files -z -- Makefile \
    GNUmakefile makefile pyproject.toml setup.cfg tox.ini pytest.ini ':(glob)**/conftest.py')
  # GNU make prefers GNUmakefile and makefile over Makefile: the probe and the
  # check must read the same file, so only a lone Makefile gets a third run.
  if [ -e GNUmakefile ] || [ -e makefile ] || [ ! -f Makefile ]; then return 99; fi
  label="$(printf '%s' "$files" | paste -sd, -)"
  out="$(read_tests_var)" || out=""
  origin="$(printf '%s\n' "$out" | head -1)"
  if [ "$origin" = "file" ]; then
    python3 "$HARNESS_DIR/_expect_red.py" c-makefile-ok || return 99
    while IFS= read -r t; do
      [ -n "$t" ] || continue
      skip=""
      while IFS= read -r f; do [ "$t" = "$f" ] || [ "$t" = "./$f" ] && skip=1; done <<<"$files"
      [ -n "$skip" ] || keep+=("$t")
    done < <(printf '%s\n' "$out" | tail -n +2)
    # Behaviour, not shape: the dry run of check must differ only by the files of T.
    local dry; dry="$(mktemp -d "${TMPDIR:-/tmp}/harness-dry.XXXXXX")"
    printf '%s\n' "$files" >"$dry/t"
    printf '%s\n' "$out" | tail -n +2 | grep -v '^$' | paste -sd' ' - >"$dry/every"
    printf '%s\n' "${keep[*]}" >"$dry/kept"
    "${CLEAN_ENV[@]}" make -n -s --no-print-directory check >"$dry/full" 2>&1 </dev/null
    "${CLEAN_ENV[@]}" make -n -s --no-print-directory check "TESTS=${keep[*]}" \
      >"$dry/reduced" 2>&1 </dev/null
    if ! python3 "$HARNESS_DIR/_expect_red.py" same-recipes "$dry/full" "$dry/reduced" "$dry/t" \
        "$dry/every" "$dry/kept"; then
      rm -rf "$dry"; return 99
    fi
    rm -rf "$dry"
    # Evidence is best effort: without a temp file the run is not instrumented.
    local ev rc cenv=()
    ev="$(mktemp "${TMPDIR:-/tmp}/harness-clog.XXXXXX" 2>/dev/null)" || ev=""
    [ -z "$ev" ] || cenv=(HARNESS_ORACLE_CLOG="$ev")
    "${CLEAN_ENV[@]}" HARNESS_ASSUME_APPROVED="$target" HARNESS_EXPECT_RED="$label" \
      "${cenv[@]}" make check "TESTS=${keep[*]}" >"$log" 2>&1 </dev/null
    rc=$?
    if [ -n "$ev" ]; then third_run_evidence "$log" "$label" --clog "$ev"; rm -f -- "$ev"
    else third_run_evidence "$log" "$label"; fi
    return $rc
  elif [ "$origin" = "undefined" ] && [ -f pyproject.toml ]; then
    while IFS= read -r f; do [ -n "$f" ] && args+=("--ignore=$f"); done <<<"$files"
    # Evidence is best effort: without a temp dir (or its quoting) the run keeps only
    # its --ignore options, and the evidence step reports "cannot establish".
    local ev rc q="" opts="${args[*]}"
    ev="$(mktemp -d "${TMPDIR:-/tmp}/harness-junit.XXXXXX" 2>/dev/null)" || ev=""
    [ -z "$ev" ] || q="$(python3 -E -s -c 'import shlex,sys; print(shlex.quote(sys.argv[1]))' \
      "$ev/junit.xml")" || q=""
    [ -z "$q" ] || opts="$opts --junitxml=$q -o junit_family=xunit1"
    "${CLEAN_ENV[@]}" HARNESS_ASSUME_APPROVED="$target" HARNESS_EXPECT_RED="$label" \
      PYTEST_ADDOPTS="$opts" make check >"$log" 2>&1 </dev/null
    rc=$?
    if [ -n "$q" ]; then third_run_evidence "$log" "$label" --junit "$ev/junit.xml"
    else third_run_evidence "$log" "$label"; fi
    [ -z "$ev" ] || rm -rf -- "$ev"
    return $rc
  else
    return 99
  fi
}
# Oracle evidence of the third run (issue #65, WARN): only the validated expected-red
# set T may be missing; it is appended to the log and never changes the run's result.
third_run_evidence() {
  local log="$1" label="$2"; shift 2
  python3 "$HARNESS_DIR/_oracle.py" evidence "$@" --expect-red "$label" >>"$log" 2>&1 || true
}
new_log() { mktemp "${TMPDIR:-/tmp}/harness-check-$1.XXXXXX"; }

# Code-gate attempt record (issue #39, lib/_gate_run.py): every code-gate attempt
# is recorded under .git/harness-gate-runs/ with the check result and fingerprints
# of the implementation (files: + tests/unit/) and the toolkit.
GATE_RUN_PY="$HARNESS_DIR/_gate_run.py"
# $1 = id, $2 = check rc, $3 = log. A red check stops the code gate here: no
# reviewer, no verdict, no round (never called in plan mode nor the re-gate).
code_gate_record() {
  local rc
  python3 "$GATE_RUN_PY" record "$1" "$2" "$3" "$$"; rc=$?
  [ "$rc" -eq 0 ] && return 0
  if [ "$rc" -eq 10 ]; then
    fail "$CHECK_CMD is red (exit $2): the code review was not launched (log: $3)" >&2
  fi
  exit 1
}
# After a final code verdict: an APPROVED one binds the recorded attempt.
code_gate_bind() {
  if [ "$2" = "APPROVED" ]; then
    python3 "$GATE_RUN_PY" approve "$1" "$$" ||
      warn "could not bind the approval of $1 to its gate record: re-run the code gate" >&2
  else
    python3 "$GATE_RUN_PY" drop "$1"
  fi
}
# The close refuses an approval that no longer covers the tree.
code_gate_fresh() { python3 "$GATE_RUN_PY" fresh "$1"; }

# Commit a close: the documenter's message, only with the check green.
close_commit() {
  local id="$1" msg="progress/close_$1.msg" log text
  python3 "$HARNESS_DIR/_guard.py" commit-message "$msg" "$id" || return 1
  code_gate_fresh "$id" || return 1
  log="$(new_log close)"
  if ! run_check_recorded "$log"; then
    fail "$CHECK_CMD is red after the close of $id: nothing committed (log: $log)" >&2
    tail -n 30 "$log" >&2
    return 1
  fi
  # The check itself may rewrite files: the approval must still cover what is staged.
  code_gate_fresh "$id" || return 1
  # Stage only the subtask's manifest (issue #40): a foreign path in the tree or the
  # index blocks the close with its reason; the manifest is never widened.
  local scope_py="$HARNESS_DIR/_close_scope.py" scope
  if ! python3 "$scope_py" check "$id"; then
    fail "the close of $id touches paths outside its manifest: nothing committed" >&2
    return 1
  fi
  text="$(cat "$msg")"
  rm -f "$msg"  # never part of the commit: list the manifest without it
  scope="$(new_log scope)"
  if ! python3 "$scope_py" files "$id" >"$scope" || { [ -s "$scope" ] &&
      ! GIT_LITERAL_PATHSPECS=1 git add -A --pathspec-from-file="$scope" --pathspec-file-nul; }
  then
    rm -f "$scope"
    printf '%s\n' "$text" >"$msg"
    fail "could not stage the manifest of $id: nothing committed" >&2
    return 1
  fi
  rm -f "$scope"
  if ! printf '%s\n' "$text" | git commit -q -F -; then
    printf '%s\n' "$text" >"$msg"  # kept for the retry
    fail "git commit failed for the close of $id: message restored to $msg" >&2
    return 1
  fi
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
  # PR barrier (issue #55): never push or open a PR for a state without a green
  # full check. The Stop hook only warns; this is where the check is enforced.
  # git push sends only HEAD: uncommitted changes (untracked included) would let a
  # green check of the working tree stand for a commit it never ran on.
  if [ -n "$(git status --porcelain -uall 2>&1)" ]; then
    fail "the tree has uncommitted changes: the PR for task $tid was not pushed or" \
      "opened. Commit or remove them first; $retry" >&2
    return 1
  fi
  # Index flags hide edits from git status and the fingerprint alike: the check
  # would read files whose content is not what gets pushed.
  if ! index_flags_clear; then
    fail "files marked assume-unchanged or skip-worktree: the PR for task $tid was not" \
      "pushed or opened. Clear the flags (git update-index --no-assume-unchanged" \
      "--no-skip-worktree <file>) first; $retry" >&2
    return 1
  fi
  # Branch guard (issue #80): only the branch the task/issue lives on, never the
  # base or main/master, never one carrying base commits origin does not have.
  if ! python3 "$PR_PY" guard "$tid" "$base"; then
    fail "the PR for task $tid was not pushed or opened; $retry" >&2
    return 1
  fi
  case "$branch" in main|master|"$base"|HEAD)
    fail "refusing to push $branch for task $tid; $retry" >&2; return 1 ;;
  esac
  if ! green_record_current; then
    # No record of this exact state: run the real, clean check here (the same
    # producer as gates and close) and go on only if it records green.
    local log
    log="$(new_log pr)"
    if ! run_check_recorded "$log" || ! green_record_current; then
      fail "no green make check for this exact state (log: $log): the PR for task $tid" \
        "was not pushed or opened. Fix the tree until \`make check\` is green; $retry" >&2
      tail -n 30 "$log" >&2
      return 1
    fi
  fi
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
  # The close commit moved HEAD, so the record of the pre-commit check no longer
  # matches: check the committed state again (only when a PR is due).
  local log
  log="$(new_log pr)"
  if ! run_check_recorded "$log"; then
    warn "$CHECK_CMD is red after the close commit: the PR for task $tid was not opened" \
      "(log: $log). Fix it until \`make check\` is green, then:" \
      "./harness/review_with_codex.sh pr $tid" >&2
    return 0
  fi
  open_pr "$tid" || true
}

case "$PHASE" in
  seal)    exec python3 "$HARNESS_DIR/_guard.py" seal "$@" ;;
  close)   [ -n "${1:-}" ] || usage; close_commit "$1" || exit 1; open_pr_if_due "$1"; exit 0 ;;
  pr)      [ -n "${1:-}" ] || usage; open_pr "$1"; exit $? ;;
  # Selection contract (issue #65, I4b): Codex reviews progress/oracle_contract.json,
  # bound to its diff hash; the human then runs _oracle.py contract-approve <hash>.
  contract) exec python3 "$HARNESS_DIR/_oracle.py" contract-review ;;
  roadmap) ID="roadmap"; ROLE="$CODEX_DIR/codex-planner.md" ;;
  phase)   ID="${1:-}"; ROLE="$CODEX_DIR/codex-planner.md" ;;
  task|task-close|phase-close) ID="${1:-}"; ROLE="$CODEX_DIR/codex-planner.md" ;;
  plan|code) ID="${1:-}"; ROLE="$CODEX_DIR/reviewer.md" ;;
  issue)   ID="${1:-}"; [[ "$ID" =~ ^[0-9]+$ ]] && ID="ISS-$ID"; ROLE="$CODEX_DIR/reviewer.md" ;;
  issue-draft) ID="draft"; ROLE="$CODEX_DIR/reviewer.md" ;;
  doc)     ID="${1:-}"; ROLE="$CODEX_DIR/documenter.md" ;;
  *) usage ;;
esac
[ -n "$ID" ] || usage
# A plan gate run never reuses an older red record (issue #65): it is dropped before
# anything that can exit, and only an approval that sealed the oracle writes a new one.
[ "$PHASE" != "plan" ] || python3 "$HARNESS_DIR/_oracle.py" red-drop "$ID" || true
CODEX_PHASE="$PHASE"
[ -f "$ROLE" ] || { echo "role prompt not found: $ROLE" >&2; exit 1; }

# Preconditions: never review a step whose prerequisite is not approved yet.
case "$PHASE" in
  roadmap) python3 "$HARNESS_PY" roadmap-ready || exit 1 ;;
  phase)   python3 "$HARNESS_PY" phase-ready "$ID" || exit 1 ;;
  task)    python3 "$HARNESS_PY" task-ready "$ID" || exit 1 ;;
  task-close)  python3 "$HARNESS_PY" task-close-ready "$ID" || exit 1 ;;
  phase-close) python3 "$HARNESS_PY" phase-close-ready "$ID" || exit 1 ;;
  issue)   python3 "$HARNESS_PY" issue-ready "$ID" || exit 1 ;;
  doc)     python3 "$HARNESS_PY" close-ready "$ID" || exit 1
           code_gate_fresh "$ID" || exit 1 ;;
  plan)    python3 "$HARNESS_PY" subtask-ready "$ID" || exit 1
           python3 "$HARNESS_DIR/_guard.py" migrates-ready "$ID" || exit 1 ;;
  issue-draft)
    [ -f progress/issue_draft.md ] || {
      fail "no progress/issue_draft.md: the leader writes the draft first" >&2; exit 1; } ;;
esac

# Round cap (issue #42): after harness.toml [review] max_rounds rejections in a row of
# this gate on this id, stop and hand over to the human. No env override on purpose.
if [ "$PHASE" != "doc" ]; then
  python3 "$PR_PY" round-cap "$PHASE" "$ID" || exit 1
fi

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
  task)
    TARGET="the breakdown of task $ID: its subtask specs ($(python3 "$HARNESS_PY" close-sources "$ID" 2>/dev/null | grep '^docs/' | paste -sd' ' -))"
    VERDICT_FILE="progress/task_review_$ID.md"; SEAL_ARGS=(task "$ID"); SEAL_ON="TASK_APPROVED" ;;
  task-close)
    TARGET="the delivered work of task $ID against its goal (its phase file) and its subtask specs"
    VERDICT_FILE="progress/task_close_$ID.md"; SEAL_ARGS=(); SEAL_ON="" ;;
  phase-close)
    TARGET="the delivered work of phase $ID against its goal in docs/plan/PLAN.md and its phase file"
    VERDICT_FILE="progress/phase_close_$ID.md"; SEAL_ARGS=(); SEAL_ON="" ;;
  issue)
    ISSUE_FILE="$(ls docs/plan/issues/"$ID"-*.md 2>/dev/null | head -1)"
    TARGET="the breakdown of issue $ID: ${ISSUE_FILE:-docs/plan/issues/$ID-<slug>.md} (header) and every subtask spec in ${ISSUE_FILE%.md}/"
    VERDICT_FILE="progress/issue_review_$ID.md"; SEAL_ARGS=(issue "$ID"); SEAL_ON="ISSUE_APPROVED" ;;
  issue-draft)
    TARGET="the draft of a new GitHub issue: progress/issue_draft.md (front matter + body)"
    VERDICT_FILE="progress/issue_draft_review.md"; SEAL_ARGS=(draft); SEAL_ON="DRAFT_APPROVED" ;;
  *)
    TARGET="subtask $ID, spec: $(python3 "$HARNESS_PY" spec "$ID" 2>/dev/null)"
    SEAL_ARGS=("$ID"); SEAL_ON=""
    case "$PHASE" in
      plan) VERDICT_FILE="progress/plan_review_$ID.md"; SEAL_ON="PLAN_APPROVED" ;;
      code) VERDICT_FILE="progress/review_$ID.md" ;;
      doc)  VERDICT_FILE="" ;;
    esac ;;
esac
FEATURE_NAME="$(python3 "$HARNESS_PY" feature-name "$ID" 2>/dev/null)"

CHECK_LOG="$(new_log "$PHASE")"
# Close gates (issue #62) read a state the last close already checked: when the green
# record matches this exact state, reuse it instead of a second full check.
if { [ "$PHASE" = "task-close" ] || [ "$PHASE" = "phase-close" ]; } && green_record_current; then
  printf 'reused the green record %s (fingerprint %s): no new %s run\n' \
    "$GREEN_RECORD" "$(cat "$GREEN_RECORD")" "$CHECK_CMD" >"$CHECK_LOG"
  ok "reused the green record: $CHECK_CMD already passed on this exact state"
  CHECK_RC=0
else
  run_check_recorded "$CHECK_LOG"
  CHECK_RC=$?
fi
[ "$PHASE" != "code" ] || code_gate_record "$ID" "$CHECK_RC" "$CHECK_LOG"
# Red evidence (issue #65, I4b, WARN): the code gate needs the plan gate's record of the
# oracle collected and failing, with the oracle and selection inputs unchanged since.
[ "$PHASE" != "code" ] || python3 "$HARNESS_DIR/_oracle.py" red-check "$ID" || true

# Re-gate (issue #27): a red check of an approved roadmap/phase/issue may come only
# from failures this same approval clears (its seal, its plan-rule lines). Run the
# check again assuming exactly this target approved; the reviewer may approve a red
# first run only if this one is green. A HARNESS_CHECK_CMD override gets no second
# run (fail-closed: the harness cannot know what it checks).
# In-progress re-gate (issue #31): with the counterfactual run red too, an issue gate
# computes T (_expect_red.py) and runs the check a third time without T. Any
# ineligible `migrates:` entry, an empty T or a project whose tests cannot be
# filtered means no third run (fail-closed, the #27 rule applies).
third_run_step() {
  local iid="$1" target="$2" files rc log
  files="$(python3 "$HARNESS_DIR/_expect_red.py" "$iid" 2>&1)"; rc=$?
  if [ "$rc" -eq 3 ]; then
    printf '\n%s\n%s\n%s' \
      "No third run, so the red counterfactual run blocks: T cannot be computed safely" \
      "(ineligible migrates: entries, or paths the gate cannot exclude exactly):" "$(printf '%s\n' "$files" | sed 's/^/  - /')"
    return 0
  fi
  [ "$rc" -eq 0 ] && [ -n "$files" ] || return 0
  log="$(new_log "$PHASE-expect-red")"
  run_check_tolerating "$log" "$target" "$files"; rc=$?
  if [ "$rc" -eq 99 ]; then
    rm -f "$log"
    printf '\n%s' "No third run: this project's tests cannot be filtered by file."
    return 0
  fi
  printf '\n%s\n%s\n%s\n%s\n%s\n%s\n%s\n%s\n%s' \
    "Third run (in-progress re-gate, issue mode): \`$CHECK_CMD\` again with" \
    "HARNESS_ASSUME_APPROVED=$target and HARNESS_EXPECT_RED=$(printf '%s' "$files" | paste -sd, -) (the gate" \
    "excluded these test files of T from the tests; their lint and build still ran in the first two runs):" \
    "$(printf '%s\n' "$files" | sed 's/^/  - /')" \
    "exit code $rc, full output in $log." \
    "With the counterfactual run red you may approve only if this run is green AND your" \
    "verdict accounts for every test in every file of T, not only the failures in the logs" \
    "(read each file and the subtask's changes; see your role's in-progress re-gate rule);" \
    "list T, the first run's FAIL lines and that per-file accounting."
}
ASSUME_STEP=""
if [ "$CHECK_RC" -ne 0 ] && [ -z "${HARNESS_CHECK_CMD+set}" ]; then
  ASSUME=""
  case "$PHASE" in
    roadmap) ASSUME="roadmap" ;;
    phase)   ASSUME="phase:$ID" ;;
    task)    ASSUME="task:$ID" ;;
    issue)   ASSUME="issue:$ID" ;;
  esac
  if [ -n "$ASSUME" ]; then
    ASSUME_LOG="$(new_log "$PHASE-assumed")"
    run_check_assuming "$ASSUME_LOG" "$ASSUME"
    ASSUME_RC=$?
    ASSUME_STEP="
Counterfactual run (re-gate): \`$CHECK_CMD\` again with HARNESS_ASSUME_APPROVED=$ASSUME
(only this target's verdict reads approved and only its seal reads matching):
exit code $ASSUME_RC, full output in $ASSUME_LOG.
With the first run red you may approve only if this run is green; list the first
run's FAIL lines in your verdict."
    if [ "$ASSUME_RC" -ne 0 ] && [ "$PHASE" = "issue" ]; then
      ASSUME_STEP="$ASSUME_STEP$(third_run_step "$ID" "$ASSUME")"
    fi
  fi
fi
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

# What Codex reads. The close gates (issue #62) read only their specs, reports and
# the diff of their close commits: never the whole repo (cost limit).
CONTEXT="Context you must read: AGENTS.md, docs/architecture.md, docs/conventions.md,
CHECKPOINTS.md, feature_list.json, and progress/current.md."
case "$PHASE" in
  task-close|phase-close)
    GOAL_FILE="$(ls docs/plan/phase-"${ID%%.*}"-*.md docs/plan/phase-0"${ID%%.*}"-*.md 2>/dev/null | head -1)"
    [ "$PHASE" = "task-close" ] || GOAL_FILE="docs/plan/PLAN.md $GOAL_FILE"
    SOURCES="$(python3 "$HARNESS_PY" close-sources "$ID" 2>/dev/null | sed 's/^/  - /')"
    COMMITS="$(python3 "$HARNESS_PY" close-commits "$ID" 2>/dev/null)"
    MISSING="$(python3 "$HARNESS_PY" close-missing "$ID" 2>/dev/null | paste -sd' ' -)"
    if [ -n "$COMMITS" ]; then
      SHOW="git show --stat --patch $(printf '%s\n' "$COMMITS" | paste -sd' ' -)"
    else
      SHOW="(no close commit found: say so in your verdict)"
    fi
    CONTEXT="Read ONLY these, nothing else of the repo (cost limit):
  - goal: $GOAL_FILE
$SOURCES
  - the diff of its close commits: $SHOW
Write the traceability matrix your role describes (one row per goal point and per
acceptance criterion: test, met/gap); any gap means CHANGES_REQUESTED.${MISSING:+
Subtasks with NO close commit (each is a gap row; the harness rejects an approval
while any remains): $MISSING}" ;;
esac

# Build the concrete instruction for Codex.
PROMPT="$(cat <<EOF
Act strictly according to your role defined in: $ROLE
Phase: $CODEX_PHASE
Feature: id=$ID name=$FEATURE_NAME
Review: $TARGET
Project root: $PROJECT_DIR

$CONTEXT

Test suite: this sandbox cannot run it (local sockets are blocked).
Do NOT run \`make check\`, \`make verify\`, the tests or anything that runs them.
The harness ran \`$CHECK_CMD\` outside the sandbox just before this session:
exit code $CHECK_RC, full output in $CHECK_LOG.
That result is the one AGENTS.md step 1 asks for.$ASSUME_STEP$CLOSE_STEP

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
  [ -z "$VERDICT_FILE" ] || echo "--- verdict file: $VERDICT_FILE ---"
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
    echo "--- after Codex writes the verdict, write its receipt (human only) ---"
    echo "python3 harness/_verdict.py receipt $PHASE $ID"
    echo "--- then keep it in the review history ---"
    echo "python3 harness/_pr.py record $PHASE $ID $VERDICT_FILE"
    if [ "$PHASE" = "code" ]; then
      echo "--- if APPROVED, bind it to this gate's record (the close needs it) ---"
      echo "python3 harness/_gate_run.py approve $ID $$"
    fi
  fi
  exit 0
fi

if ! command -v codex >/dev/null 2>&1; then
  fail "codex is not in PATH. Use HARNESS_CODEX_MODE=manual or install Codex." >&2
  exit 1
fi

# A review gate only succeeds on a verdict written by this run (I2): the previous
# verdict is set aside first and restored on any failure; see _verdict.py.
VERDICT_PY="$HARNESS_DIR/_verdict.py"
verdict_abort() { python3 "$VERDICT_PY" abort --owner $$ "$PHASE" "$VERDICT_FILE"; }
if [ "$PHASE" != "doc" ]; then
  PREV_COPY="$(python3 "$VERDICT_PY" begin --owner $$ "$PHASE" "$VERDICT_FILE")" || exit 1
  # The reviewer runs in the foreground, so bash runs these after it exits.
  trap verdict_abort EXIT
  trap 'exit 143' TERM
  trap 'exit 130' INT
  if [ -n "$PREV_COPY" ]; then
    PROMPT="$PROMPT
Previous verdict (read-only copy): $PREV_COPY
It is the last round of this gate; write the new verdict to $VERDICT_FILE."
  fi
fi

# Non-interactive execution via `codex exec`. The prompt goes in as a here-string,
# not through a pipeline, so RC is the reviewer's own exit status.
codex exec "${CODEX_ARGS[@]}" <<<"$PROMPT"
RC=$?

if [ "$PHASE" = "doc" ]; then
  [ $RC -eq 0 ] || exit $RC
  close_commit "$ID" || exit 1
  open_pr_if_due "$ID"
  exit 0
fi

if [ $RC -ne 0 ]; then
  fail "the reviewer exited $RC: $VERDICT_FILE not accepted, nothing recorded or sealed" >&2
  exit $RC
fi
# Validate and make the verdict final (receipt included); an invalid one is undone.
TOKEN="$(python3 "$VERDICT_PY" commit --owner $$ "$PHASE" "$VERDICT_FILE")" || exit 1
trap - EXIT TERM INT
[ "$PHASE" != "code" ] || code_gate_bind "$ID" "$TOKEN"

# From here the verdict is final and never undone; re-running the gate repairs a
# failed record or seal.
# Keep every round, rejected ones included: the next verdict overwrites the file.
python3 "$PR_PY" record "$PHASE" "$ID" "$VERDICT_FILE" ||
  warn "could not record $VERDICT_FILE in progress/review_history/" >&2

# Seal what Codex just approved (roadmap, phase breakdown, or the subtask's
# acceptance tests + spec): check.sh then fails if it changes afterwards,
# whichever tool changed it.
if [ -n "$SEAL_ON" ] && [ "$TOKEN" = "$SEAL_ON" ]; then
  python3 "$HARNESS_DIR/_guard.py" seal "${SEAL_ARGS[@]}" || exit 1
  # After Codex wrote the oracle: run it alone and record it collected and failing,
  # with the check result and the oracle/selection fingerprints (WARN only).
  if [ "$PHASE" = "plan" ]; then
    python3 "$HARNESS_DIR/_oracle.py" red-record "$ID" "$CHECK_RC" || true
  fi
fi
exit 0
