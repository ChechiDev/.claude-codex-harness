---
name: implement
description: "Claude only: Trigger: implement subtask, start implementation, code, subtask N.M.K. Implementation loop: branch, test-first, code, verify, commit, output format."
license: Apache-2.0
metadata:
  author: "chechidev"
  version: "1.0"
---

## Activation Contract

Claude only. Load when receiving a subtask to implement from the orchestrator.

Standards always active: `py-pytest`, `py-style`, `py-pydantic`, `py-typing`, `commit`, `branch`.

## Hard Rules

1. **One subtask per session.** Never batch multiple subtasks.
2. **Read only the current subtask and its listed files.** Do not scan the repo beyond what the subtask references. The plan file (`docs/plan/<file>.md`) is always read to get the subtask spec — that is not scanning. Editing it is allowed only for the `Status` field.
3. **Test first.** Write the test, confirm it fails, then implement.
4. **Never commit with failing tests, ruff, or mypy.**
5. **Scope is the subtask.** Never change files outside the `Files` list. If an out-of-scope change is needed → stop, report `STATUS: blocked`.
6. **Never touch CI files** (`ci.yml`, `e2e.yml`, anything under `.github/workflows/`) unless the subtask explicitly lists them in `Files`.
7. **Reply only with the Output format.** No explanations unless blocked.

## Implementation Loop

1. **Create the task branch** if it does not exist:
   `git checkout main && git pull origin main && git checkout -b <type>/<task-id>-<short-name>`
   Use the `branch` standard for naming. If the branch already exists: `git checkout <branch-name>`.
2. **Read the subtask**: open `docs/plan/<phase-file>.md`, read only the `N.M.K` section and the files listed in `Files`.
   If the subtask touches code with potential callers outside `Files`, run Graphify before editing:
   ```bash
   uv run graphify affected "<module>"
   uv run graphify path <module-A> <module-B>
   ```
   If impact analysis reveals safe implementation requires touching files not in `Files` → report `STATUS: blocked`. Never expand scope unilaterally.
3. **Set Status to `in progress`** in the subtask.
4. **Write the test** as specified in the `Test` field. Run the `Done when` command and confirm it **fails** — if it already passes, the behavior is already implemented; stop and report `STATUS: blocked`.
5. **Implement** the code changes in the files listed in `Files`.
6. **Verify**:
   ```bash
   <Done when command>
   uv run ruff check .
   uv run mypy
   ```
   All three must pass. Fix any failure before continuing.
7. **Set Status to `done`** in the subtask.
8. **Commit** using the `commit` standard:
   `<type>(<scope>): <what> [N.M.K]` + 2-4 line body (why) + `Plan: docs/plan/<file>.md#NMK`
9. **Reply** with the Output format.

## Output Format

```
STATUS: done | blocked
SUBTASK: N.M.K
COMMIT: <hash> | none
FILES: <comma-separated list> | none
DONE_WHEN: pass | fail (<command output summary>)
NOTES: <only if blocked or relevant; 1-2 lines>
DECISIONS: <non-obvious choices or discarded alternatives; "none" if nothing relevant>
```

`COMMIT` and `FILES` are `none` when `STATUS: blocked`.

## Blocked Protocol

Stop and reply `STATUS: blocked` when:
- The spec is ambiguous and cannot be resolved from the subtask text alone.
- Implementing the subtask requires changing files not listed in `Files`.
- The `Done when` command fails and the fix is out of scope.
- A dependency (another subtask) is not yet `done`.

Never guess. Never expand scope. Never edit the plan beyond the `Status` field.

## References

- `../py-pytest/SKILL.md` — test standards
- `../py-style/SKILL.md` — code style
- `../py-pydantic/SKILL.md` — Pydantic validation patterns
- `../py-typing/SKILL.md` — type annotation standards
- `../github/commit/SKILL.md` — commit format
- `../github/branch/SKILL.md` — branch naming
