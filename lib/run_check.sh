#!/usr/bin/env bash
# run_check.sh — the trusted check launcher (issue #64). Exposed to projects as
# harness/run_check.sh. Gates, close and the PR barrier decide only through it; a
# direct `make check` is a convenience run that reports but decides nothing.
#
#   harness/run_check.sh [--decision] [--record] [--assume <target>] [--log <file>]
#   harness/run_check.sh --validate        findings only, the check does not run
#
# It (1) validates the effective Makefile: no GNUmakefile or lowercase makefile
# shadowing Makefile, `include harness/harness.mk` present, every harness target
# defined only there, `check` with no extra prerequisites; (2) runs the check in a
# controlled environment: no command-line assignments, the _clean_env.sh drops, plus
# interpreter/loader startup variables (PYTHONPATH, PYTHONSTARTUP, PYTHONUSERBASE,
# PYTHONHOME, LD_PRELOAD…) and the lock bypass HARNESS_CHECK_LOCK_HELD, with the user
# site disabled (PYTHONNOUSERSITE=1); startup hooks in the tree (sitecustomize.py,
# usercustomize.py, a .pth outside site-packages) are reported; (3) with --record, is
# the only writer of the green record .git/harness-check.ok (issue #55).
#
# WARN rollout (plan 0.5): every finding is a [WARN] with its remedy and never changes
# the exit code, which is always the check's own. Variables outside the allowlist
# still pass, listed by name only (never values). Blocking is the I4a-fail issue.
#
# --decision  a gate/close/PR run: a HARNESS_CHECK_CMD override warns (test fixtures
#             that write no record or approval may still use it quietly).
# --record    write the green record on a clean green run of the default command
#             whose fingerprint did not move; remove it on any other outcome.
# --assume T  the gate's counterfactual run: HARNESS_ASSUME_APPROVED=T (issue #27).
# --log F     check output and findings go to F; findings also go to stderr.
set -uo pipefail
for _f in $(builtin compgen -A function); do builtin unset -f "$_f"; done; builtin unset _f
if [ -n "$(builtin compgen -A function)" ]; then
  echo "[FAIL] inherited shell functions remain: refusing to run" >&2; exit 1
fi

HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HARNESS_DIR/_colors.sh"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
cd "$PROJECT_DIR" || exit 1

DECISION=0; RECORD=0; VALIDATE=0; ASSUME=""; LOG=""; IGNORED=()
while [ $# -gt 0 ]; do
  case "$1" in
    --decision) DECISION=1 ;;
    --record)   RECORD=1 ;;
    --validate) VALIDATE=1 ;;
    --assume)   ASSUME="${2:-}"; shift ;;
    --log)      LOG="${2:-}"; shift ;;
    *)          IGNORED+=("$1") ;;
  esac
  shift
done

FINDINGS=()
finding() { FINDINGS+=("$*"); }

# ── command line ─────────────────────────────────────────────────────────────
if [ ${#IGNORED[@]} -gt 0 ]; then
  finding "run_check: command-line arguments ignored (${IGNORED[*]}): the check takes" \
    "no assignments, -f or targets — fix: run harness/run_check.sh with no arguments"
fi

# ── environment ──────────────────────────────────────────────────────────────
. "$HARNESS_DIR/_clean_env.sh"   # CLEAN_ENV=(env -u …) for the gate's drops
ALLOW_RE='^(PATH|HOME|USER|LOGNAME|SHELL|PWD|OLDPWD|SHLVL|_|TERM|COLORTERM|TZ|TMPDIR|LANG|LANGUAGE|LC_[A-Z_]+|XDG_[A-Z_]+|NO_COLOR|FORCE_COLOR|COLUMNS|LINES|CI|CLAUDE_PROJECT_DIR|HARNESS_[A-Z_]+|UV_[A-Z_]+|SSH_AUTH_SOCK|DOCKER_HOST|DOCKER_CONFIG|HOSTNAME)$'
ENVCMD=("${CLEAN_ENV[@]}")
OTHERS=()
while IFS= read -r -d '' _kv; do
  _v="${_kv%%=*}"
  case "$_v" in
    MAKELEVEL|MAKE_TERMOUT|MAKE_TERMERR) ;;  # set by any parent make: harmless
    MAKE*|GNUMAKE*|MFLAGS|TESTS|PYTEST_*)
      # Dropped by CLEAN_ENV; warn only when the value could change what runs (a
      # parent make sets MAKEFLAGS to bare flags such as "w" or "s").
      _val="${_kv#*=}"
      # pytest sets PYTEST_CURRENT_TEST/PYTEST_VERSION itself: dropped quietly.
      [[ "$_v" != PYTEST_* || "$_v" == PYTEST_ADDOPTS || "$_v" == PYTEST_PLUGINS ]] || _val=""
      if [ -n "$_val" ] && { [[ "$_v" != MAKEFLAGS && "$_v" != MFLAGS ]] ||
          [[ "$_val" == *=* || "$_val" =~ (^|[[:space:]])-?[a-zA-Z]*[fIC] ||
             "$_val" == *--file* || "$_val" == *--eval* || "$_val" == *--include* ]]; }; then
        finding "run_check: $_v is set: it could change what make or pytest runs;" \
          "dropped — fix: unset $_v"
      fi
      unset _val ;;
    PYTHONPATH|PYTHONSTARTUP|PYTHONUSERBASE|PYTHONHOME|PYTHONINSPECT|PYTHONEXECUTABLE|\
    PYTHONPLATLIBDIR|PERL5LIB|PERL5OPT|PERLLIB|RUBYOPT|RUBYLIB|NODE_OPTIONS|NODE_PATH|\
    LD_PRELOAD|LD_LIBRARY_PATH|LD_AUDIT|DYLD_*)
      ENVCMD+=(-u "$_v")
      finding "run_check: $_v is set: it runs or loads code outside the project's tools;" \
        "dropped — fix: unset $_v" ;;
    HARNESS_CHECK_LOCK_HELD)
      ENVCMD+=(-u "$_v")
      finding "run_check: HARNESS_CHECK_LOCK_HELD is set: it would skip the check lock;" \
        "dropped — fix: unset HARNESS_CHECK_LOCK_HELD" ;;
    HARNESS_RUN_CHECK) ENVCMD+=(-u "$_v") ;;
    *) [[ "$_v" =~ $ALLOW_RE ]] || OTHERS+=("$_v") ;;
  esac
done < <("${CLEAN_ENV[0]}" -0)
unset _kv _v
ENVCMD+=(PYTHONNOUSERSITE=1 HARNESS_RUN_CHECK=1)
[ -z "$ASSUME" ] || ENVCMD+=(HARNESS_ASSUME_APPROVED="$ASSUME")

if [ "$DECISION" = 1 ] && [ -n "${HARNESS_CHECK_CMD+set}" ]; then
  finding "run_check: HARNESS_CHECK_CMD overrides the check in a decision run (it is" \
    "for test fixtures only) — fix: unset HARNESS_CHECK_CMD"
fi

# ── startup hooks in the tree ────────────────────────────────────────────────
while IFS= read -r -d '' _p; do
  case "$_p" in */site-packages/*.pth) continue ;; esac
  finding "run_check: ${_p#./} can run code at interpreter startup — fix: remove it" \
    "(or move the code into the project's tools)"
done < <(find . \( -name .git -o -name node_modules \) -prune -o \
  \( -name sitecustomize.py -o -name usercustomize.py -o -name '*.pth' \) -print0 2>/dev/null)
unset _p

# ── effective Makefile ───────────────────────────────────────────────────────
HARNESS_TARGETS=(plan status check issue issue-start issue-new)
if [ -e GNUmakefile ]; then
  finding "run_check: GNUmakefile is read by make before Makefile — fix: git rm GNUmakefile"
fi
if [ -e makefile ] && ! [ makefile -ef Makefile ]; then
  finding "run_check: lowercase makefile is read by make before Makefile — fix: git rm makefile"
fi
if [ ! -e Makefile ]; then
  finding "run_check: no Makefile — fix: install.sh <project>"
else
  # Parse as the real run sees it: MAKECMDGOALS=check (a conditional on the goal
  # must not hide a redefinition), but with a dummy goal so no recipe runs (a
  # recursive $(MAKE) line would run even under -n). stderr keeps make's
  # "overriding recipe" warnings: they show a harness target defined twice, in
  # either order around the include.
  _err="$(mktemp "${TMPDIR:-/tmp}/run-check-db.XXXXXX")"
  _db="$("${ENVCMD[@]}" make -pn --no-print-directory __harness_none__ MAKECMDGOALS=check \
    2>"$_err" </dev/null)"
  _over="$(grep -oE "overriding recipe for target '[^']+'" "$_err" | sed -E "s/.*'(.*)'/\1/")"
  rm -f "$_err"
  if ! printf '%s\n' "$_db" | grep -E '^MAKEFILE_LIST :?= .*(^| )harness/harness\.mk( |$)' \
      >/dev/null; then
    finding "run_check: the Makefile does not include harness/harness.mk — fix:" \
      "install.sh --migrate <project> (adds \`include harness/harness.mk\` last)"
  else
    _origins="$(printf '%s\n' "$_db" | awk '
      /^[A-Za-z0-9_.-]+:/ { t = $0; sub(/:.*/, "", t); deps = $0; sub(/^[^:]*:+ */, "", deps)
                            cur = t; dep[t] = deps; next }
      /^#  *recipe to execute \(from / && cur != "" {
        s = $0; sub(/.*\(from \047/, "", s); sub(/\047.*/, "", s); org[cur] = s }
      END { for (t in dep) printf "%s\t%s\t%s\n", t, org[t], dep[t] }')"
    for _t in "${HARNESS_TARGETS[@]}"; do
      if printf '%s\n' "$_over" | grep -Fx -- "$_t" >/dev/null; then
        finding "run_check: target $_t has a second recipe besides harness/harness.mk" \
          "(make: overriding recipe) — fix: delete the project's $_t rule"
      fi
      _line="$(printf '%s\n' "$_origins" | awk -F'\t' -v t="$_t" '$1 == t')"
      _org="$(printf '%s' "$_line" | cut -f2)"
      _dep="$(printf '%s' "$_line" | cut -f3)"
      if [ -n "$_line" ] && [ "$_org" != "harness/harness.mk" ]; then
        finding "run_check: target $_t is redefined in ${_org:-the Makefile} (it must come" \
          "only from harness/harness.mk) — fix: delete that $_t rule"
      fi
      if [ "$_t" = check ] && [ -n "${_dep// /}" ]; then
        finding "run_check: target check has extra prerequisites ($_dep) — fix: remove" \
          "them; put project steps in verify"
      fi
    done
    unset _origins _line _org _dep _t
  fi
  unset _db _over
fi

# ── report ───────────────────────────────────────────────────────────────────
report() {
  local f
  for f in "${FINDINGS[@]}"; do warn "$f"; done
  if [ ${#OTHERS[@]} -gt 0 ]; then
    echo "note: run_check: ${#OTHERS[@]} variable(s) outside the allowlist passed through" \
      "(WARN rollout): ${OTHERS[*]}"
  fi
}
if [ -n "$LOG" ]; then
  report >"$LOG"
  [ ${#FINDINGS[@]} -eq 0 ] || report | grep '^\[WARN\]' >&2
  # (colors off in the log: report() is not on a terminal there)
else
  report >&2
fi
[ "$VALIDATE" = 0 ] || exit 0

# ── the check ────────────────────────────────────────────────────────────────
GREEN_RECORD="$(git rev-parse --git-path harness-check.ok 2>/dev/null)"
# The fingerprint runs in the check's environment, with -E -s: an inherited
# PYTHONPATH or a user site must not shape the state the record vouches for.
fingerprint() { "${ENVCMD[@]}" python3 -E -s "$HARNESS_DIR/_guard.py" fingerprint 2>/dev/null; }
# Index flags hide edits from git status and the fingerprint (issue #55).
index_flags_clear() {
  local flags
  flags="$(git ls-files -v 2>&1)" || return 1
  ! printf '%s\n' "$flags" | grep -E '^([a-z]|S)' >/dev/null
}
run() {
  if [ -n "${HARNESS_CHECK_CMD+set}" ]; then
    "${ENVCMD[@]}" bash -c "$HARNESS_CHECK_CMD" </dev/null
  else
    "${ENVCMD[@]}" make check </dev/null
  fi
}

before=""
[ "$RECORD" = 0 ] || before="$(fingerprint)" || before=""
if [ -n "$LOG" ]; then run >>"$LOG" 2>&1; else run; fi
rc=$?
if [ "$RECORD" = 1 ] && [ -n "$GREEN_RECORD" ]; then
  after="$(fingerprint)" || after=""
  if [ "$rc" -eq 0 ] && [ -z "${HARNESS_CHECK_CMD+set}" ] && [ -z "$ASSUME" ] &&
      [ -n "$before" ] && [ "$before" = "$after" ] && index_flags_clear; then
    printf '%s' "$after" >"$GREEN_RECORD" ||
      warn "could not write the green record $GREEN_RECORD" >&2
  else
    rm -f "$GREEN_RECORD"
  fi
fi
exit "$rc"
