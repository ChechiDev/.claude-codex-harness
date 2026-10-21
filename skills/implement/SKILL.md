---
name: implement
description: "Claude only: Trigger: implement subtask, start implementation, code, subtask N.M.K. Implementation loop: read the subtask, test-first, code, make verify, write the impl report."
license: Apache-2.0
metadata:
  author: "chechidev"
  version: "2.0"
---

## Activation Contract

Claude only. Load when receiving a subtask `N.M.K` to implement from the leader.

Standards: the project's `docs/conventions.md` and `docs/architecture.md` (authoritative).
Python projects also load `py-pytest`, `py-style`, `py-pydantic`, `py-typing`;
C projects follow the C conventions (C11, warning-clean compile, explicit error/ownership).

## Hard Rules

1. **One subtask per session.** Never batch multiple subtasks.
2. **Plan approved first.** No `PLAN_APPROVED` in `progress/plan_review_<N.M.K>.md` → stop
   (the harness blocks writes to `src/` anyway).
3. **Read only the subtask and its declared `files:`.** Reading the plan file and the
   acceptance test is not scanning. Never edit `docs/plan/` or `feature_list.json`.
4. **Test first.** Write the unit test, confirm it fails, then implement.
5. **Scope is the subtask.** Write under `src/` only the files in its `files:` list; unit
   tests go in `tests/unit/`. Needing another file → stop, report `blocked`.
6. **Acceptance tests are read-only** (`tests/acceptance/`, the Codex oracle). If one looks
   wrong → stop, report `blocked`.
7. **No git, no status.** The leader owns the branch and `in_progress`; Codex sets `done`
   and makes the single commit at close.
8. **Never touch CI files** (`.github/workflows/`) unless the subtask lists them.

## Implementation Loop

1. **Read the subtask spec** `docs/plan/phase-NN-<slug>/<N.M.K>-<slug>.md`: its `files:`,
   `depends`, `acceptance:` and `## Description`, plus `tests/acceptance/test_<N.M.K>.*`.
2. **Write the unit test** in `tests/unit/` and run it: it must **fail**. If it already
   passes, the behavior exists → stop, report `blocked`.
3. **Implement** in the declared `files:` only.
4. **Verify**: `make verify` (lint + type + tests: acceptance and unit). Fix until green;
   refactor while green.
5. **Report**: write `progress/impl_<N.M.K>.md` with the files touched, the decisions
   taken (non-obvious choices, discarded alternatives) and the real `make verify` output.
6. **Reply** to the leader with the Output Format and return control.

## Output Format

A single line:

```
done -> subtask <N.M.K> implemented; see progress/impl_<N.M.K>.md (pending Codex review)
```
or
```
blocked -> see progress/current.md
```

Never return the diff in chat.

## Blocked Protocol

Write the reason in `progress/current.md` and reply `blocked` when:
- The spec is ambiguous and cannot be resolved from the subtask text alone.
- Implementing it requires files not listed in `files:`.
- An acceptance test looks wrong, or `make verify` fails for an out-of-scope reason.
- A dependency (another subtask) is not yet `done`.

Never guess. Never expand scope.

## References

- `../py-pytest/SKILL.md`, `../py-style/SKILL.md`, `../py-pydantic/SKILL.md`,
  `../py-typing/SKILL.md` — Python standards
- `docs/conventions.md`, `docs/architecture.md` — project standards (all languages)
