# AGENTS.md — Multi-Agent Working Agreement

> Template — replace `<placeholders>` before committing to a project.
> Claude reads this as CLAUDE.md via symlink: `ln -s AGENTS.md CLAUDE.md`

## §1 Working Model

Two agents, one codebase:

- **Codex / OpenCode** — orchestrator and reviewer. Owns the plan, creates issues, opens PRs, reviews commits.
- **Claude** — implementer. Reads subtasks, writes tests, implements code, commits.

Human is the final decision-maker. All agents defer to the human on ambiguous scope or conflicting instructions.

## §2 Project Scope

- **Project**: `<project-name>`
- **Source**: `src/<package-name>/`
- **Tests**: `tests/unit/`, `tests/integration/`, `tests/e2e/`
- **Plan**: `docs/plan/`
- **Modules**: `<project-modules>`

## §3 Communication Protocol

Claude → Codex output format (one block per subtask):

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

If `STATUS: blocked`: stop, do not guess, do not expand scope. Wait for Codex to resolve.

## §4 Task and ID System

- **Phase**: `N` (e.g., `2`)
- **Task**: `N.M` (e.g., `2.3`) — maps to one PR and one branch
- **Subtask**: `N.M.K` (e.g., `2.3.1`) — maps to one commit

Plan lives in `docs/plan/phase-NN-<name>.md` (format in `docs/plan/PLAN.md`; written by the leader).

## §5 Toolchain

| Tool | Purpose | Notes |
|------|---------|-------|
| `uv` | Package manager | `uv sync --all-groups` locally; per group in CI |
| `ruff` | Linter + formatter | Must pass before every commit |
| `mypy` | Type checker | `strict = true` in `pyproject.toml`; must pass before every commit |
| `pytest` | Test runner | unit: mocked; integration: real services; e2e: real network |
| `pyrightconfig.json` | Editor type checking | Committed for editor support — agents never run pyright |

## §6 Claude Implementation Rules

1. One subtask per session. Never batch.
2. Test first: write the test, confirm it fails, then implement.
3. Never commit with failing tests, ruff, or mypy.
4. Scope = `Files` field of the subtask. Never go outside it without stopping.
5. Only touches CI when it is an explicit subtask in the plan.

Use the `implement` skill for the full loop.

## §7 Codex Review Rules

1. Review the commit (`git show <hash>`), not the conversation.
2. Validate against spec: `Files`, `What`, `Out of scope`, `Test`, `Done when`.
3. Max 2 review rounds. Escalate to human after two failed rounds.
4. Give only concrete, actionable fixes: `file.py:42 — replace X with Y`.
5. When all subtasks of a task are approved → use the `pr` skill.
6. Before sending a subtask to Claude, check its `Model` and `Effort` fields. If set, switch Claude to them (e.g. `/model opus` via herdr). Use `opus/high` for queue concurrency, migrations on existing data, hard-to-reproduce bugs, or changes spanning several layers.

Use the `review` skill for the full checklist.

## §8 PR Flow

- Codex pushes the branch (`git push -u origin <branch>`) and opens the PR after all subtasks of a task are approved.
- One PR per task. Draft first. CI must pass before marking ready.
- Squash merge on `main`. Branch deleted after merge.
- Claude never pushes branches or opens PRs — Claude commits locally only.

Use the `pr` skill.

## §9 Issue Lifecycle

```
Issue opened → accepted by Codex → added to docs/plan/ with "Issue: #<n>"
  → Claude implements (subtask) → PR includes "Closes #<n>" → merged → issue closed
```

Claude never works on an issue directly — only on subtasks in the plan.

## §10 Quality Gates

All must pass before any commit:
- `uv run ruff check .`
- `uv run mypy`
- `<Done when command for the subtask>`

All must pass before PR is marked ready:
- CI green (lint + unit + integration)

## §11 Tools

- **herdr** — transport layer between Claude and Codex (spec in `docs/plan/`)
- **Graphify** — dependency graph for the plan
- **Archify** — architecture diagram

## §12 Graphify

Graphify is a project dev dependency (`uv add --dev graphifyy`). Always invoke via `uv run graphify` — never bare `graphify`:

```bash
uv run graphify query "<question>"       # understand how parts connect
uv run graphify path <A> <B>             # dependency path between two components
uv run graphify explain <node>           # explain a module or class
uv run graphify affected "<X>"           # find what depends on X (reverse traversal)
```

After cloning or recreating the venv: `uv sync && uv run graphify hook install`

Rules:
1. Query Graphify before reading more than 3–4 files to understand dependencies.
2. Never build the graph manually; the git hooks handle it.
3. If `graphify-out/` is missing or stale, say so. Do not rebuild it yourself unless asked.

## References

- `~/.claude/skills/implement/SKILL.md` — Claude's full implementation loop
- `~/.agents/skills/review/SKILL.md` — Codex's review loop
- `~/.agents/skills/pr/SKILL.md` — PR creation flow
- `~/.agents/skills/plan/SKILL.md` — plan structure and subtask format
- `~/.agents/skills/issue/SKILL.md` — issue lifecycle and templates
- `~/.claude/skills/github/commit/SKILL.md` — commit format standard
- `~/.claude/skills/github/branch/SKILL.md` — branch naming
