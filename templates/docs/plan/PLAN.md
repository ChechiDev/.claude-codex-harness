# Project plan

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
- **Subtask** = `N.M.K` → **one atomic micro-task** (a single class/function/method).

## Micro-task rule (enforced)

A subtask is the smallest traceable unit. It declares a `files:` list of the
**exact 1-3 production files (under `src/`)** it creates/modifies. The harness
blocks any write to `src/` outside that list (`guard_write.sh`), and Codex rejects
subtasks that bundle several concerns. Unit tests (`tests/unit/`) are the
implementer's and are not declared. If a unit needs more than ~3 `src/` files,
split it into more subtasks.

Ids are assigned here during planning and are the ids used everywhere else
(`feature_list.json`, `progress/`, `docs/reference/<id>-<name>.md`, commit footer
`Plan: docs/plan/phase-NN.md#NMK`).

## Phase file format (parseable)

```markdown
# Phase 01 — <phase name>

## 1.1 <task title>
- status: pending
- layer: application
- description: <one sentence>
- acceptance:
  - AC1 | <observable criterion> | tests/acceptance/test_1.1.py::test_ac1_<slug>
  - AC2 | <observable criterion> | tests/acceptance/test_1.1.py::test_ac2_<slug>

### 1.1.1 <subtask title>
- status: pending
- layer: application
- files:
  - src/<pkg>/application/foo.py        # crea: clase Foo
  - src/<pkg>/application/__init__.py    # modifica: export

### 1.1.2 <subtask title>
- status: pending
- layer: infrastructure
- files:
  - src/<pkg>/infrastructure/bar.py     # crea: adapter Bar
```

`files:` lists the 1-3 files the subtask will touch (inline `# crea:`/`# modifica:`
comments are optional and ignored by the parser).

`status` ∈ `pending | in_progress | done | blocked`.
`layer` ∈ `domain | application | infrastructure | interfaces`.

## Goals ‹fill in›

- ‹what this project commits to›
