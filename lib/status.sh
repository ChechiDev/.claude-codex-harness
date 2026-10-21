#!/usr/bin/env bash
# status.sh <ok|skip|warn|fail> <message…> — print one status line with the
# harness tag colors (see _colors.sh). For Makefiles and other non-bash callers.
set -euo pipefail

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_colors.sh"

kind="${1:-}"; shift || true
case "$kind" in
  ok|skip|warn|fail) "$kind" "$*" ;;
  *) echo "usage: status.sh ok|skip|warn|fail <message>" >&2; exit 2 ;;
esac
