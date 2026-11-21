# Oracle execution integrity (issue #65, I4b) — WARN rollout

A sealed oracle (`tests/acceptance/`, sealed in `progress/acceptance_<leaf>.sha256`)
proves something only if the normal check **selects and executes** it. The file hash
check (`_guard.py integrity`) does not show that. I4b adds that evidence. It lives in
`lib/_oracle.py`.

**WARN semantics (plan 0.5).** Every finding is a `[WARN]` with a remedy and never
changes an exit code. Blocking is the separate I4-fail issue. Without a sealed I4
baseline nothing is active: the launcher prints a one-line note.

## 1. Execution evidence (`_oracle.py evidence`)

Once the baseline is sealed (before that the check runs exactly as it did),
`harness/run_check.sh` sets two variables after the `CLEAN_ENV` drops, so an inherited
`PYTEST_ADDOPTS` never reaches the check:

- `PYTEST_ADDOPTS=--junitxml=<tmp, shell-quoted> -o junit_family=xunit1` gives
  node-level results with each test's `file`. That path is relative to pytest's rootdir
  and is matched by path suffix, so the rootdir is not forced.
- `HARNESS_ORACLE_CLOG=<tmp>` is where the C template's `test` target writes one line
  per test, `<source>\t<sha256 of the source>\t<exit>`. Each test is rebuilt from its
  source in the same run.

After the check, `evidence` warns, for every sealed oracle of a `done`, `in_progress`
or `baseline` leaf, when:

| Case | WARN |
|---|---|
| No node ran: collection or setup errors and skips do not count (deselected by conftest, `-k`/`-m` in addopts, a plugin, another Makefile, exit 5, an empty C `TESTS`) | `not executed` |
| A `done` leaf has a skipped or xfailed node | `skipped or xfailed` |
| A `done` leaf has a failing node, or a C exit ≠ 0 | `did not pass` |
| A C source hash differs from the one that ran | `stale` |
| No junit or run log, or no adapter for the file type | `cannot establish` (fail closed) |
| A sealed oracle file is missing | `sealed but missing` |

**Diagnostic run.** `HARNESS_EXPECT_RED` (the issue re-gate's labelled third run) counts
only in the gate's counterfactual launcher run (`--assume`), and then only that set is
omitted. An inherited value anywhere else is ignored and warns. The launcher never
writes a green record in that run.

## 2. Selection inputs (`_oracle.py inventory`)

The inventory maps each input to its digest:

- `Makefile`, `GNUmakefile` and `makefile`, plus every makefile make reads (its
  `MAKEFILE_LIST`, so `include $(VAR)` is resolved). Any include word it cannot
  resolve is recorded as `make-include:<word> = UNRESOLVED`. `harness/harness.mk`
  belongs to the toolkit;
- every `conftest.py`;
- `pytest.ini`;
- `pyproject.toml[tool.pytest]`, `setup.cfg[tool:pytest]` and `tox.ini[pytest]`;
- `package.json` test scripts;
- `plugin:<name>`: each `pytest11` entry point of the project `.venv`, read statically
  (no code runs). It is bound to `dist==version`, the module path and its content hash.
  A plugin with no module file is `UNRESOLVED` and warns.
- `plugin-module:<name>`: each module named by `pytest_plugins = [...]` or `-p name` in
  a conftest, the config files or a makefile. It is resolved in the project root,
  `src/` or the venv and bound to its path and content hash; otherwise `UNRESOLVED`.

Any input added, removed or changed relative to the sealed baseline warns. The write
guard adds a warning (`systemMessage` and `additionalContext`) when Claude edits one of
these files, or a file the sealed inventory resolved (an included makefile, a plugin
module), while the baseline is active.

## 3. Red evidence (plan gate → code gate)

- Every plan gate run first calls `red-drop`. That drops the old record and ends a
  baseline exemption.
- After an approval that sealed the oracle, it calls
  `red-record <leaf> <check rc>`. That runs the leaf's oracles alone and writes
  `.git/harness-oracle/red_<leaf>.json` with:
  - the node ids collected and the ones failing (a collection error is not a
    collected node);
  - the check rc;
  - the oracle hashes and the selection digest;
  - a fresh `run_id`. It is never reused.
- It warns when the new oracle does not fail.
- The code gate calls `red-check <leaf>`. It warns when:
  - there is no record;
  - the record does not prove the red run (an unsupported adapter, nothing collected,
    or not every collected node failing);
  - the oracle or the selection inputs moved since the record.

  Declared `files:` may change.

## 4. Contract flow (approved change of selection inputs)

1. `_oracle.py contract-propose` writes `progress/oracle_contract.json`. Its `diff` is
   the sha256 of `{from: baseline selection, to: current inventory}`.
2. `review_with_codex.sh contract` makes Codex review the proposal and the git diff in
   read-only mode. The verdict goes to `progress/oracle_contract_review.md` with the
   line `diff: <hash>`.
3. The human OKs it with `_oracle.py contract-approve <hash>`. That reseals only when
   these are all the same hash: the proposal's `diff`, the hash recomputed from the
   proposal's own `from`/`to`, the verdict and the given hash. On top of that, `from`
   must be the sealed baseline and `to` the current inventory. What gets sealed is the
   recomputed current inventory. A change after the proposal invalidates it.

A refusal (or a missing verdict) is a `[WARN]` with its remedy, exits 0 and reseals
nothing.

## 5. The I4 baseline

`progress/oracle_baseline.json` is separate from the M0b legacy inventory. It holds:

- the commit it was taken from;
- the selection inventory;
- the oracles of done and baseline leaves;
- `exempt_in_progress`: the `in_progress` leaves without a red record. They stay exempt
  until their next plan gate.

The steps:

1. The human runs `_oracle.py baseline-create --from <pre-rule commit>`. It warns for
   every input that differs from that commit.
2. Codex reviews it.
3. `_oracle.py baseline-seal` writes `progress/oracle_baseline.sha256`. If the baseline
   changes outside the seal, every check warns.

**Deliberate exception to WARN.** The guard blocks the following, which protect the
harness's own sealed records. That is the same class as the sealed oracle and
`progress/*.sha256`, an invariant that predates this issue. Rejections of Claude's
*work* (selection edits, evidence, the contract) only warn. Claude cannot run these
commands:

- `baseline-*`, `contract-review`, `contract-approve` and `red-*` are sealer verbs
  (`SEALERS`), so the Bash guard blocks Claude running them.
- The baseline and the contract review are Codex-owned files.

## Rollout and limits

- **C template.** `templates-c/Makefile` writes the run log. Existing C projects keep
  their own `test` recipe, which is a project-owned section and not a managed fragment,
  so they get `cannot establish` until they add the line. That is the fail-closed
  answer. The 0.7 migration is the human step that adds it.
- **Red evidence for C.** Not yet: it warns `cannot establish`.
- **`--junitxml` on the command line.** If the project's Makefile passes it, it
  overrides the launcher's, and that is reported as `cannot establish`.
- **Plugin modules.** Plugins named in `pytest_plugins` or `-p` that live in the
  project are covered through the `conftest.py` and config hashes. Plugins outside
  `.venv` are not resolved.
- **FAIL promotion (I4-fail).** It needs C0 with zero WARNs on codexion and guakamole.
