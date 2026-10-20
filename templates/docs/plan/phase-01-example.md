# Phase 01 — ‹phase name›

<!--
This is a template phase file. Rename it to phase-01-<name>.md, replace the
‹…› placeholders during planning, and run `make plan` to regenerate
feature_list.json. The leader writes/edits this; Codex sets status at close.
-->

## 1.1 ‹task title›
- status: pending
- layer: application
- description: ‹what this feature does, in one sentence›
- acceptance:
  - AC1 | ‹observable criterion (input/output, exit code, API contract)› | tests/acceptance/test_1.1.py::test_ac1_‹slug›
  - AC2 | ‹observable criterion› | tests/acceptance/test_1.1.py::test_ac2_‹slug›

### 1.1.1 ‹subtask title: port/entity…›
- status: pending
- layer: domain
- files:
  - src/‹pkg›/domain/‹entity›.py          # crea: ‹una sola clase/VO›

### 1.1.2 ‹subtask title: adapter…›
- status: pending
- layer: infrastructure
- files:
  - src/‹pkg›/infrastructure/‹adapter›.py  # crea: ‹un solo adapter›
