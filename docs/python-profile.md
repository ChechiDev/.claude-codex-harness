# Python profile (v4)

The canonical quality profile for **new** Python projects: what `install.sh --python
<pkg>` (through `lib/scaffold_python.sh`) and the `py-init` skill write. It is the
Python profile, not the only standard and not an architecture: a project's stack and
layering are decided in its `docs/stack.md` (stack step, #115/#116).

## What a new project gets

| File | Source in the toolkit | Notes |
|---|---|---|
| `pyproject.toml` | `skills/py-style/assets/pyproject.toml` | Ruff (pinned), mypy strict, pytest markers, coverage |
| `Makefile` | `templates/Makefile` | `verify` = lint + type + test + deps; `fix`, `audit`, `hooks` |
| `conftest.py` | `skills/py-pytest/assets/root_conftest.py` | marker and test-selection rules |
| `.pre-commit-config.yaml` | `skills/github/assets/.pre-commit-config.yaml` | opt-in hooks (`make hooks`) |
| `.github/workflows/*.yml` | `skills/github/assets/` (py-init, Phase 3) | jobs call the Makefile targets |

The scaffold never overwrites an existing `conftest.py` or `.pre-commit-config.yaml`.
C projects (`--c`) get none of them.

### Ruff

- `select`: `E4,E7,E9,F,W,I,N,A,ICN,UP,FURB,PTH,SIM,C4,PIE,RET,PERF,B,BLE,RUF,PGH,DTZ,
  TRY,LOG,G,ANN,TC,TID,SLF,ARG,ERA,S,PT,C90` and `PLR0911,PLR0912,PLR0913,PLR0915,
  PLR1702`; `ignore`: `TRY003`, `ANN401`.
- `lint.preview = true` with `explicit-preview-rules = true`: only the preview rules
  named exactly (PLR1702) are on; `format.preview = false`.
- Limits: line length 88, McCabe 10, 5 arguments, 10 branches, 40 statements, 4 nested
  blocks. Tests relax `ANN`, `S101`, `ARG`, `SLF001`, `PLR09`, `C901`.
- No `target-version` (inferred from `requires-python >= 3.12`); mypy keeps
  `python_version = "3.12"` (mypy does not infer it).
- **Pinned** (`ruff==0.17.1`): the rule set is tested against that version
  (`tests/test_python_profile_v4.py` checks the effective set with `ruff check
  --show-settings`). Updating Ruff is a deliberate change: bump the pin in the template
  and in the toolkit, re-run those tests.
- TC + Pydantic: `runtime-evaluated-base-classes = ["pydantic.BaseModel",
  "pydantic_settings.BaseSettings"]`, `runtime-evaluated-decorators =
  ["pydantic.validate_call"]`. Add your own bases/decorators to those lists.
- Exceptions: `per-file-ignores`, or inline `# noqa: CODE  # reason` (the reason is a
  convention, not enforced). RUF100 flags a dead `noqa`, PGH004 a blanket one. Examples
  in `skills/py-style/SKILL.md` ("Lint exceptions").

### Tools

`dev` group: ruff (pinned), mypy, pytest, pytest-cov, pytest-mock, deptry, hypothesis,
pre-commit. Runtime dependencies: `pydantic` and `pydantic-settings` (every project
validates at its boundaries with Pydantic); the rest are the project's (`uv add <pkg>`).

`make deps` (deptry) is strict, with one exception: while `src/` holds no module (only
`__init__.py` files), the declared pydantic/pydantic-settings are unused yet, so their
DEP002 is tolerated with a warning ("expected until src/ imports pydantic") and the
target exits 0. Any other finding (DEP001, another unused dependency…) still fails, and
the first module in `src/` makes deptry fully strict: no ignores at all, so a project
that never imports `pydantic_settings` must drop it (`uv remove pydantic-settings`).
There is no `[tool.deptry]` ignore in the profile.

Not in the base, documented as opt-in:

- **pytest-asyncio** (async projects): `uv add --dev pytest-asyncio` and
  `asyncio_mode = "auto"` under `[tool.pytest.ini_options]`.
- **import-linter** (hexagonal projects): a `layers` contract, example in
  `skills/py-style/SKILL.md`.
- **pip-audit**: `make audit` (`uv export` + `uvx pip-audit`), outside `verify` because it
  needs the network (or a prepared pip-audit cache). The project CI runs it in its own
  job.

### Tests and coverage

- `make test` runs every test except the `integration` and `e2e` markers (selection
  `TEST_SELECT` in the Makefile, the only place it is written; CI runs `make test`),
  with branch coverage of `src/` and `[tool.coverage.report] fail_under = 80` as the
  single threshold.
- Markers are the only criterion: `make test-integration` (CI `integration` job, inside
  `ci.yml`, with Postgres/Redis service containers) and `make test-e2e` (by hand). The
  root `conftest.py` fails a test under `tests/integration/` or `tests/e2e/` that lacks
  its marker.
- Contract: no test selected (empty scaffold, or only integration/e2e tests) is green;
  a suite under 80% is red; a collection error is red; a suite emptied by any other
  deselection (`-k` typo, wrong `-m`) is red.
- `HARNESS_NO_COV_THRESHOLD=1` drops the threshold only (coverage is still measured and
  printed). The harness sets it in one place: the issue gate's third run, which re-runs
  `make check` without the expected-red tests (`lib/review_with_codex.sh`). Every other
  check drops it from the environment (`lib/_clean_env.sh`) and the Bash guard blocks
  commands that spell it.

### Hooks

`make hooks` installs pre-commit (opt-in). The hooks are local `make lint` and `make type`
with `pass_filenames: false`, `always_run: true` and `stages: [pre-commit]`: check-only,
they run even when no `.py` is staged, and never run the tests or `make check`. An
existing Git hook that is not pre-commit's is kept: `make hooks` warns and fails unless
`FORCE=1`. The post-write hook stays check-only; `make fix` is the explicit fixer.

## Migrating an existing project

Two kinds of files, two behaviours:

- **Symlinked, already live**: `.claude/skills` and `harness/` are symlinks to the toolkit, so
  the updated skills (py-style, py-pytest, py-init, github/ci-*) and the harness changes
  (`HARNESS_NO_COV_THRESHOLD` in the issue gate, `_clean_env.sh`, the guard) reach every
  project as soon as the toolkit updates. The skills now describe the v4 profile even
  where the project still runs the old one.
- **Copied, unchanged until you act**: `pyproject.toml`, `Makefile`, `conftest.py`,
  `.pre-commit-config.yaml`, `.gitignore` and `.github/workflows/` are the project's.
  Neither the toolkit update nor `install.sh --migrate` rewrites them (the migration
  only manages the harness-owned Makefile block).

Steps, on a branch, each verified with `make verify`:

1. `pyproject.toml`: take the `[tool.ruff*]`, `[tool.mypy]`, `[tool.pytest.ini_options]`
   and `[tool.coverage.*]` blocks from `skills/py-style/assets/pyproject.toml`, and
   **merge** the profile's tools (with the Ruff pin) into the project's existing dev
   dependencies — keep its own tools (`uv add --dev ...`; move a `lint`/`test` group
   into `dev` if CI should keep syncing only the default group). Declare `pydantic` and
   `pydantic-settings` if the project lacks them (`uv add`; drop the one it never
   imports, deptry is strict once `src/` has code). Remove `flake8` and
   the `.flake8` file. If
   the project has async tests, keep `pytest-asyncio` and `asyncio_mode = "auto"`.
   `uv sync`, then `make fix` and fix the rest by hand or with reasoned exceptions.
2. `Makefile`: copy the project targets of `templates/Makefile` (`STATUS` fallback,
   `TEST_SELECT`, `test`, `test-integration`, `test-e2e`, `deps`, `verify`, `fix`,
   `audit`, `hooks`); keep the harness block at the end and the project's own targets.
3. `conftest.py`: copy `skills/py-pytest/assets/root_conftest.py` to the project root
   (merge it if one exists). Mark every test in `tests/integration/` and `tests/e2e/`.
4. `.gitignore`: add `.coverage`, `.coverage.*`, `coverage.xml`, `htmlcov/`.
5. Coverage: if the suite is under 80% today, raise the coverage or set a lower
   `fail_under` with a dated plan to raise it — never a CLI flag.
6. CI: replace `.github/workflows/ci.yml` and `e2e.yml` with the assets (drop the
   flake8 job and any `needs` on it; integration selects by marker). Update the branch
   protection checks (`skills/github/branch/SKILL.md`).
7. Optional: `.pre-commit-config.yaml` + `make hooks`.

## What remains outside the profile

The architecture (layers, import contracts), the runtime stack and the services of
the integration job are each project's decisions (`docs/stack.md`).
