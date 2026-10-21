# Configuration

Every knob the harness reads: `install.sh` options, environment variables and
the per-role Codex settings. Defaults are least privilege; each opt-out is
explicit.

## `install.sh` options

```bash
~/.claude-codex-harness/install.sh <project-path> [--force] [--lang python|c] [--python <pkg> | --c <name>]
```

| Option | Effect |
|--------|--------|
| `--python <pkg>` | Python template set + `lib/scaffold_python.sh`: canonical `pyproject.toml`, `src/<pkg>/__init__.py`, `src/<pkg>/py.typed`, `README.md`, `tests/unit/` (only what is missing). `<pkg>` must be snake_case. |
| `--c <name>` | C template set + `lib/scaffold_c.sh`: `src/`, `include/<name>/`, `tests/unit/`. |
| `--lang python\|c` | Template set only, no scaffold (existing project). Default `python`. |
| `--force` | Overwrite the copied templates (and scaffold files) that already exist. |

Without `--python`, a Python project needs its own `pyproject.toml` with ruff,
mypy and pytest; if there is none, the final `make check` is skipped.

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
