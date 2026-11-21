#!/usr/bin/env bash
# Launch command of a parallel Claude session (issue #101; see _session_launch.py).
# Model/effort come from harness.toml [sessions] and the unit's files: (high_risk_paths).
#   harness/session_launch.sh 1.2.3 --worktree ../wt-1.2.3        (prints the command)
#   harness/session_launch.sh 1.2 --worktree ../wt-1.2 --exec     (runs claude there)
#   harness/session_launch.sh ISS-7.1 --hook ./open-pane          (hands the line to a hook)
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_session_launch.py" "$@"
