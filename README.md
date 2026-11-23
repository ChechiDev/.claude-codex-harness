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
[leader]      discovery with you, one question at a time; runs every gate, never writes the plan
[planner]     PLAN.md + every phase                   → [Codex] roadmap gate → you approve
[planner]     break down one phase into tasks         → [Codex] phase gate   → you approve
per task:     [planner] kickoff: its subtask specs    → [Codex] task gate    → you approve
per subtask:  [Codex] plan gate (writes acceptance tests, red unless a confirmed marker)
              → [implementer] TDD until make verify is green
              → [Codex] code gate → [Codex] close: docs + status done + ONE commit
              → you approve the next subtask
task done:    [Codex] task-close gate; phase done: [Codex] phase-close gate
              the last close pushes the task branch and opens ONE PR → you merge
per issue:    a GitHub issue, at any point, in its own issues lane (never a task):
              queue → claim → [Codex] issue gate → the same per-subtask cycle
              → ONE PR with "Closes #n" → you merge
```

Claude cannot write `src/` before the plan gate, cannot touch files outside the
subtask's declared `files:`, cannot edit the acceptance tests or Codex verdicts,
and cannot commit. Details: [architecture](docs/architecture.md); the
task-by-task breakdown and the close gates: [task flow](docs/design/task-flow.md).

### Roles

| Role | Runs on | Does |
|------|---------|------|
| `leader` (`agents/leader.md`) | Claude, `opus` | talks to you, runs every gate, never writes the plan or code |
| `claude-planner` (`agents/claude-planner.md`) | Claude, `opus` | writes `PLAN.md`, phases, task kickoffs and subtask specs |
| `implementer` (`agents/implementer.md`) | Claude, `sonnet` | TDD on one subtask until `make verify` is green |
| `codex-planner` (`codex/codex-planner.md`) | Codex, `gpt-6-astra` / `high` | roadmap, phase, task, task-close and phase-close gates |
| `reviewer` (`codex/reviewer.md`) | Codex, `gpt-6-sol` / `high` | plan, code, issue and issue-draft gates |
| `documenter` (`codex/documenter.md`) | Codex, `gpt-6-luna` / `low` | the doc step of the close |

The Codex model and effort come from each role file's header:
[Codex model per role](docs/configuration.md#codex-model-and-effort-per-role).

### Gates, green record and verification integrity

Gates, close and the PR step decide through one launcher,
`harness/run_check.sh`, which runs plain `make check` in a cleaned environment
and writes the green record (`.git/harness-check.ok`) only for a clean, green
check of an unchanged tree; the PR step requires that record to match the exact
current state. Launcher findings are `[WARN]` for now
([check launcher](docs/design/check-launcher.md)).

Two more layers are also WARN only, and stay inactive (one-line note) until the
human creates and seals their baseline with the sealer verbs Claude cannot run:

- **I4b, oracle execution** — the acceptance tests really ran and were selected
  as sealed: `python3 harness/_oracle.py baseline-create --from <pre-rule-commit>`,
  then `baseline-seal` ([oracle execution](docs/design/oracle-exec.md)).
- **I5, verification contract** — the lint/type/build config that decides
  "done": `python3 harness/_contract.py baseline-create --from <pre-rule-commit>`,
  then `baseline-seal`. A later config change goes `_contract.py contract-propose` →
  `./harness/review_with_codex.sh contract` → human
  `python3 harness/_contract.py contract-approve <hash>`
  ([verification contract](docs/design/verification-contract.md),
  [changing lint/type/build config](docs/configuration.md#changing-linttypebuild-config)).

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

**New Python project, guided:** open Claude Code in an empty dir and ask
`init project <name>`. The `py-init` skill scaffolds the project (uv, `src/`
layout, tests, `pyproject.toml`) and then offers `install.sh <project-dir>`
(without `--python`, so its `pyproject.toml` stays) and adds the CI workflows.
Details: [usage, step 0](docs/usage.md#0-create-a-new-python-project-py-init-then-the-harness).

**New Python project, one command** (no `uv init` needed):

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
   Codex writes the acceptance tests, which stay red until the code exists
   (unless the spec declares a confirmed marker, whose tests may already pass).
4. **Approve the plan:** `OK, launch the implementer, then the code gate.`
5. **Approve the code:** `OK, run the close.` Codex documents the subtask and
   makes one commit on the task branch (`feat/<N.M>-<slug>`, or your team's
   pattern in [`harness.toml`](docs/configuration.md#git-conventions-harnesstoml-git)).
6. **Next:** check `make status` and `git log -1`, then approve the next
   subtask. Merge the task branch into `main` when the task is done.

When the last subtask of a task closes, the harness pushes the task branch and
opens one PR. The PR step never pushes `main`, `master` or the base, refuses a
branch other than the task's (with a `[git]` section; without one it warns),
and the body has a **For the human** section: base drift, changed
dependency / migration / CI / auth paths, and Codex's note
([task pull request](docs/configuration.md#task-pull-request)).

What each gate writes, what to check before approving, manual Codex mode and
troubleshooting: [usage](docs/usage.md).

### Commands

The project's `Makefile` is the single source of truth. It holds the project's
own toolchain targets and ends with `include harness/harness.mk`, the harness
targets shared by every project through the `harness/` symlink:

```
make plan    # regenerate feature_list.json + the PLAN.md index from docs/plan/
make status  # the whole plan: done / in progress / pending, gate verdicts, issue queue, next step
make verify  # lint + type + test (Python: ruff, strict mypy, pytest)
make check   # verify + harness invariants (plan sync, gates, seals, src/ scope)
harness/issue.sh intake 14                    # issues lane: duplicates, missing info, scope (read only)
harness/issue.sh ask 14                       # with your OK: post the question, park it as needs-info
make issue N=14 [PRIORITY=now|next|backlog]   # queue a GitHub issue
make issue-start N=14                         # claim it and create its branch
make issue-new                                # draft a new issue (Codex reviews it)
```

The workflow rules Claude follows are shared the same way: each project's
`CLAUDE.md` imports `@harness/WORKFLOW.md`. A project installed before that
moves to it with `install.sh <project> --migrate`: a dry-run by default,
`--apply` to write (all-or-nothing, backed up, `--rollback <id>` to undo; it never
touches links). Human-only; see [usage](docs/usage.md). The issues lane in
full: [issues lane](docs/design/issues-lane.md).

### Hooks

`settings.json` wires four hooks, each with an explicit timeout: `guard_write.sh`
and `guard_bash.sh` (PreToolUse, 15 s, fail closed: a crash or timeout blocks the
call), `post_write.sh` (PostToolUse, 120 s, `make lint` feedback, never blocks) and
`stop_gate.sh` (Stop, 15 s, advisory). Claude Code >= 2.1.295 is required.
Details and recovery: [usage, hooks](docs/usage.md#hooks).

## Documentation

- [Architecture](docs/architecture.md): distribution model, planning model, rules and enforcement layers, on-disk state.
- [Usage](docs/usage.md): the full cycle step by step, verification, manual mode, troubleshooting.
- [Configuration](docs/configuration.md): `install.sh` options, `HARNESS_*` variables, Codex model per role, hooks.
- [Design notes](docs/design/): task flow, check launcher, oracle execution, verification contract, issues lane.
- [Testing](docs/testing.md): the toolkit suite's two tiers (fast / e2e) and when to run each.

## Developing the toolkit

The hooks have their own test suite, run on throwaway projects:

```bash
uv sync
uv run pytest -n auto    # fast tier (default: excludes e2e)
make test-e2e            # e2e tier only: real install.sh / make check in temp projects
uv run ruff check lib/_guard.py lib/_harness.py tests
uv run mypy              # strict, over lib/_guard.py, lib/_harness.py and tests/
```

CI (`.github/workflows/ci.yml`) runs the fast tier, ruff and mypy on every PR and
push to `main`, and the full e2e tier on every PR (8 shards plus an aggregate check), nightly and by hand. Tiers, the e2e guard, CI and the session rule: [docs/testing.md](docs/testing.md).
