# Configuration

Every knob the harness reads: `install.sh` options, environment variables and
the per-role Codex settings. Defaults are least privilege; each opt-out is
explicit.

## `install.sh` options

```bash
~/.claude-codex-harness/install.sh <project-path> [--force] [--lang python|c] [--python <pkg> | --c <name>] [--src-dirs a,b]
```

| Option | Effect |
|--------|--------|
| `--python <pkg>` | Python template set + `lib/scaffold_python.sh`: canonical `pyproject.toml`, `src/<pkg>/__init__.py`, `src/<pkg>/py.typed`, `README.md`, `tests/unit/` (only what is missing). `<pkg>` must be snake_case. |
| `--c <name>` | C template set + `lib/scaffold_c.sh`: `src/`, `include/<name>/`, `tests/unit/`. |
| `--lang python\|c` | Template set only, no scaffold (existing project). Default `python`. |
| `--src-dirs a,b` | Write `harness.toml` with these production-code dirs (see below). Never overwrites an existing one without `--force`. |
| `--force` | Overwrite the copied templates (and scaffold files) that already exist. |

Without `--python`, a Python project needs its own `pyproject.toml` with ruff,
mypy and pytest; if there is none, the final `make check` is skipped.

### Installing into an existing repo

When the project already has git history, or is a subdirectory of another repo
(a monorepo), `install.sh` only adds files:

- no `git init` (a monorepo subdir never gets a nested repo) and no commit;
- the symlinks (`.claude/agents`, `.claude/codex`, `.claude/skills`,
  `.claude/settings.json`, `harness`) point into this machine's toolkit, so they
  are excluded **locally** in `.git/info/exclude`; shared `.gitignore` files are
  not touched for them;
- everything else (`CLAUDE.md`, `AGENTS.md`, `Makefile`, `docs/`, `progress/`,
  `harness.toml`) is meant to be committed: you commit the install yourself.

Open Claude Code in the directory you installed into (for a monorepo, the
subdir), so its hooks load.

## Production-code dirs (`harness.toml`)

The guards and `make check` protect production code: writes need the plan gate
and must be declared in the subtask's `files:`. By default that is any path with
a `src/` or `include/` segment. For another layout, declare the dirs, relative
to the project root; the list replaces the default:

```toml
[scope]
src_dirs = ["app", "scripts"]
```

`harness.toml` is human-owned: Claude's guards block any write to it, since
removing a dir would switch its protection off. If the file is unreadable or
`src_dirs` is not a non-empty list, every guarded write is blocked and
`make check` fails until it is fixed. Point the copied `Makefile`'s `type`
target at the same dirs (for example `uv run mypy app scripts`).

## Environment variables

| Variable | Values | Default | Read by |
|----------|--------|---------|---------|
| `HARNESS_CODEX_MODE` | `auto`, `manual` | `auto` | `review_with_codex.sh`: `manual` prints the `codex exec` command and prompt instead of running it |
| `HARNESS_CODEX_SANDBOX` | `workspace-write`, `read-only`, `danger-full-access` | `workspace-write` | `review_with_codex.sh`: the `--sandbox` passed to Codex |
| `HARNESS_CODEX_NETWORK` | `1` | off | `review_with_codex.sh`: allows network inside `workspace-write` |
| `HARNESS_ALLOW_COMMIT` | `1` | unset | `guard_bash.sh`: lets Claude run `git commit` for a conscious manual commit |

Set them for the whole Claude Code session, not inline on one command. The
guard reads the environment of the hook process, so `HARNESS_ALLOW_COMMIT=1 git commit`
typed as a command is still blocked:

```bash
HARNESS_ALLOW_COMMIT=1 claude          # this session may commit
HARNESS_CODEX_MODE=manual claude       # gates print the Codex command instead
```

### Codex sandbox

Under `workspace-write`, Codex can write the project but not `.git`, and has no
network. `review_with_codex.sh` adds only the writable roots each gate needs:

| Gate | Extra writable roots |
|------|----------------------|
| `roadmap`, `phase`, `plan`, `code` | the uv cache |
| `doc` (close) | the uv cache + the git dir (it makes the commit) |

Network is needed only if uv must download dependencies during a gate. Run
`uv sync --all-groups` before the gates instead of setting `HARNESS_CODEX_NETWORK=1`.

## Codex model and effort per role

Each Codex role prompt starts with a YAML header that `review_with_codex.sh`
passes to the CLI as `--model` and `-c model_reasoning_effort=…`:

```yaml
---
codex_model: gpt-6-sol
codex_effort: medium
---
```

| Role file | Used by gates |
|-----------|---------------|
| `codex/reviewer.md` | `roadmap`, `phase`, `plan`, `code` |
| `codex/documenter.md` | `doc` |

`codex_effort` accepts `low`, `medium`, `high` or `xhigh`; any other value is
not passed. The files are symlinked into every project, so a change here
applies to all of them.

## Hooks and permissions

`settings.json` (symlinked as `.claude/settings.json`) wires the hooks and a
small allowlist:

| Hook | Script | Effect |
|------|--------|--------|
| `PreToolUse` `Edit\|Write\|MultiEdit\|NotebookEdit` | `harness/guard_write.sh` | blocks protected and out-of-scope writes |
| `PreToolUse` `Bash` | `harness/guard_bash.sh` | blocks commits, attribution and shell writes to protected paths |
| `PostToolUse` `Edit\|Write` | `harness/post_write.sh` | fast lint + tests after each write; never blocks |
| `Stop` | `harness/stop_gate.sh` | runs `make check` before the session ends |

Allowed without a prompt: `make *`, `uv run *`, `./harness/review_with_codex.sh *`,
`./harness/check.sh`.
