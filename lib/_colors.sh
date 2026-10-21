# _colors.sh — sourced (not executed). Status tags shared by every harness script:
#   [OK] green · [SKIP] orange · [WARN] yellow · [FAIL] red
# Colors only when the stream is a terminal (or FORCE_COLOR is set, for output
# captured to be replayed on one) and never with NO_COLOR (https://no-color.org),
# so output captured by hooks, Claude or CI stays plain.
# Python counterpart: _status.py (same tags, same colors).
# The tty test runs inline, never inside $(...), where stdout is a pipe.

_colorable() { [ -z "${NO_COLOR:-}" ] && { [ -n "${FORCE_COLOR:-}" ] || [ -t 1 ]; }; }

# Label colors for the install/scaffold progress lines ([symlink], [copy]…).
if _colorable; then
  GREEN=$'\033[0;32m'; ORANGE=$'\033[38;5;208m'; YELLOW=$'\033[0;33m'; RED=$'\033[0;31m'
  NC=$'\033[0m'
else
  GREEN=''; ORANGE=''; YELLOW=''; RED=''; NC=''
fi

_status() {  # _status <code> <tag> <message…>; tty checked per call (works with >&2)
  local code="$1" tag="$2" reset=$'\033[0m'
  shift 2
  _colorable || { code=''; reset=''; }
  printf '%s%s%s%*s%s\n' "$code" "$tag" "$reset" $((8 - ${#tag})) "" "$*"
}
# row <color> <label> <text…>: one compact install/scaffold line, label column 10.
row() {
  local color="$1" label="$2"
  shift 2
  printf '%s%s%s%*s%s\n' "$color" "$label" "$NC" $((10 - ${#label})) "" "$*"
}

ok()   { _status $'\033[0;32m'     "[OK]"   "$@"; }
skip() { _status $'\033[38;5;208m' "[SKIP]" "$@"; }
warn() { _status $'\033[0;33m'     "[WARN]" "$@"; }
fail() { _status $'\033[0;31m'     "[FAIL]" "$@"; }
