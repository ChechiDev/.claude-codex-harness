#!/usr/bin/env bash
# Fail if the environment holds any variable outside the allowlist (C0, plan 0.3).
# Docker always sets PATH, HOSTNAME and HOME; bash adds PWD, SHLVL, _ and OLDPWD.
set -euo pipefail
allowed=" PATH HOSTNAME HOME LANG UV_CACHE_DIR UV_PROJECT_ENVIRONMENT UV_PYTHON_DOWNLOADS"
allowed+=" UV_OFFLINE UV_NO_SYNC PWD SHLVL _ OLDPWD "
bad=()
while IFS= read -r -d '' entry; do
  name="${entry%%=*}"
  case "$allowed" in
    *" $name "*) ;;
    *) bad+=("$name") ;;
  esac
done < <(env -0)
if ((${#bad[@]})); then
  echo "compat-guard: variables outside the allowlist: ${bad[*]}" >&2
  exit 70
fi
