---
name: ci-unit
description: "Claude only: Trigger: unit test CI, test job, coverage CI, coverage comment PR, make test. Generate the test job with coverage comment for .github/workflows/ci.yml."
license: Apache-2.0
metadata:
  author: "chechidev"
  version: "2.0"
---

## Activation Contract

Claude only. Load when:
- Writing or reviewing the `test` job of `.github/workflows/ci.yml`
- User asks about coverage reporting or the coverage comment on PRs in CI

Always load `ci-setup` first. Output goes into `.github/workflows/ci.yml`.

## Hard Rules

1. **One tests job: `make test`**: it runs every test except the `integration` and `e2e` markers (the selection is defined once, in the Makefile), measures branch coverage and enforces `[tool.coverage.report] fail_under` (80). Never re-type pytest, `-m` expressions, `--cov` flags or a threshold in the workflow.
2. **`needs: [lint, type]`**: tests MUST NOT run if a static job fails. Only jobs of the same workflow.
3. **Coverage XML for the comment**: `make test COVERAGE_XML=coverage.xml`. The threshold is still the pyproject one — a single source.
4. **Coverage comment on PR only**: `MishaKav/pytest-coverage-comment@main` with `if: always() && github.event_name == 'pull_request' && hashFiles('coverage.xml') != ''` (a red threshold still posts the report).
5. **`permissions: pull-requests: write`**: at workflow level.
6. **No tests yet is green**: `make test` treats "nothing selected" (a new project, or only integration/e2e tests) as green, and a suite emptied by a wrong `-k`/`-m` as red.

## Decision Gates

| Situation | Action |
|-----------|--------|
| Coverage drops below 80% | Job fails — add tests for the uncovered branches |
| Coverage comment not appearing | Check `permissions: pull-requests: write` at workflow level |
| A test needs a database | Mark it `integration`: it moves to the `integration` job |
| Running on push (not PR) | Coverage report prints to the log only — no comment posted |

## Execution Steps

1. Declare `needs: [lint, type]`.
2. Add canonical setup steps (`uv sync --locked`).
3. Run `make test COVERAGE_XML=coverage.xml`.
4. Add the coverage comment step (rule 4).
5. See `../assets/ci.yml` for the complete job definition.

## Output Contract

- `needs: [lint, type]` declared.
- `make test COVERAGE_XML=coverage.xml` is the only test command.
- No coverage flags or threshold anywhere in the workflow.
- Coverage comment step restricted to pull requests.
- `permissions: pull-requests: write` at workflow level.

## References

- `../assets/ci.yml` — complete workflow (test job)
- `../ci-setup/SKILL.md` — canonical setup steps
- `../../py-pytest/SKILL.md` — selection and coverage rules (rules 3, 10, 17)
