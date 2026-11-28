#!/usr/bin/env bash
# The single entry point through which Claude (leader) invokes Codex.
#   review_with_codex.sh stack                Codex reviews docs/stack.md (issue #115)
#   review_with_codex.sh roadmap              Codex reviews PLAN.md + every phase (scope)
#   review_with_codex.sh releases             Codex reviews docs/releases.md (release plan)
#   review_with_codex.sh phase <NN>           Codex reviews the breakdown of phase NN (tasks)
#   review_with_codex.sh task <N.M>           Codex reviews the subtask specs of task N.M
#   review_with_codex.sh task-close <N.M>     Codex checks a finished task against its goal
#   review_with_codex.sh phase-close <NN>     Codex checks a finished phase against PLAN.md
#   review_with_codex.sh issue <ISS-n>        Codex reviews the breakdown of issue ISS-n
#   review_with_codex.sh issue-draft          Codex reviews progress/issue_draft.md
#   review_with_codex.sh <plan|code|doc> <N.M.K|ISS-n.k>   per-subtask gates and close
#   review_with_codex.sh close <N.M.K>        check + commit a close (after manual `doc`)
#   review_with_codex.sh pr <N.M|ISS-n>       push the task/issue branch and open its PR
#   review_with_codex.sh seal <N.M.K|roadmap|stack|releases|phase NN|task N.M|issue ISS-n|draft>
#                                             (human, manual mode: seal)
#
# Each gate checks its preconditions first (roadmap approved before a phase gate,
# phase approved before a subtask plan gate) and, when Codex approves, seals what
# it approved so `make check` catches any later silent change.
#
# Reads the model and effort from the role prompt header (codex/*.md) and passes
# them to the codex CLI; a subtask's plan/code gates may raise the effort and add
# checklists by its risk/surfaces rating (issue #44, _review_risk.py), and so may the
# phase/task/issue gates by the strictest rating of their subtasks (issue #107).
# Execution mode via harness.toml [codex] mode:
#   auto   (default) → runs codex
#   manual           → prints the command and prompt for you to launch it yourself
#
# Sandbox (least privilege by default), harness.toml [codex]:
#   sandbox  workspace-write (default) | read-only | danger-full-access
#   network  true → allow network inside workspace-write (default: false)
# Issue #90: the old HARNESS_CODEX_MODE/_SANDBOX/_NETWORK, HARNESS_GH and
# HARNESS_AUTO_PR environment overrides are ignored (a warning names each): the
# environment is the caller's, harness.toml is the human's.
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
# one PR for the task with `gh` (harness.toml [git] auto_pr or [pr] auto = false
# turns it off; [pr] gh sets the gh binary). A failed push or PR never
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
cd "$PROJECT_DIR" || exit 1
# Run marker (issue #46): resume waits while any gate run of this project is alive,
# whatever its mode or stage. A dead run's marker is ignored and later rotated.
_RUNS="$(git rev-parse --git-path harness-gate-runs 2>/dev/null)" &&
  mkdir -p "$_RUNS" && : >"$_RUNS/run_$$" 2>/dev/null
unset _RUNS

# Gate controls (issue #90): from harness.toml only. A control variable in the
# environment is dropped with one warning, so no child script can read it either.
for _v in HARNESS_CODEX_MODE HARNESS_CODEX_SANDBOX HARNESS_CODEX_NETWORK HARNESS_GH \
  HARNESS_AUTO_PR; do
  if [ -n "${!_v+set}" ]; then
    warn "review_with_codex: $_v from the environment is ignored — fix: unset $_v;" \
      "the setting lives in harness.toml ([codex] mode/sandbox/network, [pr] auto/gh)" >&2
    unset "$_v"
  fi
done
unset _v
_CONTROL="$(python3 "$HARNESS_DIR/_config.py" control)" || exit 1
IFS=$'\t' read -r MODE_EXEC SANDBOX NETWORK _ GH <<<"$_CONTROL"
unset _CONTROL
# _oracle.py contract-review reads the mode; set from harness.toml, never inherited.
export HARNESS_CODEX_MODE="$MODE_EXEC"

usage() {
  echo "usage: review_with_codex.sh stack | roadmap | releases | phase <NN> | task <N.M>" \
       "| task-close <N.M>" \
       "| phase-close <NN> | issue <ISS-n> | issue-draft" \
       "| <plan|code|doc> <N.M.K|ISS-n.k> | close <N.M.K> | pr <N.M> | pr <ISS-n>" \
       "| seal <N.M.K|roadmap|stack|releases|phase NN|task N.M|issue ISS-n|draft>" \
       "| contract" >&2
  exit 2
}
PHASE="${1:-}"; shift || true
HARNESS_PY="$HARNESS_DIR/_harness.py"
PR_PY="$HARNESS_DIR/_pr.py"
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
  # setup.cfg, tox.ini, pytest.ini or conftest.py (git-ignored ones too, and tracked
  # ones hidden by index flags) means no third run. Issue #125 (B1-config): except a
  # file in the `files:` of the in-progress subtask, never a conftest.py; the prompt
  # lists it for the reviewer (TOLERATED_CFG).
  TOLERATED_CFG="$(python3 "$HARNESS_DIR/_expect_red.py" config-ok 2>/dev/null)" ||
    { TOLERATED_CFG=""; return 99; }
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
    # Excluded tests lower the coverage by design: the threshold is off for this run
    # only (the Python profile's `make test` still measures and reports it).
    "${CLEAN_ENV[@]}" HARNESS_ASSUME_APPROVED="$target" HARNESS_EXPECT_RED="$label" \
      HARNESS_NO_COV_THRESHOLD=1 PYTEST_ADDOPTS="$opts" make check >"$log" 2>&1 </dev/null
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
# Attempt ledger and budget (issue #46, lib/_resume.py): every attempt of a step
# (a Codex gate in auto mode, the close, the PR) on one id is recorded under
# .git/harness-gate-runs/attempts_<step>_<id>.json. Only an outcome (a final verdict,
# the close commit, the PR) ends one well; after harness.toml [review] max_attempts
# failed ones in a row the step is refused before it launches (the human decides).
RESUME_PY="$HARNESS_DIR/_resume.py"
# $1 = step, $2 = id. Checks the budget and records the start in one locked step;
# exit 1 (with the reason) when the budget is used up or the ledger cannot be read
# or written (fail closed: a broken ledger must not lift the budget).
attempt_begin() { python3 "$RESUME_PY" begin "$1" "$2" "$$"; }
# $1 = step, $2 = id, $3 = ok|fail, $4 = reason. The outcome already happened, so a
# write failure cannot undo it: it is reported for the human to repair.
attempt_end() {
  python3 "$RESUME_PY" end "$1" "$2" "$$" "$3" "${4:-}" ||
    fail "could not record the outcome ($3) of the $1 attempt on $2: the human checks" \
      "$(git rev-parse --git-path harness-gate-runs 2>/dev/null)" >&2
}

# $1 = id, $2 = check rc, $3 = log. A red check stops the code gate here: no
# reviewer, no verdict, no round (never called in plan mode nor the re-gate).
code_gate_record() {
  local rc
  python3 "$GATE_RUN_PY" record "$1" "$2" "$3" "$$"; rc=$?
  [ "$rc" -eq 0 ] && return 0
  if [ "$rc" -eq 10 ]; then
    fail "$CHECK_CMD is red (exit $2): the code review was not launched (log: $3)" >&2
    [ "$MODE_EXEC" != "auto" ] || attempt_end code "$1" fail "check red (exit $2)"
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

# The close message during the commit (I7): it waits in the git dir, where no hook
# can stage it, and goes back to progress/ on any failure or signal; only a
# successful commit removes it. After a SIGKILL the next close (or doc) restores it,
# so the retry reuses it and nothing regenerates it.
close_msg_backup() { git rev-parse --git-path "harness-close-$1.msg" 2>/dev/null; }
# $1 = id. Exit 0 when it put a waiting message back in progress/.
close_msg_recover() {
  local msg="progress/close_$1.msg" bak
  bak="$(close_msg_backup "$1")" && [ -n "$bak" ] && [ -f "$bak" ] || return 1
  # A message in progress/ is newer; one HEAD already carries was committed.
  if [ -e "$msg" ] || [ "$(git log -1 --format=%B 2>/dev/null)" = "$(cat "$bak")" ]; then
    rm -f "$bak"; return 1
  fi
  mkdir -p progress && mv -f "$bak" "$msg" || return 1
  warn "the close of $1 was interrupted: its message is back in $msg" >&2
}

# Doc repair (issue #133): when the leaf's reference doc fails the doc gate, or the
# close owes its changelog fragment (activated project, review rel1rev #4), the
# documenter repairs it in a bounded number of attempts. A repair may change only
# what failed when it started (the doc, the owner's fragment, or both): the tree (HEAD + every tracked and untracked, non-ignored file) is
# snapshotted in the git dir before it, where no Codex sandbox or Claude tool can
# write, and the close refuses (fail closed) while anything else differs. The
# snapshot lives until the close commits; only the human removes it otherwise.
R_DOC="the reference doc fails the doc gate"
R_OUT="the repairs ran out: the reference doc still fails the doc gate"
R_SCOPE="the repair changed files outside the reference doc or fragment it repairs"
R_FRAG="the changelog fragment of the close is missing or invalid"
R_OUT_FRAG="the repairs ran out: the changelog fragment is still missing or invalid"
DOC_REPAIRS=2   # repairs per run; the attempt budget ([review] max_attempts) bounds the streak
doc_repair_snap() { git rev-parse --git-path "harness-doc-repair-$1.json" 2>/dev/null; }
# $1 = id. The one doc a repair may change: the doc gate's own pick, else the spec's name.
repair_doc_path() {
  local doc
  for doc in docs/reference/"$1"-*.md docs/reference/"$1".md; do
    [ -f "$doc" ] && { printf '%s\n' "$doc"; return 0; }
  done
  printf 'docs/reference/%s\n' "$(basename "$(python3 "$HARNESS_PY" spec "$1" 2>/dev/null)")"
}
# $1 = id. The changelog fragment of the leaf's task or issue.
repair_frag_path() { printf 'changelog.d/%s.md\n' "${1%.*}"; }
# $1 = id. Exit 0 when the close owes no fragment; 1 when it owes one (FRAG_FAILS says
# why); 2 when that cannot be told (callers fail closed).
frag_owed() {
  local rc
  FRAG_FAILS="$(python3 "$HARNESS_DIR/_changelog.py" fragment "$1" 2>&1)"; rc=$?
  case "$rc" in
    0) FRAG_FAILS=""; return 0 ;;
    1) return 1 ;;
    *) fail "cannot tell whether the close of $1 owes its changelog fragment:" \
         "$FRAG_FAILS" >&2; return 2 ;;
  esac
}
# $1 = id. What a repair must fix: REPAIR_DOC (the doc, when it fails the doc gate)
# and REPAIR_FRAG (the fragment, when the close owes it), with DOC_FAILS / FRAG_FAILS.
# Exit 0 = nothing to repair, 1 = something, 2 = cannot tell (fail closed).
repair_targets() {
  local rc
  REPAIR_DOC=""; REPAIR_FRAG=""
  if DOC_FAILS="$(bash "$HARNESS_DIR/doc_gate.sh" --leaf "$1" 2>&1)"; then
    DOC_FAILS=""
  else
    REPAIR_DOC="$(repair_doc_path "$1")"
  fi
  frag_owed "$1"; rc=$?
  [ "$rc" -le 1 ] || return 2
  [ "$rc" -eq 0 ] || REPAIR_FRAG="$(repair_frag_path "$1")"
  [ -z "$REPAIR_DOC$REPAIR_FRAG" ] || return 1
}
repair_fails() { printf '%s\n' "${DOC_FAILS:-}" "${FRAG_FAILS:-}" | sed '/^$/d'; }
repair_what() {
  printf '%s\n' "${REPAIR_DOC:-}" "${REPAIR_FRAG:-}" | sed '/^$/d' | paste -sd' ' -
}
# $1 = id, $2 = begin (record, or verify an existing snapshot) | check (verify if any).
# The snapshot holds the allowed paths (REPAIR_DOC / REPAIR_FRAG when it was recorded),
# HEAD, the index and the tree's files.
doc_repair_tree() {
  python3 -I -c '
import hashlib, json, os, re, subprocess, sys
sid, mode, snap, doc, frag = sys.argv[1:6]
def git(*args):
    return subprocess.run(["git", *args], capture_output=True, check=True).stdout
def state():
    head = git("rev-parse", "HEAD").decode().strip()
    files = {}
    for raw in sorted(set(filter(None, git("ls-files", "-z", "-co", "--exclude-standard").split(b"\0")))):
        p = os.fsdecode(raw)
        if os.path.islink(p):
            files[p] = "link:" + os.readlink(p)
        elif os.path.isfile(p):
            with open(p, "rb") as f:
                files[p] = hashlib.sha256(f.read()).hexdigest()
        else:
            files[p] = None
    index = {}
    for row in filter(None, git("ls-files", "-s", "-z").split(b"\0")):
        meta, _, path = row.partition(b"\t")
        index[os.fsdecode(path)] = meta.decode()
    return {"head": head, "files": files, "index": index}
try:
    if not snap:
        raise OSError("no git dir")
    if not os.path.exists(snap):
        if mode == "check":
            sys.exit(0)
        tmp = snap + ".tmp"
        if doc and not re.fullmatch(r"docs/reference/" + re.escape(sid) + r"(-[^/]*)?\.md", doc):
            raise ValueError(f"unexpected doc path {doc!r}")
        owner = sid.rsplit(".", 1)[0]
        if frag and frag != f"changelog.d/{owner}.md":
            raise ValueError(f"unexpected fragment path {frag!r}")
        if not doc and not frag:
            raise ValueError("nothing to repair")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({**state(), "doc": doc, "fragment": frag}, f)
        os.replace(tmp, snap)
        sys.exit(0)
    with open(snap, encoding="utf-8") as f:
        old = json.load(f)
    allowed = {old.get("doc") or "", old.get("fragment") or ""} - {""}
    if mode == "begin":  # a later repair never widens what the first one may change
        wider = sorted({doc, frag} - {""} - allowed)
        if wider:
            listed = ", ".join(wider)
            raise ValueError(f"the repair snapshot does not cover {listed}")
    new = state()
    bad = [] if old["head"] == new["head"] else ["HEAD (a commit was made)"]
    for p in sorted(set(old["files"]) | set(new["files"])):
        if old["files"].get(p) != new["files"].get(p) and p not in allowed:
            bad.append(p)
    for p in sorted(set(old["index"]) | set(new["index"])):
        if old["index"].get(p) != new["index"].get(p):
            bad.append(f"{p} (git index)")
except Exception as exc:
    print(f"[FAIL] cannot verify the doc repair of {sid} ({exc}): fail closed", file=sys.stderr)
    sys.exit(1)
for p in bad:
    print(f"[FAIL] changed since the repair started: {p}", file=sys.stderr)
sys.exit(1 if bad else 0)
'  "$1" "$2" "$(doc_repair_snap "$1")" "${REPAIR_DOC:-}" "${REPAIR_FRAG:-}"
}
# $1 = id. Exit 0 when the leaf is done in the tree; a helper error fails closed.
leaf_done() {
  local ids
  ids="$(python3 "$HARNESS_PY" done-ids)" || { fail "cannot read the done leaves" >&2; exit 1; }
  printf '%s\n' "$ids" | grep -Fxq -- "$1"
}
# Repair mode of `doc` on a pending close (message waiting, spec done): $1 = id.
doc_repair_ready() {
  local id="$1" subjects rc
  subjects="$(git log --format=%s HEAD 2>/dev/null)" ||
    { fail "cannot read the git log: no repair of $id" >&2; return 1; }
  if printf '%s\n' "$subjects" | grep -Fq -- "[$id]"; then
    fail "$id already has a close commit: no repair. progress/close_$id.msg is stale;" \
      "the human removes it" >&2
    return 1
  fi
  if ! python3 -I -c 'import sys; sys.path.insert(0, sys.argv[1]); import _harness
from pathlib import Path
sys.exit(_harness.read_verdict(Path(sys.argv[2])) != "APPROVED")' "$HARNESS_DIR" \
      "progress/review_$id.md"; then
    fail "$id has no APPROVED code review (progress/review_$id.md): no repair" >&2
    return 1
  fi
  code_gate_fresh "$id" || return 1
  repair_targets "$id"; rc=$?
  [ "$rc" -ne 2 ] || return 1
  if [ "$rc" -eq 0 ]; then
    fail "progress/close_$id.msg is waiting, the reference doc passes the doc gate and no" \
      "changelog fragment is owed: run ./harness/review_with_codex.sh close $id" >&2
    return 1
  fi
  doc_repair_tree "$id" begin
}
# $1 = id, $2 = the failures (doc gate, owed fragment). REPAIR_DOC / REPAIR_FRAG say
# what to fix.
repair_prompt() {
  local id="$1" what="" due_txt
  if [ -n "${REPAIR_DOC:-}" ]; then
    what+="
- edit $REPAIR_DOC so it has the frontmatter keys and the five section anchors of your
  role's step 1, writing every missing section from the spec, the code, the tests and
  progress/review_history/."
  fi
  if [ -n "${REPAIR_FRAG:-}" ]; then
    due_txt="$(python3 "$HARNESS_DIR/_changelog.py" due "$id" 2>/dev/null)"
    what+="
- write $REPAIR_FRAG, the changelog fragment this close owes (your role's step 5; the
  format is in changelog.d/README.md), contrasting the declaration with what was
  delivered (the spec, the code, the tests, progress/review_history/):
$due_txt"
  fi
  cat <<EOF
Act strictly according to your role defined in: $ROLE
Phase: doc (repair)
Feature: id=$id name=$FEATURE_NAME
Project root: $PROJECT_DIR

Repair mode (issue #133): the close of $id already ran (its spec says done and its commit
message waits in progress/close_$id.msg), but it cannot commit yet:
$2

Do only this:$what

Change NO other file: not the spec, PLAN.md, feature_list.json, progress/ (the waiting
commit message included), the code, the tests, any other doc or fragment, or any seal.
Skip the other close steps and write no commit message. The harness verifies that only
$(repair_what) changed and refuses the close otherwise; then it runs the check
and commits with the waiting message.
Do NOT run \`make check\`, the tests or anything that runs them.
Do not add agent attribution. Final line: repaired -> $(repair_what)
EOF
}
# After the documenter (auto mode): repair the leaf's doc while it fails the gate.
doc_repair_loop() {
  local id="$1" n=0 fails rc why
  [ -z "$REPAIR" ] || n=1
  while :; do
    if [ "$n" -gt 0 ] && ! doc_repair_tree "$id" check; then
      attempt_end close "$id" fail "$R_SCOPE"
      fail "$R_SCOPE of $id: nothing committed. needs human: the human checks the paths" \
        "above and removes $(doc_repair_snap "$id") to go on" >&2
      return 1
    fi
    repair_targets "$id"; rc=$?
    [ "$rc" -ne 0 ] || return 0
    if [ "$rc" -eq 2 ]; then
      attempt_end close "$id" fail "cannot tell whether the changelog fragment is owed"
      return 1
    fi
    [ -e "progress/close_$id.msg" ] || return 0   # the close reports the missing message
    fails="$(repair_fails)"
    printf '%s\n' "$fails" >&2
    if [ "$n" -ge "$DOC_REPAIRS" ]; then
      why="$R_OUT"; [ -n "$REPAIR_DOC" ] || why="$R_OUT_FRAG"
      attempt_end close "$id" fail "$why"
      fail "needs human: $why after $n repairs of $id: nothing committed; the human" \
        "completes $(repair_what), then: ./harness/review_with_codex.sh close $id" >&2
      return 1
    fi
    why="$R_DOC"; [ -n "$REPAIR_DOC" ] || why="$R_FRAG"
    attempt_end close "$id" fail "$why"
    attempt_begin close "$id" || return 1
    if ! doc_repair_tree "$id" begin; then
      attempt_end close "$id" fail "could not snapshot the tree for the repair"
      return 1
    fi
    n=$((n + 1))
    warn "repairing $(repair_what) of $id (repair $n of $DOC_REPAIRS)" >&2
    codex exec "${CODEX_ARGS[@]}" <<<"$(repair_prompt "$id" "$fails")"; rc=$?
    if [ "$rc" -ne 0 ]; then
      if ! doc_repair_tree "$id" check; then
        attempt_end close "$id" fail "$R_SCOPE"
        fail "$R_SCOPE of $id (documenter exited $rc): needs human" >&2
        return 1
      fi
      attempt_end close "$id" fail "documenter exited $rc (repair)"
      return 1
    fi
  done
}
# Commit a close: the documenter's message, only with the check green.
close_commit() {
  local id="$1" msg="progress/close_$1.msg" log text bak rc
  close_msg_recover "$id" || true
  if ! doc_repair_tree "$id" check; then
    CLOSE_WHY="$R_SCOPE"
    fail "$R_SCOPE of $id: nothing committed. The human checks the paths above and" \
      "removes $(doc_repair_snap "$id") to go on" >&2
    return 1
  fi
  CLOSE_WHY="no valid commit message ($msg)"
  python3 "$HARNESS_DIR/_guard.py" commit-message "$msg" "$id" || return 1
  CLOSE_WHY="the code approval is stale or missing"
  code_gate_fresh "$id" || return 1
  if ! bash "$HARNESS_DIR/doc_gate.sh" --leaf "$id" >&2; then
    CLOSE_WHY="$R_DOC"
    fail "$R_DOC for $id: nothing committed. Repair it:" \
      "./harness/review_with_codex.sh doc $id" >&2
    return 1
  fi
  # The fragment its last close owes (activated project, releases §3, rel1rev #4).
  frag_owed "$id"; rc=$?
  if [ "$rc" -eq 2 ]; then
    CLOSE_WHY="cannot tell whether the changelog fragment is owed"
    return 1
  fi
  if [ "$rc" -eq 1 ]; then
    CLOSE_WHY="$R_FRAG"
    fail "$R_FRAG for $id ($FRAG_FAILS): nothing committed. Repair it:" \
      "./harness/review_with_codex.sh doc $id" >&2
    return 1
  fi
  log="$(new_log close)"
  if ! run_check_recorded "$log"; then
    CLOSE_WHY="check red after the close"
    fail "$CHECK_CMD is red after the close of $id: nothing committed (log: $log)" >&2
    tail -n 30 "$log" >&2
    return 1
  fi
  # The check itself may rewrite files: the approval must still cover what is staged.
  CLOSE_WHY="the check changed what the approval covers"
  code_gate_fresh "$id" || return 1
  # Stage only the subtask's manifest (issue #40): a foreign path in the tree or the
  # index blocks the close with its reason; the manifest is never widened.
  local scope_py="$HARNESS_DIR/_close_scope.py" scope
  if ! python3 "$scope_py" check "$id"; then
    CLOSE_WHY="paths outside the manifest"
    fail "the close of $id touches paths outside its manifest: nothing committed" >&2
    return 1
  fi
  # A message in the index would be committed with the close: refuse it.
  if [ -n "$(GIT_LITERAL_PATHSPECS=1 git ls-files -- "$msg" 2>&1)" ]; then
    CLOSE_WHY="the close message is in the index"
    fail "$msg is in the git index: nothing committed. Unstage it (git rm --cached" \
      "-- $msg) and retry" >&2
    return 1
  fi
  text="$(cat "$msg")"
  # Never part of the commit: set aside (I7, see close_msg_recover) until it succeeds.
  bak="$(close_msg_backup "$id")"
  if [ -z "$bak" ] || ! mv -f "$msg" "$bak"; then
    CLOSE_WHY="could not set the close message aside"
    fail "could not move $msg to the git dir: nothing committed" >&2
    return 1
  fi
  CLOSE_ID="$id"
  trap 'close_msg_recover "$CLOSE_ID"; exit 130' INT
  trap 'close_msg_recover "$CLOSE_ID"; exit 143' TERM
  trap 'close_msg_recover "$CLOSE_ID"; exit 129' HUP
  scope="$(new_log scope)"
  if ! python3 "$scope_py" files "$id" >"$scope" || { [ -s "$scope" ] &&
      ! GIT_LITERAL_PATHSPECS=1 git add -A --pathspec-from-file="$scope" --pathspec-file-nul; }
  then
    rm -f "$scope"
    close_msg_recover "$id" 2>/dev/null; trap - INT TERM HUP
    CLOSE_WHY="could not stage the manifest"
    fail "could not stage the manifest of $id: nothing committed (message kept in $msg)" >&2
    return 1
  fi
  rm -f "$scope"
  if ! printf '%s\n' "$text" | git commit -q -F -; then
    close_msg_recover "$id" 2>/dev/null; trap - INT TERM HUP
    CLOSE_WHY="git commit failed"
    fail "git commit failed for the close of $id: message kept in $msg for the retry" >&2
    return 1
  fi
  rm -f "$bak"; trap - INT TERM HUP
  rm -f -- "$(doc_repair_snap "$id")"
  ok "closed -> $id ; commit $(git rev-parse --short HEAD)"
}
# The close as one recorded attempt of the `close` step: $1 = id.
close_attempt() {
  CLOSE_WHY="the close stopped early"
  if close_commit "$1"; then
    attempt_end close "$1" ok "closed"
    return 0
  fi
  attempt_end close "$1" fail "$CLOSE_WHY"
  return 1
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
    PR_WHY="the tree has uncommitted changes"
    fail "the tree has uncommitted changes: the PR for task $tid was not pushed or" \
      "opened. Commit or remove them first; $retry" >&2
    return 1
  fi
  # Index flags hide edits from git status and the fingerprint alike: the check
  # would read files whose content is not what gets pushed.
  if ! index_flags_clear; then
    PR_WHY="files marked assume-unchanged or skip-worktree"
    fail "files marked assume-unchanged or skip-worktree: the PR for task $tid was not" \
      "pushed or opened. Clear the flags (git update-index --no-assume-unchanged" \
      "--no-skip-worktree <file>) first; $retry" >&2
    return 1
  fi
  # Branch guard (issue #80): only the branch the task/issue lives on, never the
  # base or main/master, never one carrying base commits origin does not have.
  if ! python3 "$PR_PY" guard "$tid" "$base"; then
    PR_WHY="the branch guard refused $branch"
    fail "the PR for task $tid was not pushed or opened; $retry" >&2
    return 1
  fi
  case "$branch" in main|master|"$base"|HEAD)
    PR_WHY="refusing to push $branch"
    fail "refusing to push $branch for task $tid; $retry" >&2; return 1 ;;
  esac
  if ! green_record_current; then
    # No record of this exact state: run the real, clean check here (the same
    # producer as gates and close) and go on only if it records green.
    local log
    log="$(new_log pr)"
    if ! run_check_recorded "$log" || ! green_record_current; then
      PR_WHY="no green check for this exact state"
      fail "no green make check for this exact state (log: $log): the PR for task $tid" \
        "was not pushed or opened. Fix the tree until \`make check\` is green; $retry" >&2
      tail -n 30 "$log" >&2
      return 1
    fi
  fi
  if ! command -v "$GH" >/dev/null 2>&1; then
    PR_WHY="gh not found"
    warn "gh not found: the PR for task $tid was not opened; $retry" >&2
    return 0
  fi
  if ! git push -q -u origin "$branch"; then
    PR_WHY="could not push $branch"
    warn "could not push $branch: the PR for task $tid was not opened; $retry" >&2
    return 0
  fi
  if "$GH" pr view "$branch" --json url >/dev/null 2>&1; then
    ok "PR already open for $branch (pushed the new commits)"
    # Only an open PR is an outcome: a merged or closed one is not (attempt ledger).
    if [ "$("$GH" pr view "$branch" --json state --jq .state 2>/dev/null)" = "OPEN" ]; then
      PR_OK=1
    else
      PR_WHY="the PR of $branch is not open (merged, closed or unknown)"
    fi
    cleanup_claim "$tid" "$retry"
    return 0
  fi
  title="$(python3 "$PR_PY" title "$tid")"
  body="$(mktemp "${TMPDIR:-/tmp}/harness-pr.XXXXXX")"
  if ! python3 "$PR_PY" body "$tid" "$base" >"$body"; then
    PR_WHY="could not build the PR body"
    warn "could not build the PR body for task $tid; $retry" >&2
    return 0
  fi
  if "$GH" pr create --head "$branch" --base "$base" --title "$title" --body-file "$body"; then
    ok "PR opened for task $tid ($branch -> $base)"
    PR_OK=1
    cleanup_claim "$tid" "$retry"
  else
    PR_WHY="gh pr create failed"
    warn "gh pr create failed for task $tid; $retry" >&2
  fi
  rm -f "$body"
  return 0
}

# The PR as one recorded attempt of the `pr` step: $1 = task/issue id. Returns
# open_pr's own code; the attempt is a success only when the PR is open.
open_pr_attempt() {
  local rc
  PR_WHY="the PR step stopped early"; PR_OK=""
  open_pr "$1"; rc=$?
  if [ -n "$PR_OK" ]; then attempt_end pr "$1" ok "PR open"
  else attempt_end pr "$1" fail "$PR_WHY"; fi
  return $rc
}

# After a close: open the task PR when that close finished the task.
open_pr_if_due() {
  local tid
  tid="$(python3 "$PR_PY" pr-due "$1")" || return 0
  if ! attempt_begin pr "$tid"; then
    warn "the PR for task $tid was not pushed or opened (see above)" >&2
    return 0
  fi
  # The close commit moved HEAD, so the record of the pre-commit check no longer
  # matches: check the committed state again (only when a PR is due).
  local log
  log="$(new_log pr)"
  if ! run_check_recorded "$log"; then
    attempt_end pr "$tid" fail "check red after the close commit"
    warn "$CHECK_CMD is red after the close commit: the PR for task $tid was not opened" \
      "(log: $log). Fix it until \`make check\` is green, then:" \
      "./harness/review_with_codex.sh pr $tid" >&2
    return 0
  fi
  open_pr_attempt "$tid" || true
}

case "$PHASE" in
  seal)    exec python3 "$HARNESS_DIR/_guard.py" seal "$@" ;;
  close)   [ -n "${1:-}" ] || usage; attempt_begin close "$1" || exit 1
           close_attempt "$1" || exit 1; open_pr_if_due "$1"; exit 0 ;;
  pr)      [ -n "${1:-}" ] || usage; attempt_begin pr "$1" || exit 1
           open_pr_attempt "$1"; exit $? ;;
  # Selection contract (issue #65, I4b): Codex reviews progress/oracle_contract.json,
  # bound to its diff hash; the human then runs _oracle.py contract-approve <hash>.
  contract) exec python3 "$HARNESS_DIR/_oracle.py" contract-review ;;
  roadmap) ID="roadmap"; ROLE="$CODEX_DIR/codex-planner.md" ;;
  stack)   ID="stack"; ROLE="$CODEX_DIR/codex-planner.md" ;;
  # The release plan (docs/design/releases.md): asked, never blocking; no execution
  # gate depends on it, so it has no re-gate and its later changes only warn.
  releases) ID="releases"; ROLE="$CODEX_DIR/codex-planner.md" ;;
  phase)   ID="${1:-}"; ROLE="$CODEX_DIR/codex-planner.md" ;;
  task|task-close|phase-close) ID="${1:-}"; ROLE="$CODEX_DIR/codex-planner.md" ;;
  plan|code) ID="${1:-}"; ROLE="$CODEX_DIR/reviewer.md" ;;
  issue)   ID="${1:-}"; [[ "$ID" =~ ^[0-9]+$ ]] && ID="ISS-$ID"; ROLE="$CODEX_DIR/reviewer.md" ;;
  issue-draft) ID="draft"; ROLE="$CODEX_DIR/reviewer.md" ;;
  doc)     ID="${1:-}"; ROLE="$CODEX_DIR/documenter.md" ;;
  *) usage ;;
esac
[ -n "$ID" ] || usage
# Attempt budget (issue #46), read-only pre-check: a refused attempt changes nothing,
# not even the red record below. The locked check-and-start comes after the caps.
STEP="$PHASE"; [ "$PHASE" != "doc" ] || STEP="close"
if [ "$MODE_EXEC" = "auto" ]; then python3 "$RESUME_PY" budget "$STEP" "$ID" || exit 1; fi
# A plan gate run never reuses an older red record (issue #65): it is dropped before
# anything that can exit, and only an approval that sealed the oracle writes a new one.
[ "$PHASE" != "plan" ] || python3 "$HARNESS_DIR/_oracle.py" red-drop "$ID" || true
CODEX_PHASE="$PHASE"
[ -f "$ROLE" ] || { echo "role prompt not found: $ROLE" >&2; exit 1; }

REPLAN_SNAP=""; REPAIR=""; DOC_FAILS=""; FRAG_FAILS=""; REPAIR_DOC=""; REPAIR_FRAG=""
# Preconditions: never review a step whose prerequisite is not approved yet.
case "$PHASE" in
  # The roadmap waits for an approved, intact docs/stack.md (issue #115).
  roadmap) python3 "$HARNESS_PY" roadmap-ready || exit 1
           python3 "$HARNESS_DIR/_stack.py" roadmap-ready || exit 1 ;;
  stack)   python3 "$HARNESS_DIR/_stack.py" ready || exit 1 ;;
  releases) python3 "$HARNESS_DIR/_releases.py" ready || exit 1 ;;
  phase)   python3 "$HARNESS_PY" phase-ready "$ID" || exit 1 ;;
  task)    python3 "$HARNESS_PY" task-ready "$ID" || exit 1 ;;
  task-close)  python3 "$HARNESS_PY" task-close-ready "$ID" || exit 1 ;;
  phase-close) python3 "$HARNESS_PY" phase-close-ready "$ID" || exit 1 ;;
  issue)   python3 "$HARNESS_PY" issue-ready "$ID" || exit 1 ;;
  doc)     close_msg_recover "$ID" || true
           # Repair mode (issue #133): a pending close, red because of its doc.
           if [ -e "progress/close_$ID.msg" ] && leaf_done "$ID"; then
             doc_repair_ready "$ID" || exit 1
             REPAIR=1
           fi
           [ -n "$REPAIR" ] || python3 "$HARNESS_PY" close-ready "$ID" || exit 1
           code_gate_fresh "$ID" || exit 1
           # A waiting message (a failed or interrupted close) is for the approval that
           # still holds: `close` reuses it; doc never regenerates it (I7). Only the
           # human removes it to get a new one (Claude cannot write progress/close_*).
           if [ -z "$REPAIR" ] && [ -e "progress/close_$ID.msg" ]; then
             fail "progress/close_$ID.msg is waiting from an earlier close: run" \
               "./harness/review_with_codex.sh close $ID (the doc gate would regenerate" \
               "it; to get a new one, the human removes it first)" >&2
             exit 1
           fi ;;
  plan)    python3 "$HARNESS_PY" subtask-ready "$ID" || exit 1
           python3 "$HARNESS_DIR/_guard.py" migrates-ready "$ID" || exit 1
           if [ -f "progress/acceptance_$ID.sha256" ]; then  # a re-plan (issue #125)
             REPLAN_SNAP="$(python3 "$HARNESS_DIR/_oracle.py" replan-snapshot "$ID")" ||
               { printf '%s\n' "$REPLAN_SNAP" >&2; exit 1; }
           fi ;;
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

# Review policy (issue #44): a subtask's plan and code gates take the effort and the
# extra checklists of its rating (spec `- risk:`/`- surfaces:`, its sealed rating and
# its files:, see _review_risk.py); never below the role's effort. Fails closed.
# Issue #107: the phase, task and issue gates, which approve those specs first, take
# the strictest rating among their subtasks; their checklists apply to the specs.
POLICY_STEP=""
case "$PHASE" in plan|code|phase|task|issue)
  _POLICY="$(python3 "$HARNESS_DIR/_review_risk.py" gate-policy "$PHASE" "$ID" "$EFFORT" \
    "$CODEX_DIR")" || exit 1
  { IFS= read -r EFFORT; IFS= read -r _WHY; } <<<"$_POLICY"
  _LISTS="$(printf '%s\n' "$_POLICY" | sed '1,2d' | sed 's/^/  - /')"
  if [ -n "$_WHY" ]; then
    POLICY_STEP="

Review policy (issue #44): reasoning effort $EFFORT; rating: $_WHY."
    if [ -n "$_LISTS" ]; then
      _APPLY=""
      case "$PHASE" in phase|task|issue)
        _APPLY="
At this gate apply every item to the subtask specs under review (what each subtask
must change, prove and keep safe), for the subtasks the rating names." ;;
      esac
      POLICY_STEP="$POLICY_STEP
Extra review checklists: read each file and apply every item on top of your role;
your verdict adds a \`## Checklists\` section, one line per item: met, gap (file:line)
or n/a (why). Any gap means CHANGES_REQUESTED.$_APPLY
$_LISTS"
    fi
  fi
  unset _POLICY _WHY _LISTS _APPLY ;;
esac

# What this gate reviews, and where its verdict goes.
case "$PHASE" in
  roadmap)
    TARGET="the roadmap: docs/plan/PLAN.md and the title + goal of every docs/plan/phase-*.md"
    VERDICT_FILE="progress/roadmap_review.md"; SEAL_ARGS=(roadmap); SEAL_ON="ROADMAP_APPROVED" ;;
  stack)
    TARGET="the stack: docs/stack.md (project type, language and each stack decision with its why)"
    VERDICT_FILE="progress/stack_review.md"; SEAL_ARGS=(stack); SEAL_ON="STACK_APPROVED" ;;
  releases)
    TARGET="the release plan: docs/releases.md (applies, versioning, version source, audience, planned releases, compatibility, patches)"
    VERDICT_FILE="progress/releases_review.md"; SEAL_ARGS=(releases); SEAL_ON="RELEASES_APPROVED" ;;
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

# Attempt budget (issue #46): only runs the harness launches itself are attempts, so
# manual mode (the human runs Codex) records none. The doc gate is the `close` step.
# Started here, after every refusal that launches nothing (preconditions, round cap,
# review policy): from now on the run does work (the check, Codex, a commit).
if [ "$MODE_EXEC" = "auto" ]; then
  attempt_begin "$STEP" "$ID" || exit 1
fi
CHECK_LOG="$(new_log "$PHASE")"
# Close gates (issue #62) read a state the last close already checked: when the green
# record matches this exact state, reuse it instead of a second full check.
if { [ "$PHASE" = "task-close" ] || [ "$PHASE" = "phase-close" ]; } && green_record_current; then
  printf 'reused the green record %s (fingerprint %s): no new %s run\n' \
    "$GREEN_RECORD" "$(cat "$GREEN_RECORD")" "$CHECK_CMD" >"$CHECK_LOG"
  ok "reused the green record: $CHECK_CMD already passed on this exact state"
  CHECK_RC=0
elif [ -n "$REPAIR" ]; then
  # A repair reads what failed; the full check runs again at the close.
  repair_fails >"$CHECK_LOG"
  CHECK_RC=1
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
# Issue #125 (B1): every re-gate (stack, roadmap, phase, task) gets the third run too;
# its T is the in-progress subtask's own oracle (`_expect_red.py leaf`).
TOLERATED_CFG=""
third_run_step() {
  local iid="$1" target="$2" files rc log mode="issue mode" why
  why="an acceptance test of its own in-progress subtask (red by design) or a migrated oracle"
  if [ "$iid" = "leaf" ]; then
    mode="$PHASE gate"
    why="an acceptance test of the in-progress subtask (its own plan and code gates decide it)"
  fi
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
  if [ -n "$TOLERATED_CFG" ]; then
    printf '\n%s\n%s\n%s\n%s' \
      "The changed test configuration below differs from HEAD; it is tolerated only because each" \
      "file is in the files: of the in-progress subtask (issue #125). Read each diff (git diff HEAD --" \
      "<file>, or the whole file if untracked) and confirm it changes no test collection or selection:" \
      "$(printf '%s\n' "$TOLERATED_CFG" | sed 's/^/  - /')"
  fi
  printf '\n%s\n%s\n%s\n%s\n%s\n%s\n%s\n%s\n%s\n%s' \
    "Third run (in-progress re-gate, $mode): \`$CHECK_CMD\` again with" \
    "HARNESS_ASSUME_APPROVED=$target and HARNESS_EXPECT_RED=$(printf '%s' "$files" | paste -sd, -) (the gate" \
    "excluded these test files of T from the tests; their lint and build still ran in the first two runs):" \
    "$(printf '%s\n' "$files" | sed 's/^/  - /')" \
    "exit code $rc, full output in $log." \
    "With the counterfactual run red you may approve only if this run is green AND your" \
    "verdict accounts for every test in every file of T, not only the failures in the logs" \
    "(read each file and the subtask's changes; see your role's in-progress re-gate rule);" \
    "list T, the first run's FAIL lines and that per-file accounting." \
    "Each test of T may fail only as $why; anything else blocks."
}
ASSUME_STEP=""
if [ "$CHECK_RC" -ne 0 ] && [ -z "${HARNESS_CHECK_CMD+set}" ]; then
  ASSUME=""
  case "$PHASE" in
    roadmap) ASSUME="roadmap" ;;
    stack)   ASSUME="stack" ;;
    phase)   ASSUME="phase:$ID" ;;
    task)    ASSUME="task:$ID" ;;
    issue)   ASSUME="issue:$ID" ;;
  esac
  if [ -n "$ASSUME" ]; then
    # Coupled re-gates (issue #125, A1): a re-gate (this target sealed before) also
    # assumes every re-gate pending below it (sealed before, now changed or not
    # approved). Never one above it, so the re-gates go top down. A first approval,
    # or a failure to compute it, assumes nothing more and gets no third run (B1).
    REGATE=""; CASCADE=""
    if python3 "$HARNESS_DIR/_expect_red.py" sealed "$ASSUME" 2>/dev/null; then
      REGATE=1
      CASCADE="$(python3 "$HARNESS_DIR/_expect_red.py" cascade "$ASSUME" 2>/dev/null)" || CASCADE=""
    fi
    SCOPE="(only this target's verdict reads approved and only its seal reads matching)"
    if [ -n "$CASCADE" ]; then
      SCOPE="(only these targets' verdicts read approved and only their seals read matching:
$ASSUME, this gate's target, and the pending re-gates below it: $CASCADE.
Those stay pending: each one still needs its own gate, run after this one)"
      ASSUME="$ASSUME,$CASCADE"
    fi
    ASSUME_LOG="$(new_log "$PHASE-assumed")"
    run_check_assuming "$ASSUME_LOG" "$ASSUME"
    ASSUME_RC=$?
    ASSUME_STEP="
Counterfactual run (re-gate): \`$CHECK_CMD\` again with HARNESS_ASSUME_APPROVED=$ASSUME
$SCOPE:
exit code $ASSUME_RC, full output in $ASSUME_LOG.
With the first run red you may approve only if this run is green; list the first
run's FAIL lines in your verdict."
    if [ "$ASSUME_RC" -ne 0 ]; then
      if [ "$PHASE" = "issue" ]; then
        ASSUME_STEP="$ASSUME_STEP$(third_run_step "$ID" "$ASSUME")"
      elif [ -n "$REGATE" ]; then
        ASSUME_STEP="$ASSUME_STEP$(third_run_step leaf "$ASSUME")"
      fi
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

# Re-plan (issue #125, C1): the plan gate of a subtask whose oracle is already sealed
# (work in progress, or a defective oracle) may rewrite it. The reviewer gets a copy
# of the sealed oracle verified against its seal (REPLAN_SNAP, taken before anything
# runs: no verified copy, no re-plan); an approval re-seals it, which voids the
# subtask's earlier code approval (_guard.py seal), and appends the oracle's diff to
# the review history.
REPLAN_STEP=""
if [ -n "$REPLAN_SNAP" ]; then
  REPLAN_STEP="

Re-plan (issue #125): subtask $ID already has a sealed oracle (progress/acceptance_$ID.sha256)
and may have work in progress. Apply your role's re-plan rules: rewrite the oracle from the
spec, account for every test you change, add or remove, and never weaken one without a
criterion change the upstream gates approved. An approval re-seals the oracle and voids any
earlier code approval of $ID (its code gate runs again).
Your read-only copy of the sealed oracle: $REPLAN_SNAP/oracle"
fi

# What Codex reads. The close gates (issue #62) read only their specs, reports and
# the diff of their close commits: never the whole repo (cost limit).
CONTEXT="Context you must read: AGENTS.md, docs/architecture.md, docs/conventions.md,
CHECKPOINTS.md, feature_list.json, and progress/current.md."
case "$PHASE" in
  stack)
    CONTEXT="Context you must read: docs/stack.md, AGENTS.md, the existing code and its
package manifest or lock file if there is one (pyproject.toml, package.json, Cargo.toml…),
and progress/current.md. No plan exists yet: the stack comes before the discovery."
    if ls docs/plan/phase-*.md >/dev/null 2>&1; then
      CONTEXT="Context you must read: docs/stack.md, AGENTS.md, the existing code and its
package manifest or lock file if there is one (pyproject.toml, package.json, Cargo.toml…),
and progress/current.md. A plan already exists (docs/plan/): this is a re-gate of the stack;
review the stack itself, the plan is context."
    fi ;;
  releases)
    CONTEXT="Context you must read: docs/releases.md, docs/plan/PLAN.md and the title + goal
of every docs/plan/phase-*.md, docs/stack.md, the version source it names (pyproject.toml
[project] version, or VERSION), CHANGELOG.md, every changelog.d/*.md fragment, AGENTS.md
and progress/current.md. Apply your role's releases mode; no gate of the work depends on
this plan, so a red check is not by itself a reason to reject it." ;;
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
That result is the one AGENTS.md step 1 asks for.$ASSUME_STEP$CLOSE_STEP$POLICY_STEP$REPLAN_STEP

Respect your role's output contract (write the verdict/result to the indicated
progress/ file and return a single reference line).
Do not edit production code. Do not add agent attribution.
EOF
)"
[ -z "$REPAIR" ] || PROMPT="$(repair_prompt "$ID" "$(repair_fails)")"

# --- Model, effort and sandbox for the codex CLI (verified with codex exec --help).
# Effort is not a dedicated flag: it is passed as a config override.
# Sandbox: the reviewer must WRITE (acceptance tests + verdict) and may run static
# tools through uv, so read-only fails. workspace-write covers the project; it
# keeps .git read-only and blocks the network (verified with `codex sandbox`).
CODEX_ARGS=(--model "$MODEL" --sandbox "$SANDBOX")
case "$EFFORT" in
  low|medium|high|xhigh) CODEX_ARGS+=(-c "model_reasoning_effort=$EFFORT") ;;
esac
if [ "$SANDBOX" = "workspace-write" ]; then
  ROOTS=("$(uv cache dir 2>/dev/null || echo "${UV_CACHE_DIR:-$HOME/.cache/uv}")")
  # A JSON array of strings is valid TOML for the -c override.
  ROOTS_TOML="$(python3 -c 'import json,sys; print(json.dumps(sorted({p for p in sys.argv[1:] if p})))' "${ROOTS[@]}")"
  CODEX_ARGS+=(-c "sandbox_workspace_write.writable_roots=$ROOTS_TOML")
  if [ "$NETWORK" = "1" ]; then
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
  if [ -n "$REPLAN_SNAP" ]; then
    echo "--- after the re-plan's seal, record the superseded oracle ---"
    echo "python3 harness/_oracle.py replan-history $ID $REPLAN_SNAP"
  fi
  if [ "$PHASE" = "doc" ]; then
    if [ -n "$REPAIR" ]; then
      echo "--- after Codex repairs $(repair_what) (only that may change), check and commit ---"
    else
      echo "--- after Codex writes progress/close_$ID.msg, check and commit ---"
    fi
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
  fail "codex is not in PATH. Set [codex] mode = \"manual\" in harness.toml or install Codex." >&2
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
  if [ $RC -ne 0 ]; then
    if [ -n "$REPAIR" ] && ! doc_repair_tree "$ID" check; then
      attempt_end close "$ID" fail "$R_SCOPE"
      fail "$R_SCOPE of $ID (documenter exited $RC): needs human" >&2
      exit 1
    fi
    attempt_end close "$ID" fail "documenter exited $RC"
    exit $RC
  fi
  doc_repair_loop "$ID" || exit 1
  close_attempt "$ID" || exit 1
  open_pr_if_due "$ID"
  exit 0
fi

if [ $RC -ne 0 ]; then
  fail "the reviewer exited $RC: $VERDICT_FILE not accepted, nothing recorded or sealed" >&2
  attempt_end "$STEP" "$ID" fail "reviewer exited $RC"
  exit $RC
fi
# Validate and make the verdict final (receipt included); an invalid one is undone.
if ! TOKEN="$(python3 "$VERDICT_PY" commit --owner $$ "$PHASE" "$VERDICT_FILE")"; then
  attempt_end "$STEP" "$ID" fail "invalid verdict in $VERDICT_FILE"
  exit 1
fi
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
  if ! python3 "$HARNESS_DIR/_guard.py" seal "${SEAL_ARGS[@]}"; then
    attempt_end "$STEP" "$ID" fail "seal failed after $TOKEN"
    exit 1
  fi
  # After Codex wrote the oracle: run it alone and record it collected and failing,
  # with the check result and the oracle/selection fingerprints (WARN only).
  if [ "$PHASE" = "plan" ]; then
    python3 "$HARNESS_DIR/_oracle.py" red-record "$ID" "$CHECK_RC" || true
    # The superseded oracle must reach the history: a failure fails the step (the
    # approval and the seal stand; the snapshot stays for the retry).
    if [ -n "$REPLAN_SNAP" ] &&
        ! python3 "$HARNESS_DIR/_oracle.py" replan-history "$ID" "$REPLAN_SNAP"; then
      fail "the superseded oracle of $ID is not in its review history; retry:" \
        "python3 harness/_oracle.py replan-history $ID $REPLAN_SNAP" >&2
      attempt_end "$STEP" "$ID" fail "superseded oracle not recorded"
      exit 1
    fi
  fi
fi
attempt_end "$STEP" "$ID" ok "$TOKEN"
exit 0
