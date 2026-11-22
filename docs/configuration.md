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
| `{attention}` | harness + Codex | "For the human": base drift (commits behind `origin/<base>`, `git merge-tree` conflicts; information only, nothing is rebased), changed dependency / migration / CI / auth paths (path heuristic), and Codex's `## For the human` note |

Before pushing, the PR step checks the branch (issue #80): it never pushes the
base, `main` or `master`; it refuses a branch other than the one the task or
issue lives on (for tasks only with a `[git]` section; without one it warns);
and it refuses a branch that contains commits of the local base that
`origin/<base>` does not have. A refusal pushes nothing and exits non-zero.

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

## Review round cap (`harness.toml` `[review]`)

```toml
[review]
max_rounds = 3   # default: 3; 0 = no cap
```

After `max_rounds` consecutive `CHANGES_REQUESTED` verdicts of one gate on one id
(roadmap, phase `NN`, issue `ISS-n`, issue draft, or the `plan` / `code` gate of
one subtask), counted from `progress/review_history/`, `review_with_codex.sh`
refuses to launch that review (non-zero exit, nothing written) and `make status`
shows `Next: needs human (N rounds) ...`. The subtask stays `in_progress`. An
approval of that gate resets the count; a red check in code mode never makes a
round, so it never counts. Each gate and id has its own counter.

Only the human sets the cap: there is no environment variable for it, so an agent
cannot lift it. To resume after deciding what to do, raise `max_rounds` (or set it
to `0`) in `harness.toml`. A value that is not an integer `>= 0` fails closed.

## Attempt budget (`harness.toml` `[review]`)

```toml
[review]
max_attempts = 3   # default: 3; 0 = no cap
```

The round cap counts rejections; the attempt budget counts runs that ended with
**no outcome** (issue #46). Every attempt of a step on one id is recorded in
`.git/harness-gate-runs/attempts_<step>_<id>.json` (outside the tree, never
committed). The steps are every Codex gate (in `auto` mode), the close (`doc` and
`close <N.M.K>`) and the PR (`pr <N.M|ISS-n>`). An attempt fails when the check is
red at a code gate, the reviewer or documenter exits non-zero, the verdict is
invalid, the seal or the close commit fails, the push or `gh pr create` fails, or
the run stops before any outcome (killed, interrupted). A final verdict, the close
commit or an open PR ends an attempt well and resets the count. A refusal by a
precondition (the gate's prerequisite not approved, a stale code approval at `doc`,
the round cap) is not an attempt: it launches nothing and names its own cause.

After `max_attempts` failed attempts in a row, `review_with_codex.sh` refuses that
step before it launches anything (no check, no Codex, no commit, no push), and
`make status` shows `Next: needs human (N failed attempts) ...` with the last
reason and the ledger path. Each step and id has its own count (a cap above the 20
entries the ledger keeps still works). The budget check and the start of an
attempt are one locked step, and a ledger that cannot be read or written stops the
step (fail closed). The count started with this ledger: code-gate records from
before it never count.

Only the human sets the budget (no environment variable). To resume after fixing
the cause, raise `max_attempts` (or set it to `0`) in `harness.toml`, or remove the
ledger file named in the message. A value that is not an integer `>= 0` fails
closed. Manual mode records no gate attempt: the human runs Codex there.

The numbered code-gate records next to the ledger (`code.<n>_<id>.json`) are
rotated: only the newest 5 of each subtask stay, plus the record of a run still in
progress. The approved record
(`code.approved_<id>.json`), which the close needs, is never removed.

## Review effort and checklists by risk (`harness.toml` `[review.risk]`, `[review.surfaces]`)

A subtask spec may rate itself, among its `- key: value` lines:

```markdown
# 2.1.3 Login rate limit
- status: pending
- risk: high                 # low | medium | high (optional)
- surfaces: security, data   # optional; or: none
```

The rating sets the Codex reasoning effort and adds review checklists to that
subtask's `plan` and `code` gates (issue #44). The built-in table, which
`harness.toml` overrides per field and extends with new surfaces:

```toml
[review.risk.high]
codex_effort = "xhigh"          # low | medium | high | xhigh
checklists = ["high-risk"]      # codex/checklist-<name>.md

[review.surfaces.security]
codex_effort = "xhigh"
checklists = ["security"]
paths = ["src/*/auth/**"]       # optional: these files add the surface by themselves

[review.surfaces.data]
checklists = ["data"]

[review.surfaces.migrations]
codex_effort = "xhigh"
checklists = ["migrations"]
```

`risk.low` and `risk.medium` add nothing by default. A level applies with every
level below it (`high` also gets what `medium` and `low` give), so a higher risk never
reviews lighter than a lower one. A new surface
(`[review.surfaces.payments]`) can reuse any built-in checklist; checklists are
toolkit files (`codex/checklist-high-risk.md`, `-security`, `-data`, `-migrations`),
so an agent cannot rewrite one inside a project.

The policy is conservative, so a low rating never buys a lighter review:

- **Never below the role.** The effort is the highest of `reviewer.md`'s
  `codex_effort` and every rule that applies; `risk: low`, or a rule with a lower
  effort, keeps the role's.
- **Files raise it.** A `files:` entry matching `[sessions] high_risk_paths` makes
  the risk `high`, and one matching a surface's `paths` adds that surface, whatever
  the spec declares. `make check` warns when an open subtask's spec understates what
  its files imply.
- **Sealed with the plan.** The phase, task or issue gate reviews the ratings (an
  understated one is `CHANGES_REQUESTED`) and seals them with the specs: the seal
  gets a `rating:<spec>` line for each rated spec (unrated specs get none, so older
  seals stay valid). The sealed rating is a floor for the subtask's gates. Raising
  it in the plan gate is fine and its re-seal keeps the raise; lowering it fails
  `make check` until the phase/task/issue gate approves it again or the human
  re-seals (`review_with_codex.sh seal …`).
- **Fails closed.** An invalid `risk:`, a malformed or unknown surface, a missing
  checklist file or an invalid table makes `make check` fail and the gate stop
  before Codex runs. Done and baseline subtasks are exempt from that check (their
  specs may predate the fields); a seal written before issue #44 has no `rating:`
  lines and stays valid while its specs are unchanged. Such a seal approved no
  rating, so it gives no floor: the first plan-gate re-seal records the spec's rating
  from then on.

With a rating, the gate prompt gets a `Review policy` paragraph (effort, why, and
the checklist paths); the reviewer adds a `## Checklists` section to its verdict
(one line per item: met, gap, n/a; any gap is `CHANGES_REQUESTED`). A subtask
without a rating, and no matching `paths`, is reviewed exactly as before. The
reviewer is always Codex.

## Codex and PR controls (`harness.toml` `[codex]`, `[pr]`)

```toml
[codex]
mode = "auto"               # auto (default) | manual: print the codex exec command and prompt
sandbox = "workspace-write" # workspace-write (default) | read-only | danger-full-access
network = false             # true: network inside workspace-write

[pr]
auto = true                 # false: the last close keeps the branch local (no push, no PR)
gh = "gh"                   # the GitHub CLI the PR step and the issue cleanup use
```

`[pr] auto = false` and `[git] auto_pr = false` both turn the PR step off. A value
outside these fails closed: the gate exits non-zero before doing anything.

Only the human sets them (issue #90). `review_with_codex.sh` ignores the old
environment overrides `HARNESS_CODEX_MODE`, `HARNESS_CODEX_SANDBOX`,
`HARNESS_CODEX_NETWORK`, `HARNESS_GH` and `HARNESS_AUTO_PR`: each one found
prints a warning naming it and pointing here, and is dropped before any child
script runs. The Bash guard blocks a command that assigns one of them inline next
to a gate (`review_with_codex.sh` or `make`). `harness/issue.sh`, `_issue.py` and
`plan_status.py` (not decision runs) still read `HARNESS_GH`.

## Parallel sessions (`harness.toml` `[sessions]`)

```toml
[sessions]
model = "sonnet"            # a session that executes a task/subtask (default)
effort = "medium"           # low | medium | high | xhigh | max
high_risk_model = "opus"    # when the unit's files: match high_risk_paths
high_risk_effort = "high"
high_risk_paths = []        # globs, e.g. ["migrations/**", "src/*/auth/**"]
```

`harness/session_launch.sh <id> [--worktree <path>] [--prompt <text>] [--exec | --hook <prog>]`
prints `cd <worktree> && claude --model … --effort … --permission-mode auto "<prompt>"`
for a subtask, a task or an issue (a task or issue uses the files of all its
subtasks), every word shell-quoted. In a glob `*` and `?` stay within one path
segment and `**` crosses them; a glob that matches a directory covers the files
under it. An unknown key or invalid value fails closed. The launcher never
creates the worktree.

## Environment variables

| Variable | Values | Default | Read by |
|----------|--------|---------|---------|
| `HARNESS_CHECK_CMD` | a shell command | `make check` | `review_with_codex.sh`: the check it runs outside the sandbox before each gate and before the close commit (test fixtures only: a decision run warns) |
| `HARNESS_ALLOW_COMMIT` | `1` | unset | `guard_bash.sh`: lets Claude run `git commit` for a conscious manual commit |
| `HARNESS_GH` | a path | `gh` | `_issue.py`, `plan_status.py` and `issue.sh` outside the gates; the gates use `[pr] gh` |

Set them for the whole Claude Code session, not inline on one command. The
guard reads the environment of the hook process, so `HARNESS_ALLOW_COMMIT=1 git commit`
typed as a command is still blocked:

```bash
HARNESS_ALLOW_COMMIT=1 claude          # this session may commit
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
`uv sync --all-groups` before the gates instead of setting `[codex] network = true`.

## Codex model and effort per role

Each Codex role prompt starts with a YAML header that `review_with_codex.sh`
passes to the CLI as `--model` and `-c model_reasoning_effort=…`:

```yaml
---
codex_model: gpt-6-sol
codex_effort: high
---
```

| Role file | Model / effort | Used by gates |
|-----------|----------------|---------------|
| `codex/codex-planner.md` | `gpt-6-astra` / `high` | `roadmap`, `phase`, `task`, `task-close`, `phase-close` |
| `codex/reviewer.md` | `gpt-6-sol` / `high` | `plan`, `code`, `issue`, `issue-draft` |
| `codex/documenter.md` | `gpt-6-luna` / `low` | `doc` |

`codex-planner.md` holds no criteria of its own: it applies `reviewer.md`'s common
criteria, the section of its planning mode and its hard rules with a stronger model.

`codex_effort` accepts `low`, `medium`, `high` or `xhigh`; any other value is
not passed. A subtask's rating may raise the reviewer's effort for its `plan` and
`code` gates, never lower it (see "Review effort and checklists by risk"). The files are symlinked into every project, so a change here
applies to all of them.

## Hooks and permissions

`settings.json` (symlinked as `.claude/settings.json`) wires the hooks and a
small allowlist:

| Hook | Script | Effect |
|------|--------|--------|
| `PreToolUse` `Edit\|Write\|MultiEdit\|NotebookEdit` | `harness/guard_write.sh` | blocks protected and out-of-scope writes; a call from the `claude-planner` subagent (`agent_type` in the hook payload) may write only `docs/plan/**` and `CLAUDE.md` |
| `PreToolUse` `Bash` | `harness/guard_bash.sh` | blocks commits, attribution and shell writes to protected paths |
| `PostToolUse` `Edit\|Write` | `harness/post_write.sh` | `make lint` only after each write; never blocks |
| `Stop` | `harness/stop_gate.sh` | advisory, every turn: warns when there is no green record (`.git/harness-check.ok`) for the current state; never blocks, never runs `make check` |

The full `make check` runs at the review gates, at `close` and before a PR:
gates and `close` write the green record after a real, clean, green run whose
fingerprint did not change during the run (a plain `make check` you run by hand
writes nothing). `review_with_codex.sh pr`, and the PR the last close of a task
opens, push only a committed tree (no uncommitted or untracked changes) whose
green record matches its exact state; without one, `pr` runs that clean check
itself and blocks if it is red.

Allowed without a prompt: `make *`, `uv run *`, `./harness/review_with_codex.sh *`,
`./harness/check.sh`.

## Changing lint/type/build config

Once the I5 baseline (`progress/contract_baseline.json`) is sealed, the config that
decides how lint, type checking and build run is sealed with it (issue #66). That covers
ruff/mypy config in `pyproject.toml`, `ruff.toml`, `.ruff.toml`, `mypy.ini`, `.mypy.ini`
and `setup.cfg`, config-path flags, every makefile, the recipes and prerequisites of
`lint`/`type`/`build`/`verify`, and the variables feeding them (`CC`, `CFLAGS`, `WARN`,
`CSTD`, `EXTRA_CFLAGS`…). `harness/run_check.sh` warns on any drift, even under
`HARNESS_CHECK_CMD`. To change it:

1. Make the change, then run `python3 harness/_contract.py contract-propose`. For a
   relaxation, add `--reason "<why>"`.
2. Run `./harness/review_with_codex.sh contract`. Codex passes a tightening as is; a
   relaxation passes only with a justified reason.
3. The human approves with `python3 harness/_contract.py contract-approve <hash>`. That
   reseals only that exact diff; a change made after the proposal is refused.

Rollout is WARN only (plan 0.5). The design is in `docs/design/verification-contract.md`.
