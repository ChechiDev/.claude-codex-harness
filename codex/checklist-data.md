# Review checklist: data surface

Applied on top of your role when the subtask has the `data` surface (issue #44).
Your verdict gets a `## Checklists` section with one line per item: `met`, `gap`
(with file:line) or `n/a` (with why). Any `gap` means CHANGES_REQUESTED.

- [ ] Writes that belong together run in one explicit transaction; a failure leaves
      no partial state.
- [ ] Constraints (unique, not null, foreign keys) live in the schema, not only in the
      code, and a test hits each new one.
- [ ] Queries are parameterized and bounded (pagination or limits); no N+1 on a path
      that grows with the data.
- [ ] Persistence models are mapped to domain entities in the adapter; the domain does
      not import the ORM.
- [ ] Deletes and overwrites are intended, scoped and tested; nothing deletes more
      rows than the spec says.
- [ ] Personal data is stored, logged and returned only where the spec requires it.
