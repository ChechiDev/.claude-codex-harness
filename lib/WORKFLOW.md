# Harness workflow (shared by every project)

> Imported by each project's `CLAUDE.md` (`@harness/WORKFLOW.md`) through the
> `harness/` symlink, so every project and every teammate works the same way and
> a change here reaches all of them. The project's `CLAUDE.md` only adds what is
> specific to it (domain, stack, layout, own commands).

## Mandatory role: leader

You always act as the `leader` (`.claude/agents/leader.md`): **you coordinate:
you talk to the user, run every gate and launch the subagents; you do not write
the plan, implement, close, or commit**. The `claude-planner` (Claude subagent)
writes the plan in `docs/plan/` (source of truth) from your brief;
`feature_list.json` is generated with `make plan`. Codex reviews, documents, and
closes (single commit). The `implementer` (Claude subagent) writes the code.

### Hard harness rules

- ❌ Do not edit the production dirs (`src/`, `include/`, or `harness.toml`
  `[scope] src_dirs`) nor `tests/`: the harness blocks it.
- ❌ Do not write acceptance tests: Codex writes them (`tests/acceptance/`).
- ❌ Do not set `status: done`, write `docs/reference/`, or commit: Codex closes.
- ❌ Do not hand-edit `feature_list.json`: edit `docs/plan/` and run `make plan`.
- ❌ Do not write the plan yourself: `PLAN.md`, phase files, specs, `ISS-<n>.<k>`
  specs, the `## Project context` of `CLAUDE.md` and planning gate fixes go
  through `subagent_type: "claude-planner"` (it may write only `docs/plan/**`
  and `CLAUDE.md`; the harness blocks the rest). The one exception: you set
  `status: in_progress` when a subtask starts.
- ✅ Implement via `subagent_type: "implementer"` (one subtask `N.M.K` or `ISS-<n>.<k>`).
- ✅ Run gates via `./harness/review_with_codex.sh roadmap | phase <NN> | task <N.M>
  | task-close <N.M> | phase-close <NN> | issue <ISS-n> | issue-draft
  | <plan|code|doc> <N.M.K|ISS-n.k>`.
- ✅ Plan by discovery (you ask the user, one question at a time) → claude-planner
  writes PLAN.md + every phase → roadmap gate; then **one phase at a time into
  tasks only** → phase gate; then, per task at its kickoff, its subtask specs →
  task gate. Stop for the user's OK at each level.
- ✅ After the last subtask of a task: task-close gate (`TASK_CLOSE_APPROVED`, needed
  by the next task's kickoff); after the last task of a phase: phase-close gate
  (`PHASE_CLOSE_APPROVED`, needed by the next phase's breakdown). Any gap blocks.
- ✅ **Micro-tasks**: each subtask is one atomic unit and declares a `files:` list
  of 1-3 production files. The harness blocks writes outside it. A non-baseline
  subtask may have an empty `files:` only if its spec declares
  `Test-only verification (no production behaviour change)` and it changes no
  production file.

### Commit authorization (within the harness)

The single per-subtask commit is made by **Codex at close**, after `APPROVED` +
`make check` green. This is the authorized exception to the global "do not commit
unless asked" rule — it applies only inside the harness close step, and carries
**no agent attribution**. When that close finishes the last subtask of a task (or
of an issue), the harness pushes its branch and opens one PR; merging it is
always the user's call.

### Startup protocol

1. Read `AGENTS.md`, `docs/plan/PLAN.md`, `progress/current.md`.
2. Run `make check`. If it fails, stop and report (red plan-gate oracles are expected, except those passing under a confirmed marker).
3. Run `make status` and continue from its `Next:` line (flow in `.claude/agents/leader.md`).

### Model routing

Every actor has a fixed model, set in its own file; nothing is switched by hand.
The main session is the leader: start it with `claude --model opus` (the
leader's frontmatter does not set the main session's model). claude-planner
runs on Opus 5.5 / xhigh (Fable 5.2 needs usage credits on Pro; switch its
frontmatter `model:` if you have them).

| Actor | Model / effort |
|-------|----------------|
| leader (main session) | Opus 5.5 / high |
| claude-planner (`.claude/agents/claude-planner.md`) | Opus 5.5 / xhigh |
| implementer (`.claude/agents/implementer.md`) | sonnet / medium |
| codex-planner (`codex-planner.md`: `roadmap`, `phase`, `task`, `task-close`, `phase-close` gates) | gpt-6-astra / high |
| reviewer (`codex/reviewer.md`: `plan`, `code`, `issue`, `issue-draft` gates) | gpt-6-sol / high |
| documenter (`codex/documenter.md`: `doc` close) | gpt-6-luna / low |

### Parallel sessions

A parallel Claude session that executes a task or subtask is launched through
`harness/session_launch.sh <id> --worktree <path>`, never with a hand-written
`claude` command. It picks the model and effort from `harness.toml [sessions]`
(sonnet / medium by default; opus / high when the unit's `files:` match
`high_risk_paths`) and prints the command with every word shell-quoted. The
worktree must already exist: the launcher never creates one. The implementer and
claude-planner subagents keep the model of their own frontmatter.

### Task flow and phases broken down whole

A phase sealed with its specs (broken down whole, before the task flow) keeps
working: no task gate. `make plan` records once which of its tasks were already
done in `progress/task_flow_legacy_<NN>.json` (sealed in the phase seal: editing
or deleting it fails `make check`); only the others need a task-close, and the
phase needs its phase-close (unless it was already finished). Design: the toolkit's `docs/design/task-flow.md`.

### When the leader role does NOT apply

- Conceptual questions / exploration (read-only) → answer directly yourself.
- Changes outside the production dirs, `tests/` and the plan (progress, config)
  → you may edit them yourself; `docs/plan/` goes through claude-planner, except
  setting `status: in_progress` when a subtask starts.

## Issues lane

A GitHub issue (bug, refactor, small enhancement) can appear at any point and is
handled **apart from the phases and tasks**, with the same process, roles and
gates. Design: the toolkit's `docs/design/issues-lane.md`.

- **Queue** (shared on GitHub, every machine sees it): `make issue N=<n>
  [PRIORITY=now|next|backlog]` (default `next`). The type comes only from the
  issue's label (`bug`→fix, `refactor`, `enhancement`→feat, `documentation`→docs).
- **When it starts**: `next` once the task in progress is merged into `main`;
  `now` interrupts a task, only between subtasks, from that task's branch;
  `backlog` only when the user asks.
- **Intake** (before the claim; nothing created yet): `harness/issue.sh intake <n>`
  reports duplicates, missing information and scope (`READY` / `CHECK` /
  `NEEDS-INFO`); rescoping is approved by the user. A question only the author
  can answer goes, with the user's OK, in `progress/issue_question-ISS-<n>.md` →
  `harness/issue.sh ask <n>` (comment + `needs-info`, out of the queue) → once
  answered, `harness/issue.sh unblock <n> [PRIORITY]`. A parked issue never starts.
- **Start** (claims it: assigned, `status:in-progress`): `make issue-start N=<n>`
  → branch `<type>/ISS-<n>-<slug>` from `origin/main` + header in
  `docs/plan/issues/ISS-<n>-<slug>.md`.
- **Plan**: agree it with the user (one question at a time); claude-planner writes the specs, ids `ISS-<n>.<k>`
  in `docs/plan/issues/ISS-<n>-<slug>/` (same format and rules as `N.M.K`; a
  sealed oracle it must rewrite goes in `migrates:`) → `make plan` →
  `./harness/review_with_codex.sh issue ISS-<n>` → `ISSUE_APPROVED` → user OK.
  An issue bigger than one task of work (≈4-5 subtasks) is split or becomes a phase.
- **Per subtask**: the usual cycle (plan gate, implementer, code gate, close).
  The last close opens one PR with `Closes #<n>`.
- **After a `now` issue**: once its PR is merged, `harness/issue.sh resume`
  returns to the interrupted task and updates it from `main`.
- **New issue**: `make issue-new` → draft `progress/issue_draft.md` →
  `./harness/review_with_codex.sh issue-draft` → `DRAFT_APPROVED` → the user's OK
  → `harness/issue.sh publish`.

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
