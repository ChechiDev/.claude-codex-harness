#!/usr/bin/env bash
# scaffold_python.sh — minimal Python layer so a harness project runs green.
# Single responsibility: the Python scaffold. The harness layer is install.sh;
# this script knows nothing about it and can be run on its own.
#
#   scaffold_python.sh <project-dir> <package-name> [--force]
#
# It is the minimal counterpart to the full `py-init` skill (git, CI, remote,
# graphify, archify). Both read the SAME canonical template, so there is no
# config duplication: skills/py-style/assets/pyproject.toml.
#
# Idempotent and non-destructive:
#   - pyproject.toml: written from the canonical template (backed up if it exists
#     and is not already the canonical one); skipped if already canonical.
#   - src/<pkg>/__init__.py: written only if it has no __version__ yet.
#   - README.md (required by the pyproject), src/<pkg>/py.typed + tests/unit/:
#     created only if missing (no smoke test: an empty suite is green in the
#     Makefile).
#   - conftest.py (the profile's test-selection rules) and .pre-commit-config.yaml
#     (opt-in hooks, `make hooks`): copied only if missing, never overwritten.
set -euo pipefail

TOOLKIT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$TOOLKIT/skills/py-style/assets/pyproject.toml"
PRECOMMIT="$TOOLKIT/skills/github/assets/.pre-commit-config.yaml"
CONFTEST="$TOOLKIT/skills/py-pytest/assets/root_conftest.py"

DEST="${1:-}"; PKG="${2:-}"; FORCE=0
[ "${3:-}" = "--force" ] && FORCE=1
if [ -z "$DEST" ] || [ -z "$PKG" ]; then
  echo "usage: scaffold_python.sh <project-dir> <package-name> [--force]" >&2; exit 2
fi
if ! printf '%s' "$PKG" | grep -qE '^[a-z_][a-z0-9_]*$'; then
  echo "error: package name '$PKG' is not a valid Python identifier (snake_case)" >&2; exit 2
fi
for f in "$TEMPLATE" "$PRECOMMIT" "$CONFTEST"; do
  [ -f "$f" ] || { echo "error: canonical template not found: $f" >&2; exit 1; }
done
mkdir -p "$DEST"
DEST="$(cd "$DEST" && pwd)"

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_colors.sh"
# Output: one line for what was created, one for what was kept.
DONE=(); KEPT=()
did()  { DONE+=("$1"); }
skip() { KEPT+=("$1"); }

DIST="${PKG//_/-}"   # distribution name: snake_case -> kebab-case

# ── pyproject.toml ───────────────────────────────────────────────────────────
PYPROJECT="$DEST/pyproject.toml"
if [ -f "$PYPROJECT" ] && grep -q "Clean-code caps" "$PYPROJECT" && [ "$FORCE" -ne 1 ]; then
  skip "pyproject.toml"
else
  if [ -f "$PYPROJECT" ]; then
    cp "$PYPROJECT" "$PYPROJECT.bak.$(date +%Y%m%d%H%M%S)"
  fi
  sed -e "s/^name = \"your-project\"/name = \"$DIST\"/" \
      -e "s/known-first-party = \[\"your_package\"\]/known-first-party = [\"$PKG\"]/" \
      -e "s|^# Add packages = .*|packages = [\"$PKG\"]|" \
      "$TEMPLATE" > "$PYPROJECT"
  did "pyproject.toml"
fi

# ── src/<pkg>/__init__.py + py.typed ─────────────────────────────────────────
PKGDIR="$DEST/src/$PKG"
mkdir -p "$PKGDIR"
INIT="$PKGDIR/__init__.py"
if [ -f "$INIT" ] && grep -q "__version__" "$INIT" && [ "$FORCE" -ne 1 ]; then
  skip "src/$PKG/__init__.py"
else
  printf '__version__: str = "0.1.0"\n' > "$INIT"
  did "src/$PKG/__init__.py"
fi
if [ -f "$PKGDIR/py.typed" ]; then
  skip "src/$PKG/py.typed"
else
  touch "$PKGDIR/py.typed"; did "src/$PKG/py.typed"
fi

# ── README.md — the canonical pyproject declares it (uv cannot build without) ──
[ -f "$DEST/README.md" ] || { printf '# %s\n' "$PKG" > "$DEST/README.md"; did "README.md"; }

# ── tests/unit/ — real project tests only (pytest "no tests" is green) ──────
[ -d "$DEST/tests/unit" ] || { mkdir -p "$DEST/tests/unit"; did "tests/unit/"; }

# ── conftest.py + .pre-commit-config.yaml — the project's once written ──────
for pair in "conftest.py:$CONFTEST" ".pre-commit-config.yaml:$PRECOMMIT"; do
  name="${pair%%:*}"
  if [ -e "$DEST/$name" ]; then skip "$name"; else cp "${pair#*:}" "$DEST/$name"; did "$name"; fi
done

[ ${#DONE[@]} -gt 0 ] && row "$GREEN" "[python]" "${DONE[*]}"
[ ${#KEPT[@]} -gt 0 ] && row "$ORANGE" "[SKIP]" "${KEPT[*]} (exist; --force to rewrite)"
exit 0
