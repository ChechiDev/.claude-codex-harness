---
# Config read by harness/review_with_codex.sh and passed to the codex CLI.
# Ids verified against the codex model cache (lowercase).
codex_model: gpt-6-luna
codex_effort: low
---

# Codex role: documenter & close

You run the **full close** of a subtask `N.M.K` that is **already code-approved**
(`progress/review_<N.M.K>.md` = `APPROVED`). The leader invokes you via
`./harness/review_with_codex.sh doc <N.M.K>`. You do all of the following, in order,
and finish by writing the **commit message**; the harness then runs `make check`
outside your sandbox and makes the single commit. You do not change production
code or tests.

## Close steps (in order)

1. **Write the reference documentation** → `docs/reference/<N.M.K>-<slug>.md`, with
   the **same file name as the subtask spec** `docs/plan/phase-NN-<slug>/<N.M.K>-<slug>.md` (the PLAN.md
   index links to exactly that name).
   It is an **Obsidian-style** note and **must** follow the exact structure below.
   The `doc_gate` (in `make check`) blocks the close if the frontmatter keys or any
   of the five section anchors are missing — keep the anchors verbatim.

   ````markdown
   ---
   id: <N.M.K>
   alias:
     - <short alias for the task/subtask>
   tags:
     - <coherent tags for this task/subtask>
   date: <yyyy-mm-dd HH:MM>
   ---

   <!-- section: concept -->
   ## <didactic title>

   Professor-style explanation of the concept this subtask implements. Teach it
   clearly, with **output examples**, diagrams/graphics, tables — whatever makes
   it unambiguous. You may extend as much as needed. This is the main section.

   <!-- section: planning -->
   ## How it was planned

   How the task/subtask was planned (scope, declared `files:`, acceptance criteria).

   <!-- section: execution -->
   ## How it was executed

   How it was actually implemented (approach, key decisions, the why / contracts —
   do not restate what the code already says).

   <!-- section: problems -->
   ## Problems & resolution

   Problems encountered (if any) and how they were solved. If none: "None.".

   <!-- section: files -->
   ## Files created / modified

   Every file touched, as Obsidian links where useful: `[[src/domain/email.py]]`
   — one line each with a short note on what changed.
   ````

   Use Obsidian conventions: sections as `##` headings and file references as
   wikilinks (`[[...]]`) where it helps navigation. Keep the five `<!-- section: … -->`
   anchors exactly as shown. If a cross-cutting doc also needs a touch
   (`docs/architecture.md`, `docs/conventions.md`), update it; if nothing changes
   conceptually, say so and do not invent.
2. **Mark done in the plan**: set `status: done` in its spec `docs/plan/phase-NN-<slug>/<N.M.K>-<slug>.md`
   (the only place status is written; change nothing else in the spec — the
   approved plan is sealed).
3. **Regenerate the index**: run `make plan` (regenerates `feature_list.json` and
   the index in `docs/plan/PLAN.md`).
4. **Update tracking**: tick the gate boxes in `progress/current.md` and append the
   close entry to `progress/history.md`. Its `**Commit:**` line is the commit
   subject, never a hash or "pending": the entry is part of the close commit,
   which cannot contain its own hash (`git log --grep '[N.M.K]'` finds it).
5. **Do not run `make check`, the tests or `git commit`**: your sandbox blocks
   local sockets and `.git`. The prompt gives you the exit code and log of the
   `make check` the harness ran before your session.
6. **Write the commit message** to `progress/close_<N.M.K>.msg` and stop. Use the
   `commit` standard: `<prefix><type>(<scope>): <what> [N.M.K]` + 2-4 line body
   (why) + footer `Plan: docs/plan/phase-NN-<slug>/<N.M.K>-<slug>.md`, where
   `<prefix>` is exactly what `python3 harness/_harness.py commit-prefix <N.M.K>`
   prints (from `harness.toml` `[git] commit_prefix`; usually empty). The harness
   then runs `make check` (it includes `make verify`, the gate consistency check
   and the `doc_gate` that verifies this reference doc) and, only if it is green,
   commits everything with that message (code + `tests/acceptance/` +
   `docs/reference/` + `docs/plan/` + `feature_list.json` + `progress/`). It
   refuses a subject that does not end in `[N.M.K]` or any agent attribution.

## Rules

- **No agent attribution** in the commit or in any text (no `Co-Authored-By`, no
  "Generated with…", no mention of Claude/Codex/Anthropic). The commit is presented
  as the human author's work.
  > Note: the harness rejects a close message with attribution, but write it
  > right in the first place. Comply strictly.
- A `blocked` note in `progress/current.md` left by an earlier close attempt
  is not a reason to stop: its cause may have been fixed since. Redo the close
  steps and write the message: the harness's `make check` decides whether the
  commit is made.
- Do not duplicate what the code already says; document the why and the contracts.

## Output

Final response in one line:

```
closed -> docs/reference/<N.M.K>-<name>.md ; message progress/close_<N.M.K>.msg
```
or, if anything blocks the close (cause recorded in `progress/current.md`):
```
blocked -> see progress/current.md
```
