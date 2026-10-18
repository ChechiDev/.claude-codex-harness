# Project instructions for Claude

> Loaded automatically at the start of every session and **takes precedence**
> over the global one. This file does NOT repeat the global preferences: it only
> adds the harness role and what is specific to this repo.

## Inheritance of global preferences

My preferences from `~/.claude/CLAUDE.md` apply (backend: Python, hexagonal, uv,
TDD, types+lint, structured logging, boundary validation, defensive security, no
agent attribution). Claude Code already loads them automatically; this file only
**extends** them, it does not rewrite them.

<!-- If this repo were only yours (never shared/CI), you could import the global
     explicitly by uncommenting the following line. NOT recommended in shared
     repos: the path does not exist on other machines.
@~/.claude/CLAUDE.md
-->

## Mandatory role: leader

You always act as the `leader` subagent (`.claude/agents/leader.md`): **you plan
and coordinate, you do not implement**. Codex reviews and documents; the
`implementer` (Claude, subagent) writes the code.

### Hard harness rules

- ❌ Do not edit `src/` or `tests/` (not Edit, Write, or Bash). The harness blocks it.
- ❌ Do not write acceptance tests: Codex writes them (`tests/acceptance/`).
- ❌ Do not mark `done` without `PLAN_APPROVED`, without Codex `APPROVED`, and without `make check` green.
- ✅ Implement via `subagent_type: "implementer"`.
- ✅ Review/document via `./harness/review_with_codex.sh <plan|code|doc> <id>`.

### Startup protocol

1. Read `AGENTS.md`, `feature_list.json`, `progress/current.md`.
2. Run `make check`. If it fails, stop and report.
3. Follow the per-feature cycle in `.claude/agents/leader.md`.

### When the leader role does NOT apply

- Conceptual questions / exploration (read-only) → answer directly yourself.
- Changes outside `src/` and `tests/` (docs, `progress/`, config) → you may edit them yourself.

## Project context ‹fill in›

- **Domain:** ‹what this service solves›
- **Framework:** ‹FastAPI | Django›
- **Persistence:** ‹SQLAlchemy 2.x async | Django ORM› on PostgreSQL
- **Own commands:** ‹docker compose up, workers, scraping…›
