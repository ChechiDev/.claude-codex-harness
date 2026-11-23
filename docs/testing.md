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

## Session rule (orchestrated sessions, #117)

CI owns the full suite; a session tests what it touches.

- A session runs only its new tests, the tests of the files it changes and the tests
  that exercise its code (`uv run pytest -n 4 tests/test_<x>.py ...`; for e2e ones,
  `uv run pytest -n 4 -o addopts= -m e2e tests/test_<x>.py ...`), plus
  `uv run ruff check .`, `uv run ruff format --check .` and `uv run mypy`. Target:
  1-3 minutes. No shared lock.
- A session never runs the full fast tier or the full e2e tier. When a change is
  cross-cutting (guards, checks, gates, close, `harness.mk`, install, shared
  fixtures), its report says so and the orchestrator decides.
- The full fast tier, ruff, `ruff format --check`, mypy and the full e2e tier run in
  CI on the PR. A PR is mergeable only when both required checks (below) are green
  on its latest commit.

## CI (GitHub Actions)

`.github/workflows/ci.yml` (#74, #117):

| Event | Jobs |
|-------|------|
| `pull_request`, `merge_group` | `fast tier, ruff, mypy`; `e2e shard 1/8` ... `e2e shard 8/8`; `e2e tier` (aggregate) |
| `push` to `main` | `fast tier, ruff, mypy` |
| `schedule` (nightly, 03:17 UTC), `workflow_dispatch` | the 8 e2e shards and `e2e tier` |

- **fast tier, ruff, mypy**: `uv sync --locked`, `uv run pytest -n auto -rs`,
  `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`.
- **e2e shard N/8** (`fail-fast: false`, so every shard reports): warms the uv cache
  with `uv run python tests/tools/warm_uv_cache.py` (the throwaway projects sync with
  `UV_OFFLINE=1`, so a cold runner cache fails every installed-project test). It runs
  `install.sh --python` online and then offline in the fixtures' own `isolated_env`:
  that env drops every `UV_*` variable, setup-uv's `UV_PYTHON` included, so a bare
  `install.sh` in the workflow env would cache wheels for another interpreter (#123).
  Then

  ```bash
  uv run python tests/tools/pytest_timed.py -o addopts= -q -m e2e -n 2 \
    --splits 8 --group N --splitting-algorithm=duration_based_chunks \
    --durations-path=tests/.test_durations \
    --timing-dir=e2e-out --junitxml=e2e-out/junit.xml
  ```

  and uploads `e2e-out/` (JUnit and per-test timing records) as `e2e-shard-N`, also
  when the shard fails.
- **e2e tier** (aggregate; runs `if: always()`): red unless every shard job
  succeeded and `tests/tools/measure_suite.py check-shards` confirms that the 8
  shards ran exactly the full e2e collection (`--collect-only`): no shard missing or
  empty or all-skipped, no test missing, none run twice, none unknown, no failure.
  A cancelled or skipped shard makes it red too. A skipped test counts as not run:
  the only skips it accepts are the ones its `--allow-skip` regexes list (today the
  Docker compat tests, which need the `harness-compat:local` image the runner does
  not build); any other skip or xfail makes it red.
- Shards come from [pytest-split](https://github.com/jerry-git/pytest-split)
  (pinned in `uv.lock`) using the recorded durations in `tests/.test_durations`; a
  test missing from that file counts as the average duration, so new tests are
  still run, only balanced worse. `duration_based_chunks` cuts the collection
  order into 8 contiguous chunks: a module and its module-scoped install
  (`build_legacy` takes minutes) stay in one shard, at the cost of a less even
  split than `least_duration`, which spreads every file over all shards.
- `setup-uv` caches the uv cache (key: OS, Python, `uv.lock`, plus the scaffold
  `pyproject.toml` for the e2e jobs).
- Actions are pinned to a commit SHA (tag in a comment); `permissions: contents: read`;
  no secrets; checkout without persisted credentials. A concurrency group per PR/ref
  cancels superseded runs.
- Tests that need tools the runner lacks (the Docker compat image, norminette,
  valgrind inside it) `pytest.skip` with a reason; `-rs` prints every skip reason in
  the job log, and in the e2e tier an unlisted skip turns `e2e tier` red.
- `tests/test_ci_workflow.py` is the contract test for the workflow.

### Required checks (branch protection)

Applied on GitHub by the repository owner (the workflow cannot set it). For `main`,
in *Settings → Branches → Add branch protection rule* (or an equivalent ruleset):

| Setting | Value |
|---------|-------|
| Branch name pattern | `main` |
| Require a pull request before merging | on |
| Require status checks to pass before merging | on |
| Require branches to be up to date before merging | on (strict) |
| Status checks that are required | `fast tier, ruff, mypy` and `e2e tier` |
| Do not allow bypassing the above settings | on |

Require the aggregate `e2e tier`, not the individual `e2e shard N/8` jobs: their
names change with the shard count, and the aggregate already fails when a shard is
red, cancelled, skipped or missing. Both required jobs run on every
`pull_request` (and `merge_group`), so neither can be skipped into a green state.

The same rule through the API (classic branch protection):

```bash
gh api -X PUT repos/<owner>/<repo>/branches/main/protection --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "checks": [{"context": "fast tier, ruff, mypy"}, {"context": "e2e tier"}]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {"required_approving_review_count": 0},
  "restrictions": null
}
JSON
```

`required_pull_request_reviews` must be an object, not `null` (`null` turns off the
pull-request requirement, and a direct push to `main` would skip the e2e gate);
`required_approving_review_count: 0` requires a PR without requiring a second
reviewer. `enforce_admins: true` applies the rule to the owner too; drop it only to
keep an emergency bypass. `strict` makes GitHub test the PR merged with the current `main`: when `main` moves,
update the branch and let CI rerun before merging. A merge queue (the workflow
already handles `merge_group`) would automate that, but GitHub offers it only for
organisation-owned repositories.

### Runner capacity

The repository is public, so hosted-runner minutes are free; the limit is
concurrency (20 jobs at a time on the Free plan). One PR run uses 10 jobs (fast,
8 shards, aggregate), so two PR runs at once fill the quota and a third queues.
Superseded runs of the same PR are cancelled (concurrency group).

## Measuring the suite

`tests/tools/suite_timing.py` is a pytest plugin that records, per test, setup/call/
teardown time, outcome and xdist worker; per fixture setup, its time; and with
`--timing-subprocess`, every subprocess the test process starts (classified as
`git`, `uv sync`, `make check`, `install.sh`, harness scripts, ...) and the
installed-project helpers (`_build_template`, `install_project`, `build_legacy`,
`copytree`). `tests/tools/measure_suite.py` runs pytest with it and reports:

```bash
M=/tmp/suite-measure
uv run python tests/tools/measure_suite.py run --label fast-n8 --out $M/fast-n8 -- -n 8
uv run python tests/tools/measure_suite.py run --label e2e-n8 --out $M/e2e-n8 -- \
  -o addopts= -q -m e2e -n 8
uv run python tests/tools/measure_suite.py report $M/fast-n8 $M/e2e-n8 > report.md
```

`run` goes through `tests/tools/pytest_timed.py`, which loads the plugin without
exporting `PYTHONPATH` (an exported one reaches every subprocess the tests start,
and `run_check` refuses it). `--lock FILE` takes an exclusive `flock` on FILE first
and records the wait, for a machine shared with other runs. Pass plugin options as
one token (`--timing-dir=DIR`): pytest reads a separate existing path as a test path
and roots the run there, losing the `pyproject.toml` settings.

Refresh the shard durations after the e2e tier changes noticeably (a measured e2e
run, or the `e2e-shard-*` artifacts of a CI run, downloaded into one directory each):

```bash
uv run python tests/tools/measure_suite.py durations $M/e2e-n8 \
  --output tests/.test_durations
uv run pytest -o addopts= -q -m e2e --collect-only > $M/collected.txt
uv run python tests/tools/measure_suite.py split-compare \
  --durations tests/.test_durations --collected $M/collected.txt --splits 8
```

## Timings

Measured on 2026-11-23 (#117) with `measure_suite.py` (full report:
[design/suite-timing-117.md](design/suite-timing-117.md)), 8 cores (WSL2), commit
`a56fb8a` plus this change. Other sessions' runs shared the machine (load 9-19), so
absolute times are inflated; the shares and the rankings are the useful part. For
scale, the CI fast job takes about 3.5 min on a 2-core runner for the same tier.

| Run | Tests | Wall | Worker time | Setup / call | Notes |
|-----|-------|------|-------------|--------------|-------|
| fast tier, `-n 8` | 2,238 | 10 min 54 s | 84 min | 8 / 76 min | waited 5 min 48 s for the shared lock first |
| e2e tier, `-n 8` | 1,160 | 47 min 59 s | 373 min | 78 / 295 min | 1 failure, pre-existing on `main` (I8 legacy warning) |

Where the e2e time goes (spans seen from the test process; they nest):

| Cost | Count | Total |
|------|-------|-------|
| harness scripts (gates, close, hooks) run by the tests | 1,189 | 243 min |
| `build_legacy` (module-scoped legacy project) | 11 | 56 min |
| real `make check` | 149 | 37 min |
| `install_project` (template copy + offline `uv sync`) | 157 | 33 min |
| `_build_template` (real `install.sh`, once per worker and kind) | 35 | 23 min |
| `uv sync` alone | 115 | 9 min |
| `git` | 5,134 | 5 min |

Slowest e2e files: `test_regate_inflight.py` (75 min of worker time),
`test_oracle_supersede.py` (39), `test_regate_deadlock.py` (24), `test_i0_legacy.py`
(18), `test_i0_task_cycle.py` (18). Slowest fast files: `test_i8_commit_guard.py`
(12 min), `test_resume_46.py` (8), `test_fingerprint_commit_61b.py` (6). In the
fast tier fixtures are cheap (`project`: 820 setups, 5.7 min); the time is in the
test bodies' subprocesses.

Workers on one e2e slice (1/32 of the tier by duration: 23 tests of
`test_i12b_stop_cache.py`, `test_i1_legacy_and_discovery.py`,
`test_i1_seals_required.py`):

| `-n` | Wall | Worker time | `build_legacy` runs |
|------|------|-------------|---------------------|
| 1 | 11 min 31 s | 11 min | 2 |
| 2 | 8 min 36 s | 14 min | 3 |
| 4 | 6 min 28 s | 20 min | 5 |
| 8 | 11 min 37 s | 71 min | 9 |

Module-scoped installs are rebuilt in every worker that runs a test of the module,
so more workers stop paying off quickly, and past the core count they cost more
than they save. This is why CI uses `-n 2` per shard and contiguous shards.

Shard balance over the recorded durations (`split-compare`, 8 splits):
`duration_based_chunks` gives 2,187-3,114 s per shard (6-16 files each);
`least_duration` gives an even 2,794 s but puts 41-49 files in every shard.

Before #117 (2026-11-21, 8 cores, `-n auto`): full suite before #68, 2,493 tests,
52 min 27 s; fast tier after #68, 1,412 tests, 4 min 28 s; full suite after #68
with other sessions running, 2,522 tests, 68 min 18 s.
