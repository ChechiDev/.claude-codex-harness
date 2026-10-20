# Phase 01 — ‹phase name›

<!--
This is a template phase file. Rename it to phase-01-<name>.md, replace the
‹…› placeholders during planning, and run `make plan` to regenerate
feature_list.json. The leader writes/edits this; Codex sets status at close.
-->

## 1.1 ‹task title›
- status: pending
- layer: core
- description: ‹what this feature does, in one sentence›
- acceptance:
  - AC1 | ‹observable criterion (return value, output, exit code)› | tests/acceptance/test_1.1.1.c
  - AC2 | ‹observable criterion› | tests/acceptance/test_1.1.1.c

### 1.1.1 ‹subtask title: a function/struct›
- status: pending
- layer: core
- files:
  - include/‹name›/‹module›.h   # crea: ‹API pública de un solo módulo›
  - src/core/‹module›.c          # crea: ‹su implementación›

### 1.1.2 ‹subtask title: an adapter›
- status: pending
- layer: adapters
- files:
  - src/io/‹adapter›.c           # crea: ‹un solo adapter›
