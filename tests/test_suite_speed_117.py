"""#117: suite timing plugin, measurement report and the e2e shard aggregate check.

The plugin (tests/tools/suite_timing.py) records per-test phases, fixture setups and
subprocess spans; tests/tools/measure_suite.py turns those records into a report, the
pytest-split durations file and the CI aggregate verdict over the e2e shards.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tests" / "tools"
sys.path.insert(0, str(TOOLS))

import measure_suite as ms  # noqa: E402
import suite_timing as st  # noqa: E402

# ── subprocess classification ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("argv", "category"),
    [
        (["uv", "sync", "--all-groups", "--quiet", "--offline"], "uv sync"),
        (["uv", "run", "pytest"], "uv (other)"),
        (["make", "-s", "--no-print-directory", "check"], "make check"),
        (["make", "-n", "check"], "make (other)"),
        (["make", "close"], "make (other)"),
        (["git", "commit", "-qm", "x"], "git"),
        (["/usr/bin/git", "status"], "git"),
        (["bash", "/x/install.sh", "/tmp/p", "--python", "demo"], "install.sh"),
        (["bash", "/x/harness/review_with_codex.sh", "code"], "harness script (.sh)"),
        (["bash", "/x/harness/close_subtask.sh"], "harness script (.sh)"),
        (["bash", "/x/lib/stop_gate.sh"], "harness script (.sh)"),
        (["bash", "-c", "true"], "shell (other)"),
        (["python3", "/x/lib/plan_status.py"], "python"),
        (["/tmp/venv/bin/python", "-m", "x"], "python"),
        (["sleep", "1"], "other"),
        ("git status && make check", "git"),
        ([], "other"),
    ],
)
def test_classify(argv: list[str] | str, category: str) -> None:
    assert st.classify(argv) == category


# ── outcome of a test record (only complete phase sequences count as passed) ─────


@pytest.mark.parametrize(
    ("rec", "outcome"),
    [
        ({"outcome": "passed", "setup": 0.1, "call": 0.1, "teardown": 0.0}, "passed"),
        ({"outcome": "passed", "setup": 0.1, "call": 0.1}, "incomplete"),  # no teardown
        ({"outcome": "passed", "setup": 0.1, "teardown": 0.0}, "incomplete"),  # no call
        ({"outcome": "skipped", "setup": 0.0, "teardown": 0.0}, "skipped"),  # setup skip
        ({"outcome": "skipped", "setup": 0.0}, "incomplete"),
        ({"outcome": "error", "setup": 0.1, "teardown": 0.0}, "error"),
        ({"outcome": "error", "setup": 0.1}, "error"),
        ({"outcome": "failed", "setup": 0.1, "call": 0.1, "teardown": 0.0}, "failed"),
    ],
)
def test_final_outcome(rec: dict[str, object], outcome: str) -> None:
    assert st.final_outcome(rec) == outcome


def test_incomplete_tests_make_the_aggregate_red() -> None:
    shards = {1: [_rec("tests/test_x.py::a", 0, 1, outcome="incomplete")]}
    errors = ms.check_shards(shards, ["tests/test_x.py::a"], expected=1)
    assert any("incomplete" in e for e in errors)


# ── the plugin, run on a tiny throwaway suite ────────────────────────────────

TINY = """
import subprocess
import time

import pytest


@pytest.fixture
def slow_fixture():
    time.sleep(0.05)
    yield 1


def test_a(slow_fixture):
    subprocess.run(["git", "--version"], check=True, capture_output=True)


def test_b():
    time.sleep(0.02)


@pytest.mark.skip(reason="nope")
def test_c():
    pass
"""


def _tiny_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith("PYTEST") and k != "PYTHONPATH"}


def _run_tiny(tmp_path: Path, *extra: str) -> tuple[subprocess.CompletedProcess[str], Path]:
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "test_tiny.py").write_text(TINY)
    out = tmp_path / "timing"
    argv = [sys.executable, str(TOOLS / "pytest_timed.py"), "-q", "-p", "no:cacheprovider"]
    argv += [f"--timing-dir={out}", "--timing-subprocess", *extra, "test_tiny.py"]
    res = subprocess.run(
        argv, cwd=suite, env=_tiny_env(), text=True, capture_output=True, check=False, timeout=120
    )
    return res, out


def test_plugin_records_phases_fixtures_and_subprocesses(tmp_path: Path) -> None:
    res, out = _run_tiny(tmp_path)
    assert res.returncode == 0, res.stdout + res.stderr
    run = ms.load_run(out)
    ids = {t.nodeid for t in run.tests}
    assert ids == {"test_tiny.py::test_a", "test_tiny.py::test_b", "test_tiny.py::test_c"}
    a = run.test("test_tiny.py::test_a")
    assert a.outcome == "passed"
    assert a.setup >= 0.05  # the fixture sleeps in setup
    assert run.test("test_tiny.py::test_b").call >= 0.02
    assert run.test("test_tiny.py::test_c").outcome == "skipped"
    assert "nope" in run.test("test_tiny.py::test_c").reason
    fixtures = [f for f in run.fixtures if f.name == "slow_fixture"]
    assert len(fixtures) == 1 and fixtures[0].seconds >= 0.05
    spans = [s for s in run.spans if s.category == "git"]
    assert len(spans) == 1 and spans[0].nodeid == "test_tiny.py::test_a"


def test_plugin_without_timing_dir_is_inert(tmp_path: Path) -> None:
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "test_tiny.py").write_text(TINY)
    argv = [sys.executable, str(TOOLS / "pytest_timed.py"), "-q", "test_tiny.py"]
    res = subprocess.run(
        argv, cwd=suite, env=_tiny_env(), text=True, capture_output=True, check=False, timeout=120
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert not any(p.name.startswith("worker-") for p in suite.rglob("*.json"))


def test_plugin_under_xdist_writes_one_record_per_test(tmp_path: Path) -> None:
    res, out = _run_tiny(tmp_path, "-n", "2")
    assert res.returncode == 0, res.stdout + res.stderr
    run = ms.load_run(out)
    assert sorted(t.nodeid for t in run.tests) == [
        "test_tiny.py::test_a",
        "test_tiny.py::test_b",
        "test_tiny.py::test_c",
    ]
    assert {t.worker for t in run.tests} <= {"gw0", "gw1"}


def test_run_keeps_the_project_ini(tmp_path: Path) -> None:
    # regression: `--timing-dir PATH` as two tokens made PATH the rootdir, which dropped
    # pyproject.toml (addopts, pythonpath) and ran the e2e tier inside the fast one
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\naddopts = "-q -m \'not e2e\'"\nmarkers = ["e2e: heavy"]\n'
    )
    # regression too: an exported PYTHONPATH leaks into every subprocess the tests start
    # (run_check refuses it), so the launcher must not need one
    (proj / "test_t.py").write_text(
        "import os\n\nimport pytest\n\n"
        "def test_fast():\n    assert 'PYTHONPATH' not in os.environ\n\n"
        "@pytest.mark.e2e\ndef test_heavy():\n    pass\n"
    )
    out = tmp_path / "timing"
    out.mkdir()  # `run` creates it first; only an existing path can become the rootdir
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    argv = [*ms.pytest_command(out, spans=False), "-p", "no:cacheprovider"]
    res = subprocess.run(argv, cwd=proj, env=env, text=True, capture_output=True, check=False)
    assert res.returncode == 0, res.stdout + res.stderr
    assert [t.nodeid for t in ms.load_run(out).tests] == ["test_t.py::test_fast"]


def test_run_writes_meta(tmp_path: Path) -> None:
    out = tmp_path / "run"
    target = "tests/test_ci_workflow.py::test_no_secrets_used"
    argv = ["run", "--label", "x", "--out", str(out), "--", "-p", "no:cacheprovider", target]
    assert ms.main(argv) == 0
    run = ms.load_run(out)
    assert [t.nodeid for t in run.tests] == [target]
    assert run.meta["label"] == "x" and run.meta["exit_code"] == 0
    assert run.meta["lock_wait"] == 0.0 and run.meta["workers"] == 1


# ── analysis ─────────────────────────────────────────────────────────────────


def _rec(nodeid: str, setup: float, call: float, teardown: float = 0.0, **kw: str) -> ms.TestRec:
    return ms.TestRec(
        nodeid=nodeid,
        worker=kw.get("worker", "gw0"),
        outcome=kw.get("outcome", "passed"),
        setup=setup,
        call=call,
        teardown=teardown,
    )


def test_durations_map_sums_phases() -> None:
    tests = [_rec("tests/test_x.py::a", 1.0, 2.0, 0.5), _rec("tests/test_y.py::b", 0.0, 0.25)]
    assert ms.durations_map(tests) == {"tests/test_x.py::a": 3.5, "tests/test_y.py::b": 0.25}


def test_by_file_groups_and_sorts() -> None:
    tests = [
        _rec("tests/test_x.py::a", 1.0, 2.0),
        _rec("tests/test_x.py::b[1]", 0.0, 1.0),
        _rec("tests/test_y.py::c", 5.0, 0.0),
    ]
    rows = ms.by_file(tests)
    assert [r[0] for r in rows] == ["tests/test_y.py", "tests/test_x.py"]
    assert rows[1][1:] == (2, 1.0, 3.0, 0.0, 4.0)


def test_report_mentions_the_main_tables(tmp_path: Path) -> None:
    res, out = _run_tiny(tmp_path)
    assert res.returncode == 0, res.stdout + res.stderr
    (out / "meta.json").write_text(
        json.dumps({"label": "tiny", "workers": 1, "wall": 1.5, "lock_wait": 0.25})
    )
    text = ms.render_report([ms.load_run(out)], top=5)
    for needle in ("tiny", "Slowest tests", "Slowest files", "Fixtures", "Subprocesses", "git"):
        assert needle in text, needle


def test_split_simulation_covers_every_test_once() -> None:
    durations = {f"tests/test_{i % 3}.py::t{i}": float(i % 7 + 1) for i in range(40)}
    for algo in ("duration_based_chunks", "least_duration"):
        groups = ms.simulate_split(list(durations), durations, 8, algo)
        assert len(groups) == 8
        flat = [n for g in groups for n in g]
        assert sorted(flat) == sorted(durations)


# ── shard aggregate (the CI gate over the 8 e2e shards) ──────────────────────

COLLECTED = ["tests/test_x.py::a", "tests/test_x.py::b", "tests/test_y.py::c"]


def _shards(*groups: list[ms.TestRec]) -> dict[int, list[ms.TestRec]]:
    return {i + 1: g for i, g in enumerate(groups)}


def test_shards_ok_when_union_equals_collection() -> None:
    shards = _shards(
        [_rec("tests/test_x.py::a", 0, 1), _rec("tests/test_x.py::b", 0, 1)],
        [_rec("tests/test_y.py::c", 0, 1)],
    )
    assert ms.check_shards(shards, COLLECTED, expected=2) == []


def test_shards_fail_on_a_missing_shard() -> None:
    shards = _shards([_rec(n, 0, 1) for n in COLLECTED])
    errors = ms.check_shards(shards, COLLECTED, expected=2)
    assert any("shard 2" in e and "missing" in e for e in errors)


def test_shards_fail_on_an_empty_shard() -> None:
    shards = _shards([_rec(n, 0, 1) for n in COLLECTED], [])
    errors = ms.check_shards(shards, COLLECTED, expected=2)
    assert any("shard 2" in e and "no tests" in e for e in errors)


def test_shards_fail_when_a_shard_only_skipped() -> None:
    shards = _shards(
        [_rec("tests/test_x.py::a", 0, 1), _rec("tests/test_x.py::b", 0, 1)],
        [_rec("tests/test_y.py::c", 0, 0, outcome="skipped")],
    )
    errors = ms.check_shards(shards, COLLECTED, expected=2)
    assert any("shard 2" in e and "skipped" in e for e in errors)


def test_shards_fail_on_a_test_that_never_ran() -> None:
    shards = _shards([_rec("tests/test_x.py::a", 0, 1)], [_rec("tests/test_y.py::c", 0, 1)])
    errors = ms.check_shards(shards, COLLECTED, expected=2)
    assert any("tests/test_x.py::b" in e and "not run" in e for e in errors)


def test_shards_fail_on_overlap_and_unknown_tests() -> None:
    shards = _shards(
        [_rec("tests/test_x.py::a", 0, 1), _rec("tests/test_x.py::b", 0, 1)],
        [_rec("tests/test_x.py::a", 0, 1), _rec("tests/test_y.py::c", 0, 1)],
        [_rec("tests/test_z.py::new", 0, 1)],
    )
    errors = ms.check_shards(shards, COLLECTED, expected=3)
    assert any("tests/test_x.py::a" in e and "more than one shard" in e for e in errors)
    assert any("tests/test_z.py::new" in e and "not in the collection" in e for e in errors)


DOCKER = r"^tests/test_y\.py::.*Docker Engine .* not ready"


def test_a_skip_fails_the_aggregate_unless_allowed() -> None:
    shards = _shards(
        [_rec("tests/test_x.py::a", 0, 1), _rec("tests/test_x.py::b", 0, 1)],
        [ms.TestRec("tests/test_y.py::c", "gw0", "skipped", reason="needs gcc")],
    )
    errors = ms.check_shards(shards, COLLECTED, expected=2, allowed_skips=[DOCKER])
    assert any("tests/test_y.py::c" in e and "needs gcc" in e for e in errors)


def test_an_allowed_skip_keeps_the_aggregate_green() -> None:
    docker = "Skipped: Docker Engine or image harness-compat:local not ready"
    shards = _shards(
        [_rec("tests/test_x.py::a", 0, 1), _rec("tests/test_x.py::b", 0, 1)],
        [
            ms.TestRec("tests/test_y.py::c", "gw0", "skipped", reason=docker),
            _rec("tests/test_y.py::d", 0, 1),
        ],
    )
    collected = [*COLLECTED, "tests/test_y.py::d"]
    assert ms.check_shards(shards, collected, expected=2, allowed_skips=[DOCKER]) == []
    assert ms.check_shards(shards, collected, expected=2)  # no allowlist: every skip is red


@pytest.mark.parametrize("outcome", ["failed", "error"])
def test_shards_fail_on_failed_tests(outcome: str) -> None:
    shards = _shards(
        [_rec("tests/test_x.py::a", 0, 1, outcome=outcome), _rec("tests/test_x.py::b", 0, 1)],
        [_rec("tests/test_y.py::c", 0, 1)],
    )
    errors = ms.check_shards(shards, COLLECTED, expected=2)
    assert any("tests/test_x.py::a" in e and outcome in e for e in errors)


def test_shards_fail_on_an_empty_collection() -> None:
    assert ms.check_shards({}, [], expected=8)


def test_check_shards_cli(tmp_path: Path) -> None:
    for shard, nodeids in ((1, COLLECTED[:2]), (2, COLLECTED[2:])):
        d = tmp_path / f"shard-{shard}"
        d.mkdir()
        tests = [
            {"nodeid": n, "worker": "gw0", "outcome": "passed", "setup": 0, "call": 1}
            for n in nodeids
        ]
        (d / "worker-gw0.json").write_text(json.dumps({"tests": tests}))
    coll = tmp_path / "collected.txt"
    coll.write_text("\n".join(COLLECTED) + "\n")
    argv = ["check-shards", "--expected", "2", "--collected", str(coll), str(tmp_path)]
    assert ms.main(argv) == 0
    skipped = {"nodeid": COLLECTED[2], "worker": "gw0", "outcome": "skipped", "setup": 0}
    skipped |= {"teardown": 0, "reason": "Skipped: Docker Engine down"}
    (tmp_path / "shard-2" / "worker-gw0.json").write_text(json.dumps({"tests": [skipped]}))
    assert ms.main(argv) == 1
    assert ms.main([*argv[:-1], "--allow-skip", "Docker Engine", argv[-1]]) == 1  # all skipped
    (tmp_path / "shard-2" / "worker-gw1.json").write_text(
        json.dumps({"tests": [{**skipped, "nodeid": "tests/test_y.py::d", "outcome": "passed"}]})
    )
    coll.write_text("\n".join([*COLLECTED, "tests/test_y.py::d"]) + "\n")
    assert ms.main([*argv[:-1], "--allow-skip", "Docker Engine", argv[-1]]) == 0
    coll.write_text("\n".join(COLLECTED) + "\n")
    coll.write_text("\n".join([*COLLECTED, "tests/test_z.py::d"]) + "\n")
    assert ms.main(argv) == 1


def test_parse_collect_only_output() -> None:
    text = "tests/test_x.py::a\ntests/test_x.py::b[1-2]\n\n3 tests collected in 0.1s\n"
    assert ms.parse_collected(text) == ["tests/test_x.py::a", "tests/test_x.py::b[1-2]"]


# ── uv cache warm-up for the e2e shards (#123) ───────────────────────────────


def test_warm_env_is_the_tests_isolated_env_with_the_network_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import warm_uv_cache as warm  # noqa: PLC0415
    from i0_projects import isolated_env  # noqa: PLC0415

    monkeypatch.setenv("UV_PYTHON", "3.12")  # what setup-uv exports in CI
    monkeypatch.setenv("UV_PYTHON_INSTALL_DIR", "/opt/uv-python")
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    env = warm.warm_env(tmp_path / "a")
    expected = isolated_env(tmp_path / "b")
    expected.pop("UV_OFFLINE")
    assert "UV_OFFLINE" not in env
    assert not any(k.startswith("UV_") for k in env if k != "UV_CACHE_DIR")
    for key in ("HOME", "CLAUDE_HOME", "GIT_CONFIG_GLOBAL"):  # per-dir paths
        env.pop(key)
        expected.pop(key)
    assert env == expected


def test_warm_checks_the_cache_offline_after_the_online_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import warm_uv_cache as warm  # noqa: PLC0415

    calls: list[tuple[str, bool]] = []

    def fake_install(base: Path, offline: bool) -> subprocess.CompletedProcess[str]:
        calls.append((base.name, offline))
        return subprocess.CompletedProcess([], 0, "ok\n", "")

    monkeypatch.setattr(warm, "install", fake_install)
    assert warm.main([str(tmp_path)]) == 0
    assert calls == [("online", False), ("offline", True)]
    assert warm.main(["a", "b"]) == 2


def test_warm_fails_when_the_offline_install_misses_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import warm_uv_cache as warm  # noqa: PLC0415

    def fake_install(base: Path, offline: bool) -> subprocess.CompletedProcess[str]:
        rc = 1 if offline else 0
        return subprocess.CompletedProcess([], rc, "", "error: Failed to download `mypy`")

    monkeypatch.setattr(warm, "install", fake_install)
    assert warm.main([str(tmp_path)]) == 1
    assert "Failed to download" in capsys.readouterr().err


def test_isolated_env_drops_inherited_pytest_variables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The e2e job exports PYTEST_ADDOPTS=-rs; in a throwaway project's own pytest it
    # replaced the default -r (fE), so `make check` printed no FAILED summary (#123).
    from i0_projects import isolated_env  # noqa: PLC0415

    monkeypatch.setenv("PYTEST_ADDOPTS", "-rs")
    monkeypatch.setenv("PYTEST_PLUGINS", "xdist")
    env = isolated_env(tmp_path)
    assert not any(k.startswith("PYTEST_") for k in env)
