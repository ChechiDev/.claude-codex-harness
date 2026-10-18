# CHECKPOINTS — Final-state evaluation

> Objective criteria that Codex (reviewer) walks through marking `[x]`/`[ ]`.
> It rejects the close if any box in C1–C6 is left empty.

## C1 — The harness is complete
- [ ] Base files: `AGENTS.md`, `CLAUDE.md`, `Makefile`, `feature_list.json`, `CHECKPOINTS.md`.
- [ ] Docs: `docs/architecture.md`, `docs/conventions.md`, `docs/verification.md`.
- [ ] Symlinks installed: `.claude/agents`, `.claude/codex`, `harness/`.
- [ ] `make check` finishes with exit 0.

## C2 — The state is consistent
- [ ] At most one `in_progress` feature in `feature_list.json`.
- [ ] The worked feature has `plan_review_<id>.md` = `PLAN_APPROVED`.
- [ ] Every `done` feature has `review_<id>.md` = `APPROVED`.
- [ ] `progress/current.md` describes the active session (no leftover junk).

## C3 — The hexagonal architecture is respected
- [ ] `src/.../domain/` does not import FastAPI/Django/SQLAlchemy/infra-Pydantic/external clients.
- [ ] Ports in `application/` (`Protocol`/ABC); adapters in `infrastructure/`.
- [ ] DI through the constructor; no global singletons.
- [ ] The ORM model is not used as a domain entity (there is mapping in infra).

## C4 — Quality and security
- [ ] `make verify` green (ruff + strict mypy + pytest).
- [ ] No debug `print()`; structured logging.
- [ ] Boundary validation (Pydantic v2 / serializers).
- [ ] No concatenated SQL, no secrets in code, no logging of PII/tokens.

## C5 — Verification is real
- [ ] `tests/acceptance/test_<id>.py` exists, derives from `acceptance`, and passes.
- [ ] **1:1 traceability**: each criterion `ACn` has its `test_ac<n>_<slug>` and the
      `test` field in `feature_list.json` points to it. No orphan criterion.
- [ ] There are `tests/unit/` covering the design; they are not trivial.
- [ ] Tests use `tmp_path`/`TemporaryDirectory`, not fs mocks.

## C6 — The session was closed properly
- [ ] No suspicious temp files or `__pycache__` outside `.gitignore`.
- [ ] `progress/history.md` has an entry for the last feature.
- [ ] Documentation updated (`progress/doc_<id>.md`).
- [ ] No agent attribution in commits/PRs.
