---
name: ci-lint
description: "Claude only: Trigger: lint workflow, ruff CI, mypy CI, deptry CI, pip-audit CI, github actions lint. Generate the static-check jobs (lint, type, deps, audit) for .github/workflows/ci.yml."
license: Apache-2.0
metadata:
  author: "chechidev"
  version: "2.0"
---

## Activation Contract

Claude only. Load when:
- Writing or reviewing the static-check section of `.github/workflows/ci.yml`
- User asks about ruff, mypy, deptry or pip-audit in CI

Always load `ci-setup` first. Output goes into `.github/workflows/ci.yml` alongside `ci-unit` and `ci-integration`.

## Hard Rules

1. **Four parallel jobs, one Makefile target each**: `lint` (`make lint`: ruff check + format check), `type` (`make type`: mypy strict), `deps` (`make deps`: deptry) and `audit` (`make audit`: pip-audit). Separate jobs give separate failure signals; the commands live in the Makefile only, never re-typed in the workflow.
2. **Same commands as local**: the jobs call the same targets as `make verify` / the harness's `make check`, so CI cannot pass where the local gate fails (or the reverse). Ruff is pinned in `pyproject.toml`: CI runs the exact tested version.
3. **`audit` needs the network**: it is outside `make verify` (local runs stay offline); in CI it runs in its own job with no `uv sync` (it exports the lock and runs `uvx pip-audit`).
4. **Triggers**: `push` to any branch + `pull_request`. Both events.
5. **No `needs`**: static jobs run first. The `test` and `integration` jobs declare `needs: [lint, type]` — jobs of the same workflow only.

## Decision Gates

| Situation | Action |
|-----------|--------|
| ruff fails in CI | `make fix` locally (safe fixes + format), then `make lint` |
| mypy fails on tests/ | Check `[[tool.mypy.overrides]]` in `pyproject.toml` |
| deptry reports DEP001/DEP002 | Declare the import (`uv add`) or remove the unused dependency |
| pip-audit reports a vulnerability | Upgrade the dependency (`uv lock --upgrade-package <pkg>`) |
| Adding a new checker | A Makefile target + a new parallel job calling it |

## Execution Steps

1. Define `on: push + pull_request` trigger.
2. Add `concurrency` block (see `../ci-setup/SKILL.md`).
3. Add `permissions: contents: read`.
4. Write the four parallel jobs — each with the canonical setup steps and one `make` target.
5. See `../assets/ci.yml` for the complete job definitions.

## Output Contract

- Jobs `lint`, `type`, `deps`, `audit`, each running one `make` target.
- `lint`, `type`, `deps` use `uv sync --locked`.
- No tool command re-typed in the workflow (no `uv run ruff …` lines).
- No `needs:` on any static job.

## References

- `../assets/ci.yml` — complete workflow
- `../ci-setup/SKILL.md` — canonical setup steps
- `../../py-style/SKILL.md` — toolchain rules (rule 18)
