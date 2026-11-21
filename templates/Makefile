# Makefile — single source of truth for the commands. Always uv.
# Customize it per project (add docker, scraping targets, etc.), but keep
# lint/type/test/verify/env-check: the harness targets (plan, status, check,
# issue…) come from harness/harness.mk, included at the end, and call them.
.PHONY: lint type test verify env-check install-dev

# Colored status tags (ok/skip/warn/fail), plain when not on a terminal.
STATUS := ./harness/status.sh

# Scoped lint (#76): the post-write hook runs `make lint LINT_FILES=<written file>`
# for files with a LINT_EXTS extension; plain `make lint` checks the whole project.
LINT_FILES ?= .
LINT_EXTS := .py .pyi

lint:
	uv run ruff check -- $(LINT_FILES)
	uv run ruff format --check -- $(LINT_FILES)

type:
	uv run mypy src

# pytest exits 5 when it collects nothing: a fresh project is green, not red.
test:
	@uv run pytest; rc=$$?; \
	if [ $$rc -eq 5 ]; then $(STATUS) ok "no tests yet"; else exit $$rc; fi

# The "all green" the harness requires before approving/closing.
verify: lint type test

# Toolchain presence check, used by harness/check.sh (language-agnostic entry point).
env-check:
	@command -v uv >/dev/null 2>&1 || { $(STATUS) fail "uv not found"; exit 1; }
	@$(STATUS) ok "uv -> $$(uv --version)"

install-dev:
	uv sync

# Harness targets, shared by every project (keep this block last). harness/ is a
# per-machine symlink: where it is missing (a CI checkout, a new machine) only these
# targets fail; the project's own targets above still run.
ifneq ($(wildcard harness/harness.mk),)
include harness/harness.mk
else
.PHONY: plan status check issue issue-start issue-new
plan status check issue issue-start issue-new:
	@echo "harness/ is missing on this machine: run install.sh <project> first" >&2; exit 1
endif
