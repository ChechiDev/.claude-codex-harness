#!/usr/bin/env bash
# Entrypoint of the C0 check container: refuse to run unless the environment and the
# isolation are exactly as expected, then run the requested command.
set -euo pipefail
/usr/local/lib/compat/guard_env.sh
/usr/local/lib/compat/selfcheck.sh
mkdir -p "$HOME"
exec "$@"
