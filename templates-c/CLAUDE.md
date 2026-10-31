# Project instructions for Claude (C project)

> Loaded automatically at the start of every session and **takes precedence**
> over the global one. The harness workflow (leader role, rules, startup
> protocol, issues lane) is shared by every project and imported below; this
> file only adds what is specific to this C repo.

@harness/WORKFLOW.md

## Inheritance of global preferences

My global preferences apply (TDD, defensive security, structured error handling,
boundary validation, no agent attribution). This is a **C** project, so the
Python-specific parts of the global file (uv, ruff, mypy, Pydantic) do **not**
apply; the C toolchain and conventions live here and in `docs/conventions.md`.
Production dirs: `src/` and `include/` (micro-task `files:` point there).

## Project context ‹filled in by the leader during discovery›

- **Domain:** ‹what this program/library does›
- **Build:** ‹plain Makefile | CMake wrapper› · C standard ‹c11›
- **Test framework:** ‹assert.h runner (default) | Unity | Criterion›
- **Own commands:** ‹how to build/run the binary, fuzzers, benchmarks…›
