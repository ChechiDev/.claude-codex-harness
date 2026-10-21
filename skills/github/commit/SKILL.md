---
name: commit
description: "Shared standard: Trigger: commit, git commit, commit message, subtask commit, conventional commit, commit format. Conventional commit format for subtask commits: type, scope, subject, body, plan footer."
license: Apache-2.0
metadata:
  author: "chechidev"
  version: "1.0"
---

## Activation Contract

Shared standard. Load when:
- Writing a commit message for a subtask
- Reviewing whether a commit message is well-formed
- Asking about commit format, scope, or body content

## Hard Rules

1. **Format**: `<type>(<scope>): <what> [<subtask-id>]` — subject line only, no period, imperative mood, max 72 chars.
2. **Subtask ID is three-part**: `[2.3.1]`, `[2.3.2]`, etc. from `docs/plan/`. PR title uses the two-part task ID — commits use the three-part subtask ID.
3. **Scope is mandatory**: One lowercase word describing the affected area (e.g., `stats`, `queue`, `auth`, `ci`, `parser`). If the commit spans several areas, use the primary one. If it truly cannot be scoped to one area, the subtask is too large — split it.
4. **Body is mandatory**: 2–4 lines explaining WHY. Not what — the subject already says what.
5. **Footer — `Plan:` link**: Always close with `Plan: docs/plan/phase-NN-<slug>/<N.M.K>-<slug>.md`. This links the commit back to its subtask spec in the plan.
6. **One subtask per commit**: Each commit maps to exactly one subtask ID. Never batch multiple subtasks into one commit.
7. **All messages in English**: Subject, body, and footer.
8. **Allowed types**: `feat`, `fix`, `refactor`, `test`, `chore`, `docs` — same set as branch types.
9. **Never commit with failing tests, ruff, or mypy.** All three must pass before running `git commit`. A broken scaffold or lint failure does not qualify as "partial progress".

## Commit Structure

```
<type>(<scope>): <what> [<subtask-id>]

<why — 2 to 4 lines explaining the motivation,
context, or constraint behind this change>

Plan: docs/plan/phase-NN-<slug>/<N.M.K>-<slug>.md
```

## Examples

```
feat(stats): implement shooting stats scraper [2.3.1]

The stats endpoint returns per-game shooting data but was never
persisted. This scraper pulls from the public API and writes to
the local cache so downstream analysis has stable inputs.

Plan: docs/plan/phase-02-stats/2.3.1-shooting-stats-scraper.md
```

```
fix(queue): skip jobs with stale lock timestamps [3.1.2]

Jobs were hanging indefinitely when the worker crashed mid-run.
The lock timestamp was never cleared, blocking the queue permanently.
Now jobs older than 5 minutes are released and re-queued.

Plan: docs/plan/phase-03-queue/3.1.2-remove-stale-jobs.md
```

## Execution Steps

1. Identify the subtask ID from `docs/plan/`.
2. Write the subject: `<type>(<scope>): <what> [<subtask-id>]` — max 72 chars.
3. Write the body: 2–4 lines of WHY.
4. Add the footer: `Plan: docs/plan/phase-NN-<slug>/<N.M.K>-<slug>.md`.
5. Commit using one `-m` flag per paragraph (never `\n` inside `-m` — git writes it literally; each `-m` is already a separate paragraph):
   ```bash
   git commit \
     -m "<subject>" \
     -m "<body — 2 to 4 lines of WHY on a single paragraph>" \
     -m "Plan: docs/plan/phase-NN-<slug>/<N.M.K>-<slug>.md"
   ```
   For a multi-line body, prefer a heredoc with `-F`:
   ```bash
   git commit -F - <<'EOF'
   <type>(<scope>): <what> [<subtask-id>]

   <why line 1>
   <why line 2>

   Plan: docs/plan/phase-NN-<slug>/<N.M.K>-<slug>.md
   EOF
   ```

## Output Contract

- Subject matches `<type>(<scope>): <what> [x.y.z]`.
- Body present: 2–4 lines of motivation/context.
- Footer present: `Plan: docs/plan/phase-NN-<slug>/<N.M.K>-<slug>.md`.
- No period at end of subject.
- No co-author attribution.

## References

- `~/.agents/skills/pr/SKILL.md` — PR title uses the two-part task ID; this skill uses the three-part subtask ID
- `../branch/SKILL.md` — branch and type conventions
- `~/.agents/skills/plan/SKILL.md` — plan structure, subtask IDs, and phase file naming
- `../assets/.gitmessage` — commit message template (deployed to project root by `py-init`)
