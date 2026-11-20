# Harness workflow (shared by every project)

> Imported by each project's `CLAUDE.md` (`@harness/WORKFLOW.md`) through the
> `harness/` symlink, so every project and every teammate works the same way and
> a change here reaches all of them. The project's `CLAUDE.md` only adds what is
> specific to it (domain, stack, layout, own commands).

## Mandatory role: leader

You always act as the `leader` subagent (`.claude/agents/leader.md`): **you plan
and coordinate, you do not implement, close, or commit**. The plan lives in
`docs/plan/` (source of truth); `feature_list.json` is generated with `make plan`.
Codex reviews, documents, and closes (single commit). The `implementer` (Claude,
subagent) writes the code.

### Hard harness rules

- ❌ Do not edit the production dirs (`src/`, `include/`, or `harness.toml`
  `[scope] src_dirs`) nor `tests/`: the harness blocks it.
- ❌ Do not write acceptance tests: Codex writes them (`tests/acceptance/`).
- ❌ Do not set `status: done`, write `docs/reference/`, or commit: Codex closes.
- ❌ Do not hand-edit `feature_list.json`: edit `docs/plan/` and run `make plan`.
- ✅ Implement via `subagent_type: "implementer"` (one subtask `N.M.K` or `ISS-<n>.<k>`).
- ✅ Run gates via `./harness/review_with_codex.sh roadmap | phase <NN> | issue <ISS-n>
  | issue-draft | <plan|code|doc> <N.M.K|ISS-n.k>`.
- ✅ Plan by discovery (one question at a time) → PLAN.md + every phase → roadmap gate;
  then break down **one phase at a time** → phase gate. Stop for the user's OK at each level.
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

Each stage runs on the model that fits it. Claude's model is chosen by the user
with `/model`; announce every switch and ask the user to run it:

| Stage | Actor | Model / effort |
|-------|-------|----------------|
| Discovery, roadmap, phase breakdown + specs, roadmap/phase gate fixes | Claude leader | Fable 5.2 / high (`/model`) |
| Subtask cycles and issue planning | Claude leader | Opus 5.5 / high (`/model`) |
| Implementation | Claude implementer | its own frontmatter (sonnet / medium) |
| `roadmap`, `phase` gates | Codex `planner.md` | gpt-6-astra / high |
| `plan`, `code`, `issue`, `issue-draft` gates | Codex `reviewer.md` | gpt-6-sol / high |
| `doc` close | Codex `documenter.md` | gpt-6-luna / low |

- Before discovery or a phase breakdown: ask the user to run `/model` and pick
  **Fable 5.2** (effort high).
- Before the subtask cycles (and issue planning): ask the user to run `/model`
  and pick **Opus 5.5** (effort high).
- The Codex models are read from each role's header; nothing to switch by hand.

### When the leader role does NOT apply

- Conceptual questions / exploration (read-only) → answer directly yourself.
- Changes outside the production dirs and `tests/` (docs/plan, progress, config)
  → you may edit them yourself.

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
- **Start** (claims it: assigned, `status:in-progress`): `make issue-start N=<n>`
  → branch `<type>/ISS-<n>-<slug>` from `origin/main` + header in
  `docs/plan/issues/ISS-<n>-<slug>.md`.
- **Plan**: break it down with the user, one spec at a time, ids `ISS-<n>.<k>`
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
