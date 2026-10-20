# Makefile — single source of truth for the commands. Always uv.
# Customize it per project (add docker, scraping targets, etc.),
# but keep lint/type/test/verify/check/plan as the contract the harness uses.
.PHONY: lint type test verify check plan env-check install-dev

lint:
	uv run ruff check .
	uv run ruff format --check .

type:
	uv run mypy src

test:
	uv run pytest

# The "all green" the harness requires before approving/closing.
verify: lint type test

# Regenerate the derived feature_list.json from docs/plan/ (source of truth).
plan:
	python3 harness/sync_plan.py

# Single entry point for agent and hooks: toolchain + harness invariants.
check: verify
	./harness/check.sh

# Toolchain presence check, used by harness/check.sh (language-agnostic entry point).
env-check:
	@command -v uv >/dev/null 2>&1 || { echo "[FAIL]  uv not found"; exit 1; }
	@echo "[OK]    uv -> $$(uv --version)"

install-dev:
	uv sync
