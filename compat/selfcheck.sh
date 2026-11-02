#!/usr/bin/env bash
# Isolation self-check of the C0 check container (plan 0.3). Fails, never warns.
set -euo pipefail
fail() {
  echo "compat-selfcheck: $*" >&2
  exit 71
}
mount_opts() { awk -v m="$1" '$2 == m { opts = $4 } END { print opts }' /proc/mounts; }
require_ro() {
  case ",$(mount_opts "$1")," in
    *,ro,*) ;;
    *) fail "$2 is not read-only" ;;
  esac
}

# Network: only the loopback interface may exist (no route, no DNS).
for dev in /sys/class/net/*; do
  [ "${dev##*/}" = lo ] || fail "network interface present: ${dev##*/} (expected only lo)"
done

require_ro / "root filesystem (read-only)"
[ -d /toolkit ] && require_ro /toolkit "/toolkit mount"
[ -d /deps ] && require_ro /deps "/deps mount"

# No Unix socket may be reachable through any mount (PostgreSQL, docker, ssh-agent...).
for m in /work /toolkit /deps; do
  [ -d "$m" ] || continue
  found="$(find "$m" -type s -print -quit 2>/dev/null || true)"
  [ -z "$found" ] || fail "unix socket in a mount: $found"
done
exit 0
