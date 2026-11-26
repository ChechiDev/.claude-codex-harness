# Claude's Bash in Claude Code's native sandbox (I3c, WARN phase, opt-in)

The Bash guard (`guard_bash.sh`) reads the command text. It cannot see a
target that only exists at run time: `> "$(echo progress)/x"`, a Python
`open()`, a make recipe or a script that writes. I3c puts Claude's Bash inside
Claude Code's own sandbox (bubblewrap on Linux/WSL2). Write denials there are
enforced by the kernel, whatever the command looks like. It **complements** the
guard. It does not replace it: the guard, the Edit rules, the receipts and
`make check`'s integrity checks stay as they are.

Part 1 (this change) ships the policy **switched off** and a doctor check that
only warns. The toolkit `settings.json` reaches every installed project through
the `.claude/settings.json` link, so the sandbox is **opt-in per project**: off,
nothing changes (Bash writes and reaches the network as before, and the doctor
says nothing). `failIfUnavailable` stays `false`, so an opted-in machine without
bubblewrap still starts Claude Code, unsandboxed. Part 2 decides the FAIL
promotion (see the end).

## Switching it on in a project

The human writes the opt-in in the project's `.claude/settings.local.json`
(git-ignored, per machine; Claude cannot write it: the guard and the
`Edit(/.claude/**)` rule block it):

```json
{ "sandbox": { "enabled": true } }
```

Claude Code merges it over the linked toolkit settings, so the rest of the
policy below (denyWrite, allowWrite, excludedCommands, network,
`allowUnsandboxedCommands: false`) applies as shipped. Restart Claude Code,
then run `make env-check`: it reports `[OK] Bash sandbox: on …` or a `[WARN]`
with its fix. To switch it off again, delete the key (or the file).

- The opt-in goes **in the project's local settings or in managed settings**.
  `~/.claude/settings.json` does not work: project settings outrank user
  settings, and the toolkit's explicit `enabled: false` holds.
- A team that wants it on everywhere sets it in managed settings
  (`/etc/claude-code/managed-settings.json`), which outrank the project too.
- Other keys in `settings.local.json`'s `sandbox` block widen the policy
  (`excludedCommands`, `allowWrite`) or open the escape hatch
  (`allowUnsandboxedCommands: true`): the doctor warns about each.

## The policy (`settings.json`, reached through `.claude/settings.json`)

| Key | Value | Why |
|-----|-------|-----|
| `sandbox.enabled` | `false` | opt-in: a project's `settings.local.json` sets `true` (above) |
| `sandbox.autoAllowBashIfSandboxed` | `true` | sandboxed Bash does not prompt (deny and content `ask` rules still apply) |
| `sandbox.allowUnsandboxedCommands` | `false` | Claude Code ignores `dangerouslyDisableSandbox`: no retry outside |
| `sandbox.failIfUnavailable` | `false` | WARN phase: no bubblewrap means a warning, not a refusal to start |
| `filesystem.denyWrite` | `./progress ./harness ./.claude ./tests/acceptance ./harness.toml ./.harness` | the paths the guard protects, now also for code Claude runs |
| `filesystem.allowWrite` | `~/.cache/uv ~/.codex` | `uv run`/`uv sync` and `codex` keep their state (the project and `$TMPDIR` are writable by default) |
| `network.allowedDomains` | GitHub, PyPI, the OpenAI/ChatGPT hosts codex uses | everything else is denied |
| `excludedCommands` | the harness writers (below) and `tail`/`head` | they must write `progress/` and commit |

`./` paths are relative to the project root (the settings file is the
project's, even through the symlink). On Linux `denyWrite` takes only plain
paths or a trailing `/**`. Claude Code bind-mounts each denied path read-only,
and a path that does **not exist yet** gets a read-only `/dev/null` mounted on
it, so it cannot be created either. Inside the sandbox it shows as an empty
file, and Claude Code removes the mount point afterwards.

### Edit rules

Edit, Write, MultiEdit and NotebookEdit do not go through the sandbox. They
follow the permission rules, which are not part of the `sandbox` block: they
apply to **every** project, opted in or not. `permissions.deny` gains `Edit(/…)` rules for
`harness/**`, `.claude/**`, `tests/acceptance/**`, `harness.toml`,
`.harness/**`, `.git/harness-*` (and below: verdict slots, the attempt
ledger, supersede tickets; in a linked worktree the git dir lives elsewhere and
only the guard covers it) and every Codex-owned file under `progress/`
(verdicts, closes, seals, the PR narrative, review history, baselines). `progress/` as a whole is
not denied: Claude writes `current.md`, `impl_<N.M.K>.md` and `issue_draft.md`
itself. Claude Code ignores `Write(path)` rules ("only Edit(path) rules are"
matched), so there are none. Claude Code also folds Edit deny rules into the
sandbox's `denyWrite`.

### The harness writers run outside the sandbox

The gates and the close (`review_with_codex.sh`: Codex writes the verdict,
the script runs `make check` and commits), the issues lane (`issue.sh`,
`make issue*`), `make plan` (it writes `progress/task_flow_legacy_<NN>.json`
once, issue #62), the contract proposals (`_contract.py` / `_oracle.py
contract-propose`, which write `progress/*_contract.json`) and the plan gate's
re-plan retry (`_oracle.py replan-history`, issue #125, which appends to
`progress/review_history/<leaf>.md`) must write `progress/`. They are
`excludedCommands`:

```
./harness/review_with_codex.sh *   harness/review_with_codex.sh *
./harness/issue.sh *               harness/issue.sh *
make issue *   make issue-start *   make issue-new   make plan
python3 harness/_contract.py contract-propose [*]
python3 harness/_oracle.py contract-propose [*]
python3 harness/_oracle.py replan-history *
tail *         head *
```

The approvals and seals (`contract-approve`, `baseline-*`, `seal`) are not
excluded. They are human steps, and inside Claude's sandbox they fail
read-only, which is one more barrier.

### Adding a harness command

**Rule: a harness command that Claude runs and that writes `progress/` (or
another denied path) goes in `sandbox.excludedCommands`.** Without the entry
it fails `Read-only file system` as soon as the sandbox is on.
`tests/test_i3c_sandbox.py` enforces the rule. It scans `lib/` for scripts that
mention `progress` and can write a file. Each one must be classified either
in `RUN_BY_CLAUDE`, with the commands Claude types, each checked against the
exclusions, or in `NOT_IN_CLAUDES_SANDBOX`, with the reason (a hook, human-run,
only reached through an excluded gate). A new writer fails the test until it
is classified. The net is coarse, not a proof: it over-includes (a script that
only reads is classified as such) so as not to miss one, and a reviewer still
checks each new command against the rule. Keep each entry as narrow as the command: a whole interpreter or
`make *` would carry any write past the sandbox, and the test refuses both.

A Bash call leaves the sandbox only when **every** command in it is covered,
with no redirection other than fd duplication (`2>&1`), `cd`, substitution or
subshell. `tail`/`head` are listed so `review_with_codex.sh plan 1.1.1 2>&1 | tail -40`
still runs outside. On their own they can only print. Any other compound stays
sandboxed and fails `Read-only file system` when it reaches `progress/`.
Running the gate as a plain command fixes it. The close never needs
`git add -A` inside the sandbox, where bubblewrap's mount points make it fail
(`can only add regular files`).

`make check` is **not** excluded: it runs the tests Claude wrote, which is
exactly the code the sandbox has to contain.

## Verified (Claude Code 2.1.330 and 2.1.332, WSL2, bubblewrap 0.9.0)

With `claude -p` in throwaway projects whose `.claude/settings.json` is a
symlink to the toolkit file, as `install.sh` makes it.

Off (the toolkit default, 2.1.332): `SANDBOX_RUNTIME` is unset,
`> "$(echo progress)/probe.md"` and a Python `open()` write, and
`curl https://www.wikipedia.org` returns 200. `make env-check` and `make check`
print nothing about the sandbox.

Opted in with `{"sandbox": {"enabled": true}}` in `.claude/settings.local.json`:

- `d=progress; echo x > "$(echo $d)/probe.md"`, a Python `open()` under
  `progress/`, and `touch "$(echo harness)/x"` all fail with
  `Read-only file system`. A file outside the denied paths is written.
- `curl https://www.wikipedia.org` (not in `allowedDomains`) fails:
  `deny network-outbound www.wikipedia.org:443`.
- Write `progress/current.md`: allowed. Write `progress/review_9.9.9.md`,
  `harness/x` and an Edit of `.claude/settings.json`: `denied by your
  permission settings`.
- `make issue-new` and `make issue-new 2>&1 | tail -3` write
  `progress/issue_draft.md` (excluded). A pipe into another command stays
  sandboxed and fails.
- `uv run …` and `make check` (ruff, mypy, pytest with `tmp_path`, every
  invariant) are green inside the sandbox, `harness.toml` and
  `tests/acceptance/` absent included. A linked git worktree too.
- `SANDBOX_RUNTIME=1` is set inside the sandbox. The doctor uses it.
- Hooks run outside the sandbox (docs). `guard_bash.sh` still blocks first
  what it can see.

The e2e test (`tests/test_i3c_sandbox_e2e.py`) replays the policy with real
bubblewrap, read from `settings.json`, on an installed and opted-in project:
denied writes through expansions fail, `src/` is writable, `make check` is
green. It is skipped where bubblewrap is missing or cannot start. Without the
opt-in, `make env-check` and `make check` say nothing about the sandbox.

## What it does not cover

- **Projects that did not opt in**: none of this applies there. Their only
  barriers are the guard, the Edit rules (they apply to every project, opted in
  or not), the receipts and `make check`'s integrity checks.
- **The opt-in is a local file**: one machine of the team can have it and the
  next not; nothing in git records it. Managed settings are the shared form.
- **The doctor reads managed settings from the file only**
  (`/etc/claude-code/managed-settings.json`). An opt-in delivered by MDM or
  server-managed settings is not visible to it: Claude Code runs sandboxed and
  the doctor stays silent, missing bubblewrap included. Check with `/sandbox`
  in Claude Code there.
- **Hooks, MCP servers, the status line**: they run with full access (Claude
  Code's design). The harness hooks are trusted toolkit code.
- **Read**: the sandbox denies writes. Reads stay allowed.
- **Excluded commands run unsandboxed and run project code**. This is the
  known escape of part 1. Every gate runs `make check` first, which runs the
  tests Claude wrote. `make issue*` and `make plan` run recipes from the
  project Makefile (or a `GNUmakefile`/`makefile` that shadows it). The
  `contract-propose` commands parse it with `make -pn`, so its `$(shell …)` runs.
  **The guard does not protect the Makefile**: Claude may edit it. Once the I5
  baseline is sealed, an edit is a config-input WARN (`_guard.py`
  `_warn_selection_input`), and without the baseline it is not even that.
  `run_check.sh` warns about shadowing makefiles and redefined harness targets,
  but only inside the check. Code planted there runs with full access. The make targets stay excluded so the documented workflow
  keeps working in the WARN phase. Dropping them would not close the escape
  while the gate's check still runs outside. There the existing defenses still
  apply: the guard, verdict receipts, seals and `run_check.sh`'s Makefile
  checks. Exclusion is "a convenience, not a security boundary" (Claude Code
  docs). Part 2 closes this escape.
- **Project settings can relax it**. `.claude/settings.local.json` (or a
  project that drops the link) can set `enabled: false` or add
  `excludedCommands`/`allowWrite`. The doctor warns about it. It is strong only
  with **managed settings** (`/etc/claude-code/managed-settings.json` setting
  `allowUnsandboxedCommands: false`), which make the sandbox admin-required and
  ignore the repository's loosening entries. That is the team's decision.
- **Other agents**: Codex has its own sandbox (`docs/configuration.md`).
- **Platforms**: macOS uses Seatbelt and the harness does not verify it.
  Native Windows has no sandbox. Both get a permanent WARN.

## Doctor check (`make env-check`)

`lib/_sandbox_check.py`, added to `env-check` by `harness.mk` as a
prerequisite (the recipe stays the project's). It never fails: each finding is
a `[WARN]` with its fix, and the exit code is 0.

While the effective `sandbox.enabled` is not `true` (the toolkit default) it
prints **nothing**: `make check` runs `env-check` in every project. A settings
file it cannot parse counts as absent, as Claude Code skips it (the guard
already reports a broken `settings.local.json`). Once a project opts in, it
runs these checks, and an unreadable settings file is a `[WARN]`:

1. Platform: Linux/WSL2, or a permanent WARN.
2. `bwrap` and `socat` on PATH (fix: `sudo apt-get install bubblewrap socat`).
   `bwrap` must also start a namespace. Where unprivileged user namespaces are
   restricted (Ubuntu 24.04's AppArmor), it warns. Inside Claude Code's sandbox
   the probe is skipped.
3. Effective settings: user < project < local < managed. Arrays merge. A `false`
   `allowUnsandboxedCommands` in user or managed settings holds against a
   project `true`. `allowUnsandboxedCommands` must be `false`, and `denyWrite` must keep
   `./progress ./harness ./.claude ./tests/acceptance`, and `filesystem.disabled`
   must not be set. Any `excludedCommands` or `allowWrite` entry that a file
   adds beyond the toolkit's own also warns. An exclusion runs that command
   unsandboxed. A warning names the file that set the wrong value.

## WARN → FAIL (part 2)

0. Decide whether the sandbox becomes the default (`enabled: true` in the
   toolkit) or stays opt-in, once the points below are done.
1. Promote the doctor to FAIL in installed projects on Linux/WSL2 (missing
   tools, sandbox off, escape hatch open). macOS and Windows stay WARN.
2. Set `failIfUnavailable: true` once every team machine passes the doctor.
3. Make the harness writers fail early and clearly when they run sandboxed
   (`SANDBOX_RUNTIME=1`), instead of a late `Read-only file system` from Codex.
4. Close the escape through excluded commands. Run the project's `make check`
   inside a gate under the same bubblewrap policy (only the verdict and commit
   steps outside). Point the workflow at the scripts (`harness/issue.sh new`,
   `python3 harness/sync_plan.py`) instead of `make` targets. Then drop the
   `make` exclusions.
5. Recommend the managed-settings lock to teams that need a hard barrier.
