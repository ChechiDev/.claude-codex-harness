---
name: ci-integration
description: "Claude only: Trigger: integration test CI, github actions integration, service containers, postgres CI, redis CI, PR to main. Generate the integration job (with service containers) for .github/workflows/ci.yml."
license: Apache-2.0
metadata:
  author: "chechidev"
  version: "2.0"
---

## Activation Contract

Claude only. Load when:
- Writing or reviewing the `integration` job of `.github/workflows/ci.yml`
- User asks about running integration tests against a database or cache in CI

Always load `ci-setup` first. Output goes into `.github/workflows/ci.yml` — a job of the same workflow, never a separate one.

## Hard Rules

1. **Selected by marker**: the job runs `make test-integration` (`pytest -m integration`). The marker is the only criterion — not the `tests/integration/` path. The project's root `conftest.py` fails any test under `tests/integration/` without the marker, and a marked test elsewhere still runs here.
2. **`needs: [lint, type]`**: jobs of `ci.yml` only (a `needs` cannot point to another workflow).
3. **`if: github.event_name == 'pull_request' && github.base_ref == 'main'`**: only on PRs targeting `main`.
4. **Service containers with healthcheck**: the job declares the services it needs (Postgres, Redis…) under `services:` with `options: --health-cmd …` so tests start only once they are ready. Drop the services the project does not use.
5. **No coverage enforcement**: integration tests are not part of `make test`'s coverage. No `--cov` flags.
6. **No mocks**: integration tests hit real services. Container credentials are throwaway values; any real credential MUST come from `${{ secrets.* }}`.
7. **`timeout-minutes: 20`**: always set.

## Decision Gates

| Situation | Action |
|-----------|--------|
| Integration job runs on every push | Restore the `if:` condition (rule 3) |
| Tests need a DB or cache | Keep/add a service container with a healthcheck |
| Service not ready when tests run | Raise `--health-retries` / `--health-start-period` |
| A test in tests/integration/ ran in `make test` | It cannot: conftest.py fails it unmarked — add `@pytest.mark.integration` |

## Execution Steps

1. Declare `needs: [lint, type]` and the `if:` condition.
2. Declare `services:` with healthchecks and the `env:` that points tests at them.
3. Add canonical setup steps (`uv sync --locked`).
4. Run `make test-integration`.
5. See `../assets/ci.yml` for the complete job definition.

## Output Contract

- The job lives in `ci.yml`; `needs: [lint, type]`.
- `if:` restricts it to PR → `main`.
- `make test-integration` is the test command (selection by marker).
- Every service has a healthcheck.
- No `--cov` flags; real secrets only via `${{ secrets.* }}`.

## References

- `../assets/ci.yml` — complete workflow (integration job with Postgres + Redis)
- `../ci-setup/SKILL.md` — canonical setup steps
- `../../py-pytest/SKILL.md` — markers and where each kind runs (rules 3, 17)
