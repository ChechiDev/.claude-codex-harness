---
name: branch
description: "Shared standard: Trigger: branch, create branch, git branch, naming, PR, pull request, merge, branch strategy. GitHub Flow branching conventions: naming, types, PR rules, merge strategy, protection."
license: Apache-2.0
metadata:
  author: "chechidev"
  version: "1.0"
---

## Activation Contract

Shared standard. Load when:
- Creating a new branch for any task
- Opening or reviewing a pull request
- Asking about branch naming or merge strategy

## Hard Rules

1. **Branch naming — `<type>/<task-id>-description`**: All branches follow this exact pattern. Type is lowercase, task ID comes from `docs/plan/`, description is kebab-case. Examples: `feat/2.3-shooting-stats`, `fix/3.1-queue-stale-jobs`, `refactor/1.4-normalize-player-names`.
2. **Allowed types**: `feat` (new functionality), `fix` (bug fix), `refactor` (restructure without behavior change), `test` (add or fix tests only), `chore` (tooling, deps, config), `docs` (documentation only). No other types.
3. **Always branch from `main`**: Never branch from another feature branch. `git checkout -b <branch> main`.
4. **Never commit directly to `main`**: All changes go through a branch and PR. No exceptions.
5. **One task per branch**: A branch maps to one task ID. If work spans two tasks, open two branches.
6. **Short-lived branches**: Merge within days, not weeks. Long-lived branches = merge conflicts and stale context.
7. **CI must pass before merge**: All status checks (ruff, mypy, unit tests) must be green. Never merge a red branch.
8. **Squash merge only**: All PRs are merged with squash. The squash commit message is the PR title: `type(scope): description [task-id]`. Disallow merge commits and rebase merges in GitHub settings.
9. **Auto-delete after merge**: GitHub is configured to delete branches automatically after merge. Never let merged branches accumulate.
10. **No human approval required**: CI is the gate. Code review is done on the draft PR before marking it ready — not as a post-merge step.

## Branch Types Reference

| Type | When to use | Example |
|------|-------------|---------|
| `feat` | New functionality from a task | `feat/2.3-shooting-stats` |
| `fix` | Bug fix | `fix/3.1-queue-stale-jobs` |
| `refactor` | Structural improvement, no behavior change | `refactor/1.4-normalize-names` |
| `test` | Add or improve tests only | `test/2.1-add-scraper-coverage` |
| `chore` | Deps, config, tooling, CI setup | `chore/project-setup` |
| `docs` | Documentation only | `docs/update-readme` |

## PR Title Convention

PR title follows conventional commit format — it becomes the squash commit message on `main`:

```
<type>(<scope>): <short description> [<task-id>]
```

- `task-id` is the two-part task ID from `docs/plan/` (e.g., `2.3`).
- Omit `[<task-id>]` only for ad-hoc branches without a plan entry (e.g., `chore/project-setup`).

Examples:
- `feat(stats): add shooting stats scraper [2.3]`
- `fix(queue): remove stale jobs on startup [3.1]`
- `chore(ci): add github actions workflows`

## Execution Steps — Creating a Branch

1. Identify the task ID from `docs/plan/`.
2. `git checkout main && git pull origin main`
3. `git checkout -b <type>/<task-id>-description`
4. Work, commit using conventional commits (see `../commit/SKILL.md`). Report `STATUS: done` to Codex when the subtask is complete.
5. After all subtasks are reviewed and approved: Codex pushes the branch and opens the PR via the `pr` skill — Claude never pushes and never opens PRs. See `~/.agents/skills/pr/SKILL.md`.
6. Wait for CI to run.
7. Review already done per subtask via the `review` skill — no additional review step needed here.
8. `gh pr ready` when CI is green.
9. Squash merge: `gh pr merge --squash`.
10. Clean up local branch:
    ```bash
    git checkout main && git pull origin main && git branch -D <branch-name>
    ```
    Remote branch is deleted automatically by GitHub. Use `-D` (force) — squash merge creates a new SHA on `main` so `-d` thinks the branch is unmerged and will fail.

## Branch Protection Setup (GitHub)

Configure once per repo via GitHub → Settings → Branches → Add rule for `main`:

- ✅ Require status checks to pass before merging
  - Add: `ruff`, `flake8`, `mypy`, `unit` (from `ci.yml`)
- ✅ Require branches to be up to date before merging
- ✅ Do not allow bypassing the above settings
- ✅ Allow force pushes: OFF
- ✅ Allow deletions: OFF
- ❌ Require approvals: OFF (CI is the gate)

## Decision Gates

| Situation | Action |
|-----------|--------|
| Task has no ID (ad-hoc fix) | Use `fix/description` or `chore/description` without ID |
| Branch is behind main | `git rebase main` before pushing |
| CI fails on PR | Fix locally, push again — never merge red |
| PR spans multiple tasks | Split into one PR per task ID |
| Need to update a merged PR | Open a new branch, new PR — never reopen |

## Output Contract

- Branch name matches `<type>/<task-id>-description` or `<type>/description` for ID-less tasks.
- Branch was created from up-to-date `main`.
- PR title follows `<type>(<scope>): <what> [<task-id>]` format (or without `[task-id]` for ad-hoc branches).
- CI passes before merge.
- Squash merge used — no merge commits on `main`.
- Branch deleted after merge.

## References

- `~/.agents/skills/pr/SKILL.md` — PR body template, draft PRs, merge checklist
- `../commit/SKILL.md` — conventional commit conventions and message format
- `~/.agents/skills/issue/SKILL.md` — issue creation, labels, and linking to branches
- `../ci-lint/SKILL.md` — lint jobs that must pass (ruff, flake8, mypy)
- `../ci-unit/SKILL.md` — unit test job that must pass
- `../ci-integration/SKILL.md` — integration job (PR to main only)
- `../../py-init/SKILL.md` — initial branch setup (`chore/project-setup`)
