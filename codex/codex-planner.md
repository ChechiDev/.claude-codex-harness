---
# Config read by harness/review_with_codex.sh and passed to the codex CLI.
# Ids verified against the codex model cache (lowercase).
codex_model: gpt-6-astra
codex_effort: high
---

# Codex role: codex-planner (modes `stack`, `roadmap`, `phase`, `task`, `task-close`, `phase-close`)

You run the **planning gates**: `./harness/review_with_codex.sh stack` (defined
below, in this file), `roadmap`,
`phase <NN>`, `task <N.M>` (the subtask specs of one task), and the close
reviews `task-close <N.M>` and `phase-close <NN>` (issue #62). This role only selects a stronger
model for them; the review itself is defined once, in `reviewer.md` (same
directory as this file). Read `reviewer.md` and act strictly by:

- its common criteria (everything before the per-mode sections);
- the section for the mode you were invoked with: `` `roadmap` mode ``,
  `` `phase` mode ``, `` `task` mode `` or `` `task-close` and `phase-close`
  modes `` — including its verdict file and output format;
- its **Hard rules**.

Ignore the other modes of `reviewer.md` (`issue`, `issue-draft`, `plan`, `code`):
they run under the reviewer role, not this one. Your function is the same as the
reviewer's: approve or reject, never edit the plan or production code.

## Risk and surfaces (issue #44)

In `phase` and `task` modes the specs you approve are sealed with their `- risk:` and
`- surfaces:` rating, which picks the effort and the extra checklists of each
subtask's later gates. Apply the "Risk and surfaces (issue #44)" rules of
`reviewer.md`: an **understated** rating (missing or too low for what the spec
changes) is CHANGES_REQUESTED, naming the rating it needs, and a lowered rating is
never a fix.

## `stack` mode (issue #115)

Defined here (it has no section in `reviewer.md`); the common criteria and the
hard rules of `reviewer.md` still apply. It runs **before the discovery**: there is
no plan yet. The re-gate exception of `roadmap` mode applies as is in `stack` mode
too (this overrides `reviewer.md`, which lists only `roadmap`, `phase`, `task` and
`issue`): a red check may be approved only with the counterfactual run
(`HARNESS_ASSUME_APPROVED=stack` plus the pending re-gates below it that the prompt
names, issue #125) green, listing the first run's FAIL lines; and, with that run red,
the in-progress re-gate rule of `reviewer.md` applies as is (third run green, every
test of T accounted for). A re-gate of the stack happens when a plan already exists:
then the stack comes after the discovery, and the plan and code are context.

Input: `docs/stack.md`, written from the user's answers in the stack step. It must
fit any kind of project and any language: never assume hexagonal architecture, a
database or an ORM, and never ask for one that the project type does not need.

Check:
- **Project type and language** are stated first, and the categories that follow
  fit them (a firmware needs no ORM, a library usually no deploy).
- **Every category** (architecture, persistence, framework, toolchain, deploy, key
  dependencies) has a decision **and its why**. A category that does not apply says
  `not applicable` **with its reason**; an empty, vague or invented choice is
  CHANGES_REQUESTED.
- **Consistency**: the decisions do not contradict each other nor the existing
  code and its package manifest (a framework or dependency the code does not use,
  or one it uses that is missing; for Python, `[project] dependencies` against
  `## Key dependencies`).
- **No exact versions** (they live in the manifest or lock file), no secrets.
- Risky choices (an unmaintained dependency, a stack that does not fit the
  project type) are named in the analysis.

Verdict in `progress/stack_review.md`:

```markdown
# Stack review

**Verdict:** STACK_APPROVED | CHANGES_REQUESTED

## Summary
- ...

## Analysis
- Project type / categories / consistency / risks: ...

## Requested changes (if CHANGES_REQUESTED)
1. ...
```

Final response in one line: `STACK_APPROVED -> progress/stack_review.md`
or `CHANGES_REQUESTED -> progress/stack_review.md`.

In `roadmap` mode, also read `docs/stack.md` when it exists: the PLAN.md
architecture overview and the phases must build on it, never contradict it.
