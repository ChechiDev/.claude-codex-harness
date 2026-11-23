"""Suite measurement and the e2e shard aggregate (#117).

Reads the per-worker JSON that tests/tools/suite_timing.py writes. Subcommands:

  run           run pytest with the timing plugin (optionally under a lock, timing the
                wait) and write meta.json next to the records
  report        a Markdown report over one or more run directories
  durations     write the pytest-split durations file (nodeid -> seconds)
  split-compare simulate pytest-split's algorithms over a durations file
  check-shards  the CI aggregate: every shard present, non-empty, green, and the union
                of the shards equal to the full collection, without overlap

Examples (from the toolkit root):

  uv run python tests/tools/measure_suite.py run --label e2e-n8 --out /tmp/m/e2e-n8 \\
      --lock ~/.claude-codex-harness-wt/_orchestrator/.suite.lock -- \\
      -o addopts= -q -m e2e -n 8
  uv run python tests/tools/measure_suite.py report /tmp/m/* > report.md
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parents[1]
SPLIT_ALGORITHMS = ("duration_based_chunks", "least_duration")


# ── records ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TestRec:
    nodeid: str
    worker: str
    outcome: str
    setup: float = 0.0
    call: float = 0.0
    teardown: float = 0.0
    reason: str = ""  # why it was skipped (or xfailed)

    __test__ = False  # not a pytest test class

    @property
    def total(self) -> float:
        return self.setup + self.call + self.teardown

    @property
    def file(self) -> str:
        return self.nodeid.split("::", 1)[0]


@dataclass(frozen=True)
class FixtureRec:
    name: str
    scope: str
    nodeid: str | None
    seconds: float
    worker: str


@dataclass(frozen=True)
class SpanRec:
    category: str
    nodeid: str | None
    seconds: float
    worker: str
    detail: str = ""


@dataclass
class Run:
    label: str
    meta: dict[str, Any] = field(default_factory=dict)
    tests: list[TestRec] = field(default_factory=list)
    fixtures: list[FixtureRec] = field(default_factory=list)
    spans: list[SpanRec] = field(default_factory=list)

    def test(self, nodeid: str) -> TestRec:
        return next(t for t in self.tests if t.nodeid == nodeid)


def _test_rec(raw: dict[str, Any]) -> TestRec:
    return TestRec(
        nodeid=str(raw["nodeid"]),
        worker=str(raw.get("worker", "main")),
        outcome=str(raw.get("outcome", "passed")),
        setup=float(raw.get("setup", 0.0)),
        call=float(raw.get("call", 0.0)),
        teardown=float(raw.get("teardown", 0.0)),
        reason=str(raw.get("reason", "")),
    )


def load_run(directory: Path) -> Run:
    """All worker-*.json in `directory`, plus its meta.json when present."""
    meta_path = directory / "meta.json"
    meta: dict[str, Any] = json.loads(meta_path.read_text()) if meta_path.is_file() else {}
    run = Run(label=str(meta.get("label", directory.name)), meta=meta)
    for path in sorted(directory.glob("worker-*.json")):
        data = json.loads(path.read_text())
        run.tests.extend(_test_rec(t) for t in data.get("tests", []))
        run.fixtures.extend(FixtureRec(**f) for f in data.get("fixtures", []))
        run.spans.extend(SpanRec(**s) for s in data.get("spans", []))
    return run


# ── analysis ─────────────────────────────────────────────────────────────────


def durations_map(tests: Iterable[TestRec]) -> dict[str, float]:
    """pytest-split's durations format: nodeid -> setup + call + teardown seconds."""
    return {t.nodeid: round(t.total, 3) for t in tests}


def by_file(tests: Iterable[TestRec]) -> list[tuple[str, int, float, float, float, float]]:
    """(file, tests, setup, call, teardown, total), slowest file first."""
    acc: dict[str, list[float]] = defaultdict(lambda: [0, 0.0, 0.0, 0.0])
    for t in tests:
        row = acc[t.file]
        row[0] += 1
        row[1] += t.setup
        row[2] += t.call
        row[3] += t.teardown
    rows = [(f, int(r[0]), r[1], r[2], r[3], r[1] + r[2] + r[3]) for f, r in acc.items()]
    return sorted(rows, key=lambda r: r[5], reverse=True)


def _by_key(pairs: Iterable[tuple[str, float]]) -> list[tuple[str, int, float, float]]:
    """(key, count, total, max), largest total first."""
    acc: dict[str, list[float]] = defaultdict(lambda: [0, 0.0, 0.0])
    for key, seconds in pairs:
        row = acc[key]
        row[0] += 1
        row[1] += seconds
        row[2] = max(row[2], seconds)
    rows = [(k, int(r[0]), r[1], r[2]) for k, r in acc.items()]
    return sorted(rows, key=lambda r: r[2], reverse=True)


@dataclass(frozen=True)
class _Item:
    nodeid: str


def simulate_split(
    nodeids: Sequence[str], durations: dict[str, float], splits: int, algorithm: str
) -> list[list[str]]:
    """The groups pytest-split would select (same code, without running pytest)."""
    from pytest_split.algorithms import Algorithms

    algo = Algorithms[algorithm].value
    groups = algo(splits, [_Item(n) for n in nodeids], durations)
    return [[item.nodeid for item in g.selected] for g in groups]


# ── report ───────────────────────────────────────────────────────────────────


def _fmt(seconds: float) -> str:
    if seconds >= 60:
        return f"{int(seconds // 60)}m{seconds % 60:04.1f}s"
    return f"{seconds:.2f}s"


def _outcomes(tests: Iterable[TestRec]) -> str:
    counts: dict[str, int] = defaultdict(int)
    for t in tests:
        counts[t.outcome] += 1
    return ", ".join(f"{n} {k}" for k, n in sorted(counts.items()))


def _runs_table(runs: Sequence[Run]) -> list[str]:
    lines = [
        "| run | workers | tests | outcomes | wall | lock wait | worker-s | setup | call |"
        " teardown | busy/(wall*workers) |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for run in runs:
        wall = float(run.meta.get("wall", 0.0))
        workers = int(run.meta.get("workers", 1) or 1)
        busy = sum(t.total for t in run.tests)
        eff = f"{busy / (wall * workers):.0%}" if wall else "-"
        lines.append(
            f"| {run.label} | {workers} | {len(run.tests)} | {_outcomes(run.tests)} "
            f"| {_fmt(wall)} | {_fmt(float(run.meta.get('lock_wait', 0.0)))} "
            f"| {_fmt(busy)} | {_fmt(sum(t.setup for t in run.tests))} "
            f"| {_fmt(sum(t.call for t in run.tests))} "
            f"| {_fmt(sum(t.teardown for t in run.tests))} | {eff} |"
        )
    return lines


def _run_section(run: Run, top: int) -> list[str]:
    lines = [f"## {run.label}", "", f"### Slowest tests (top {top})", ""]
    lines += ["| test | outcome | setup | call | teardown | total |", "|---|---|---|---|---|---|"]
    for t in sorted(run.tests, key=lambda t: t.total, reverse=True)[:top]:
        lines.append(
            f"| `{t.nodeid}` | {t.outcome} | {_fmt(t.setup)} | {_fmt(t.call)} "
            f"| {_fmt(t.teardown)} | {_fmt(t.total)} |"
        )
    lines += ["", f"### Slowest files (top {top})", ""]
    lines += ["| file | tests | setup | call | teardown | total |", "|---|---|---|---|---|---|"]
    for f, n, s, c, td, tot in by_file(run.tests)[:top]:
        lines.append(f"| `{f}` | {n} | {_fmt(s)} | {_fmt(c)} | {_fmt(td)} | {_fmt(tot)} |")
    lines += ["", f"### Fixtures (setup time, top {top})", ""]
    lines += ["| fixture | setups | total | max |", "|---|---|---|---|"]
    fixtures = ((f"{f.name} ({f.scope})", f.seconds) for f in run.fixtures)
    for key, n, tot, mx in _by_key(fixtures)[:top]:
        lines.append(f"| `{key}` | {n} | {_fmt(tot)} | {_fmt(mx)} |")
    lines += ["", "### Subprocesses and helpers (spans nest; seen from the test process)", ""]
    lines += ["| category | count | total | mean | max |", "|---|---|---|---|---|"]
    for key, n, tot, mx in _by_key((s.category, s.seconds) for s in run.spans):
        lines.append(f"| {key} | {n} | {_fmt(tot)} | {_fmt(tot / n)} | {_fmt(mx)} |")
    lines += ["", "### Busy time per worker", "", "| worker | tests | busy |", "|---|---|---|"]
    per_worker = _by_key((t.worker, t.total) for t in run.tests)
    for key, n, tot, _ in sorted(per_worker):
        lines.append(f"| {key} | {n} | {_fmt(tot)} |")
    return [*lines, ""]


def render_report(runs: Sequence[Run], top: int = 20) -> str:
    lines = ["# Suite timing report", "", *_runs_table(runs), ""]
    for run in runs:
        lines += _run_section(run, top)
    return "\n".join(lines) + "\n"


# ── shard aggregate ──────────────────────────────────────────────────────────


def _skip_allowed(test: TestRec, allowed_skips: Sequence[str]) -> bool:
    return any(re.search(p, f"{test.nodeid}: {test.reason}") for p in allowed_skips)


def _test_error(test: TestRec, allowed_skips: Sequence[str]) -> str | None:
    if test.outcome == "passed":
        return None
    if test.outcome == "skipped":
        if _skip_allowed(test, allowed_skips):
            return None
        return f"{test.nodeid} skipped, not an allowed skip ({test.reason})"
    return f"{test.nodeid} {test.outcome}"


def _shard_errors(shard: int, tests: list[TestRec] | None, allowed: Sequence[str]) -> list[str]:
    if tests is None:
        return [f"shard {shard}: missing (no timing records uploaded)"]
    if not tests:
        return [f"shard {shard}: ran no tests"]
    errors = [f"shard {shard}: {e}" for t in tests if (e := _test_error(t, allowed))]
    if all(t.outcome == "skipped" for t in tests):
        errors.append(f"shard {shard}: every test skipped")
    return errors


def check_shards(
    shards: dict[int, list[TestRec]],
    collected: Sequence[str],
    expected: int,
    allowed_skips: Sequence[str] = (),
) -> list[str]:
    """Every reason the sharded run must not count as a full green e2e run. A skip is
    a test that did not run: red unless "<nodeid>: <reason>" matches an allowed regex."""
    if not collected:
        return ["the full collection is empty"]
    errors: list[str] = []
    for shard in range(1, expected + 1):
        errors += _shard_errors(shard, shards.get(shard), allowed_skips)
    errors += [f"unexpected shard {s}" for s in sorted(shards) if not 1 <= s <= expected]
    seen: dict[str, list[int]] = defaultdict(list)
    for shard, tests in shards.items():
        for t in tests:
            seen[t.nodeid].append(shard)
    full = set(collected)
    errors += [f"{n}: not run by any shard" for n in collected if n not in seen]
    errors += [f"{n}: ran in more than one shard {s}" for n, s in seen.items() if len(s) > 1]
    errors += [f"{n}: not in the collection" for n in seen if n not in full]
    return errors


def parse_collected(text: str) -> list[str]:
    """Node ids from `pytest --collect-only -q` output."""
    return [line.strip() for line in text.splitlines() if "::" in line and line[:1].strip()]


def load_shards(root: Path) -> dict[int, list[TestRec]]:
    """Shard number (the trailing integer of each subdirectory name) -> its records."""
    shards: dict[int, list[TestRec]] = {}
    for sub in sorted(p for p in root.iterdir() if p.is_dir()):
        match = re.search(r"(\d+)$", sub.name)
        if match:
            shards[int(match.group(1))] = load_run(sub).tests
    return shards


# ── commands ─────────────────────────────────────────────────────────────────


def _acquire(lock: Path) -> tuple[int, float]:
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o644)
    start = time.monotonic()
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd, time.monotonic() - start


def _workers(pytest_args: Sequence[str]) -> int:
    for i, arg in enumerate(pytest_args):
        value = pytest_args[i + 1] if arg == "-n" and i + 1 < len(pytest_args) else None
        if arg.startswith("-n") and len(arg) > 2:
            value = arg[2:]
        if value is not None:
            return os.cpu_count() or 1 if value == "auto" else int(value)
    return 1


def pytest_command(out: Path, spans: bool) -> list[str]:
    """pytest with the timing plugin, through the launcher (no exported PYTHONPATH).
    One token per option: pytest takes the separate value of a plugin option for a
    path and roots the run there (no ini, no addopts)."""
    argv = [sys.executable, str(TOOLS / "pytest_timed.py"), f"--timing-dir={out}"]
    return [*argv, "--timing-subprocess"] if spans else argv


def cmd_run(args: argparse.Namespace) -> int:
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    pytest_args = [a for a in args.pytest_args if a != "--"]
    fd, lock_wait = _acquire(Path(args.lock).expanduser()) if args.lock else (-1, 0.0)
    argv = pytest_command(out, spans=not args.no_subprocess)
    start = time.monotonic()
    try:
        res = subprocess.run([*argv, *pytest_args], cwd=ROOT, stdin=subprocess.DEVNULL, check=False)
    finally:
        if fd >= 0:
            os.close(fd)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=False
    ).stdout.strip()
    meta = {
        "label": args.label,
        "pytest_args": pytest_args,
        "workers": _workers(pytest_args),
        "wall": time.monotonic() - start,
        "lock_wait": lock_wait,
        "exit_code": res.returncode,
        "cpu_count": os.cpu_count(),
        "loadavg_end": os.getloadavg(),
        "commit": sha,
        "date": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    return res.returncode


def cmd_report(args: argparse.Namespace) -> int:
    text = render_report([load_run(Path(d)) for d in args.dirs], top=args.top)
    if args.output:
        Path(args.output).write_text(text)
    else:
        sys.stdout.write(text)
    return 0


def cmd_durations(args: argparse.Namespace) -> int:
    merged: dict[str, float] = {}
    for d in args.dirs:
        merged.update(durations_map(load_run(Path(d)).tests))
    Path(args.output).write_text(json.dumps(dict(sorted(merged.items())), indent=1) + "\n")
    return 0


def cmd_split_compare(args: argparse.Namespace) -> int:
    durations: dict[str, float] = json.loads(Path(args.durations).read_text())
    nodeids = parse_collected(Path(args.collected).read_text())
    print(f"{len(nodeids)} tests, {sum(durations.get(n, 0.0) for n in nodeids):.0f}s total")
    for algo in SPLIT_ALGORITHMS:
        groups = simulate_split(nodeids, durations, args.splits, algo)
        sums = [sum(durations.get(n, 0.0) for n in g) for g in groups]
        files = [len({n.split("::")[0] for n in g}) for g in groups]
        print(
            f"{algo}: max {max(sums):.0f}s min {min(sums):.0f}s "
            f"groups {[round(s) for s in sums]} files/group {files}"
        )
    return 0


def cmd_check_shards(args: argparse.Namespace) -> int:
    collected = parse_collected(Path(args.collected).read_text())
    shards = load_shards(Path(args.root))
    errors = check_shards(shards, collected, args.expected, args.allow_skip)
    skipped = sum(t.outcome == "skipped" for tests in shards.values() for t in tests)
    if not errors:
        print(f"{skipped} allowed skip(s)")
    ran = sum(len(t) for t in shards.values())
    print(f"{len(collected)} collected, {ran} run across {len(shards)} shard(s)")
    for err in errors:
        print(f"FAIL {err}")
    print("OK: the shards ran the full e2e collection" if not errors else "e2e aggregate: RED")
    return 1 if errors else 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run pytest with the timing plugin")
    run.add_argument("--label", required=True)
    run.add_argument("--out", required=True)
    run.add_argument("--lock", help="flock this file first and record the wait")
    run.add_argument("--no-subprocess", action="store_true", help="skip subprocess spans")
    run.add_argument("pytest_args", nargs=argparse.REMAINDER)
    run.set_defaults(func=cmd_run)
    rep = sub.add_parser("report", help="Markdown report over run directories")
    rep.add_argument("dirs", nargs="+")
    rep.add_argument("--top", type=int, default=20)
    rep.add_argument("--output")
    rep.set_defaults(func=cmd_report)
    dur = sub.add_parser("durations", help="write the pytest-split durations file")
    dur.add_argument("dirs", nargs="+")
    dur.add_argument("--output", required=True)
    dur.set_defaults(func=cmd_durations)
    cmp_ = sub.add_parser("split-compare", help="simulate pytest-split's algorithms")
    cmp_.add_argument("--durations", required=True)
    cmp_.add_argument("--collected", required=True, help="`pytest --collect-only -q` output")
    cmp_.add_argument("--splits", type=int, default=8)
    cmp_.set_defaults(func=cmd_split_compare)
    chk = sub.add_parser("check-shards", help="CI aggregate over the e2e shards")
    chk.add_argument("--expected", type=int, required=True)
    chk.add_argument("--collected", required=True, help="`pytest --collect-only -q` output")
    chk.add_argument(
        "--allow-skip",
        action="append",
        default=[],
        metavar="REGEX",
        help='a skip this regex finds in "<nodeid>: <reason>" is accepted (repeatable)',
    )
    chk.add_argument("root", help="one subdirectory per shard, named ...<shard number>")
    chk.set_defaults(func=cmd_check_shards)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    code: int = args.func(args)
    return code


if __name__ == "__main__":
    sys.exit(main())
