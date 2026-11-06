#!/usr/bin/env bash
# Sealed legacy inventory (plan 0.6, see _legacy.py). Human-run only: no make target.
#   harness/legacy_inventory.sh create --at <commit>
#   harness/legacy_inventory.sh review     (Codex, read-only; needs network)
#   harness/legacy_inventory.sh seal
#   harness/legacy_inventory.sh verify
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_legacy.py" "$@"
