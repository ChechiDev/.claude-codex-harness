# Testing the toolkit

The toolkit suite (`tests/`) has two tiers.

| Tier | What it is | How to run it |
|------|------------|---------------|
| **fast** (default) | unit and hook tests on throwaway projects linked to `lib/` | `uv run pytest -n auto` or `make test` |
| **e2e** | a real `install.sh`, a real `make check`, or a whole gate/close cycle (git commits, hooks, `uv` syncs) in a temp project; also the Docker compat tests | `make test-e2e` (`uv run pytest -n auto -o addopts= -q -m e2e`) |
| **full** | both tiers | `make test-all` (`uv run pytest -n auto -o addopts= -q`) |

The default `addopts` in `pyproject.toml` are `-q -m 'not e2e'`, so a plain
`pytest` runs only the fast tier. `-o addopts=` clears them, which is why the full
suite command runs everything. `pytest-xdist` is a dev dependency: `-n auto` works
after `uv sync`, no `--with` needed.

## The e2e marker and its guard

Mark a heavy test `@pytest.mark.e2e` (or the whole module with
`pytestmark = pytest.mark.e2e`). `tests/conftest.py` enforces it: while a test runs,
a guard on `subprocess.Popen` refuses, with `UnmarkedE2EError`, any argv that

- runs `install.sh` (any argument whose file name is `install.sh`), or
- runs `make ... check` without a dry-run flag (`-n`, `--dry-run`, `-pn`, ...).

It reads list argvs (str, bytes or paths) and shell strings (split on `&&`, `;`,
`|`). It does not look inside `bash -c '...'` or scripts.

So a test that starts using the install fixtures (`i0_projects.install_project`,
`i0_legacy.build_legacy`) or a real `make check` fails in the fast tier until it is
marked. `tests/test_suite_tiers.py` is the contract test for the guard, the
default tier, the xdist dependency and `make test-e2e`.

The guard only sees argvs the test process starts itself. Tests whose cost is a
gate or close script that runs `make check` internally (for example
`test_oracle_supersede.py`, `test_pr.py`, `test_green_record_pr.py`) are marked by
hand; when you add a test that takes more than a few seconds, mark it too.

The guard checks the test that is running when the command starts. A module- or
session-scoped fixture is built by the first test that needs it, so an unmarked
test sharing a fixture with marked ones is caught only when it runs first (always
true in a fast-tier run, where the marked ones are deselected): run the fast tier
after adding such a test.

Isolation does not change between tiers: every test gets its own temp copy;
module- or session-scoped fixtures may build a project once, but each test copies
it (`copytree`) and never mutates the shared one.

## Session rule (orchestrated sessions)

- While developing: run only the affected test files, plus the fast tier when a
  change can touch many tests (`uv run pytest -n auto`).
- Before the report (and after merging `origin/main`): the full suite once, in
  parallel and under the shared lock so two sessions never run it at the same time:

  ```bash
  flock ~/.claude-codex-harness-wt/_orchestrator/.suite.lock \
    uv run pytest -o addopts= -q -n auto </dev/null
  ```

- Do not rerun the full suite for changes that do not invalidate it; apply all
  review fixes first, then one final full run.

## Timings

Measured on 2026-11-21, 8 cores, `-n auto`:

| Run | Tests | Wall time |
|-----|-------|-----------|
| full suite before #68 | 2,493 | 52 min 27 s |
| fast tier after #68 | 1,412 | 4 min 28 s |
| full suite after #68 (fast + e2e, other sessions running) | 2,522 | 68 min 18 s |
