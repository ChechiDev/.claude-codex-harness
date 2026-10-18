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
and finish with a **single commit**. You do not change production code or tests.

## Close steps (in order)

1. **Write the reference documentation** → `docs/reference/<N.M.K>-<name>.md`
   (`<name>` is the subtask slug; the id matches `docs/plan/` and `feature_list.json`).
   Document the **why** and the contracts, not what the code already says. Follow
   the `docs` skill standard (Diátaxis "reference"). If a cross-cutting doc also
   needs a touch (`docs/architecture.md`, `docs/conventions.md`), update it; if
   nothing changes conceptually, say so and do not invent.
2. **Mark done in the plan**: set `status: done` for subtask `N.M.K` in
   `docs/plan/phase-NN-<name>.md`.
3. **Regenerate the index**: run `make plan` (regenerates `feature_list.json`).
4. **Update tracking**: tick the gate boxes in `progress/current.md` and append the
   close entry to `progress/history.md`.
5. **Single commit** of everything (code + `tests/acceptance/` + `docs/reference/`
   + `docs/plan/` + `feature_list.json` + `progress/`). Use the `commit` standard:
   `<type>(<scope>): <what> [N.M.K]` + 2-4 line body (why) +
   footer `Plan: docs/plan/phase-NN-<name>.md#NMK`.

## Rules

- **No agent attribution** in the commit or in any text (no `Co-Authored-By`, no
  "Generated with…", no mention of Claude/Codex/Anthropic). The commit is presented
  as the human author's work.
  > Note: this commit is made by Codex outside Claude's Bash hooks, so the
  > no-attribution rule here is enforced by THIS instruction, not by a hook.
  > Comply strictly.
- Never commit with `make verify` red.
- Do not duplicate what the code already says; document the why and the contracts.

## Output

Final response in one line:

```
closed -> docs/reference/<N.M.K>-<name>.md ; commit <hash>
```
or, if anything blocks the close:
```
blocked -> see progress/review_<N.M.K>.md
```
