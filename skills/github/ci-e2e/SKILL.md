---
name: ci-e2e
description: "Claude only: Trigger: e2e CI, end to end, workflow_dispatch, scheduled workflow, pytest e2e. Generate .github/workflows/e2e.yml with manual and scheduled triggers."
license: Apache-2.0
metadata:
  author: "chechidev"
  version: "1.1"
---

## Activation Contract

Claude only. Load when:
- Writing or reviewing `.github/workflows/e2e.yml`
- User asks about manual dispatch or scheduled CI runs

Always load `ci-setup` first. Output goes into a SEPARATE file: `.github/workflows/e2e.yml` — never into `ci.yml`.

## Hard Rules

1. **Separate workflow file**: E2E lives in `.github/workflows/e2e.yml`, not `ci.yml`. Different triggers make it a different workflow.
2. **Trigger — `workflow_dispatch` only**: E2E runs manually on demand. Add `schedule` only when there are stable e2e tests worth monitoring nightly — not before. Premature scheduling generates noise without value.
3. **No `needs:`**: E2E has no lint dependency — it runs on demand or on schedule, not gated by lint. Lint runs in `ci.yml` on push/PR.
4. **No coverage**: E2E tests are not subject to coverage enforcement. No `--cov` flags.
5. **Verbose + continue on error awareness**: Run with `-v`. E2E failures may be transient (network, rate limits) — the job MUST still fail (`continue-on-error: false`) so failures are visible.
6. **Secrets via `env:`**: Any real credentials (API keys, tokens) MUST come from `${{ secrets.* }}`.
7. **No `cancel-in-progress`** for scheduled runs: Scheduled runs should complete — use `cancel-in-progress: false` or omit the concurrency block.
8. **`timeout-minutes: 30`**: Always set on the e2e job. Network-dependent tests can hang indefinitely.
9. **Failure notification — add when schedule is enabled**: When a `schedule` trigger is added in the future, a `if: failure() && github.event_name == 'schedule'` notification step MUST be added at the same time. A silent scheduled failure is the same as no monitoring. See `../assets/e2e.yml` for the canonical notification pattern.
10. **`e2e-failure` label prerequisite**: The notification script creates issues with label `e2e-failure`. This label must exist before enabling the schedule. Create it with `gh label create e2e-failure --color e11d48 --description "Automated: nightly e2e run failed"` when enabling the schedule trigger.

## Decision Gates

| Situation | Action |
|-----------|--------|
| E2E fails due to transient network issue | Re-run manually via `workflow_dispatch` |
| Need to run e2e on a specific branch | Add `workflow_dispatch` input for `ref` |
| E2E needs real API credentials | Add `env:` block with `${{ secrets.* }}` |
| Scheduled run should not cancel a running one | Set `cancel-in-progress: false` |
| Scheduled run fails silently | Add `if: failure()` notification step — issue or Slack |
| E2E hangs in CI | `timeout-minutes: 30` cuts it before consuming the runner |

## Execution Steps

1. Create `.github/workflows/e2e.yml` — separate file.
2. Set `on: workflow_dispatch` only. Add `schedule` later when e2e tests are stable.
3. Add `concurrency` with `cancel-in-progress: false`.
4. Add `permissions: contents: read`.
5. Add canonical setup steps with `uv sync --group test`.
6. Run `uv run pytest tests/e2e/ -v`.
7. Add `env:` block for required secrets.
8. See `../assets/e2e.yml` for the complete workflow.

## Output Contract

- Separate file: `.github/workflows/e2e.yml`.
- Only `workflow_dispatch` trigger — no `schedule` until stable e2e tests exist.
- No `needs:`, no `--cov`.
- `-v` flag on pytest.
- `cancel-in-progress: false`.
- `timeout-minutes: 30` on the e2e job.
- Scheduled failure notification step present as a comment in `e2e.yml` — ready to uncomment when `schedule` trigger is enabled.

## References

- `../assets/e2e.yml` — complete e2e workflow file
- `../ci-setup/SKILL.md` — canonical setup steps
- `../../py-pytest/SKILL.md` — e2e test rules (rule 4)
