"""pytest plugin (#117): per-test phases, fixture setups and subprocess spans, as JSON.

Load it with `PYTHONPATH=tests/tools pytest -p suite_timing --timing-dir DIR`. Without
`--timing-dir` it does nothing. Every process that runs tests (each xdist worker, or
the single process without xdist) writes `DIR/worker-<id>.json` when its session ends;
the xdist controller writes nothing, so each test is recorded exactly once.

`--timing-subprocess` also times every subprocess the test process starts (from
`Popen()` to the `wait()` that reaps it) and the installed-project helpers
(`i0_projects._build_template`, `install_project`, `i0_legacy.build_legacy`,
`shutil.copytree`). Spans nest: a `_build_template` span contains its `install.sh`
subprocess span. Processes started by those subprocesses are not seen.

tests/tools/measure_suite.py reads the files.
"""

from __future__ import annotations

import functools
import importlib
import json
import os
import re
import shlex
import subprocess
import time
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

import pytest

SHELL_OPS = {"&&", "||", ";", "|", "&"}
SHELLS = {"bash", "sh", "zsh", "dash"}
DRY_RUN = {"-n", "--dry-run", "--just-print", "--recon"}
DRY_CLUSTER = re.compile(r"-[BbeiknLpqRrSstw]*n[BbeiknLpqRrSstw]*")
HELPERS = (
    ("i0_projects", "_build_template"),
    ("i0_projects", "install_project"),
    ("i0_legacy", "build_legacy"),
    ("shutil", "copytree"),
)


# ── classification ───────────────────────────────────────────────────────────


def first_argv(cmd: object) -> list[str]:
    """The first command a Popen argument runs (a shell string: up to its first operator)."""
    if isinstance(cmd, str | bytes):
        try:
            words = shlex.split(os.fsdecode(cmd).replace(";", " ; "))
        except ValueError:
            return []
        out: list[str] = []
        for word in words:
            if word in SHELL_OPS:
                break
            out.append(word)
        return out
    if isinstance(cmd, os.PathLike):
        return [os.fsdecode(cmd)]
    if isinstance(cmd, list | tuple):
        return [os.fsdecode(a) if isinstance(a, bytes | os.PathLike) else str(a) for a in cmd]
    return []


def _is_make_check(args: list[str]) -> bool:
    if "check" not in args:
        return False
    flags = [a for a in args if a.startswith("-")]
    return not any(f in DRY_RUN or DRY_CLUSTER.fullmatch(f) for f in flags)


def _classify_script(argv: list[str]) -> str | None:
    if any(Path(a).name == "install.sh" for a in argv):
        return "install.sh"
    name = Path(argv[0]).name
    if name.endswith(".sh"):
        return "harness script (.sh)"
    if name in SHELLS and len(argv) > 1:
        return "harness script (.sh)" if argv[1].endswith(".sh") else "shell (other)"
    return None


def classify(cmd: object) -> str:
    """A coarse category for a subprocess command line."""
    argv = first_argv(cmd)
    if not argv:
        return "other"
    script = _classify_script(argv)
    if script:
        return script
    name = Path(argv[0]).name
    by_tool = {
        "git": "git",
        "uv": "uv sync" if argv[1:2] == ["sync"] else "uv (other)",
        "make": "make check" if _is_make_check(argv[1:]) else "make (other)",
    }
    if name in by_tool:
        return by_tool[name]
    return "python" if name.startswith("python") else "other"


# ── recording ────────────────────────────────────────────────────────────────


def _worker_id() -> str:
    return os.environ.get("PYTEST_XDIST_WORKER", "main")


class Recorder:
    """The per-process recorder, registered as a plugin when --timing-dir is set."""

    def __init__(self, out_dir: Path, spans: bool) -> None:
        self.out_dir = out_dir
        self.spans_enabled = spans
        self.worker = _worker_id()
        self.current: str | None = None
        self.tests: dict[str, dict[str, Any]] = {}
        self.fixtures: list[dict[str, Any]] = []
        self.spans: list[dict[str, Any]] = []
        self._restore: list[Callable[[], None]] = []

    # tests ------------------------------------------------------------------

    def pytest_runtest_logstart(self, nodeid: str) -> None:
        self.current = nodeid

    def pytest_runtest_logfinish(self, nodeid: str) -> None:
        self.current = None

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        rec = self.tests.setdefault(
            report.nodeid,
            {"nodeid": report.nodeid, "worker": self.worker, "outcome": "passed"},
        )
        rec[report.when] = rec.get(report.when, 0.0) + report.duration
        rec["outcome"] = _outcome(rec["outcome"], report)
        if report.skipped and "reason" not in rec:
            rec["reason"] = _skip_reason(report)

    @pytest.hookimpl(wrapper=True)
    def pytest_fixture_setup(
        self, fixturedef: pytest.FixtureDef[Any], request: pytest.FixtureRequest
    ) -> Generator[None, object, object]:
        start = time.perf_counter()
        try:
            return (yield)
        finally:
            self.fixtures.append(
                {
                    "name": fixturedef.argname,
                    "scope": fixturedef.scope,
                    "nodeid": self.current,
                    "seconds": time.perf_counter() - start,
                    "worker": self.worker,
                }
            )

    # spans ------------------------------------------------------------------

    def span(self, category: str, seconds: float, nodeid: str | None, detail: str = "") -> None:
        self.spans.append(
            {
                "category": category,
                "nodeid": nodeid,
                "seconds": seconds,
                "worker": self.worker,
                "detail": detail,
            }
        )

    def pytest_sessionstart(self, session: pytest.Session) -> None:
        if self.spans_enabled:
            self._patch_popen()
            self._patch_helpers()

    def _patch_popen(self) -> None:
        rec = self
        orig_init = subprocess.Popen.__init__
        orig_wait = subprocess.Popen.wait

        def init(proc: Any, args: Any, *rest: Any, **kwargs: Any) -> None:
            orig_init(proc, args, *rest, **kwargs)
            argv = " ".join(first_argv(args))[:200]
            proc._suite_timing = (time.perf_counter(), classify(args), rec.current, argv)

        def wait(proc: Any, timeout: float | None = None) -> int:
            try:
                return orig_wait(proc, timeout)
            finally:
                info = proc.__dict__.get("_suite_timing")
                if info and proc.returncode is not None:
                    del proc._suite_timing
                    start, category, nodeid, argv = info
                    rec.span(category, time.perf_counter() - start, nodeid, argv)

        subprocess.Popen.__init__ = init  # type: ignore[method-assign,assignment]
        subprocess.Popen.wait = wait  # type: ignore[method-assign,assignment]

        def restore() -> None:
            subprocess.Popen.__init__ = orig_init  # type: ignore[method-assign]
            subprocess.Popen.wait = orig_wait  # type: ignore[method-assign]

        self._restore.append(restore)

    def _patch_helpers(self) -> None:
        for module_name, attr in HELPERS:
            try:
                module = importlib.import_module(module_name)
            except ImportError:
                continue
            orig = getattr(module, attr, None)
            if callable(orig):
                setattr(module, attr, self._timed(orig, attr))
                self._restore.append(functools.partial(setattr, module, attr, orig))

    def _timed(self, func: Callable[..., Any], category: str) -> Callable[..., Any]:
        depth = [0]  # copytree recurses through its own module global: time the outermost

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            depth[0] += 1
            start = time.perf_counter()
            try:
                return func(*args, **kwargs)
            finally:
                depth[0] -= 1
                if depth[0] == 0:
                    self.span(category, time.perf_counter() - start, self.current)

        return wrapper

    # output -----------------------------------------------------------------

    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        for restore in reversed(self._restore):
            restore()
        self._restore.clear()
        self.out_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "worker": self.worker,
            "tests": [{**t, "outcome": final_outcome(t)} for t in self.tests.values()],
            "fixtures": self.fixtures,
            "spans": self.spans,
        }
        path = self.out_dir / f"worker-{self.worker}.json"
        path.write_text(json.dumps(data), encoding="utf-8")


def final_outcome(rec: dict[str, Any]) -> str:
    """A record counts as passed or skipped only with its whole phase sequence: a run
    cut short after setup (or before teardown) is "incomplete", never green."""
    outcome = str(rec.get("outcome", "passed"))
    if outcome in ("failed", "error"):
        return outcome
    if "teardown" not in rec or (outcome == "passed" and "call" not in rec):
        return "incomplete"
    return outcome


def _skip_reason(report: pytest.TestReport) -> str:
    """'Skipped: <reason>' for a skip, 'xfail: <reason>' for an expected failure."""
    if hasattr(report, "wasxfail"):
        return f"xfail: {report.wasxfail}"
    longrepr = report.longrepr
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        return str(longrepr[2])
    return str(longrepr or "")


def _outcome(previous: str, report: pytest.TestReport) -> str:
    """passed < skipped < failed < error: setup/teardown failures are errors."""
    if report.failed:
        return "failed" if report.when == "call" and previous != "error" else "error"
    if report.skipped and previous == "passed":
        return "skipped"
    return previous


# ── plugin entry points ──────────────────────────────────────────────────────


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("suite_timing", "suite timing records (#117)")
    group.addoption("--timing-dir", default=None, help="write per-worker timing JSON here")
    group.addoption(
        "--timing-subprocess",
        action="store_true",
        default=False,
        help="also time subprocesses and the installed-project helpers",
    )


def _is_xdist_controller(config: pytest.Config) -> bool:
    return bool(getattr(config.option, "numprocesses", None)) and not hasattr(config, "workerinput")


def pytest_configure(config: pytest.Config) -> None:
    out = config.getoption("timing_dir")
    if not out or _is_xdist_controller(config):
        return
    recorder = Recorder(Path(out).resolve(), bool(config.getoption("timing_subprocess")))
    config.pluginmanager.register(recorder, "suite_timing_recorder")
