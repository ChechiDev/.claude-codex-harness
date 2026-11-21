#!/usr/bin/env bash
# PostToolUse hook (matcher: Edit|Write|MultiEdit|NotebookEdit). Fast feedback
# after each write: `make lint` only (issue #55) — the suite runs at gates, close and PR.
# Scoped lint (#76): when the project Makefile declares LINT_FILES (an assignment line), the lint runs
# on the written file only (`make lint LINT_FILES=<rel path>`); otherwise, or when
# the payload has no usable path inside the project, the full lint runs. An
# optional LINT_EXTS (e.g. `LINT_EXTS := .py .pyi`) limits scoping to those
# extensions; other files get the full lint.
# NEVER blocks (always exit 0); only informs Claude.
set -uo pipefail
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
cd "$PROJECT_DIR" || exit 0

command -v make >/dev/null 2>&1 && [ -f Makefile ] || exit 0

# Relative path of the written file, or empty. The payload is untrusted: only a
# plain path inside the project, made of safe characters, is passed to make.
REL="$(python3 -c '
import json, os, re, sys
try:
    ti = json.load(sys.stdin).get("tool_input") or {}
    raw = ti.get("file_path") or ti.get("notebook_path")
    root = os.path.realpath(os.getcwd())
    path = os.path.realpath(os.path.join(root, raw)) if isinstance(raw, str) and raw else ""
    rel = os.path.relpath(path, root) if path else ""
    exts = []
    for line in open("Makefile", encoding="utf-8", errors="replace"):
        m = re.match(r"\s*LINT_EXTS\s*[?:+]?=(.*)", line)
        if m:
            exts = m.group(1).split()
    ok_ext = not exts or os.path.splitext(rel)[1] in exts
    if rel and ok_ext and not rel.startswith("..") and re.fullmatch(r"[A-Za-z0-9._/+-]+", rel):
        print(rel)
except Exception:
    pass
' 2>/dev/null)" || REL=""

echo "[harness] fast lint after the write:"
if [ -n "$REL" ] && grep -Eq '^[[:space:]]*LINT_FILES[[:space:]]*[?:+]?=' Makefile; then
  make lint LINT_FILES="$REL" 2>&1 | tail -5 || true
else
  make lint 2>&1 | tail -5 || true
fi
exit 0
