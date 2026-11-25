# Verification contract (issue #66, I5) — WARN rollout

"Done" means lint, types and build pass, but the project controls how they run. I5
seals the inputs that decide it and reports any drift. The code is in `lib/_contract.py`.

**WARN semantics (plan 0.5).** Every finding is a `[WARN]` with a remedy and never
changes an exit code. Blocking is the separate I5-fail issue. Without the I5 baseline
nothing is active: the launcher prints a one-line note (`I5 inactive`).

## 1. The sealed inputs (`_contract.py inventory`)

| Key | What |
|---|---|
| `<dir>/pyproject.toml[tool.ruff]`, `[tool.mypy]` | the sections, canonical JSON, at every location |
| `ruff.toml`, `.ruff.toml`, `mypy.ini`, `.mypy.ini` | whole file, at every location (ruff uses the nearest) |
| `setup.cfg[mypy]`, `setup.cfg[mypy-*]` | the mypy sections |
| `ruff-extend:<path>` | the file named by ruff's `extend` |
| `<dir>/.flake8` | whole file, at every location (#126) |
| `setup.cfg[flake8]`, `tox.ini[flake8]`, `[flake8:*]` | the flake8 sections; an unparsable `tox.ini` with a `[flake8]` header is sealed whole (`setup.cfg` already is) |
| `pyproject.toml[tool.flake8]` | the section read by the flake8-pyproject plugin |
| `config-path:<path>` | the file named by `--config`, `--config-file`, `--rcfile` or `--settings` in a recipe |
| `make:<file>` | every makefile make reads (`MAKEFILE_LIST`, as I4b resolves it) except the toolkit's `harness/harness.mk`; an unresolvable include is `UNRESOLVED` |
| `target:<t>` and `target:<t>:prereqs` | recipe and phony prerequisites of `lint`, `type`, `build`, `verify` and the `%.o: %.c` rule; removing `lint` from `verify` changes it |
| `var:<V>` and `var:<V>:origin` | origin, flavor and raw value of every variable the recipes reference, transitively, plus the C template's `CC CFLAGS CSTD WARN EXTRA_CFLAGS SRC` |
| `env:RUFF_*`, `env:MYPY*` | tool environment variables |

The make database is read as the real run parses it (`make -pn … MAKECMDGOALS=check`
with a dummy goal), and in the check's own environment. An inherited `CC` or
`EXTRA_CFLAGS` therefore shows up as an `environment` origin. Ownership: I4 owns test
selection and execution (`test`, conftest, pytest config); I5 owns lint, type and build.

## 2. The launcher (`_contract.py check`)

`harness/run_check.sh` runs it after every check, decision or not, **also when
`HARNESS_CHECK_CMD` replaces the check**. It warns when:

- an input was added, removed or changed since the sealed baseline;
- an input is `UNRESOLVED` (fail closed);
- a sealed variable comes from the environment, the command line or `override`, or is
  set in the environment even though the makefile wins (an unaccounted override);
- the baseline does not match its seal, or the seal is missing.

Command-line assignments, `-f` and `MAKEFLAGS`/`MAKE*` are already rejected (dropped,
with a warning) by the launcher itself (I4a).

## 3. Approved change flow

This is the same single step as I4's contract. It goes through the same entry point:
`review_with_codex.sh contract` calls `_oracle.py contract-review`, which also reviews a
pending I5 proposal.

1. `_contract.py contract-propose [--reason R]` writes `progress/config_contract.json`.
   Its `diff` is the sha256 of `{from: sealed inputs, to: current inputs, reason}`.
2. `review_with_codex.sh contract`: Codex approves a tightening with no reason needed. A
   relaxation is approved only when `reason` justifies it. The verdict goes to
   `progress/config_contract_review.md` with `diff: <hash>`.
3. The human runs `_contract.py contract-approve <hash>`. It reseals only when the
   proposal's `diff`, the hash recomputed from the proposal, the verdict and the
   argument agree, and `from`/`to` are still the sealed baseline and the current
   inputs. A change made after the proposal is refused (WARN, nothing resealed).

## 4. The I5 baseline

`progress/contract_baseline.json` + `progress/contract_baseline.sha256` is separate
from the M0b legacy inventory and from the I4 baseline. The steps:

1. The human runs `_contract.py baseline-create --from <pre-rule commit>`. It warns for
   every file that differs from that commit and for every environment or command-line
   override.
2. Codex reviews it.
3. `baseline-seal` writes the seal.

**Baseline version (#126).** `baseline-create` writes `"version": 2`: its inventory
covers flake8. A baseline sealed earlier (`version` 1 or absent) has no flake8 keys; it
is not rewritten. Instead `check`, `contract-propose` and `contract-approve` derive its
flake8 side from the tracked files of its sealed `from` commit (same pruning as the
walk), with the same keys. So a project without flake8, or with flake8 config unchanged
since that commit, gets no new warning; flake8 config relaxed since then warns as
changed, added or removed. If that commit cannot be read (not a full hash, missing in a
shallow clone), the derived side is empty and every current flake8 input warns as
added (fail closed). Symlinks are followed inside the commit as the live walk follows
them; a link that leaves the project is `UNRESOLVED` (fail closed) when the file is a
`.flake8` or has flake8 config today, and is skipped otherwise (a shared file without
flake8 today gets no warning; that is the residual gap). The first approved contract
reseals the baseline as version 2. The guard looks up `tox.ini` in the derived side
too.

The guard treats both files and the review as Codex-owned. `baseline-*`,
`contract-review` and `contract-approve` are sealer verbs, so Claude cannot run them.
That is the deliberate exception to WARN, the same as I4b.

## 5. Guard

When the I5 baseline exists, an edit by Claude to `pyproject.toml`, `ruff.toml`,
`.ruff.toml`, `mypy.ini`, `.mypy.ini`, `setup.cfg`, `.flake8`, a `tox.ini` with a
`[flake8]` section (or one the baseline sealed), any makefile, `*.mk` or a file the
baseline resolved (config path, ruff extend) is allowed with a `[WARN]` (`systemMessage`
and `additionalContext`). It is merged with I4b's warning into one hook answer.

## Rollout and limits

- Adding a source file does not warn: `SRC` is sealed as its definition, not its
  expansion.
- Toolkit `harness/harness.mk` is covered by the toolkit, not the seal.
- The `$(VAR)` references are found lexically. A variable reached only through
  `$(call …)` with a computed name is not followed, but its makefile is still hashed.
- Migration rehearsal on C0 copies and the FAIL promotion (I5-fail, C0 with zero WARNs
  or all migrations done) are human steps outside this issue.
