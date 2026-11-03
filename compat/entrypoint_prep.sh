#!/usr/bin/env bash
# Entrypoint of the C0 dependency-prep containers (init / fetch / install):
#   compat-entrypoint-prep [isolation options] -- COMMAND...
# Same guards as the check container; the options relax only what the phase needs.
set -euo pipefail
opts=()
while (($#)) && [ "$1" != "--" ]; do
  opts+=("$1")
  shift
done
if [ "${1:-}" != "--" ]; then
  echo "compat-entrypoint-prep: missing '--' before the command" >&2
  exit 72
fi
shift
/usr/local/lib/compat/guard_env.sh
/usr/local/lib/compat/selfcheck.sh "${opts[@]}"
mkdir -p "$HOME"
exec "$@"
