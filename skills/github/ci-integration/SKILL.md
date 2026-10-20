---
name: ci-integration
description: "Claude only: Trigger: integration test CI, github actions integration, pytest integration, PR to main. Generate the integration test job for .github/workflows/ci.yml."
license: Apache-2.0
metadata:
  author: "chechidev"
  version: "1.1"
---

## Activation Contract

Claude only. Load when:
- Writing or reviewing the integration test section of `.github/workflows/ci.yml`
- User asks about running integration tests only on PR to main

Always load `ci-setup` first. Output goes into `.github/workflows/ci.yml`.

## Hard Rules

1. **`needs: [ruff, flake8, mypy]`**: Same as unit — integration tests must not run if lint fails.
2. **`if: github.event_name == 'pull_request' && github.base_ref == 'main'`**: Integration tests run ONLY on PRs targeting `main`. Never on plain pushes or PRs to feature branches.
3. **No coverage enforcement**: Integration tests are not subject to the 80% threshold. No `--cov` flags in this job.
4. **Verbose output**: Run with `-v` to see individual test names in the CI log — integration failures need context.
5. **No mocks**: Integration tests hit real services. If the job needs env vars for a test DB or API, declare them as `env:` using GitHub secrets.
6. **Secrets via `env:`**: Any credentials needed by integration tests MUST come from `${{ secrets.SECRET_NAME }}` — never hardcoded.
7. **`timeout-minutes: 20`**: Always set on this job. Integration tests hit real services and can hang.
8. **Service containers with healthcheck**: When a job requires a DB or cache, declare it under `services:` with a `health_check` that waits until the service is ready. Never assume the service is up immediately — always use `options: --health-cmd`. See `../assets/integration-with-services.yml` for the canonical Postgres + Redis pattern.

## Decision Gates

| Situation | Action |
|-----------|--------|
| Integration job runs on every push | Remove the `if:` condition — it's missing or wrong |
| Tests need a DB or external service | Add `env:` block with secrets, or add a service container |
| Adding a service container (e.g. Postgres) | Use `../assets/integration-with-services.yml` pattern — always include healthcheck |
| Service container not ready when tests run | Add `--health-retries` + `--health-start-period` to service options |

## Execution Steps

1. Declare `needs: [ruff, flake8, mypy]`.
2. Add `if: github.event_name == 'pull_request' && github.base_ref == 'main'`.
3. Add canonical setup steps with `uv sync --group test`.
4. Run `uv run pytest tests/integration/ -v`.
5. Add `env:` block for any required secrets.
6. See `../assets/ci.yml` for the complete job definition.

## Output Contract

- `needs: [ruff, flake8, mypy]` declared.
- `if:` condition restricts to PR → `main` only.
- No `--cov` flags.
- `-v` flag present on pytest command.
- Secrets accessed via `${{ secrets.* }}` only.

## References

- `../assets/ci.yml` — complete workflow (integration job section)
- `../assets/integration-with-services.yml` — integration job with Postgres + Redis service containers
- `../ci-setup/SKILL.md` — canonical setup steps
- `../../py-pytest/SKILL.md` — integration test rules (rule 4)
