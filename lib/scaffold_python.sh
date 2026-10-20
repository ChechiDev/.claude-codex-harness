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
#   - src/<pkg>/py.typed + tests/unit/test_smoke.py: created only if missing.
set -euo pipefail

TOOLKIT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$TOOLKIT/skills/py-style/assets/pyproject.toml"

DEST="${1:-}"; PKG="${2:-}"; FORCE=0
[ "${3:-}" = "--force" ] && FORCE=1
if [ -z "$DEST" ] || [ -z "$PKG" ]; then
  echo "usage: scaffold_python.sh <project-dir> <package-name> [--force]" >&2; exit 2
fi
if ! printf '%s' "$PKG" | grep -qE '^[a-z_][a-z0-9_]*$'; then
  echo "error: package name '$PKG' is not a valid Python identifier (snake_case)" >&2; exit 2
fi
[ -f "$TEMPLATE" ] || { echo "error: canonical template not found: $TEMPLATE" >&2; exit 1; }
mkdir -p "$DEST"
DEST="$(cd "$DEST" && pwd)"

GREEN='\033[0;32m'; YELLOW='\033[0;33m'; NC='\033[0m'
did()  { printf "${GREEN}[python]${NC} %s\n" "$1"; }
skip() { printf "${YELLOW}[python]${NC} %s\n" "$1"; }

DIST="${PKG//_/-}"   # distribution name: snake_case -> kebab-case

# ── pyproject.toml ───────────────────────────────────────────────────────────
PYPROJECT="$DEST/pyproject.toml"
if [ -f "$PYPROJECT" ] && grep -q "Clean-code caps" "$PYPROJECT" && [ "$FORCE" -ne 1 ]; then
  skip "pyproject.toml already canonical (use --force to rewrite)"
else
  if [ -f "$PYPROJECT" ]; then
    cp "$PYPROJECT" "$PYPROJECT.bak.$(date +%Y%m%d%H%M%S)"
  fi
  sed -e "s/^name = \"your-project\"/name = \"$DIST\"/" \
      -e "s/known-first-party = \[\"your_package\"\]/known-first-party = [\"$PKG\"]/" \
      -e "s|^# Add packages = .*|packages = [\"$PKG\"]|" \
      "$TEMPLATE" > "$PYPROJECT"
  did "pyproject.toml (name=$DIST, package=$PKG)"
fi

# ── src/<pkg>/__init__.py + py.typed ─────────────────────────────────────────
PKGDIR="$DEST/src/$PKG"
mkdir -p "$PKGDIR"
INIT="$PKGDIR/__init__.py"
if [ -f "$INIT" ] && grep -q "__version__" "$INIT" && [ "$FORCE" -ne 1 ]; then
  skip "src/$PKG/__init__.py already has __version__"
else
  printf '__version__: str = "0.1.0"\n' > "$INIT"
  did "src/$PKG/__init__.py (__version__)"
fi
if [ -f "$PKGDIR/py.typed" ]; then
  skip "src/$PKG/py.typed exists"
else
  touch "$PKGDIR/py.typed"; did "src/$PKG/py.typed"
fi

# ── tests/unit/test_smoke.py ─────────────────────────────────────────────────
mkdir -p "$DEST/tests/unit"
SMOKE="$DEST/tests/unit/test_smoke.py"
if [ -f "$SMOKE" ]; then
  skip "tests/unit/test_smoke.py exists"
else
  cat > "$SMOKE" <<EOF
import $PKG


def test_version() -> None:
    """Package is importable and exposes a non-empty __version__."""
    assert $PKG.__version__ != ""
EOF
  did "tests/unit/test_smoke.py"
fi

echo "Python layer ready. Next: uv sync --all-groups && make verify"
