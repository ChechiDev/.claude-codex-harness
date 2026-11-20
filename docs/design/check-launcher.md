# Trusted check launcher (issue #64, I4a — WARN rollout)

Gates, close and the PR barrier used to trust whatever the project's `make check`
did. The project Makefile decides whether it includes `harness/harness.mk`, GNU make
may read a `GNUmakefile` or lowercase `makefile` first, a target can be redefined
after the include, and inherited variables can change what runs. The launcher
`harness/run_check.sh` (source: `lib/run_check.sh`, reached through the `harness/`
symlink, so no install step) is now the only decision path.

## What it does

```
harness/run_check.sh [--decision] [--record] [--assume <target>] [--log <file>]
harness/run_check.sh --validate      # findings only; the check does not run
```

1. **Effective Makefile.** Warns when a `GNUmakefile` or a lowercase `makefile`
   shadows `Makefile`, when `make -pn` shows no `harness/harness.mk` in
   `MAKEFILE_LIST`, when a harness target (`plan status check issue issue-start
   issue-new`) has its recipe anywhere but `harness/harness.mk` (redefined after the
   include, or in another included file), and when `check` gained prerequisites.
2. **Command line.** The launcher takes no assignments, `-f` or targets: any other
   argument is ignored and warned about. It always runs plain `make check`.
3. **Environment.** On top of the `_clean_env.sh` drops (issues #27/#31) it drops
   interpreter and loader startup variables (`PYTHONPATH`, `PYTHONSTARTUP`,
   `PYTHONUSERBASE`, `PYTHONHOME`, `PYTHONINSPECT`, `PERL5LIB`, `RUBYOPT`,
   `NODE_OPTIONS`, `LD_PRELOAD`, `LD_LIBRARY_PATH`, `DYLD_*`…) and the lock bypass
   `HARNESS_CHECK_LOCK_HELD`, each with a warning, and sets `PYTHONNOUSERSITE=1`
   (no user site, so no `usercustomize` or user `.pth`). A `MAKEFLAGS`/`MAKEFILES`/
   `PYTEST_ADDOPTS`/`TESTS` value that could change what runs warns (bare flags a
   parent make sets do not). `sitecustomize.py`, `usercustomize.py` and any `.pth`
   outside a `site-packages/` directory in the tree warn.
4. **Allowlist.** Variables outside the allowlist (`PATH HOME USER … LC_* XDG_*
   HARNESS_* UV_* CI …`) still pass in this rollout and are listed **by name only**
   on one `note:` line (never values: no secret reaches a log).
5. **HARNESS_CHECK_CMD.** With `--decision` an override warns; without it the
   override runs quietly (test fixtures). It never writes the green record.
6. **Green record.** `--record` is the only writer of `.git/harness-check.ok`
   (issue #55): default command, green, same fingerprint before and after, no index
   flags. Any other outcome removes the record. `--assume` (counterfactual) never
   records.

Findings print as `[WARN] run_check: <what> — fix: <remedy>` to stderr and, with
`--log`, at the top of the log. **The exit code is always the check's own.**

## Wiring

`review_with_codex.sh`: `run_check` → `--decision --log`, `run_check_recorded`
(gates, close, PR barrier, the PR after a close) → `--decision --record --log`,
`run_check_assuming` → `--assume <target> --log`. The issue gate's third run
(`run_check_tolerating`) keeps its own `make check TESTS=…` because it must pass the
reduced test list; it never records.

`harness.mk`: a `make check` not started by the launcher prints that it is a
convenience run (the launcher sets `HARNESS_RUN_CHECK=1`). The variable only labels
output; it grants nothing.

## Rollout

- **0.5 (this issue):** WARN only. No template change, so no migration; an
  untouched `templates/` or `templates-c/` Makefile validates with zero warnings
  (tested), which is the C0 baseline.
- **I4a-fail (separate issue):** findings block, variables outside the allowlist
  are dropped, gated on C0 copies showing zero WARNs.

Out of scope here: fingerprinting executable `.pth` lines inside `site-packages`
(tool code), and making the allowlist configurable.
