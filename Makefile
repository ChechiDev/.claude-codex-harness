# Toolkit test entry points (docs/testing.md). The default pytest run is the fast tier.
.PHONY: test test-e2e test-all lint

test:
	uv run pytest -n auto

test-e2e:
	uv run pytest -n auto -o addopts= -q -m e2e

test-all:
	uv run pytest -n auto -o addopts= -q

lint:
	uv run ruff check . && uv run mypy
