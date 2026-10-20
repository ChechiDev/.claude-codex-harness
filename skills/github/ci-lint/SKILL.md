---
name: ci-lint
description: "Claude only: Trigger: lint workflow, ruff CI, flake8 CI, mypy CI, github actions lint. Generate lint jobs (ruff, flake8, mypy) for .github/workflows/ci.yml."
license: Apache-2.0
metadata:
  author: "chechidev"
  version: "1.0"
---

## Activation Contract

Claude only. Load when:
- Writing or reviewing the lint section of `.github/workflows/ci.yml`
- User asks about ruff, flake8, or mypy in CI

Always load `ci-setup` first. Output goes into `.github/workflows/ci.yml` alongside `ci-unit` and `ci-integration`.

## Hard Rules

1. **Three parallel jobs**: `ruff`, `flake8`, and `mypy` run as independent parallel jobs. Never combine them into one step — separate jobs give separate failure signals.
2. **ruff checks both lint and format**: Run `uv run ruff check .` AND `uv run ruff format --check .` in the same job.
3. **mypy via config only**: `uv run mypy` — no paths or flags. Strictness and targets are defined in `pyproject.toml` (`strict = true`, `packages = [...]`). Tests are excluded via `[[tool.mypy.overrides]]`.
4. **Triggers**: `push` to any branch + `pull_request`. Both events.
5. **No `needs`**: Lint jobs have no dependencies — they run first. Unit and integration jobs declare `needs` on these.

## Decision Gates

| Situation | Action |
|-----------|--------|
| ruff format fails in CI | Fix with `uv run ruff format .` locally |
| mypy fails on tests/ | Check `[[tool.mypy.overrides]]` in `pyproject.toml` — tests have `disallow_untyped_defs = false` |
| Adding a new linter | Add as a new parallel job, never append to existing job |

## Execution Steps

1. Define `on: push + pull_request` trigger.
2. Add `concurrency` block (see `../ci-setup/SKILL.md`).
3. Add `permissions: contents: read`.
4. Write three parallel jobs: `ruff`, `flake8`, `mypy` — each with the canonical setup steps.
5. See `../assets/ci.yml` for the complete job definitions.

## Output Contract

- Three separate jobs: `ruff`, `flake8`, `mypy`.
- All three use `uv sync --group lint`.
- `ruff` job runs both `ruff check` and `ruff format --check`.
- `mypy` runs as `uv run mypy` — configuration drives targets and strictness.
- No `needs:` on any lint job.

## References

- `../assets/ci.yml` — complete workflow (lint jobs: ruff, flake8, mypy)
- `../ci-setup/SKILL.md` — canonical setup steps
- `../../py-style/SKILL.md` — toolchain rules (rule 18)
