#!/usr/bin/env bash
# Harness invariants. Invoked by `make check` (after `make verify`) and by the
# agent at session startup. Does NOT reimplement the toolchain (that is `make verify`).
set -uo pipefail

HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
cd "$PROJECT_DIR" || exit 1

. "$HARNESS_DIR/_colors.sh"

EXIT=0

# The gate's counterfactual run (issue #27): label it so its log is never taken
# for a real result.
if [ -n "${HARNESS_ASSUME_APPROVED:-}" ]; then
  echo "[ASSUMED] HARNESS_ASSUME_APPROVED=$HARNESS_ASSUME_APPROVED: its verdict reads approved and its seal matching; counterfactual run, not a real result"
  echo ""
fi
# The issue gate's third run (issue #31): the gate excluded these test files.
if [ -n "${HARNESS_EXPECT_RED:-}" ]; then
  echo "[ASSUMED] HARNESS_EXPECT_RED=$HARNESS_EXPECT_RED: the gate excluded these test files from this run; not a real result"
  echo ""
fi

echo "── 1. Environment ──────────────────────────────────────"
# Language-agnostic: delegate the toolchain check to the Makefile's env-check
# target. Fall back to the uv check for older Makefiles without that target.
if make -n env-check >/dev/null 2>&1; then
  if ! make -s --no-print-directory env-check; then EXIT=1; fi
elif command -v uv >/dev/null 2>&1; then
  ok "uv -> $(uv --version 2>/dev/null)"
else
  fail "no 'env-check' target in Makefile and uv is not installed"; EXIT=1
fi

echo ""
echo "── 2. Harness base files ───────────────────────────────"
for f in AGENTS.md CLAUDE.md Makefile CHECKPOINTS.md \
         docs/architecture.md docs/conventions.md docs/verification.md \
         docs/plan/PLAN.md \
         progress/current.md progress/history.md; do
  if [ -f "$f" ]; then ok "Exists $f"; else fail "Missing $f"; EXIT=1; fi
done
for d in .claude/agents .claude/codex harness docs/plan docs/reference; do
  if [ -e "$d" ]; then ok "Exists $d"; else fail "Missing $d (did you run install.sh?)"; EXIT=1; fi
done

echo ""
echo "── 3. Plan sync (docs/plan → feature_list + PLAN index) ─"
if ! python3 "$HARNESS_DIR/sync_plan.py" --check; then EXIT=1; fi

echo ""
echo "── 4. Plan rules (roadmap · phase by phase · subtasks) ─"
if [ -f feature_list.json ]; then
  if ! python3 "$HARNESS_DIR/_harness.py" validate; then EXIT=1; fi
else
  fail "Missing feature_list.json (run: make plan)"; EXIT=1
fi
# Stack step (issue #115): docs/stack.md approved before the discovery; WARN only for
# projects planned before it, and for dependency drift.
if ! python3 "$HARNESS_DIR/_stack.py" check; then EXIT=1; fi

echo ""
echo "── 5. Gate consistency ─────────────────────────────────"
if ! bash "$HARNESS_DIR/gate.sh"; then EXIT=1; fi

echo ""
echo "── 6. Documentation gate ───────────────────────────────"
if ! bash "$HARNESS_DIR/doc_gate.sh"; then EXIT=1; fi

echo ""
echo "── 7. Integrity (src/ scope · sealed oracle · plan) ────"
# Re-checks from git/disk what the per-call hooks guard, whatever tool wrote it.
if ! python3 "$HARNESS_DIR/_guard.py" integrity; then EXIT=1; fi

echo ""
echo "── 8. Migration level (copied harness files) ───────────"
# WARN only (plan 0.7): never changes EXIT. Fixed with install.sh --migrate.
python3 "$HARNESS_DIR/_upgrade.py" level "$PROJECT_DIR" || true

echo ""
echo "── 9. Summary ──────────────────────────────────────────"
if [ $EXIT -eq 0 ]; then
  ok "Harness invariants OK."
else
  fail "The harness is NOT healthy. Resolve the above before proceeding."
fi
exit $EXIT
