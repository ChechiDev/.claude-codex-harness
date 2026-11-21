# Sourced by review_with_codex.sh and stop_gate.sh (issues #27, #31).
# CLEAN_ENV: an `env` prefix that drops every variable able to exclude tests, change
# what make reads or overrides, or run code in the check's shell: the gate's own
# labels, the check-lock bypass HARNESS_CHECK_LOCK_HELD (#52), all MAKE*/GNUMAKE* variables (MAKEFLAGS, MAKEOVERRIDES, MAKEFILES,
# GNUMAKEFLAGS…), MFLAGS, all PYTEST_* variables, TESTS, the Bash startup controls
# (BASH_ENV, ENV, SHELLOPTS, BASHOPTS, GLOBIGNORE, CDPATH) and every exported Bash
# function (BASH_FUNC_*). Only the issue gate's third run sets any of the first ones.
# The sourcing script dropped every inherited function at its start (and failed
# closed if any was left). Run a known external env (none: fail closed).
for _e in /usr/bin/env /bin/env; do [ -x "$_e" ] && break; _e=""; done
if [ -z "$_e" ]; then
  echo "[FAIL] no /usr/bin/env or /bin/env: cannot run the check in a clean environment" >&2
  exit 1
fi
CLEAN_ENV=("$_e")
while IFS= read -r -d '' _kv; do
  _v="${_kv%%=*}"
  case "$_v" in
    HARNESS_ASSUME_APPROVED|HARNESS_EXPECT_RED|HARNESS_CHECK_LOCK_HELD|MFLAGS|TESTS|MAKE*|GNUMAKE*|PYTEST_*|\
    BASH_ENV|ENV|SHELLOPTS|BASHOPTS|GLOBIGNORE|CDPATH|BASH_FUNC_*)
      CLEAN_ENV+=(-u "$_v") ;;
  esac
done < <("$_e" -0)
unset _kv _v _e
