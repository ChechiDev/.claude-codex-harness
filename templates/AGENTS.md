# AGENTS.md — Navigation map for agents

> Entry point for any agent (Claude or Codex). It is NOT a rulebook: it is a
> **map**. Read only what you need, when you need it (progressive disclosure).

## 1. Before starting (mandatory)

1. Run `make check`. If it fails, **stop** and fix the harness before touching anything.
2. Read `progress/current.md` (where the last session left off).
3. Read `feature_list.json` and pick **one** `pending` feature. One at a time.

## 2. Repository map

| File / folder                | What it contains                                   | When to read it |
|------------------------------|----------------------------------------------------|-----------------|
| `feature_list.json`          | Backlog with status and `layer`                    | Always, at start |
| `progress/current.md`        | Current session + gate status                      | Always, at start |
| `progress/history.md`        | Append-only log                                    | If you need historical context |
| `docs/architecture.md`       | What "good work" means: hexagonal layers           | Before planning/implementing |
| `docs/conventions.md`        | Style, naming, errors, DI                          | Before writing code |
| `docs/verification.md`       | How to prove it works                              | Before declaring `done` |
| `CHECKPOINTS.md`             | Objective criteria for "correct final state"       | For self-evaluation / review |
| `.claude/agents/`            | Claude roles (leader, implementer)                 | If you orchestrate |
| `.claude/codex/`             | Codex roles (reviewer, documenter)                 | If you review/document |
| `harness/`                   | Harness scripts (check, gate, review_with_codex)   | Operation |
| `src/`                       | Code (hexagonal layers)                            | To implement |
| `tests/unit/`                | Unit tests (Claude, inner TDD)                     | To implement |
| `tests/acceptance/`          | Acceptance tests (Codex, **read-only for Claude**) | Target to satisfy |

## 3. Hard rules (non-negotiable)

- **One feature at a time.** At most one `in_progress` in `feature_list.json`.
- **Codex gates:** no implementing without `PLAN_APPROVED`; no closing without `APPROVED`.
- **`tests/acceptance/` belongs to Codex.** Claude never edits it.
- **`done` only with `make check` green.**
- **Document in real time** in `progress/current.md`, not at the end.

## 4. Flow (summary)

```
pending → [leader plans] → [Codex: plan + acceptance tests] → PLAN_APPROVED
        → [implementer: TDD] → make verify → [Codex: code review] → APPROVED
        → [Codex: documents] → done
```

## 5. If you get stuck

Re-read the relevant section of `docs/`. If a tool does not do what you expect,
**do not invent a workaround**: document the blocker in `progress/current.md`
with status `blocked` and stop the session.
