#!/usr/bin/env bash
# Isolation self-check of the C0 containers (plan 0.3). Fails, never warns.
# Options (only the dependency-prep containers pass them, via entrypoint_prep.sh):
#   --allow-network   skip the network check (prep phase "fetch" only)
#   --rw PATH         PATH may be writable (only /deps is accepted)
set -euo pipefail
fail() {
  echo "compat-selfcheck: $*" >&2
  exit 71
}
allow_network=0
rw_deps=0
while (($#)); do
  case "$1" in
    --allow-network) allow_network=1 ;;
    --rw)
      [ "${2:-}" = /deps ] || fail "option --rw only accepts /deps (got '${2:-}')"
      rw_deps=1
      shift
      ;;
    *) fail "unknown option: $1" ;;
  esac
  shift
done

mount_opts() { awk -v m="$1" '$2 == m { opts = $4 } END { print opts }' /proc/mounts; }
require_ro() {
  case ",$(mount_opts "$1")," in
    *,ro,*) ;;
    *) fail "$2 is not read-only" ;;
  esac
}

# Network: only the loopback interface may exist (no route, no DNS).
if [ "$allow_network" -eq 0 ]; then
  for dev in /sys/class/net/*; do
    [ "${dev##*/}" = lo ] || fail "network interface present: ${dev##*/} (expected only lo)"
  done
fi

require_ro / "root filesystem (read-only)"
if [ -d /toolkit ]; then require_ro /toolkit "/toolkit mount"; fi
if [ -d /deps ] && [ "$rw_deps" -eq 0 ]; then require_ro /deps "/deps mount"; fi

# No Unix socket may be reachable through any mount (PostgreSQL, docker, ssh-agent...).
for m in /work /toolkit /deps /lock; do
  [ -d "$m" ] || continue
  found="$(find "$m" -type s -print -quit 2>/dev/null || true)"
  [ -z "$found" ] || fail "unix socket in a mount: $found"
done
exit 0
