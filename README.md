# claude-codex-harness

A **Harness Engineering** toolkit to orchestrate a two-engine flow:
**Claude plans and implements, Codex reviews, documents, and closes.** The
separation of powers is real because whoever writes the code is not the one who
approves, documents, or commits it.

> This is not a Python project: it is the *scaffolding* you install on top of
> your projects (FastAPI/Django, hexagonal architecture). Verification is
> enforced by the harness hooks and the acceptance tests, not by the model's goodwill.

## Distribution model

This repo lives cloned once on your machine (`~/.claude-codex-harness`), with its
origin on GitHub. `install.sh` installs the harness onto a target project in a
**hybrid** way:

| Mechanism | What | Why |
|-----------|------|-----|
| **Symlink** | `agents/`, `codex/`, `skills/`, `settings.json`, `lib/` → `harness/` | improving the harness (`git pull`) propagates to every project |
| **Copy**    | `CLAUDE.md`, `AGENTS.md`, `Makefile`, `CHECKPOINTS.md`, `docs/`, `progress/` | each project customizes them |
| **Generated** | `feature_list.json` | derived from `docs/plan/` (`make plan`), never copied or hand-edited |

### Machine-level config (`link-global.sh`)

Skills and the global `CLAUDE.md` are **team-wide** (identical on every machine),
so the toolkit holds the real content and each machine symlinks `~/.claude` into it:

| Toolkit (real, versioned) | `~/.claude` (symlink) |
|---------------------------|-----------------------|
| `skills/`                 | `~/.claude/skills`    |
| `global/CLAUDE.md`        | `~/.claude/CLAUDE.md` |

`install.sh` runs this linking automatically at the end (it is idempotent, so it
is safe on every install). You can also run `./link-global.sh` on its own — once
per machine after `git clone` — to set up the global links without installing
into a project. It backs up any existing real files and preserves machine-local
extras (`.atl/`, `.claude/` inside `skills/`, kept out of git via `.gitignore`).
Editing a skill or the global `CLAUDE.md` then edits the toolkit → `commit`/`push`
shares it with the team.

## Planning model (A2)

`docs/plan/` is the **single source of truth** for the plan and status:

- `docs/plan/PLAN.md` / `roadmap.md` — narrative (not parsed).
- `docs/plan/phase-NN-<name>.md` — the backlog: tasks `N.M`, subtasks `N.M.K`, with `status`/`layer`/`acceptance`.
- `feature_list.json` — a generated read-only index (so the scripts read JSON, not markdown).

The **unit of work is the subtask `N.M.K`**. Ids are assigned during planning and
used everywhere (`progress/`, `docs/reference/<id>-<name>.md`, commit footer).

## Per-subtask flow

```
docs/plan: pending N.M.K
   → [Claude leader]    set in_progress + make plan
   → [Codex reviewer]   plan review + write tests/acceptance/test_N.M.K.py → PLAN_APPROVED
   → [Claude implementer] inner TDD (unit + code) → make verify green
   → [Codex reviewer]   code review → APPROVED
   → [Codex documenter & close]
         docs/reference/N.M.K-<name>.md + status:done in docs/plan + make plan
         + update progress/ + ONE commit  (type(scope): what [N.M.K], Plan: footer)
   → next N.M.K
```

## Non-bypassable rules (enforced by harness hooks)

1. Claude does not write to `src/` without `progress/plan_review_<N.M.K>.md` = `PLAN_APPROVED`.
2. **Micro-tasks**: a write to `src/` must target a file declared in the subtask's
   `files:` (1-3 files) in `docs/plan/`. Anything outside it is blocked — the plan
   has to be updated and re-approved first (`guard_write.sh`).
3. Claude **never** writes to `tests/acceptance/` (Codex oracle).
4. Claude **never** commits (`guard_bash.sh`): only Codex commits at close. Escape
   valve for a conscious manual commit: `HARNESS_ALLOW_COMMIT=1`.
5. No `done` without `progress/review_<N.M.K>.md` = `APPROVED`, a **complete**
   `docs/reference/<N.M.K>-<name>.md` (`doc_gate`), and `make check` green.
6. No agent attribution in commits.
   > The close commit is made by Codex (outside Claude's hooks): there, no-attribution
   > is enforced by the Codex prompt instruction, not by a hook.

Clean code (no monster functions, bounded complexity/args) is enforced by the
ruff caps (C90 + PLR09xx) that ship by default via the `py-style`/`py-init`
skills, so a violation fails `make verify` before Codex can approve.

## Toolchain (always uv)

The project's `Makefile` is the single source of truth for the commands:

```
make plan    # regenerate feature_list.json from docs/plan/
make lint    # uv run ruff check . && uv run ruff format --check .
make type    # uv run mypy src
make test    # uv run pytest
make verify  # lint + type + test
make check   # verify + ./harness/check.sh (plan sync + invariants + gate consistency)
```

## Getting started

### Requirements

- `uv` and Python 3.12+.
- The Codex CLI authenticated (`codex login`). The review gates run Codex with
  `--sandbox danger-full-access` by default (it must write files and run the uv
  toolchain); override with `HARNESS_CODEX_SANDBOX=workspace-write` if your
  environment is already externally sandboxed. Run `/codex:setup` in Claude Code
  to check readiness.

### 1. Clone the toolkit (once per machine)

```bash
git clone git@github.com:<you>/claude-codex-harness.git ~/.claude-codex-harness
```

### 2. Install it onto a project

**A. New project (recommended — one scaffold command):**

```bash
# create the uv package (src/ layout, Python 3.12)
uv init --package --python 3.12 --name my_pkg /path/to/project
# install the harness AND the minimal Python layer in one go
~/.claude-codex-harness/install.sh /path/to/project --python my_pkg
```

`--python <pkg>` delegates to `lib/scaffold_python.sh`, which drops in the
canonical `pyproject.toml` (ruff + strict mypy + pytest, with clean-code caps),
`src/<pkg>/py.typed`, and a smoke test — so the project runs `make check` green
with no manual editing. (For a full team project with git/CI/GitHub, use the
`py-init` skill instead of `--python`.)

**B. Existing project (harness layer only):**

```bash
~/.claude-codex-harness/install.sh /path/to/project
```

Needs a `pyproject.toml` with ruff/mypy/pytest already in place.

Either way, `install.sh`:
- creates the symlinks (`.claude/agents`, `.claude/codex`, `.claude/skills`,
  `.claude/settings.json`, `harness/`),
- copies the customizable templates (`CLAUDE.md`, `AGENTS.md`, `Makefile`,
  `CHECKPOINTS.md`, `docs/`, `progress/`),
- generates `feature_list.json` from `docs/plan/`,
- runs `link-global.sh` (idempotent) to link `~/.claude/{skills,CLAUDE.md}` into
  the toolkit.

It is **idempotent**: it does not overwrite existing files unless you pass
`--force`. Because the generic parts are symlinked, running `git pull` in
`~/.claude-codex-harness` updates every installed project at once.

### 3. Prepare the project

```bash
cd /path/to/project
# fill in the ‹…› placeholders:
#   CLAUDE.md               → project context
#   docs/architecture.md    → your bounded contexts / ORM
#   docs/plan/PLAN.md        → goals
# rename docs/plan/phase-01-example.md → phase-01-<name>.md and write the backlog
#   (each subtask declares a files: list of 1-3 src/ files — micro-tasks)
uv sync --all-groups         # install deps + the project itself
make plan                    # regenerate feature_list.json from docs/plan/
make check                   # must finish green before any agent works
```

## Usage walkthrough — one subtask end to end

Open Claude Code at the project root. `CLAUDE.md` makes Claude act as the
**leader**. Then:

1. **Plan.** Ask the leader to plan. It edits `docs/plan/phase-NN-<name>.md`,
   assigning tasks `N.M` and subtasks `N.M.K`, and runs `make plan`.
2. **Pick a subtask.** The leader sets one leaf `N.M.K` to `in_progress`
   (`make plan`) and records it in `progress/current.md`.
3. **Plan gate (Codex).** The leader runs
   `./harness/review_with_codex.sh plan <N.M.K>`. Codex reviews the plan and
   writes the acceptance tests to `tests/acceptance/test_<N.M.K>.py` (left red),
   with a verdict in `progress/plan_review_<N.M.K>.md`. Until that file says
   `PLAN_APPROVED`, the harness **blocks any write to `src/`**.
4. **Implement.** The leader launches the `implementer` subagent. It does inner
   TDD (`tests/unit/` + `src/`) until `make verify` is green. It does **not**
   commit and does **not** mark the subtask done.
5. **Code gate (Codex).** The leader runs
   `./harness/review_with_codex.sh code <N.M.K>` → `progress/review_<N.M.K>.md`
   (`APPROVED` / `CHANGES_REQUESTED`).
6. **Close (Codex).** The leader runs
   `./harness/review_with_codex.sh doc <N.M.K>`. Codex writes
   `docs/reference/<N.M.K>-<name>.md`, sets `status: done` in `docs/plan/`,
   runs `make plan`, updates `progress/`, and makes the **single commit**.
7. **Next.** The leader moves to the next `pending` leaf.

Everything of substance lives on disk (`progress/`, `docs/`), not in chat — you
can audit each step as its file appears.

### Manual Codex mode

If you prefer to drive Codex yourself, set `HARNESS_CODEX_MODE=manual`: the
script prints the exact `codex …` command and prompt instead of running it.

## On-disk state contract (per subtask `<N.M.K>`)

```
docs/plan/phase-NN-<name>.md      # the plan + status (source of truth)
progress/current.md               # live session
progress/plan_review_<N.M.K>.md   # PLAN_APPROVED | CHANGES_REQUESTED (Codex)
tests/acceptance/test_<N.M.K>.py  # independent oracle (Codex, read-only for Claude)
progress/impl_<N.M.K>.md          # implementer report (Claude)
progress/review_<N.M.K>.md        # APPROVED | CHANGES_REQUESTED (Codex)
docs/reference/<N.M.K>-<name>.md  # output documentation (Codex)
progress/history.md               # append-only log (Codex, at close)
```
