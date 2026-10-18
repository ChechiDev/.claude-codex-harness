# Makefile — single source of truth for the commands. Always uv.
# Customize it per project (add docker, scraping targets, etc.),
# but keep lint/type/test/verify/check/plan as the contract the harness uses.
.PHONY: lint type test verify check plan install-dev

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

install-dev:
	uv sync
