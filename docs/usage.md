# Usage

The full cycle on an installed project: what you tell the leader, what each
gate produces and what to check before approving the next step. Install first
(see the [README](../README.md#install)).

Open Claude Code at the project root. `CLAUDE.md` makes Claude act as the
**leader**; it stops after every gate and waits for your OK.

```bash
cd /path/to/project && claude
```

## 1. Plan the roadmap

Tell the leader what to build. It reads the repo first if there is code, then
asks one question at a time:

```text
Let's plan a library with a single function slugify(text: str) -> str.
```

It writes `docs/plan/PLAN.md`, every phase file (title + goal only) and the
`## Project context` section of `CLAUDE.md`, then runs the **roadmap gate**:

```bash
./harness/review_with_codex.sh roadmap
```

| Produces | Check before approving |
|----------|------------------------|
| `progress/roadmap_review.md` (`ROADMAP_APPROVED` / `CHANGES_REQUESTED`) | the verdict line and the analysis |
| `progress/plan_roadmap.sha256` (on approval) | `make check` green |

On `CHANGES_REQUESTED` the leader fixes the plan and re-runs the gate. From
here on the roadmap is sealed: editing it fails `make check` until the gate
approves the change again.

## 2. Break down the current phase

```text
OK, roadmap approved. Break down phase 01 and run the phase gate.
```

The leader writes the tasks into the phase file and one spec per subtask
(`docs/plan/phase-01-<slug>/<N.M.K>-<slug>.md`: `files:` with 1-3 paths,
acceptance criteria, description), then runs:

```bash
./harness/review_with_codex.sh phase 01
```

| Produces | Check before approving |
|----------|------------------------|
| `progress/phase_review_01.md` (`PHASE_APPROVED`) | every file the code needs is in `files:` (a missing one blocks the implementer later) |
| `progress/plan_phase_01.sha256` | acceptance criteria are concrete examples, not prose |

## 3. Plan gate for a subtask

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
| `tests/acceptance/test_1.1.1.py`, one test per acceptance criterion, **red** | the tests match the spec |
| `progress/plan_review_1.1.1.md` (`PLAN_APPROVED`) | the verdict line |
| `progress/acceptance_1.1.1.sha256` | `sha256sum -c progress/acceptance_1.1.1.sha256` |

`make check` is **red** from here until the code exists: the acceptance tests
fail by design. The Stop hook reports it when the session pauses; that is
expected, not a harness fault.

## 4. Implement and code gate

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

## 5. Close

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

The first close commit also carries every plan file that was never committed
(`docs/plan/`, verdicts, seals), since Claude cannot commit them earlier.

## 6. Task pull request

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

## 7. Next subtask, next phase

The leader shows `make status` and waits. When a task's PR is merged, the next
task starts from `main`. When a phase is done, the leader breaks down the next
one (step 2).

## 8. Issues lane (a GitHub issue at any point)

An issue never becomes a task of a phase: it has its own lane, with the same
gates (design: [design/issues-lane.md](design/issues-lane.md)).

```bash
make issue N=14                      # queue it (PRIORITY=now|next|backlog, default next)
make status                          # the queue (from GitHub) and Next:
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

## 9. Existing projects: migrate the copied harness files

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

## Checking that Codex really ran

Every gate is a `codex exec` session with the project as its working dir. Its
log is under `~/.codex/sessions/<yyyy>/<mm>/<dd>/rollout-*.jsonl`, and its start
time matches the verdict file's mtime:

```bash
ls -lt ~/.codex/sessions/$(date +%Y/%m/%d)/ | head
```

Claude cannot fake a verdict: writing any Codex-owned file in `progress/` is
blocked by `guard_write.sh` and `guard_bash.sh`.

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
| Reviewed by | phase, plan and code gates | the phase gate, against the code |
| Needs a code review and `docs/reference/` | yes (at the close) | no |
| Counts as done for phases and `depends` | when `done` | once its phase is `PHASE_APPROVED` |
| Acceptance tests | new, in `tests/acceptance/` | existing tests |

The phase gate requests changes for a baseline subtask the code does not
implement, or only partly implements; that one is `pending`. After the
approval, `status: baseline` is sealed: turning a subtask into baseline (or
back) fails `make check`.

## Manual Codex mode

With `HARNESS_CODEX_MODE=manual` (see [configuration](configuration.md)), each
gate prints the exact `codex exec …` command, the prompt and the verdict file
instead of running Codex. After an approved roadmap, phase or plan gate,
**you** seal what was approved; the Bash guard blocks Claude from doing it:

```bash
./harness/review_with_codex.sh seal roadmap
./harness/review_with_codex.sh seal phase 01
./harness/review_with_codex.sh seal 1.1.1
```

## Stop hook

`stop_gate.sh` runs `make check` before the session ends. A green result is
cached per working-tree fingerprint, so an unchanged tree does not re-run the
suite. If the stop was already blocked once and the check is still red, it
reports but does not block again, so the session cannot loop.

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
| `BLOCKED: there is no in_progress feature` on a `src/` write | no subtask started | let the leader start one (step 3) |
| `BLOCKED: '<path>' is NOT in the declared 'files:'` | the code needs a file the plan did not declare | the leader adds it to the spec, `make plan`, and the plan gate runs again |
| `[FAIL] … no longer matches its seal` | a sealed acceptance test or plan file changed | revert it, or re-run the gate that approved it |
| `make check` red right after the plan gate | acceptance tests are red until the code exists | expected; continue to step 4 |
| The close says `blocked -> see progress/current.md` | Codex could not finish the close | fix the cause recorded there, then re-run `doc <N.M.K>` |
| `<N.M.K> is already closed (status: done)` on `doc` | the close already ran (even if it looked interrupted) | do not close again; if only the PR is missing, `./harness/review_with_codex.sh pr <N.M>` |
| `<N.M.K> has no APPROVED code review` on `doc` | the close needs the code gate first | run `./harness/review_with_codex.sh code <N.M.K>` |
| `… is red after the close of <N.M.K>: nothing committed` | `make check` failed after Codex closed | fix it (log path in the message), then `./harness/review_with_codex.sh close <N.M.K>` |
| `[git] no git identity … root commit skipped` at install | `user.name`/`user.email` unset | set them and run `install.sh` again |
