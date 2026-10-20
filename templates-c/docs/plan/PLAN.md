# Project plan (C)

> **Single source of truth for the plan and status.** `feature_list.json` is
> generated from the `phase-*.md` files in this folder — do not hand-edit it;
> run `make plan` (or `harness/sync_plan.py`) to regenerate it.

## How this folder works

- **`PLAN.md`** (this file): narrative overview / goals. Not parsed.
- **`roadmap.md`** (optional): longer-term direction. Not parsed.
- **`phase-NN-<name>.md`**: the actual backlog. Parsed into `feature_list.json`.

## Id scheme

- **Phase** = `NN` (file).
- **Task** = `N.M` → a feature.
- **Subtask** = `N.M.K` → **one atomic micro-task** (a single function/struct/module).

## Micro-task rule (enforced)

A subtask is the smallest traceable unit. It declares a `files:` list of the
**exact 1-3 files it creates/modifies under `src/`/`include/`**. The harness
blocks any write to `src/` outside that list (`guard_write.sh`), and Codex rejects
subtasks that bundle several concerns. Unit tests (`tests/unit/`) are the
implementer's and are not declared. If a unit needs more than ~3 files, split it.

## Phase file format (parseable)

```markdown
# Phase 01 — <phase name>

## 1.1 <task title>
- status: pending
- layer: core
- description: <one sentence>
- acceptance:
  - AC1 | <observable criterion (input/output, return code)> | tests/acceptance/test_1.1.1.c
  - AC2 | <observable criterion> | tests/acceptance/test_1.1.1.c

### 1.1.1 <subtask title>
- status: pending
- layer: core
- files:
  - include/<name>/buffer.h   # crea: API pública buffer
  - src/core/buffer.c          # crea: implementación

### 1.1.2 <subtask title>
- status: pending
- layer: adapters
- files:
  - src/io/file_source.c       # crea: adapter de fichero
```

`status` ∈ `pending | in_progress | done | blocked`.
`layer` ∈ `core | adapters | interfaces` (adapt to your project).
`files:` lists the 1-3 files the subtask will touch (inline `# crea:`/`# modifica:`
comments are optional and ignored by the parser).

## Goals ‹fill in›

- ‹what this project commits to›
