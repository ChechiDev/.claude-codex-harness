# Review checklist: security surface

Applied on top of your role when the subtask has the `security` surface (issue #44).
Your verdict gets a `## Checklists` section with one line per item: `met`, `gap`
(with file:line) or `n/a` (with why). Any `gap` means CHANGES_REQUESTED.

- [ ] Input from outside (HTTP, files, env, CLI, queues) is validated at the boundary
      with a schema; invalid input is rejected, not coerced.
- [ ] Authentication and authorization are explicit on every new entry point, with
      least privilege; a test proves the unauthorized case is refused.
- [ ] No injection: SQL is parameterized, shell commands take argument lists (no
      string concatenation), templates escape their output.
- [ ] Secrets stay out of the code, the logs, the error messages and the tests.
- [ ] No sensitive data (passwords, tokens, PII) in logs or responses; errors do not
      leak internals.
- [ ] New dependencies are pinned and needed; no code is downloaded or executed at
      run time.
