---
name: ci-unit
description: "Claude only: Trigger: unit test CI, coverage CI, coverage comment PR, pytest unit. Generate the unit test job with coverage comment for .github/workflows/ci.yml."
license: Apache-2.0
metadata:
  author: "chechidev"
  version: "1.0"
---

## Activation Contract

Claude only. Load when:
- Writing or reviewing the unit test section of `.github/workflows/ci.yml`
- User asks about coverage reporting or coverage comment on PR in CI

Always load `ci-setup` first. Output goes into `.github/workflows/ci.yml`.

## Hard Rules

1. **`needs: [ruff, flake8, mypy]`**: Unit tests MUST NOT run if any lint job fails. Always declare all three lint jobs as dependencies.
2. **Coverage XML output**: Run pytest with `--cov=<project-packages> --cov-report=xml --cov-report=term-missing --cov-fail-under=80`. XML is required by the coverage comment action.
3. **Coverage comment on PR only**: The `MishaKav/pytest-coverage-comment@main` step MUST have `if: github.event_name == 'pull_request'`. Never post coverage comments on plain push events.
4. **`permissions: pull-requests: write`**: Required at workflow level for the coverage comment action to post. Declare at workflow level, not job level.
5. **Fail fast on coverage**: `--cov-fail-under=80` must be present. The job fails before the comment step if coverage drops below threshold.
6. **Targets `tests/unit/` only**: Never run `pytest tests/` in this job — integration and e2e are separate jobs with different triggers.

## Decision Gates

| Situation | Action |
|-----------|--------|
| Coverage drops below 80% | Job fails — fix tests before merging |
| Coverage comment not appearing | Check `permissions: pull-requests: write` at workflow level |
| Tests pass but coverage fails | Add missing unit tests, not more e2e tests |
| Running on push (not PR) | Coverage report prints to terminal only — no comment posted |

## Execution Steps

1. Declare `needs: [ruff, flake8, mypy]`.
2. Add canonical setup steps with `uv sync --group test`.
3. Run pytest targeting `tests/unit/` with XML coverage output.
4. Add coverage comment step with `if: github.event_name == 'pull_request'`.
5. See `../assets/ci.yml` for the complete job definition.

## Output Contract

- `needs: [ruff, flake8, mypy]` declared.
- pytest targets `tests/unit/` only.
- `--cov-fail-under=80` present.
- Coverage comment step has `if: github.event_name == 'pull_request'`.
- `permissions: pull-requests: write` at workflow level.

## References

- `../assets/ci.yml` — complete workflow (unit job section)
- `../ci-setup/SKILL.md` — canonical setup steps
- `../../py-pytest/SKILL.md` — coverage rules (rule 10)
