# Reference documentation

> Per-feature output documentation, **authored by Codex** at feature close
> (after `APPROVED`). One file per task/subtask, named `<id>-<name>.md`, where
> `<id>` matches the id in `docs/plan/` and `feature_list.json`.

- This is **output** documentation ("reference" in the Diátaxis sense): what was
  built, how it works, design decisions, contracts.
- It is durable and versioned — not session state (that lives in `progress/`).
- Claude does not write here; Codex does, as part of the close.

Examples:

```
docs/reference/1.1-user_registration.md
docs/reference/1.1.2-sqlalchemy_user_adapter.md
```
