# Task flow: per-task breakdown, task close and phase close (issue #62)

## Why

A phase used to be broken down whole: every task, subtask and spec before any
code existed. Specs of later tasks went stale, nothing ran between tasks, and
nothing checked that the subtasks together met the task or phase goal.

## Flow

```
phase breakdown (tasks only) → phase gate → user OK
  per task: kickoff → specs → task gate → user OK
            subtasks (plan → implementer → code → close)
            task-close gate (traceability matrix)
  phase-close gate (against PLAN.md) → next phase's breakdown
```

| Gate | Command | Verdict file | Token | Seal |
|------|---------|--------------|-------|------|
| phase | `review_with_codex.sh phase NN` | `progress/phase_review_NN.md` | `PHASE_APPROVED` | `progress/plan_phase_NN.sha256` (phase file) |
| task | `review_with_codex.sh task N.M` | `progress/task_review_N.M.md` | `TASK_APPROVED` | `progress/plan_task_N.M.sha256` (the task's specs) |
| task-close | `review_with_codex.sh task-close N.M` | `progress/task_close_N.M.md` | `TASK_CLOSE_APPROVED` | — |
| phase-close | `review_with_codex.sh phase-close NN` | `progress/phase_close_NN.md` | `PHASE_CLOSE_APPROVED` | — |

All four run on the Codex `codex-planner.md` role. Each verdict gets its receipt and
its rounds in `progress/review_history/` like every other gate.

## Rules (`make check`, `_harness.py validate`)

- A subtask of a task-flow phase starts only once its task is `TASK_APPROVED`.
- A task starts (any subtask in progress or done) only once the previous task of
  its phase is `TASK_CLOSE_APPROVED`.
- A phase is broken down (has tasks) only once every earlier phase is done and
  `PHASE_CLOSE_APPROVED`.
- A close approval must carry a traceability matrix (`| point | test | met/gap |`)
  with no `gap` row; `_verdict.py` rejects it otherwise (the gate fails, nothing
  recorded).
- The plan gate of a subtask re-seals its spec in its task seal.
- A subtask of the task (phase) with no `[N.M.K]`-tagged close commit is a gap:
  the prompt lists it, and `_verdict.py` rejects an approval while any remains.
- A `TASK_APPROVED` task without its seal fails `make check`, even with no spec
  left (a seal and its specs cannot be removed together).

## Compatibility: phases broken down whole

The mode of a phase is read from its seal, never from the clock:

| Phase seal | Mode | Rules |
|------------|------|-------|
| holds subtask specs | legacy | no task gate; task-close for the tasks not in its legacy record; phase-close at its end, unless every task is in the record |
| holds only the phase file | task flow | everything above |
| none, phase `PHASE_APPROVED` with specs and no task gate | unsealed (pre-#62) | the rules before #62, unchanged |
| none, otherwise | task flow | everything above |

For a legacy phase, the first `make plan` after this change writes
`progress/task_flow_legacy_NN.json` (`tasks_done`, the `HEAD` commit) and adds it
to the phase seal. It is written once: a seal that lists it is never rewritten,
so editing or deleting it fails `make check` (`changed/removed after phase NN was
approved`) and only a new phase gate approval re-seals it. It is Codex-owned for
the write and Bash guards. Until it exists a legacy phase keeps the pre-#62
rules (no task-close, no phase-close); `make plan` runs on every close.

A legacy phase already finished when it was detected (every task in its record)
needs no phase-close: requiring one would break projects that already broke
down the next phase. A legacy phase still in progress needs it at its end.

Other protections: a `TASK_APPROVED` task whose seal is missing fails `make
check`; a legacy record that is not in its phase seal fails it too; in a
task-flow phase, specs written for a task before the previous task is
`TASK_CLOSE_APPROVED` fail `make check` (a kickoff ahead of time).

## Cost

Measured by `tests/test_task_flow_62.py::test_task_close_reuses_a_matching_green_record`
(a `make check` that counts its runs):

- **`make check`**: a close gate runs right after the last close, whose recorded
  check (`.git/harness-check.ok`, issue #55) matches the tree, so the gate reuses
  it: **0 extra full checks**. Only when the tree changed since (or the record is
  missing) does the gate run one recorded check, as any other gate.
- **Codex input**: the prompt lists only the goal (phase file; plus `PLAN.md` for
  phase-close), each subtask's spec, `progress/impl_<id>.md` and
  `progress/review_<id>.md`, and one `git show` of the close commits (the single
  commit of each close, found by its `[N.M.K]` tag). For a task of 4-5 subtasks that is
  about 10-12 small files plus the task's own diff; never the whole repo.
- **Codex calls**: one codex-planner run per task (task gate) and per task close, one
  per phase close, plus one per rejected round. A phase of N tasks adds 2N + 1
  codex-planner runs to the flow before #62 (which had one phase gate).
