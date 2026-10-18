# Makefile — single source of truth for the commands. Always uv.
# Customize it per project (add docker, scraping targets, etc.),
# but keep lint/type/test/verify/check as the contract the harness uses.
.PHONY: lint type test verify check install-dev

lint:
	uv run ruff check .
	uv run ruff format --check .

type:
	uv run mypy src

test:
	uv run pytest

# The "all green" the harness requires before approving/closing.
verify: lint type test

# Single entry point for agent and hooks: toolchain + harness invariants.
check: verify
	./harness/check.sh

install-dev:
	uv sync
