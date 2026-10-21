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

The leader switches to the task branch `feat/<N.M>-<slug>` (created from `main`
for the first subtask of a task), sets the subtask to `in_progress`, runs
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
runs `make plan`, updates `progress/current.md` and `progress/history.md`, runs
`make check` and makes **one commit** on the task branch:

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

## 6. Next subtask, next phase

The leader shows `make status` and waits. When a task `N.M` is done, merge its
branch into `main` (or open a PR). When a phase is done, the leader breaks down
the next one (step 2).

## Checking that Codex really ran

Every gate is a `codex exec` session with the project as its working dir. Its
log is under `~/.codex/sessions/<yyyy>/<mm>/<dd>/rollout-*.jsonl`, and its start
time matches the verdict file's mtime:

```bash
ls -lt ~/.codex/sessions/$(date +%Y/%m/%d)/ | head
```

Claude cannot fake a verdict: writing any Codex-owned file in `progress/` is
blocked by `guard_write.sh` and `guard_bash.sh`.

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

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `BLOCKED: there is no in_progress feature` on a `src/` write | no subtask started | let the leader start one (step 3) |
| `BLOCKED: '<path>' is NOT in the declared 'files:'` | the code needs a file the plan did not declare | the leader adds it to the spec, `make plan`, and the plan gate runs again |
| `[FAIL] … no longer matches its seal` | a sealed acceptance test or plan file changed | revert it, or re-run the gate that approved it |
| `make check` red right after the plan gate | acceptance tests are red until the code exists | expected; continue to step 4 |
| The close says `blocked -> see progress/current.md` | `make check` was red at close | fix the cause recorded there, then re-run `doc <N.M.K>` |
| `[git] no git identity … root commit skipped` at install | `user.name`/`user.email` unset | set them and run `install.sh` again |
