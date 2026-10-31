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

## Git conventions (`harness.toml` `[git]`)

Each project can follow its team's branch and commit conventions. The values
live in the project's `harness.toml`, so two projects can use different ones:

```toml
[git]
branch = "feature/{ticket}-{slug}"     # default: "feat/{task}-{slug}"
commit_prefix = "{ticket} "            # default: "" (no prefix)
setup_branch = "chore/project-setup"   # default: "chore/project-setup"
auto_pr = true                         # default: true (open the task PR at its last close)
pr_template = ".github/harness_pr.md"  # default: the harness's lib/pr_template.md
```

| Placeholder | Value | Example |
|-------------|-------|---------|
| `{task}` | the task id | `1.1` |
| `{slug}` | the task title in kebab-case | `tenant-isolation` |
| `{ticket}` | the task's `- ticket:` line in its phase file | `GUAK-042` |

When a pattern uses `{ticket}`, every task carries one:

```markdown
## 1.1 Tenant isolation
- ticket: GUAK-042
- layer: domain
- goal: enforce the organization scope on every query
```

What uses it:

| Who | How |
|-----|-----|
| Leader | creates the task branch from `python3 harness/_harness.py branch <N.M>` instead of writing a name |
| Codex (close) | starts the commit subject with `python3 harness/_harness.py commit-prefix <N.M.K>`: `GUAK-042 feat(tenant): enforce organization scope [1.1.1]` |
| `make check` | fails if a pattern is invalid, a task lacks a required ticket, or the in_progress subtask is not on its task branch |
| Leader, before a subtask | `python3 harness/_guard.py setup-pending` fails while `setup_branch` has changes `main` lacks; squash merges count as merged |

Without a `[git]` section nothing is enforced and the defaults apply, so
projects installed earlier keep working on the branches they already use.

### Task pull request

With `auto_pr` on (the default, also without a `[git]` section), the close of
the last subtask of a task pushes the task branch and opens its PR with `gh`
(details in [usage](usage.md), step 6). `pr_template` points to a Markdown file,
relative to the project root, that replaces the default body. It may use these
placeholders, each as many times as it likes, and leave out any of them:

| Placeholder | Filled by | Content |
|-------------|-----------|---------|
| `{task}`, `{title}` | harness | task id and title |
| `{overview}`, `{approach}`, `{decisions}` | Codex | from `progress/pr_<N.M>.md` |
| `{issues_resolved}`, `{issues_open}`, `{follow_ups}`, `{review_order}` | Codex | from `progress/pr_<N.M>.md` |
| `{subtasks}` | harness | table: subtask, commit, spec, Codex reviews, reference note |
| `{files}` | harness | files added/modified/deleted on the branch, without harness bookkeeping |
| `{branch}` | harness + Codex | head and base, task dependencies, Codex's branch notes |

A missing `pr_template` file fails the PR step with a warning (never the close).

### Issues lane (fixed conventions, not configurable)

Issues follow the same conventions in every project, so these are not part of
`[git]`:

| What | Convention |
|------|------------|
| Branch | `<type>/ISS-<n>-<slug>` from `origin/main`, enforced while an issue subtask is in progress (with or without `[git]`) |
| Type | from the issue's GitHub label only: `bug`→`fix`, `refactor`→`refactor`, `enhancement`→`feat`, `documentation`→`docs`; zero or several known labels → `make issue` refuses |
| Queue labels | `priority:now`, `priority:next`, `priority:backlog`; set only by the harness |
| Claim | assignee + label `status:in-progress` + annotated tag `claim/ISS-<n>` on `origin` |
| Commit | subject ends in `[ISS-<n>.<k>]`, footer `Plan: docs/plan/issues/…`; no `commit_prefix` (no ticket) |
| PR | same template as a task PR (`{task}` is `ISS-<n>`), titled with the issue type, ending with `Closes #<n>` |

The labels are created on first use (`gh label create --force`). `gh` must be
logged in for `make issue`, `make issue-start`, `harness/issue.sh`, the PR step
and the queue block of `make status`; `make check` and the hooks never call it.

## Environment variables

| Variable | Values | Default | Read by |
|----------|--------|---------|---------|
| `HARNESS_CODEX_MODE` | `auto`, `manual` | `auto` | `review_with_codex.sh`: `manual` prints the `codex exec` command and prompt instead of running it |
| `HARNESS_CODEX_SANDBOX` | `workspace-write`, `read-only`, `danger-full-access` | `workspace-write` | `review_with_codex.sh`: the `--sandbox` passed to Codex |
| `HARNESS_CODEX_NETWORK` | `1` | off | `review_with_codex.sh`: allows network inside `workspace-write` |
| `HARNESS_CHECK_CMD` | a shell command | `make check` | `review_with_codex.sh`: the check it runs outside the sandbox before each gate and before the close commit |
| `HARNESS_ALLOW_COMMIT` | `1` | unset | `guard_bash.sh`: lets Claude run `git commit` for a conscious manual commit |
| `HARNESS_AUTO_PR` | `0` | on | `review_with_codex.sh`: `0` keeps the task branch local at its last close (no push, no PR) |
| `HARNESS_GH` | a path | `gh` | `review_with_codex.sh`: the GitHub CLI used to open the task PR; also `_issue.py` and `plan_status.py` (issues lane) |

Set them for the whole Claude Code session, not inline on one command. The
guard reads the environment of the hook process, so `HARNESS_ALLOW_COMMIT=1 git commit`
typed as a command is still blocked:

```bash
HARNESS_ALLOW_COMMIT=1 claude          # this session may commit
HARNESS_CODEX_MODE=manual claude       # gates print the Codex command instead
```

### Codex sandbox

Under `workspace-write`, Codex can write the project (plus the uv cache) but
not `.git`, and has no network. With the network off the sandbox also blocks
local sockets, so test clients such as FastAPI's `TestClient` hang inside it.
The suite therefore never runs in the sandbox:

| Step | Who | Where |
|------|-----|-------|
| `make check` before every gate | `review_with_codex.sh` | outside the sandbox; Codex gets the exit code and the log |
| Close: reference doc, `status: done`, tracking, commit message | Codex | sandbox, writes `progress/close_<N.M.K>.msg` |
| `make check` + the close commit | `review_with_codex.sh` | outside the sandbox; no commit if red, or if the message lacks `[N.M.K]` or carries attribution |

In manual mode, run `./harness/review_with_codex.sh close <N.M.K>` after Codex
writes the message.

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
