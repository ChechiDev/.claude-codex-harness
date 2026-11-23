# Project instructions for Claude

> Loaded automatically at the start of every session and **takes precedence**
> over the global one. This file does NOT repeat the global preferences. The
> harness workflow (leader role, rules, startup protocol, issues lane) is shared
> by every project and imported below; this file only adds what is specific to
> this repo.

@harness/WORKFLOW.md

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

## Project context ‹filled in by the leader during discovery›

- **Domain:** ‹what this project solves›
- **Stack:** see `docs/stack.md` (decided in the stack step, approved by the
  stack gate; never restated here)
- **Own commands:** ‹how to run it locally, workers, scripts…›
