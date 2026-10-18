# claude-codex-harness

A **Harness Engineering** toolkit to orchestrate a two-engine flow:
**Claude plans and implements, Codex reviews and documents.** The separation of
powers is real because whoever writes the code is not the one who approves it.

> This is not a Python project: it is the *scaffolding* that you install on top
> of your projects (FastAPI/Django, hexagonal architecture). Verification is
> enforced by the harness hooks and the acceptance tests, not by the model's
> goodwill.

## Distribution model

This repo lives cloned once on your machine (`~/.claude-codex-harness`), with
its origin on GitHub. `install.sh` installs the harness onto a target project
in a **hybrid** way:

| Mechanism | What | Why |
|-----------|------|-----|
| **Symlink** | `agents/`, `settings.json`, `lib/` → `harness/` | improving the harness (`git pull`) propagates to every project |
| **Copy**    | `CLAUDE.md`, `AGENTS.md`, `Makefile`, `docs/`, `CHECKPOINTS.md`, `feature_list.json`, `progress/` | each project customizes them without touching the source |

## Per-feature flow

```
feature_list.json: pending
      ▼
[Claude leader]       plans → progress/current.md
      ▼
[Codex reviewer]      reviews the PLAN + WRITES tests/acceptance/test_<f>.py
      │                       └─ progress/plan_review_<f>.md
      ├── CHANGES_REQUESTED ──► back to leader
      └── PLAN_APPROVED (acceptance tests red)
            ▼
[Claude implementer]  inner TDD: unit tests + code
      │               until acceptance + unit + make verify are green
      │               (does NOT mark done)
      ▼
[Codex reviewer]      reviews the CODE → progress/review_<f>.md
      │                       └─ APPROVED | CHANGES_REQUESTED
      ├── CHANGES_REQUESTED ──► back to implementer
      └── APPROVED
            ▼
[Codex documenter]    docs/ + CHANGELOG → progress/doc_<f>.md
            ▼
      status: done  +  summary appended to progress/history.md
```

## Non-bypassable rules (enforced by harness hooks)

1. Claude does not write to `src/` without `progress/plan_review_<f>.md` = `PLAN_APPROVED`.
2. Claude **never** writes to `tests/acceptance/` (they are the oracle, written by Codex).
3. No `done` without `progress/review_<f>.md` = `APPROVED` and `make check` green.
4. No agent attribution in commits (`Co-Authored-By`, "Generated with…").

## Toolchain (always uv)

The project's `Makefile` is the **single source of truth** for the commands:

```
make lint    # uv run ruff check . && uv run ruff format --check .
make type    # uv run mypy src
make test    # uv run pytest
make verify  # lint + type + test
make check   # verify + ./harness/check.sh (harness invariants)
```

Hooks and invariants call `make`; they never reimplement the commands.

## Installing onto a project

```bash
~/.claude-codex-harness/install.sh /path/to/project
```

Idempotent: it does not overwrite existing files unless `--force`.

## On-disk state contract (per feature `<f>`)

```
progress/current.md            # live plan (leader)
progress/plan_review_<f>.md    # PLAN_APPROVED | CHANGES_REQUESTED (Codex)
tests/acceptance/test_<f>.py   # independent oracle (Codex, read-only for Claude)
progress/review_<f>.md         # APPROVED | CHANGES_REQUESTED (Codex)
progress/doc_<f>.md            # what Codex documented
progress/history.md            # append-only log (leader)
```
