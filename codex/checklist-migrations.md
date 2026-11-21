# Review checklist: migrations surface

Applied on top of your role when the subtask has the `migrations` surface (issue #44).
Your verdict gets a `## Checklists` section with one line per item: `met`, `gap`
(with file:line) or `n/a` (with why). Any `gap` means CHANGES_REQUESTED.

- [ ] The schema changes only through a new versioned migration; no existing migration
      was edited.
- [ ] The migration has a working downgrade, or the spec says why it is irreversible.
- [ ] It is safe on a populated table: new non-null columns have a default or a
      backfill, and long locks on large tables are avoided or justified.
- [ ] Data migrations are idempotent and do not depend on application code that can
      change later.
- [ ] The models and the migration agree (an autogenerate diff would be empty).
- [ ] A test runs the migration up (and down, when reversible) on a database.
