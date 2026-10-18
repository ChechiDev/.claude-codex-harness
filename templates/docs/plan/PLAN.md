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
- **Subtask** = `N.M.K` → a unit of implementation (what the `implement` skill consumes).

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

### 1.1.2 <subtask title>
- status: pending
- layer: infrastructure
```

`status` ∈ `pending | in_progress | done | blocked`.
`layer` ∈ `domain | application | infrastructure | interfaces`.

## Goals ‹fill in›

- ‹what this project commits to›
