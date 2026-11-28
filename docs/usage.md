# Usage

The full cycle on an installed project: what you tell the leader, what each
gate produces and what to check before approving the next step. Install first
(see the [README](../README.md#install)).

Open Claude Code at the project root. `CLAUDE.md` makes Claude act as the
**leader** (Opus 5.5 / high); it stops after every gate and waits for your OK.
The plan itself is written by the `claude-planner` subagent (Opus 5.5 / xhigh)
from the leader's brief; you never switch models by hand.

```bash
cd /path/to/project && claude
```

## 0. Create a new Python project (py-init, then the harness)

For a brand-new project, ask Claude to "init project <name>" (the `py-init`
skill). One guided flow, always in this order:

1. **py-init** scaffolds the Python project (uv, `src/` layout, tests, canonical
   `pyproject.toml`, Graphify, Archify). It writes no `CLAUDE.md`, `AGENTS.md`,
   `Makefile` or anything under `.claude/`: those belong to the harness.
2. It **offers `install.sh <project-dir>`** (with your OK, without `--python`, so
   py-init's `pyproject.toml` stays). On a repo without history this makes the
   single initial commit `chore: install harness` (scaffold + harness), switches
   to `chore/project-setup` and requires `make check` green.
3. On `chore/project-setup` it adds the CI workflows (`ci.yml` runs the same
   ruff / mypy / pytest commands as `make check`; empty suites are green) and
   opens the draft PR.

If the harness is already installed, py-init detects it
(`skills/py-init/scripts/detect_harness.sh`) and only scaffolds Python: no role
files, commits or branches. On a repo that already had history py-init never
commits; you commit through the usual authorised flow.

Archify is installed only under the project's `.agents/skills/archify/`
(`skills/py-init/scripts/install_archify.sh`), never through the shared
`.claude/skills` link. Claude does not load it automatically: ask
"Read `.agents/skills/archify/SKILL.md` and follow it".

## 1. Define the stack (before the discovery)

A new project starts with the **stack step** (issue #115): `make status` says
`ask the leader to define the stack`. The leader reads the code and its package
manifest first if there are any, then asks one question at a time: first the
project type (API, CLI, library, scraper, firmware, front end…) and the
language, then only the categories that answer makes relevant: architecture
(hexagonal, layered, none…), persistence, framework, toolchain (tests, lint,
types, build), deploy and key dependencies. Nothing is assumed: a category that
does not apply is recorded as `not applicable` with its reason.

`claude-planner` writes `docs/stack.md` (from `templates/docs/stack.md`: each
decision and why; exact versions stay in the package manifest or lock file) and
the leader runs the **stack gate**:

```bash
./harness/review_with_codex.sh stack
```

| Produces | Check before approving |
|----------|------------------------|
| `progress/stack_review.md` (`STACK_APPROVED` / `CHANGES_REQUESTED`) | the verdict line and the analysis |
| `progress/stack.sha256` (on approval) | the decisions match what you want |

Then the leader stops for your OK. `docs/stack.md` is versioned in git; the
discovery and `claude-planner` read it and never ask for the stack again.

- **Order.** Until `docs/stack.md` is `STACK_APPROVED` and intact, the roadmap
  gate refuses to run and `make check` fails as soon as a phase file
  (`docs/plan/phase-*.md`) exists.
- **Changes.** The stack can be edited (through `claude-planner`), but any change
  to `docs/stack.md` voids its approval: `make status` asks for the stack gate
  again, and `make check` fails once the discovery has started.
- **Dependencies.** For Python, `make check` warns (never fails) when the
  first-level `[project] dependencies` of `pyproject.toml` and the
  `## Key dependencies` list of `docs/stack.md` differ. Other manifests are not
  read yet.
- **Existing projects.** A project whose roadmap or plan (a phase) was approved
  before the stack step only gets a `make check` WARN, with or without
  `docs/stack.md`; it is never blocked. To adopt the step, copy the template to
  `docs/stack.md` and ask the leader for the stack step; from its first approval
  on, the rules above apply to it. Any other project, including one installed
  before the stack step that has no `docs/stack.md` and no approved plan yet,
  starts with the stack step.

## 2. Plan the roadmap

Tell the leader what to build. It reads `docs/stack.md` and the repo first if
there is code, then asks one question at a time (never about the stack again):

```text
Let's plan a library with a single function slugify(text: str) -> str.
```

Then `claude-planner` writes `docs/plan/PLAN.md`, every phase file (title + goal only) and the
`## Project context` section of `CLAUDE.md`, and the leader runs the **roadmap gate**:

```bash
./harness/review_with_codex.sh roadmap
```

| Produces | Check before approving |
|----------|------------------------|
| `progress/roadmap_review.md` (`ROADMAP_APPROVED` / `CHANGES_REQUESTED`) | the verdict line and the analysis |
| `progress/plan_roadmap.sha256` (on approval) | `make check` green |

On `CHANGES_REQUESTED` the leader passes the verdict to `claude-planner`, which
fixes the plan, and re-runs the gate. From
here on the roadmap is sealed: editing it fails `make check` until the gate
approves the change again.

## 3. Break down the current phase (tasks only)

```text
OK, roadmap approved. Break down phase 01 and run the phase gate.
```

The leader has the `claude-planner` subagent write only the tasks into the phase file (title, layer, goal, in
order; no subtask specs yet), then runs:

```bash
./harness/review_with_codex.sh phase 01
```

| Produces | Check before approving |
|----------|------------------------|
| `progress/phase_review_01.md` (`PHASE_APPROVED`) | the tasks, in order, cover the phase goal |
| `progress/plan_phase_01.sha256` | each task has one clear goal and layer |

## 3b. Task kickoff and task gate

```text
OK, phase approved. Start the kickoff of task 1.1.
```

The leader reviews what the previous task delivered, asks you short questions
one at a time only if something no longer fits, and has `claude-planner` write one spec per subtask
of this task (`docs/plan/phase-01-<slug>/<N.M.K>-<slug>.md`: `files:` with 1-3
paths, or an empty `files:` only for a spec declaring
`Test-only verification (no production behaviour change)` that changes no
production file, acceptance criteria, description), then runs:

```bash
./harness/review_with_codex.sh task 1.1
```

| Produces | Check before approving |
|----------|------------------------|
| `progress/task_review_1.1.md` (`TASK_APPROVED`) | every file the code needs is in `files:` (a missing one blocks the implementer later) |
| `progress/plan_task_1.1.sha256` | acceptance criteria are concrete examples, not prose |

## 4. Plan gate for a subtask

```text
OK, phase approved. Start the first subtask and run its plan gate.
```

The leader switches to the task branch (`feat/<N.M>-<slug>` by default, or the
project's pattern in `harness.toml` `[git]`, see [configuration](configuration.md#git-conventions-harnesstoml-git);
created from `main` for the first subtask of a task), sets the subtask to `in_progress`, runs
`make plan`, records it in `progress/current.md` and runs:

```bash
./harness/review_with_codex.sh plan 1.1.1
```

| Produces | Check |
|----------|-------|
| `tests/acceptance/test_1.1.1.py`, one test per acceptance criterion, **red** (or already passing under a confirmed marker) | the tests match the spec |
| `progress/plan_review_1.1.1.md` (`PLAN_APPROVED`) | the verdict line |
| `progress/acceptance_1.1.1.sha256` | `sha256sum -c progress/acceptance_1.1.1.sha256` |

`make check` is **red** from here until the code exists: the acceptance tests
fail by design, unless the spec declares a confirmed marker (structural refactor
or test-only verification), whose tests may already pass. The advisory Stop hook warns that there is no green full check; that is
expected, not a harness fault.

## 5. Implement and code gate

```text
OK. Launch the implementer, then the code gate. Stop before the close.
```

The `implementer` subagent does inner TDD (`tests/unit/` + the declared `src/`
files) until `make verify` is green and writes `progress/impl_1.1.1.md`. It
does not commit or mark the subtask done. The leader then runs:

```bash
./harness/review_with_codex.sh code 1.1.1
```

| Produces | Check |
|----------|-------|
| `progress/review_1.1.1.md` (`APPROVED` / `CHANGES_REQUESTED`) | findings, if any, were addressed |
| | `git diff --stat HEAD -- src` lists only the declared files |
| | `make check` green |

## 6. Close

```text
OK, run the close.
```

```bash
./harness/review_with_codex.sh doc 1.1.1
```

Codex writes `docs/reference/1.1.1-<slug>.md`, sets `status: done` in the spec,
runs `make plan`, updates `progress/current.md` and `progress/history.md` and
writes the commit message to `progress/close_1.1.1.msg`. The harness then runs
`make check` outside Codex's sandbox and, if it is green, makes **one commit**
on the task branch:

```text
feat(domain): add slugify function [1.1.1]

Provide pure Unicode-aware slug normalization through the package API.

Plan: docs/plan/phase-01-slugify/1.1.1-slugify-function.md
```

Check it:

```bash
git log -1 --format=full          # your identity, Plan: footer, no attribution
git status --short                # clean
make status                       # the subtask is [DONE]
```

If the reference doc fails the doc gate (missing frontmatter keys or section
anchors), the harness commits nothing and repairs it: in auto mode it runs the
documenter again in *repair mode*, where only that doc may change (up to 2 repairs
per run, each one attempt of the `close` step). A close left pending that way shows
in `make status` as `close: ./harness/review_with_codex.sh doc 1.1.1 (last attempt:
…)`: `doc` repairs the doc and the close reuses the waiting
`progress/close_1.1.1.msg` (manual mode prints the repair prompt, then `close
1.1.1`). A tree snapshot (`.git/harness-doc-repair-1.1.1.json`) is checked after
each repair and before the commit: if anything else changed, the close stops with
`needs human`; check the paths it lists and remove the snapshot to go on. In an
activated project (§11) the same repair covers a last close that forgot its
`changelog.d/<N.M | ISS-n>.md` (or wrote an invalid one): only that fragment, plus
the doc if it also fails, may change.

The first close commit also carries every plan file that was never committed
(`docs/plan/`, verdicts, seals), since Claude cannot commit them earlier.

## 7. Task pull request

When a close finishes the **last subtask of a task** `N.M`, Codex also writes
`progress/pr_N.M.md` (overview, approach, decisions, issues resolved and open,
follow-ups, branch notes, where to start reviewing). After the commit the
harness pushes the task branch and opens **one PR for the task** with `gh`:

- title `feat: <task title> [N.M]`;
- body rendered from the PR template: Codex's narrative plus a subtask table
  (commit, spec, Codex reviews, reference note), the files the branch adds or
  changes (without `progress/` and other harness bookkeeping), and the head,
  base and task dependencies;
- no test output (CI shows it on the PR) and no agent attribution.

If `gh` is missing, the push fails or a PR for the branch already exists, the
close still succeeds: the harness warns (or just pushes the new commits) and
prints the retry command, `./harness/review_with_codex.sh pr N.M`. Merging is
always yours. Turn it off per project with `auto_pr = false` in `harness.toml`
(see [configuration](configuration.md)).

The "issues resolved" part comes from `progress/review_history/`: every gate
verdict (roadmap, phase, plan, code) is appended there, so rounds a gate
rejected survive the next verdict that overwrites the review file.

## 7b. Task close and phase close

After the last close of a task, the leader runs the task-close gate:

```bash
./harness/review_with_codex.sh task-close 1.1     # → progress/task_close_1.1.md
./harness/review_with_codex.sh phase-close 01     # after the last task of the phase
```

Codex checks the delivered work against the task goal and its specs (the phase
goal in `PLAN.md`, for a phase) and writes a traceability matrix: one row per
goal point and acceptance criterion, with its test and `met` or `gap`. Any gap
is `CHANGES_REQUESTED`; the harness also refuses an approval without a matrix or
with a gap row. The next task's kickoff needs `TASK_CLOSE_APPROVED`, and the
next phase's breakdown needs `PHASE_CLOSE_APPROVED`; `make status` shows both.

Both gates reuse the green `make check` of the last close when nothing changed
since (no second full run) and give Codex only the specs, the implementation
and review reports and the diff of the close commits. Cost: see the toolkit's
`docs/design/task-flow.md`.

A phase broken down whole before this flow keeps working (no task gate); only
its tasks not yet done when `make plan` first saw it
(`progress/task_flow_legacy_NN.json`, sealed) need a task-close.

## 8. Next subtask, next phase

The leader shows `make status` and waits. When a task's PR is merged, the next
task starts from `main`. When a phase is done, the leader breaks down the next
one (step 3).

## 8b. Resume after an interruption

After a crash, a closed session or a failed step, nothing has to be remembered:

```bash
make status                  # the whole plan; its Next: line is the next action
python3 harness/_resume.py   # the same action, classified (add --json for tools)
```

`_resume.py` reads only the artifacts: the specs (the single `in_progress` leaf),
the verdicts, the seals, the code-gate records and the attempt ledger. It prints
`Resume: <kind> · <step>` and then either `Run: <command>` or `Wait for the human`.
A command appears only for what the human already authorized: the cycle of the
subtask in progress (plan gate, fix, implement, code gate, close, a retry of its
close or PR, finishing an unfinished seal) and the review gates. Starting a
subtask, a kickoff, a breakdown, new subtasks for a close gate's gaps, a merge, a
seal in manual mode, an old close or PR retry while another subtask is in progress,
or any cap waits for the human: resume never takes a human OK for granted.
A phrase `_resume.py` does not recognise also waits (fail safe).

Recoverable states it names (besides the usual gate steps):

| State in the artifacts | Next action |
|---|---|
| last code gate stopped on a red check | `fix <id>: the check was red ...`, then the code gate |
| last gate run crashed or was killed | `<plan\|code> gate again (last attempt: ...)` |
| `PLAN_APPROVED`, no acceptance seal, gate stopped before sealing | `finish the seal ...` (plan gate again); manual mode: the human seals |
| `APPROVED`, but the bound approval no longer covers the tree | `the code approval of <id> ...: code gate again` |
| `progress/close_<id>.msg` waiting and the ledger says the close failed, or the code approval still covers the tree (`doc` would refuse); a stale approval with code-gate attempts in the ledger, or a used-up close budget, comes first | `retry the close of <id> (...): ... close <id>` |
| close pending, its reference doc fails the doc gate (issue #133) or its changelog fragment is missing (activated) | `close: ... doc <id> (last attempt: ...)` (repair mode) |
| a doc repair changed files outside the doc, or the repairs ran out | `needs human: the close of <id> failed ...` |
| last PR attempt failed | `retry the PR of <N.M>: ... pr <N.M>` |
| more than one leaf `in_progress` | `needs human: ...` |
| a step used its attempt budget | `needs human (N failed attempts) ...` |

With `docs/plan/` present, `feature_list.json` is checked against the specs first: a
stale index resumes as `make plan` and nothing else (a gate run still in progress
comes first: resume waits for it). A project with no `docs/plan/`
(one older than docs/plan, or a test fixture) has nothing to check it against, so
`_resume.py` prints a `WARN` on stderr naming the missing source and then the same
action, taken from `feature_list.json` alone. `--json` lists that warning in
`warnings` (an empty list when the specs exist). This is WARN only for now (plan
rule 0.5); it may become a FAIL later.

The attempt budget is in [configuration](configuration.md#attempt-budget-harnesstoml-review).

## 9. Issues lane (a GitHub issue at any point)

An issue never becomes a task of a phase: it has its own lane, with the same
gates (design: [design/issues-lane.md](design/issues-lane.md)).

```bash
make issue N=14                      # queue it (PRIORITY=now|next|backlog, default next)
make status                          # the queue (from GitHub) and Next:
harness/issue.sh intake 14           # duplicates, missing info, scope (read only)
harness/issue.sh ask 14              # with the user's OK: post the question, park (needs-info)
harness/issue.sh unblock 14          # the author answered: back to the queue
make issue-start N=14                # claim it: assigned, status:in-progress,
                                     # branch <type>/ISS-14-<slug>, header in docs/plan/issues/
# the leader breaks it down with you: ISS-14.1, ISS-14.2… (one spec at a time)
make plan && make check
./harness/review_with_codex.sh issue ISS-14        # issue gate → ISSUE_APPROVED
# per subtask, the usual cycle: plan ISS-14.1 → implementer → code ISS-14.1 → doc ISS-14.1
# the last close opens one PR with "Closes #14"; you merge it
harness/issue.sh resume              # after a `now` issue is merged: back to the task
```

- **Type** comes only from the issue's label (`bug`, `refactor`, `enhancement`,
  `documentation`); fix the label if `make issue` refuses.
- **`now`** may interrupt a task only between subtasks, and must be started
  from that task's branch; **`next`** waits until the task is merged.
- **Give it back** before any work: `harness/issue.sh release 14`.
- **A new issue**: `make issue-new` (draft in `progress/issue_draft.md`) →
  `./harness/review_with_codex.sh issue-draft` → `DRAFT_APPROVED` → your OK →
  `harness/issue.sh publish`.

## 10. Existing projects: migrate the copied harness files

Copied files (`Makefile`, `CLAUDE.md`, `AGENTS.md`, `docs/plan/issues/README.md`)
do not follow the toolkit; they change only through a migration. These commands
are **human-only**: run them from your own shell, never ask Claude to run them.

```bash
~/.claude-codex-harness/install.sh <project> --migrate            # dry-run: diff only
~/.claude-codex-harness/install.sh <project> --migrate --apply    # write (asks y/N)
~/.claude-codex-harness/install.sh <project> --rollback <id>      # dry-run of the undo
~/.claude-codex-harness/install.sh <project> --rollback <id> --apply
```

- **Dry-run by default.** `--migrate` prints the diff of every file it would
  write and touches nothing. `--yes` skips the y/N question (only for scripts
  with no terminal, such as the C0 runner).
- **Links are never touched.** The links (`.claude/agents`, `.claude/codex`,
  `.claude/skills`, `.claude/settings.json`, `harness`) must already point into
  the toolkit; a missing or wrong one stops the migration with the command to
  fix it. A project whose `.claude/skills` is a real directory (per-skill links)
  is not supported until Track 2.
- **Only harness-owned parts.** `docs/plan/issues/README.md` is the harness's
  whole file; in `Makefile`, `CLAUDE.md` and `AGENTS.md` only the harness
  fragment is managed (the `include harness/harness.mk` block, the
  `@harness/WORKFLOW.md` import, the WORKFLOW pointer). Your targets and text
  are never compared nor rewritten. A customised harness part stops the whole
  migration (a 3-way diff for a whole file); nothing is written. `src/`,
  `tests/` and any other file are never written.
- **All or nothing.** Every file is backed up first in
  `.harness/migrations/<id>/`; any failure restores all of them. `--rollback
  <id>` restores the backups and deletes the files the migration created, and
  stops if one of them was edited since.
- **The record** `.harness/templates.json` lists the version of each managed
  file and the project's migration level. Commit it with the migrated files
  (the migration never commits). The first migration of a project installed
  before it registers what is there; unknown content stops it.
- **Backups stay out of git.** Fresh installs add `.harness/migrations/` to the
  generated `.gitignore`. In an existing project the migration adds
  `/<path>/.harness/migrations/` to `.git/info/exclude` (local, not a project
  file; your `.gitignore` is never edited; works in monorepo subdirs and
  worktrees). Outside git nothing is written; add the entry yourself if you run
  `git init` later.
- **Migration level.** `make check` reports it; below the level the toolkit
  requires it is a WARN (never a FAIL in this version) with the command to run.
- Claude cannot write `.harness/` (guards).

## 11. Changelog and releases

Design: [`docs/design/releases.md`](design/releases.md). This version covers the
changelog and the release plan; bumping the version and publishing a release come
later.

- **New projects** get `CHANGELOG.md`, `changelog.d/`, a `docs/releases.md`
  template and the record `.harness/changelog.json` (the changelog is
  *activated*). They belong to the project: no install overwrites them, not even
  `--force`.
- **Projects installed before** keep working as they are; `make check` only warns
  with the command that activates it (human-only, from your own shell or with `!`
  in the Claude console):

  ```bash
  ~/.claude-codex-harness/install.sh <project> --changelog                 # dry-run: the plan
  ~/.claude-codex-harness/install.sh <project> --changelog --apply         # asks before applying and before merging
  ~/.claude-codex-harness/install.sh <project> --changelog --apply --yes   # no questions
  ```

  It works in a temporary worktree of `main` (your checkout is never touched):
  branch `chore/harness-changelog`, the files (an existing `CHANGELOG.md` is kept:
  it prints the lines to add), one commit with no agent attribution, a PR, and the
  merge only once every check is green, pinned to the commit it pushed. A repo
  with no CI checks is never merged: it prints the `gh pr merge` for you. Relaunch
  it after a red check or an interruption: it reuses the branch, the commit and the PR. Tasks and issues
  finished before it need no fragment; those in flight get one at their last close.
- **Per task or issue**: the specs declare `- changelog:` and `- incompatible:` at
  the kickoff (the task and issue gates require them; the phase gate for a phase
  broken down whole; never a spec already done or approved before the activation); Codex writes
  `changelog.d/<N.M | ISS-n>.md` at the last close, inside the PR. `make check`
  refuses that close without a valid fragment.
- **Consolidation** (the release, human or Codex; never Claude):
  `python3 harness/_changelog.py consolidate` shows the new `## [Unreleased]`
  section; `--apply` writes it and empties `changelog.d/`. Running it twice changes
  nothing.
- **Release plan**: after the roadmap, `make status` proposes the release discovery
  (`Also:`), which blocks nothing. `docs/releases.md` goes through
  `./harness/review_with_codex.sh releases` (`RELEASES_APPROVED`, sealed) and your
  OK. A later change, a roadmap change or an unplanned incompatible change only
  warns in `make check`.

## Checking that Codex really ran

Every gate is a `codex exec` session with the project as its working dir. Its
log is under `~/.codex/sessions/<yyyy>/<mm>/<dd>/rollout-*.jsonl`, and its start
time matches the verdict file's mtime:

```bash
ls -lt ~/.codex/sessions/$(date +%Y/%m/%d)/ | head
```

Claude cannot fake a verdict: writing any Codex-owned file in `progress/` is
blocked by `guard_write.sh` and `guard_bash.sh`.

## Verdict receipts

A gate succeeds, records and seals only on a valid verdict written during that
run: the previous verdict is set aside first and restored byte-identical if the
reviewer fails or writes no valid verdict for that gate. Each accepted verdict
gets a receipt, `progress/verdict_<verdict-file>.sha256`
(`<sha256>  <path>  <gate>`). In manual mode the printed steps include the
human-only `python3 harness/_verdict.py receipt <gate> <id>`.

`make check` reports, as WARN only (the exit code does not change), a receipt
that does not exactly match its verdict, verdicts without a matching receipt
(one summary line) and receipts without a verdict. The remedy is to re-run that
gate. Legacy items are exempt while the legacy inventory verifies.

Known limits:

- After a SIGKILL of the gate, an orphaned reviewer can still write a verdict
  later. It is reported if it lacks a matching receipt; a verdict byte-identical
  to the previous one still matches the previous receipt.
- A crash between accepting a verdict and sealing it leaves an approved verdict
  unsealed. The seal checks report it; re-running the gate fixes it.
- In a linked worktree the verdict slot lives outside the project root, so the
  guards do not protect it.
- A receipt detects later byte changes; it does not prove who wrote the bytes.

## Existing project: register what is already built

On a repository that already has working code, the first phases record that
work, so the plan holds the whole history and new work can depend on it:

```text
Read the code and docs/, and plan what is already built as the first phases,
by area, with its subtasks as baseline. Then plan the pending work.
```

Each implemented subtask is written with `status: baseline` instead of `pending`:

```markdown
# 1.1.1 Settings loaded from the environment
- status: baseline
- layer: infrastructure
- depends: none
- files:
  - app/core/config.py
- acceptance:
  - AC1 | rejects a missing JWT secret | tests/unit/test_core_config.py::test_missing_secret

## Description
Pydantic settings, validated at startup. Documented in docs/backend/configuration.md.
```

| | `pending` / `done` | `baseline` |
|---|---|---|
| Reviewed by | task (or phase), plan and code gates | the task gate (the phase gate, for a phase broken down whole), against the code |
| Needs a code review and `docs/reference/` | yes (at the close) | no |
| Counts as done for phases and `depends` | when `done` | once its task is `TASK_APPROVED` (its phase `PHASE_APPROVED`, for a phase broken down whole) |
| Acceptance tests | new, in `tests/acceptance/` | existing tests |

The task (or phase) gate requests changes for a baseline subtask the code does not
implement, or only partly implements; that one is `pending`. After the
approval, `status: baseline` is sealed: turning a subtask into baseline (or
back) fails `make check`.

## Legacy inventory

On a project that already passed gates before a newer rule existed, the legacy
inventory records what was approved or done at one commit, so later rules (I1, I4,
I16) can tell old work from new. It is **human-only**: no make target, and Claude's
hooks block `create`, `review` and `seal` (it may only run `verify` and read the files).

```bash
harness/legacy_inventory.sh create --at <commit>   # build from the tree at <commit>
harness/legacy_inventory.sh review                 # Codex (read-only); needs network
harness/legacy_inventory.sh seal                   # needs INVENTORY_APPROVED
harness/legacy_inventory.sh verify                 # OK | WARN not created | FAIL <reason>
```

| File | Written by |
|---|---|
| `progress/legacy_inventory.json` | `create` (from `git archive <commit>`, never the working tree) |
| `progress/legacy_inventory_review.md` | `review`, from the captured Codex output |
| `progress/legacy_inventory.sha256` | `seal`, only if the review says `INVENTORY_APPROVED` for this exact JSON hash |

The inventory is closed: `create` refuses while any of the three files exists, and
nothing rewrites it. An item stops being legacy as soon as its files change. Without
an inventory, `make check` is unchanged.

## Manual Codex mode

With `[codex] mode = "manual"` in `harness.toml` (see [configuration](configuration.md)), each
gate prints the exact `codex exec …` command, the prompt and the verdict file
instead of running Codex. After an approved stack, roadmap, phase or plan gate,
**you** seal what was approved; the Bash guard blocks Claude from doing it:

```bash
./harness/review_with_codex.sh seal stack
./harness/review_with_codex.sh seal roadmap
./harness/review_with_codex.sh seal phase 01
./harness/review_with_codex.sh seal task 1.1
./harness/review_with_codex.sh seal 1.1.1
```

## Hooks

`settings.json` wires four hooks, each with an explicit `timeout` (seconds) so a
hung hook cannot stall a session for Claude Code's 600 s default:

| Event | Matcher | Script | Timeout | On failure / timeout |
|---|---|---|---|---|
| PreToolUse | `Edit\|Write\|MultiEdit\|NotebookEdit` | `guard_write.sh` | 15 | blocks (`onFailure: "block"`) |
| PreToolUse | `Bash` | `guard_bash.sh` | 15 | blocks (`onFailure: "block"`) |
| PostToolUse | `Edit\|Write\|MultiEdit\|NotebookEdit` | `post_write.sh` | 120 | never blocks (feedback only) |
| Stop | — | `stop_gate.sh` | 15 | never blocks (advisory) |

`post_write.sh` runs `make lint`. If the project `Makefile` declares a
`LINT_FILES` variable (for example `LINT_FILES ?= .` used by the `lint`
recipe), it runs `make lint LINT_FILES=<written file>` instead; without it, or
when the payload has no plain path inside the project, the full lint runs.
An optional `LINT_EXTS` (for example `LINT_EXTS := .py .pyi`) limits the
scoping to files with those extensions, so a docs edit is not passed to the
linter; other files get the full lint.

The Python template (`templates/Makefile`) declares both (`LINT_FILES ?= .`,
`LINT_EXTS := .py .pyi`), so plain `make lint` still checks the whole project.
`install.sh <project> --migrate` upgrades the exact pre-#92 template `lint`
recipe (dry-run, `--apply`, `--rollback`); a customised `lint` recipe is left
as is, with a `[WARN]` that says how to scope it by hand. The C template does
not support it: its `lint` (clang-format, cppcheck, clang-tidy) runs over the
whole tree, so every write gets the full lint.

## Stop hook

Claude Code runs the Stop hook at the end of **every** assistant turn, not only
when the session closes, so `stop_gate.sh` is advisory and cheap:

- **What it does:** computes the working-tree fingerprint (about a second) and
  compares it with the last green full-check record (`.git/harness-check.ok`).
- **What it says:** `last green full check matches this exact state`, or
  `no green full check for this state`, or `unknown` when the fingerprint cannot
  be computed (never reported as green).
- **What it never does:** run `make check`/`check.sh` or block the stop.

The real full `make check` runs at the gates, at close and before a PR. Gates
and close write the green record only after a clean, green check of an
unchanged tree; the PR path blocks unless that record matches the exact current
state. After each Edit/Write/MultiEdit/NotebookEdit, `post_write.sh` runs only `make lint` (feedback,
never blocks).

## Recovery when a hook blocks Claude

The two guards (`guard_write.sh`, `guard_bash.sh`) fail closed: if they cannot
start, crash, time out or get an unreadable payload, the tool call is blocked
(`BLOCKED: harness hook … failed` / `harness guard error`). Claude Code
>= 2.1.295 is required for the start-up and timeout cases (`onFailure: "block"`
in `settings.json`). A broken toolkit therefore blocks Claude in every linked
project until you restore it. The toolkit adds no env var, flag or file to
turn the guards off. Claude Code's own `disableAllHooks` setting would switch
off every harness hook (guards, post-write feedback, stop gate), so it is not
a recovery path: do not use it to get past a block. Restore the toolkit
instead, yourself, from a shell outside Claude:

1. Find the root of the toolkit checkout the project uses. The `harness` link
   points to its `lib/`:

   ```bash
   TOOLKIT="$(git -C "$(readlink -f <project>/harness)" rev-parse --show-toplevel)"
   ```

   Normally this is `~/.claude-codex-harness`; during development it may be a worktree.
2. Find the last good commit. There are no tags yet: take the previous merge
   commit on `main` (`git -C "$TOOLKIT" log --merges --oneline`).
3. Restore only the code paths. Leave `skills/`, `packs/` and `catalog/` alone,
   so no skill link dangles:

   ```bash
   git -C "$TOOLKIT" checkout <good> -- lib settings.json agents codex install.sh link-global.sh
   ```

   Or revert the bad merge (`git -C "$TOOLKIT" revert -m 1 <merge>`) and push it.
   That needs no local restore.
4. Check that both guards answer a harmless payload with exit 0:

   ```bash
   cd <project>
   echo '{"tool_input":{"command":"ls"}}' | bash harness/guard_bash.sh; echo $?
   echo "{\"tool_input\":{\"file_path\":\"$PWD/notes.md\"}}" | bash harness/guard_write.sh; echo $?
   ```

5. Once a fix is merged, return to normal. Drop the temporary restore first:
   it is staged in those paths and would block the pull.

   ```bash
   git -C "$TOOLKIT" checkout HEAD -- lib settings.json agents codex install.sh link-global.sh
   git -C "$TOOLKIT" status --porcelain   # must print nothing
   git -C "$TOOLKIT" pull
   ```

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `BLOCKED: there is no in_progress feature` on a `src/` write | no subtask started | let the leader start one (step 4) |
| `BLOCKED: '<path>' is NOT in the declared 'files:'` | the code needs a file the plan did not declare | the leader adds it to the spec, `make plan`, and the plan gate runs again |
| `[FAIL] … no longer matches its seal` | a sealed acceptance test or plan file changed | revert it, or re-run the gate that approved it |
| `make check` red right after the plan gate | acceptance tests are red until the code exists (unless under a confirmed marker) | expected; continue to step 5 |
| The close says `blocked -> see progress/current.md` | Codex could not finish the close | fix the cause recorded there, then re-run `doc <N.M.K>` |
| `<N.M.K> is already closed (status: done)` on `doc` | the close already ran (even if it looked interrupted) | do not close again; if only the PR is missing, `./harness/review_with_codex.sh pr <N.M>` |
| `<N.M.K> has no APPROVED code review` on `doc` | the close needs the code gate first | run `./harness/review_with_codex.sh code <N.M.K>` |
| `… is red after the close of <N.M.K>: nothing committed` | `make check` failed after Codex closed | fix it (log path in the message), then `./harness/review_with_codex.sh close <N.M.K>` |
| `the reference doc fails the doc gate` on `close` | the doc of the close lacks frontmatter keys or section anchors | `./harness/review_with_codex.sh doc <N.M.K>` (repair mode: only that doc changes) |
| `the changelog fragment of the close is missing or invalid` on `close` | an activated project's last close of a task or issue has no valid `changelog.d/<N.M \| ISS-n>.md` | `./harness/review_with_codex.sh doc <N.M.K>` (repair mode: only that fragment changes) |
| `the repair changed files outside the reference doc` | a doc repair touched another file | needs human: check the paths listed, restore them, remove `.git/harness-doc-repair-<N.M.K>.json` |
| `[git] no git identity … root commit skipped` at install | `user.name`/`user.email` unset | set them and run `install.sh` again |
