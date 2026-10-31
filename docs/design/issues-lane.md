# Design: the issues lane

Status: approved by the Codex design review (`DESIGN_APPROVED`, revision 12,
2026-10-30) and implemented on branch `feat/issues-lane`. Target: the global
harness (`~/.claude-codex-harness`), so every installed project gets it, for a
team working on several machines.

## 1. Problem

The harness has one way to change code: a subtask `N.M.K` inside the phase
being worked on. A GitHub issue (a bug, a refactor, a small enhancement) can
appear at any point of a project and often belongs to a phase that is already
`done`. Today it cannot be handled cleanly:

- Adding a task to a closed phase makes that phase not `done`, and
  `make check` fails because a later phase is already broken down
  (`_harness._progression_problems`, "one phase at a time").
- Planning it inside the current phase mixes unrelated work into a task and
  re-opens the current phase gate.
- Doing it outside a subtask is blocked by the guard (`files:` scope) and
  loses TDD, gates and traceability; migrating a sealed oracle of another
  subtask (`tests/acceptance/test_<N.M.K>.*`) has no sanctioned path at all.

Goal: issues are a **separate lane**, never mixed with phases or tasks, with
the same guarantees as planned work (plan gate, implementer, code gate,
Codex close, one commit per subtask, one PR).

## 2. Decisions (agreed with the user)

| # | Topic | Decision |
|---|-------|----------|
| 0 | Process | Same process, roles and models as planned work: leader (Claude) plans, Codex (`reviewer.md`) reviews every plan level and every subtask, implementer (Claude) codes, Codex (`documenter.md`) closes. The user approves each level. No new agents. |
| 1 | Timing | `make issue N=<n> PRIORITY=now\|next\|backlog`, default `next`, queues the issue. **`next`**: may start once no task is open, i.e. the branch of the last task worked on is merged into `main`. **`now`**: may interrupt a task, but only **between subtasks** (no leaf `in_progress`, clean working tree); work returns to the task branch **once the issue PR is merged**, after updating the task branch from `main`. **`backlog`**: queued and visible, started only on the user's explicit request. Never two subtasks `in_progress` in one working copy. |
| 2 | Subtask id | `ISS-<n>.<k>` (`ISS-14.1`). The issue itself is `ISS-<n>`. |
| 3 | Location | `docs/plan/issues/ISS-<n>-<slug>.md` (issue header) + `docs/plan/issues/ISS-<n>-<slug>/ISS-<n>.<k>-<slug>.md` (one spec per subtask), created on the issue branch when the issue starts. |
| 4 | Breakdown gate | Own issue gate: `review_with_codex.sh issue <n>` → `progress/issue_review_ISS-<n>.md`, verdict `ISSUE_APPROVED`, sealed. |
| 5 | Branch | `<type>/ISS-<n>-<slug>` from `main`; one PR per issue. |
| 6 | Type | Only from the GitHub label: `bug`→`fix`, `refactor`→`refactor`, `enhancement`→`feat`, `documentation`→`docs`. No known label → `make issue` fails (fix the label and re-run; no `TYPE=` override). |
| 7 | Templates | `lib/pr_template.md` (exists) and a new `lib/issue_template.md`, both global through the `harness/` symlink. The issue template has the same base as the PR one, in future tense, plus `Problem`. The close PR is built from the issue body. Nothing is installed in `.github/`. |
| 8 | Creation | `make issue N=<n>` imports an open issue (Codex reviews it at the issue gate). `make issue-new`: leader drafts `progress/issue_draft.md` → Codex draft review (`DRAFT_APPROVED`, sealed) → user OK → `harness/issue.sh publish`. |
| 9 | Sealed oracles | A spec declares `migrates:`; Codex migrates those files at its plan gate and the harness re-seals them (section 3.4); nothing else sealed may change. |
| 10 | Close | Last subtask closed → push + PR with `Closes #<n>`; merge is the user's. Push/`gh` failure stays a warning with a retry command, as for tasks. |
| 11 | Status | `make status` shows a separate "Issues" block (active issue from the plan, queue from GitHub) with its own counters; `feature_list.json` gets an `issues` list. |
| 12 | Rollout | This document (Codex design review) → implementation on a harness branch with tests → validation on codexion with issue #14. |
| 13 | Subtask rules | Same as planned work: `files:` 1-3, `Structural refactor (no behaviour change)` with regression oracles. |
| 14 | Size | An issue bigger than one task of work (≈4-5 subtasks) is rejected at the issue gate (`CHANGES_REQUESTED`, "too big for an issue"); the user splits it or plans it as a new phase (roadmap gate). No automatic promotion in v1. |
| 15 | Queue and claim | The queue lives on **GitHub**, as labels `priority:now`, `priority:next`, `priority:backlog`, so every machine and teammate sees the same queue. Queuing and starting are separate actions: `make issue` queues; `make issue-start` claims the issue (assigns it to the GitHub user running it, replaces `priority:*` with `status:in-progress`) and refuses if someone else already holds it. Only the harness sets or changes these labels. |
| 16 | Distribution | Everything shared is reached through symlinks, so a change reaches every project with no per-project step: Make targets in `lib/harness.mk` (project `Makefile`: `include harness/harness.mk` + its own targets), workflow rules in `lib/WORKFLOW.md` (project `CLAUDE.md`: `@harness/WORKFLOW.md` + project context; `AGENTS.md` points to it). Existing projects are migrated **once** by a script; new projects are installed that way. |

## 3. Model

### 3.1 On disk

```
docs/plan/issues/                                  (on the issue branch)
  ISS-14-parse-config-flags.md                    issue header (from `make issue` when it starts)
  ISS-14-parse-config-flags/
    ISS-14.1-parse-config-flags.md                subtask spec (same format as N.M.K)
    ISS-14.2-print-arg-errors.md
progress/issue_review_ISS-14.md                   ISSUE_APPROVED | CHANGES_REQUESTED (Codex)
progress/plan_issue_ISS-14.sha256                 seal of the approved breakdown
progress/plan_review_ISS-14.1.md, review_…, impl_…, acceptance_….sha256   as for N.M.K
tests/acceptance/test_ISS-14.1.<ext>              oracle (Codex)
docs/reference/ISS-14.1-<slug>.md                 reference doc (Codex, at close)
progress/pr_ISS-14.md                             PR narrative (Codex)
progress/issue_draft.md                           draft of a new issue (leader, make issue-new)
progress/issue_draft_review.md                    DRAFT_APPROVED | CHANGES_REQUESTED (Codex)
progress/issue_draft.sha256                       seal of the approved draft
```

The queue (issues not started yet) is **not** on disk: it is the set of open
GitHub issues with a `priority:*` label and no `status:in-progress` label
(decision 15). Started issues are open, assigned and labelled
`status:in-progress`; they are never part of the queue.

Issue header (`ISS-14-parse-config-flags.md`), written when the issue starts:

```markdown
# ISS-14 Replace parse_config bit mask with invalid flags
- issue: https://github.com/<owner>/<repo>/issues/14
- type: refactor            # from the label, frozen at start
- origin: 2.3               # phase/task the affected code belongs to (informative)
- priority: next            # copy of the label at start (informative; GitHub is the source)
- interrupts: feat/4.2-heap-storage   # only for `now` started over an open task (3.3)
- goal: <one sentence>

## Problem
## Approach
(imported from the issue body; the leader may refine it before the issue gate)
```

Subtask spec: identical to `N.M.K` (`status`, `layer`, `depends`, `files:` 1-3,
`acceptance:`, `## Description`), plus an optional list:

```markdown
- migrates:
  - tests/acceptance/test_2.3.3.c   # assertions on the mask become invalid[] checks
```

`depends` may name `N.M.K` subtasks (must be `done` on `main`) or earlier
`ISS-<n>.<k>` of the same issue.

Draft of a new issue (`progress/issue_draft.md`): a small front matter with the
exact title and labels, then the body from `lib/issue_template.md`:

```markdown
---
title: replace parse_config bit mask with invalid flags
labels: [refactor]
priority: next
---
## Problem
…
```

`labels` holds exactly one known type label and `priority` is `now`, `next` or
`backlog`. The front matter has no inline comments: every value (a title may
contain `#`) is published exactly as reviewed.

### 3.2 Ids and parsing

Ids are `N.M.K` or `ISS-<n>.<k>`. The existing `rsplit(".", 1)[0]` already maps
`ISS-14.1` → `ISS-14`, so the issue plays the role a task plays today (branch,
PR, commit prefix). That is not enough on its own: `sync_plan.py` parses
numeric file names, headings and sort keys only, and its metadata parser has
no list fields besides `files`/`acceptance`. So:

- One shared helper `_harness.parse_id(id)` → `("task", "2.3")` or
  `("issue", "ISS-14")`, used by every caller that today assumes a numeric task
  (`_pr._task_of`, `_harness.commit_prefix`, `_guard.check_branch`, sort keys).
- `sync_plan.py` gains an **issue schema**: header file and spec file name
  regexes, the header fields (`issue`, `type`, `origin`, `priority`, `goal`),
  `migrates:` as a list field, and validation errors of its own
  (unknown type, spec outside its issue folder). A header without specs is valid (the issue is being broken down: it shows in `make status` as "planning"); the issue gate requires at least one spec.
- Sorting: phases first (numeric), then issues by number.

### 3.3 Rules

| Rule | Planned work (today) | Issues lane |
|------|----------------------|-------------|
| Breakdown order | one phase at a time | **exempt**; issues never count in phase progression |
| Breakdown gate | `PHASE_APPROVED` | `ISSUE_APPROVED` (+ seal) |
| Start | phase approved + depends done | `make issue-start` first reconciles its own claim (5, step 0); otherwise it refuses unless: the issue is queued and unclaimed; `next` → no task open; `now` → no leaf `in_progress` on the current branch and a clean tree; `backlog` → only when the user asks for it explicitly. Subtasks then need `ISSUE_APPROVED` + depends done. |
| One `in_progress` | max 1 leaf | max 1 leaf **across both lanes** in a working copy, checked across every local branch (3.3), not only the branch-local `validate()` |
| Branch | `[git] branch` template | `<type>/ISS-<n>-<slug>`, checked by `check_branch` the same way |
| `files:` scope | guard + integrity | same, unchanged |
| Sealed oracles | byte-identical | byte-identical, except the files declared in `migrates:`, re-sealed at that leaf's `PLAN_APPROVED` (3.4) |
| Commit | `type(scope): what [N.M.K]` | `type(scope): what [ISS-14.1]`, type from the header |
| PR | at the last subtask of a task | at the last subtask of the issue, body contains `Closes #<n>` |

**"Task open".** `make issue-start` runs `git fetch origin` (explicit network
command) and works against `origin/main`, never a possibly stale local `main`.
Candidates are every task id of the plan (on `origin/main` and on the current
branch) whose `task_branch()` exists locally or on `origin`. Its tips are
**every existing ref** of that branch: the local one and `origin/<branch>`
(they may differ). A candidate is **closed** only when **each** of its tips
has positive evidence:

- `_harness.branch_merged(<tip>, "origin/main")` holds (merge commit,
  fast-forward or squash: the test `setup_pending()` already uses, factored
  into one shared helper), or
- a PR merged **into `main`** whose head commit equals that tip
  (`gh pr list --head <branch> --base main --state merged --json headRefOid`);
  a PR merged into another base, or one of an older incarnation of the branch
  name, is not evidence.

A task branch with no commits of its own (its tip is an ancestor of
`origin/main`) is not open.

Any other existing task branch is **open**. A task branch that exists nowhere
is not a candidate: either it was never started, or it was deleted, and a task
branch deleted without merging is **abandoned work by definition** (deleting it
is the user's decision; the harness does not resurrect it). This keeps the rule
free of any extra record that could drift.

**One `in_progress` across branches, without extra state.** `in_progress` is
only ever an uncommitted edit (the leader sets it; the close commit turns it
into `done`), so a lane switch requires a clean working tree. To also catch a
leaf left `in_progress` in a commit, `issue-start`, `resume` and
`subtask-ready` read the committed `feature_list.json` at the tip of every
local branch (`git show <branch>:feature_list.json`) and refuse if any leaf
other than the one being worked on is `in_progress`. No cache file is involved,
so nothing can be lost or point to the wrong path (this also works in git
worktrees, where `.git` is a file).

**Which task `now` interrupts.** `issue-start` with `now` must be run **from the
open task branch it interrupts** (clean tree, no leaf
`in_progress`); that branch, and only that one, is recorded. Run from any other
branch while a task is open (from `main`, from a different task branch, or
with several open task branches and the current one not among them), it
refuses and names the open branches. With no task open, `now` starts like
`next` and records nothing.

**Return after `now`.** `make issue-start` with `now` over an open task writes
`- interrupts: <task-branch>` in the issue header, so the record is versioned,
pushed with the issue branch and visible to every machine. `sync_plan.py`
parses it like the other header fields (a branch name matching
`RE_BRANCH`) into `feature_list.json` (`issues[].interrupts`). From any
branch, `make status` and `resume` read it from
`git show origin/main:feature_list.json` (issue merged) or from the issue
branch tip (not merged yet).
Once the issue PR is merged into `origin/main`, `make status` shows
`Next: resume task <N.M>`, and
the leader runs `harness/issue.sh resume`: `git fetch`, switch to the task
branch, `git merge origin/main` (merge, not rebase: the task branch may be
pushed and shared). Before the issue is merged, `resume` refuses. On a merge
conflict the script stops, leaves the merge in progress and reports it;
resolving it is the user's call. A successful resume needs no clean-up: the
`interrupts:` record is satisfied once the task branch contains the issue's
merge.

### 3.4 Oracle migration and seals

Today `check_seals()` verifies **every** acceptance manifest, so changing
`test_2.3.3.c` breaks `acceptance_2.3.3.sha256`. Migration therefore updates
every manifest that owns the file, atomically, and only through the gate:

0. **Frozen at the issue gate.** On `ISSUE_APPROVED` the harness writes
   `progress/issue_migrations_ISS-<n>.json` (leaf → list of migrated paths),
   an independent snapshot listed in the issue seal. The plan gate and `seal`
   compare each leaf's `migrates:` with that snapshot and refuse any
   difference (changing it means re-running the issue gate).
1. Plan gate of `ISS-14.1` starts: each `migrates:` path must be a canonical
   oracle path (`tests/acceptance/test_<N.M.K|ISS-n.k>.<ext>`), have **at least
   one** existing owner manifest listing it, and still match it; otherwise the
   gate stops (absent, ownerless or already modified oracle).
2. Codex reviews the spec, writes `test_ISS-14.1.*` and edits the declared files.
3. On `PLAN_APPROVED`, `seal ISS-14.1` checks that the set of changed sealed
   files equals the approved `migrates:` set (no more, no less), then:
   (a) writes the **complete** migration record
   `progress/oracle_migrations/ISS-14.1.json` (every path with its old and new
   hash) to a temp file and renames it into place, in one atomic step, before
   touching any manifest; (b) writes every new manifest
   (`acceptance_ISS-14.1.sha256` and each owner manifest with the migrated
   entries) the same way, temp file + rename. A failure partway is
   recoverable: `make check` reports the remaining mismatch, and re-running
   `seal ISS-14.1` completes it **only if** the record exists and is complete,
   its path set equals the issue snapshot, and every migrated file hashes to
   the record's new hash; it then writes only the outstanding manifests. No
   record, an incomplete one, a file edited after approval or a changed path
   list → refused, a new plan gate is needed.
4. `check_seals()` is unchanged in spirit: every manifest must match. A migrated
   file matches its updated owner manifest; an undeclared change still fails.
5. The guard only lets the seal step rewrite owner manifests for paths in the
   approved, issue-sealed `migrates:` of the leaf being sealed.

### 3.5 Draft seal (new issues)

`review_with_codex.sh issue-draft` → on `DRAFT_APPROVED` the harness writes
`progress/issue_draft.sha256` = hash of `progress/issue_draft.md` (front matter
+ body, byte for byte). `harness/issue.sh publish`:

**Seal trust model (known limitation).** The new seals (draft, issue seal and
its migration snapshot, migration record) use **exactly the same trust model as
every seal of the harness today**: `review_with_codex.sh` writes the seal right
after the approving Codex run, and the standalone `seal` step only checks that
the verdict file says approved. It does **not** prove that the sealed bytes are
the ones approved in that run, so a human could re-seal edited content under an
old verdict. Agents cannot: the guard blocks `seal` for them. Binding every seal
to the approving gate run is out of scope here and is tracked as a separate
harness issue for all seal types at once (section 10).

1. requires `DRAFT_APPROVED` in `progress/issue_draft_review.md` and a matching
   hash (any edit after approval → refuse, re-run the draft review);
2. checks template sections, exactly one known type label, a valid
   `priority:`, no agent attribution;
3. publishes **those bytes**: title, type label and `priority:<p>` label from
   the reviewed front matter, body = the rest. It takes no arguments, so
   nothing outside the approved draft reaches GitHub, except one hidden HTML
   comment appended to the body: an attempt token, persisted first in
   `git rev-parse --git-path harness-publish`. A retry after an attempt whose
   outcome was lost lists every issue this user created since the attempt started
   (all pages of the REST listing, no search-index lag), matches the token, and
   finishes with the issue it finds instead of creating a duplicate (final review,
   follow-up c).

On import (`make issue`), an issue with zero or more than one known type label
is rejected: the type must be unambiguous.

### 3.6 Checks and network

- `make check` and the stop hook never call `gh` or the network.
- After a plan gate, `make check` is **expected red** on the new oracle **and on
  the declared migrated oracles** until the implementation lands; any other red
  test is a blocker. For a structural refactor it may already be green. Green is
  required at the code gate and close. The stop hook keeps its current
  behaviour: it blocks the first stop with a red check and only reports a
  repeated red result (loop protection); the leader confirms that only the
  expected oracles are red.
- Network only in explicit commands: `make issue`, `make issue-start`, `issue.sh release`, `issue.sh resume`, `issue.sh publish`, the PR step
  and `make status` (queue block; offline → "issue queue unavailable", the rest
  of the status still prints).

## 4. Flow

```
new issue: [leader] progress/issue_draft.md (lib/issue_template.md)
  → review_with_codex.sh issue-draft → DRAFT_APPROVED (sealed) → user OK
  → harness/issue.sh publish             (gh issue create, type + priority labels)
existing issue: opened on GitHub by the user or a teammate
  → make issue N=14 [PRIORITY=now|next|backlog]   gh: validate, set priority label (queued)
  → make issue-start N=14   allowed? (3.3; `now`: run from the task branch it interrupts)
                            → claim tag, assign @me, status:in-progress,
                            branch <type>/ISS-14-<slug> from origin/main + header
  → [leader] break down ISS-14.1…, one spec at a time with the user
  → make plan + review_with_codex.sh issue 14 → ISSUE_APPROVED (sealed) → user OK
  → per subtask ISS-14.k: in_progress → plan gate (Codex writes test_ISS-14.k,
    migrates declared oracles; harness re-seals) → make check (expected red
    on the new oracle) → implementer → code gate → close (one commit [ISS-14.k])
  → last subtask: push + PR "Closes #14" → user merges → GitHub closes the issue
  → if it was `now`: once merged, issue.sh resume → task branch + merge origin/main
    → continue the task
```

## 5. Commands

| Command | Does |
|---------|------|
| `make issue N=14 [PRIORITY=…]` | Queue: `gh issue view 14 --json` → open? exactly one known type label? not claimed? → sets `priority:<p>` (default `next`), replacing any other `priority:*`. Re-running it changes the priority. Never creates a branch. |
| `make issue-start N=14` | Start: `git fetch origin`; checks 3.3 (queued, unclaimed, task open / `backlog` rules; `now` only from the open task branch it interrupts (3.3); no other `in_progress` leaf on any local branch); **claims atomically** (below); creates the branch from `origin/main` and the header (with `interrupts:` for `now` over an open task). |
| `harness/issue.sh release 14` | Gives a claimed issue back before any commit (claim token required). The only change allowed is the header exactly as `issue-start` wrote it (compared with the hash recorded in the claim); an edited header, any spec or any other change, staged or not, makes it refuse, report what would be lost and leave everything in place. Otherwise it follows the release order of the atomic claim below (header and branch, labels and assignee, remote tag last). |
| `make issue-new` | Leader drafts `progress/issue_draft.md` from `harness/issue_template.md`. |
| `review_with_codex.sh issue-draft` | Codex reviews the draft → `DRAFT_APPROVED` \| `CHANGES_REQUESTED`; on approval the harness seals it. |
| `harness/issue.sh publish` | After the user's OK, section 3.5 (no arguments). |
| `review_with_codex.sh issue 14` | Issue gate (reviewer role, issue checklist, size threshold). |
| `review_with_codex.sh plan\|code\|doc ISS-14.1` | Same entry points; accept the new id. |
| `review_with_codex.sh pr ISS-14` | Retry the issue PR. |
| `harness/issue.sh resume` | Back to the interrupted task once the `now` issue is merged (3.3). |

**Atomic claim.** Reading "unassigned" on GitHub and then assigning is a race,
and pushing a branch at the same `origin/main` commit is not exclusive either
(the second push is "up to date"). The exclusive step is a **unique claim
tag**: `issue-start` creates an annotated tag `claim/ISS-14` whose message is a
fresh claimant token (`<gh user> <host> <uuid4>`), so every claimant creates a
different tag object even on the same commit, and pushes it **without force**
(`git push origin refs/tags/claim/ISS-14`). The server creates it only if it
does not exist; any other claimant is rejected ("already exists") and
`issue-start` refuses, naming the token's user.

Order of steps, so every partial failure is recoverable:

0. **Reconcile first.** Every `issue-start`, `release` and close run begins by
   reading the local claim file and the remote tag, before any other check.
   Remote tag with our token → we hold the claim: `issue-start` only resumes
   the missing steps (the "queued and unclaimed" precheck does not apply to our
   own claim); `release` continues its cleanup. Foreign token → refuse (and
   drop a stale local file). No tag and a local file → if the file records a
   release in progress, finish its local cleanup (local tag, then the file);
   otherwise it is a claim that never landed: drop the file and start over.
   `issue-start` while our own release is in progress → refuse ("finish
   `release` first").
1. Generate the token and **persist first** the local claim file
   `git rev-parse --git-path harness-claim-ISS-14`, a small JSON with the
   token, the previous `priority:*` label, the branch `issue-start` was run
   from, and the steps already done. Each step below updates it after it
   succeeds.
2. Create and push the tag. Rejected → delete the local file, refuse.
   Interrupted (unknown outcome) → step 0 decides on the next run.
3. Assign `@me`, swap the labels, create the branch and the header. Any failure
   → the claim is still held; the next run resumes from the recorded steps.

`release` requires our token (step 0) and undoes in reverse order, recording
each step in the claim file: header and branch (back to the recorded start
branch), then labels (restoring the recorded `priority:*`) and assignee, then
**the remote tag last**, and finally the local tag and claim file. Interrupted
at any point → the remote tag still holds the claim and the claim file still
has the start branch and priority, so re-running `release` finishes.

**Claim cleanup at close.** After the issue PR is opened, the close deletes the
remote tag, then the local tag and claim file. A failed deletion is a warning
with a retry command (`review_with_codex.sh pr ISS-14` re-runs it), like a
failed push; a leftover tag only blocks re-claiming an issue that is already
closed.

Make variables (`make issue N=14`) because `make issue 14` would treat `14` as
a target. All targets live in `lib/harness.mk` (decision 16).

## 6. Templates

`lib/issue_template.md`, derived from `lib/pr_template.md`:

```
## Problem          (what is wrong / why; steps to reproduce for a bug)
## Overview         (what will change)
## Approach
## Decisions
## Open questions
## Follow-ups
## Subtasks         (planned when the issue starts)
## Expected files
## Documentation    (specs/oracles affected)
## Branch and merge
## Where to start reviewing
```

At close, Codex writes `progress/pr_ISS-<n>.md` starting from the issue body:
same sections, past tense, `Expected files` → actual `Files`, plus `Closes #<n>`.
An issue opened by hand on the GitHub web form has no template; it is still
imported and Codex judges its content at the issue gate.

## 7. Distribution (decision 16)

| Shared content | Today | After |
|----------------|-------|-------|
| Harness Make targets (`plan`, `status`, `check`, `issue`, …) | copied into each `Makefile` | `lib/harness.mk`; project `Makefile` = `include harness/harness.mk` + project targets (`build`, `lint`, `test`, …) that `harness.mk` calls |
| Workflow rules for Claude | copied `CLAUDE.md` | `lib/WORKFLOW.md`; project `CLAUDE.md` = `@harness/WORKFLOW.md` + project context |
| Map for Codex | copied `AGENTS.md` | project `AGENTS.md` keeps the project map and points to `harness/WORKFLOW.md` for the shared flow |
| PR / issue templates | `lib/pr_template.md` | `lib/pr_template.md`, `lib/issue_template.md` |

**Target ownership.** `harness.mk` owns the harness targets (`plan`, `status`,
`check`, `issue`, `issue-start`, `issue-new`); the project
`Makefile` owns the toolchain targets that `harness.mk` calls (`verify`,
`lint`, `type`, `test`, `build`, `clean`, plus any extras such as `memcheck`).
A target is defined in exactly one of the two files, so there is never a
duplicate recipe. The guard lives in the project `Makefile`, around the
include, because a missing included file stops make while parsing:

```make
ifneq ($(wildcard harness/harness.mk),)
include harness/harness.mk
else
.PHONY: plan status check issue issue-start issue-new
plan status check issue issue-start issue-new:
	@echo "harness/ is missing on this machine: run install.sh <project> first" >&2; exit 1
endif
```

The `harness/` symlink is per machine (created by `install.sh`), so a CI checkout
or a new machine does not have it: there only the harness targets fail with that
message, and the project's own targets (`verify`, `memcheck`…) still run. A first
version stopped every target with `$(error …)` and broke a CI that runs
`make verify`; `--migrate` replaces that block.

**Migration of existing projects, once:** `install.sh --migrate <project>`
(loopable over many projects):

1. Lists the harness-owned targets defined in the project `Makefile`.
2. Each one byte-identical to the shipped template version → removed.
3. Any one that differs (a project customised `check`, for example) → the
   migration **stops for that project** without changing anything and reports
   the collisions; the user moves the custom part into a project-owned target
   and re-runs. No `include` is ever added next to a colliding target.
4. Adds `include harness/harness.mk` and `@harness/WORKFLOW.md`, and the
   `AGENTS.md` pointer; never touches other project content.
5. Verifies the result: `make -pn` shows every harness-owned target coming from
   `harness/harness.mk` and every toolchain target from the project `Makefile`;
   otherwise it restores the backup and fails.

It is idempotent. New projects are installed in the new shape.

## 8. Impact on the harness (by file)

| File | Change |
|------|--------|
| `lib/sync_plan.py` | Issue schema (3.2); `feature_list.json` gains `issues: [{id, title, type, issue, priority, interrupts, status, subtasks:[…]}]`; PLAN.md index: separate "Issues" block. |
| `lib/_harness.py` | `parse_id`; `leaves()` includes issue leaves; `_progression_problems` ignores issues; `issue-ready`; "task open" (3.3); `issue_branch()`; `validate()` one `in_progress` across lanes. |
| `lib/_plan_seal.py` | Seal per issue (`plan_issue_ISS-<n>.sha256`), same status-stripping as phases. |
| `lib/_guard.py` | `RE_CODEX_OWNED` and the shell-write protected paths add `issue_review_*`, `issue_draft_review.md`, `issue_draft.sha256`, `oracle_migrations/*.json`, `issue_migrations_*.json`; `check_branch` uses the issue branch; `commit_message()` accepts `[ISS-<n>.<k>]` and the issue `Plan:` footer; seal step for `migrates:` (3.4). |
| `lib/review_with_codex.sh` | Modes `issue`, `issue-draft`; `plan/code/doc/pr` accept `ISS-` ids; pre-check of `migrates:` paths at the plan gate. |
| `lib/_pr.py` | Issue PR from the issue body, `Closes #<n>`, branch/type from the header, "Documentation" links the issue folder; same push/`gh` warning + retry. |
| `lib/plan_status.py`, `lib/_status.py` | "Issues" block (active from the plan, queue from `gh`, offline-safe), own counters, `Next:` for the issues lane and `resume task`. |
| `lib/issue.sh` (new) | `make issue`, `make issue-start`, `make issue-new`, `resume` (`gh`, labels, claim, header, branch, merge). |
| `lib/issue_template.md` (new) | Issue template (section 6). |
| `lib/harness.mk` (new) | Shared Make targets, including `issue` / `issue-new`. |
| `lib/WORKFLOW.md` (new) | Shared workflow rules, including the issues lane, imported by `CLAUDE.md`. |
| `codex/reviewer.md` | Draft review checklist; issue gate checklist (whole issue covered, size threshold ≈4-5 subtasks, micro-tasks, `migrates:` justified, structural-refactor claim); plan gate: migrate declared oracles only. |
| `codex/documenter.md` | Close for `ISS-` ids; issue PR narrative from the issue body. |
| `agents/leader.md`, `agents/implementer.md` | Issues lane flow, timing rule, resume after `now`. |
| `install.sh` | New project shape (decision 16) and `--migrate`. |
| `templates*/Makefile`, `templates*/CLAUDE.md`, `templates*/AGENTS.md`, `templates*/progress/current.md`, `templates*/docs/plan/PLAN.md` | New shape; mention the issues lane and `ISS-` ids where they describe only numeric subtasks. |
| `templates*/CHECKPOINTS.md`, `templates*/docs/verification.md`, `templates*/docs/reference/README.md`, `templates*/progress/history.md` | Accept `ISS-<n>.<k>` wherever they describe `N.M.K` ids, paths and examples. |
| `docs/architecture.md`, `docs/usage.md`, `docs/configuration.md`, `README.md` | Lane, on-disk contract, issue branch naming next to the task branch template, commands, `gh` usage and labels; new distribution (`harness.mk`, `WORKFLOW.md`, `--migrate`, per-machine symlink). |
| `skills/implement/SKILL.md`, `skills/github/branch/SKILL.md`, `skills/github/commit/SKILL.md` | Accept `ISS-` ids, issue branch naming and the issue `Plan:` footer (`docs/plan/issues/…`). |
| `lib/_guard.py` (`setup_pending`) | Merge detection factored into `_harness.branch_merged()` and reused for "task open". |
| `tests/` | `test_sync_plan` (issue schema, sort, `migrates:`), `test_plan_rules` (exemption, task open, one in_progress), `test_plan_seal` (issue seal), `test_integrity` (migration re-seal, undeclared change fails), `test_guard_write` / `test_guard_bash` (new protected files), `test_review_with_codex` (modes, ids, migrates pre-check), `test_pr` (Closes #n, retry), `test_plan_status` (block, offline), `test_stop_gate` / `test_makefile_check` (red interval, `harness.mk` include), `test_install` (new shape, `--migrate` idempotent and non-destructive), `test_agent_contracts` (updated roles), new `test_issue.py` (`gh` mocked: labels incl. zero/multiple type labels, closed issue, claim conflict, task open incl. squash merge, priority now/next/backlog, resume before/after merge + conflict, draft seal incl. priority), `test_install` / `test_makefile_check` missing `harness/` error at parse time; `test_install` `--migrate` collisions and `make -pn` verification; deleted unmerged task branch (abandoned), committed `in_progress` on another local branch, run from a git worktree, migration record missing/incomplete on retry, two claimants on the same base commit (claim tag); header-only issue in `make plan`/`make status` and rejected at the issue gate; network used by `issue-start`/`release`/`resume` and never by `make check` or the stop hook; `now` from `main` / from a non-interrupted branch / with several open task branches refuses, and from the open task branch records it in `interrupts:`; `release` after the header or specs were edited refuses and keeps the work; claim interrupted after the token is persisted / after the tag push / during GitHub updates, and `release` interrupted before the tag deletion, all recoverable by re-running; `issue-start` retry after `status:in-progress` was set; `release` retry after header and branch deletion; claim-tag cleanup after the PR opens, failed and retried (`test_review_with_codex`); interruption after the remote tag deletion, then local cleanup and a new start; merged PR of an older incarnation of a task branch; current-tip PR merged into a non-`main` base; divergent local and remote task tips; empty task branch; `test_git_config` issue branch enforcement and issue commit prefix. |

## 9. Implementation safety

`harness/` and `.claude/*` in every project are symlinks into the toolkit
checkout (the exact links are created by `install.sh`): switching that checkout
to a branch changes the live harness of all projects at once. So the work
happens in a **separate git worktree** (`~/.claude-codex-harness-wt/issues-lane`,
branch `feat/issues-lane`); the live checkout stays on `main` until the user
approves the merge. The toolkit's own `pytest` suite runs in the worktree.
Commit/push of the toolkit only with the user's OK.

Validation on codexion (decision 12): after merge, `install.sh --migrate`
on codexion, label #14 `refactor`, `make issue N=14`, break down ISS-14.1/ISS-14.2
with the user, issue gate, normal cycle; `test_2.3.3.c` migrates at the plan
gate through `migrates:`.

## 10. Open details and follow-ups

No open decisions. Follow-up, agreed with the user and the Codex reviewer:

- **Harden seals** (separate harness issue, after this lane exists): bind every
  seal (acceptance, roadmap, phase, issue, draft, migration) to the approving
  gate run, so a stale verdict can never authorise sealing different bytes, in
  automatic and manual mode. Until then, the limitation in section 3.5 applies
  to all seals equally.
