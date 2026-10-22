# claude-codex-harness

A harness for Claude Code projects where **Claude plans and implements, and
Codex reviews, documents and commits**. Whoever writes the code is not the one
who approves, documents or commits it. Hooks and `make check` enforce the
split, so it does not depend on the model following instructions.

This repo is not a Python project. It is the scaffolding you install on top of
your projects (Python or C). It is cloned once per machine and symlinked into
each project, so `git pull` here updates all of them.

## How it works

```
[leader]      discovery → PLAN.md + every phase      → [Codex] roadmap gate → you approve
[leader]      break down one phase into subtasks      → [Codex] phase gate   → you approve
per subtask:  [Codex] plan gate (writes acceptance tests, red)
              → [implementer] TDD until make verify is green
              → [Codex] code gate → [Codex] close: docs + status done + ONE commit
              → you approve the next subtask
```

Claude cannot write `src/` before the plan gate, cannot touch files outside the
subtask's declared `files:`, cannot edit the acceptance tests or Codex verdicts,
and cannot commit. Details: [architecture](docs/architecture.md).

## Requirements

- `git` with `user.name` and `user.email` set (the root commit uses them).
- `make`, `uv` and Python 3.12+.
- [Claude Code](https://claude.com/claude-code) and the Codex CLI, logged in:

```bash
uv --version && python3 --version && claude --version
codex login status                 # must print "Logged in"
git config user.name && git config user.email
```

- C projects only: `gcc` or `clang`.

## Install

### 1. Clone the toolkit (once per machine)

```bash
git clone git@github.com:ChechiDev/.claude-codex-harness.git ~/.claude-codex-harness
~/.claude-codex-harness/link-global.sh   # links ~/.claude/{skills,CLAUDE.md} into the toolkit
```

`link-global.sh` backs up an existing `~/.claude/skills` or `~/.claude/CLAUDE.md`
before linking. `install.sh` also runs it, so this step is only needed on a
machine where you will not install a project yet.

### 2. Install it onto a project

**New Python project** (one command; no `uv init` needed):

```bash
~/.claude-codex-harness/install.sh /path/to/project --python my_pkg
```

Expected output, one line per step:

```
Installing harness (python) into /path/to/project
[symlink] .claude/agents .claude/codex .claude/skills .claude/settings.json harness
[copy]    CLAUDE.md AGENTS.md Makefile CHECKPOINTS.md docs progress
[python]  pyproject.toml src/my_pkg/__init__.py src/my_pkg/py.typed README.md tests/unit/
[gen]     feature_list.json .gitignore
[uv]      uv sync --all-groups
[git]     root commit on main · on chore/project-setup
[OK]      harness ready · make check green
```

**New C project:**

```bash
~/.claude-codex-harness/install.sh /path/to/project --c myproj
```

**Existing project** (it needs a `pyproject.toml` with ruff, mypy and pytest),
including a subdirectory of a monorepo. If the code is not under `src/`, name
its dirs:

```bash
~/.claude-codex-harness/install.sh /path/to/repo/backend --lang python --src-dirs app,scripts
```

In an existing repo the installer only adds files: it never overwrites one
without `--force`, never runs `git init` or commits, and keeps the
machine-local symlinks out of git via `.git/info/exclude`. Commit the rest
yourself. Details: [installing into an existing repo](docs/configuration.md#installing-into-an-existing-repo).

### 3. Check the result

```bash
cd /path/to/project
make check     # toolchain + harness invariants: must be green
make status    # "No phases planned yet."
git log --oneline && git branch --show-current   # chore: install harness · chore/project-setup
```

If you customize the setup (`docs/architecture.md`, `docs/conventions.md`,
`Makefile`), commit it on `chore/project-setup` and merge it into `main` before
the first subtask.

## Usage

Open Claude Code at the project root. It acts as the **leader** and stops after
every gate for your OK:

```bash
cd /path/to/project && claude
```

1. **Plan:** describe what to build, e.g. `Let's plan a library with a single
   function slugify(text: str) -> str.` Answer its questions one at a time. It
   writes the roadmap and runs the roadmap gate.
2. **Approve the roadmap:** `OK, break down phase 01 and run the phase gate.`
3. **Approve the phase:** `OK, start the first subtask and run its plan gate.`
   Codex writes the acceptance tests, which stay red until the code exists.
4. **Approve the plan:** `OK, launch the implementer, then the code gate.`
5. **Approve the code:** `OK, run the close.` Codex documents the subtask and
   makes one commit on the task branch (`feat/<N.M>-<slug>`, or your team's
   pattern in [`harness.toml`](docs/configuration.md#git-conventions-harnesstoml-git)).
6. **Next:** check `make status` and `git log -1`, then approve the next
   subtask. Merge the task branch into `main` when the task is done.

What each gate writes, what to check before approving, manual Codex mode and
troubleshooting: [usage](docs/usage.md).

### Commands

The project's `Makefile` is the single source of truth:

```
make plan    # regenerate feature_list.json + the PLAN.md index from docs/plan/
make status  # the whole plan: done / in progress / pending, gate verdicts, next step
make verify  # lint + type + test (Python: ruff, strict mypy, pytest)
make check   # verify + harness invariants (plan sync, gates, seals, src/ scope)
```

## Documentation

- [Architecture](docs/architecture.md): distribution model, planning model, rules and enforcement layers, on-disk state.
- [Usage](docs/usage.md): the full cycle step by step, verification, manual mode, troubleshooting.
- [Configuration](docs/configuration.md): `install.sh` options, `HARNESS_*` variables, Codex model per role, hooks.

## Developing the toolkit

The hooks have their own test suite, run on throwaway projects:

```bash
uv sync
uv run pytest
uv run ruff check lib/_guard.py lib/_harness.py tests
uv run mypy              # strict, over lib/_guard.py, lib/_harness.py and tests/
```
