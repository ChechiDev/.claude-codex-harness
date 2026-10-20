---
name: ci-setup
description: "Claude only: Trigger: github actions, workflow, CI setup, uv setup, python setup, actions/checkout. Canonical GitHub Actions setup steps for this project."
model: sonnet
effort: low
license: Apache-2.0
metadata:
  author: "chechidev"
  version: "1.2"
---

## Activation Contract

Claude only. Load when:
- Writing any GitHub Actions workflow job for this project
- Reviewing existing workflows for setup correctness
- Any other CI skill references setup steps

Always load before `ci-lint`, `ci-unit`, `ci-integration`, or `ci-e2e`.

## Hard Rules

1. **Python version**: Always `python-version: "3.12"`. Never `"3.x"` or `latest`.
2. **uv action**: Always `astral-sh/setup-uv@v4` with `enable-cache: true`. Never install uv via `pip` or `curl` in CI.
3. **Pin action versions**: Always pin with `@v4`, `@v5` — never `@latest` or `@main` for setup actions. Exception: `MishaKav/pytest-coverage-comment@main` (no stable tag available).
4. **Install only what the job needs**: Each job installs only its dependency group — `--group lint` for linters, `--group test` for tests. Never `--all-groups` in CI.
5. **Concurrency**: Every workflow MUST define `concurrency`. Default `cancel-in-progress: true` to cancel stale runs on new pushes. Exception: e2e workflows use `cancel-in-progress: false` — scheduled runs must complete (see `ci-e2e` Rule 7).
6. **Permissions**: Declare only the minimum permissions needed. Coverage comment jobs need `pull-requests: write`. Default to `contents: read`.
7. **Runner**: Always `ubuntu-latest` unless a platform-specific test requires otherwise.
8. **`timeout-minutes` on every job**: Every job MUST declare a timeout. Defaults by type — lint: `10`, unit: `15`, integration: `20`, e2e: `30`. Without this, a hanging test consumes the runner for up to 6 hours.
9. **`paths-ignore` on push triggers**: Every workflow with a `push` trigger MUST add `paths-ignore` to skip CI for non-code changes. Canonical list: `["**.md", "docs/**", ".gitignore", ".editorconfig"]`. This does not apply to `pull_request` — PRs always run full CI.

## Decision Gates

| Situation | Action |
|-----------|--------|
| Job runs linters | `uv sync --group lint` |
| Job runs tests | `uv sync --group test` |
| Job needs both | `uv sync --group lint --group test` |
| Workflow posts PR comments | Add `permissions: pull-requests: write` at workflow level |
| New action needed | Pin to latest stable tag, not `@latest` |
| Push to docs/readme only | `paths-ignore` skips the workflow — no CI run |
| Job hangs in CI | Set `timeout-minutes` — lint: 10, unit: 15, integration: 20, e2e: 30 |

## Execution Steps

1. Start every job with `actions/checkout@v4`.
2. Add `astral-sh/setup-uv@v4` with `enable-cache: true` and `python-version: "3.12"`.
3. Run `uv sync` with only the required group.
4. Add `concurrency` block at workflow level.
5. See `../assets/setup-steps.yml` for the canonical step block.

## Output Contract

- Every job starts with checkout + setup-uv.
- `enable-cache: true` present on every setup-uv step.
- `python-version: "3.12"` explicit on every setup-uv step.
- `concurrency.cancel-in-progress: true` on every workflow (exception: e2e workflows use `false` — see Rule 5).
- No `--all-groups` in CI installs.
- `timeout-minutes` declared on every job.
- `paths-ignore` present on every `push` trigger.

## References

- `../assets/setup-steps.yml` — canonical reusable step block
- `../../py-style/assets/pyproject.toml` — dependency group definitions
