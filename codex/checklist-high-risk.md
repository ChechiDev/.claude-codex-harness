# Review checklist: high risk

Applied on top of your role when the subtask is rated `risk: high` (declared, sealed or
implied by its files; issue #44). Your verdict gets a `## Checklists` section with one
line per item: `met`, `gap` (with file:line) or `n/a` (with why). Any `gap` means
CHANGES_REQUESTED.

- [ ] Blast radius: list every caller and data path the change reaches; each one is
      covered by a test or named as out of scope in the spec.
- [ ] Failure modes: every new error path fails closed (no silent default, no swallowed
      exception) and is exercised by at least one test.
- [ ] Rollback: the change can be reverted without data loss, or the spec says how it
      is undone.
- [ ] Concurrency and retries: shared state, idempotency and partial failure are
      handled or shown not to apply.
- [ ] Tests assert the behaviour, not the implementation: they would fail if the
      change were reverted.
- [ ] Nothing outside `files:` changed, and no check was weakened to make it pass.
