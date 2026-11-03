"""C0.5 with the real Docker Engine: `compat run` end to end on a throwaway project.

Two fake toolkits: A (baseline, the one the project links to) and B (candidate). The
project's `make check` runs `harness/check.sh`, so each side shows which toolkit it saw.
Marked `docker`; skip/fail rules as in test_compat_isolation.py.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from conftest import TOOLKIT

pytestmark = pytest.mark.docker

RUNNER = TOOLKIT / "compat" / "_compat.py"
IMAGE = "harness-compat:local"
SOCKET = "unix:///var/run/docker.sock"


def _require(reason: str) -> None:
    if os.environ.get("HARNESS_COMPAT_REQUIRE_DOCKER") == "1":
        pytest.fail(f"docker test prerequisite missing: {reason}")
    pytest.skip(reason)


def _compat(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RUNNER), *args], text=True, capture_output=True, check=False
    )


@pytest.fixture(scope="module")
def config(tmp_path_factory: pytest.TempPathFactory) -> Path:
    cfg = tmp_path_factory.mktemp("docker-config")
    res = _compat("preflight", "--image", IMAGE, "--docker-config", str(cfg))
    if res.returncode != 0:
        _require(f"Docker Engine or image {IMAGE} not ready: {res.stderr.strip()}")
    return cfg


def _toolkit(root: Path, check_sh: str) -> Path:
    for rel in ("lib", "agents", "codex", "skills"):
        (root / rel).mkdir(parents=True)
    (root / "settings.json").write_text("{}")
    (root / "lib" / "check.sh").write_text(check_sh)
    return root


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _project(tmp_path: Path, toolkit: Path) -> Path:
    repo = tmp_path / "proj"
    (repo / ".claude").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "dev@example.com")
    _git(repo, "config", "user.name", "dev")
    (repo / "harness").symlink_to(toolkit / "lib")
    for name in ("agents", "codex", "skills"):
        (repo / ".claude" / name).symlink_to(toolkit / name)
    (repo / ".claude" / "settings.json").symlink_to(toolkit / "settings.json")
    (repo / "Makefile").write_text("check:\n\t@bash harness/check.sh\n")
    (repo / ".gitignore").write_text("harness\n.claude/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    return repo


def _tree(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                out[rel] = "link:" + os.readlink(path)
            elif path.is_file():
                out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _setup(tmp_path: Path, candidate_check: str) -> dict[str, Path]:
    a = _toolkit(tmp_path / "A", "echo baseline-toolkit\n")
    b = _toolkit(tmp_path / "B", candidate_check)
    return {"A": a, "B": b, "proj": _project(tmp_path, a), "scratch": tmp_path / "scratch"}


def _compat_run(env: dict[str, Path], config: Path) -> tuple[int, dict[str, Any]]:
    res = _compat(
        "run", "--project", str(env["proj"]),
        "--baseline", str(env["A"]), "--candidate", str(env["B"]), "--origin", str(env["A"]),
        "--image", IMAGE, "--docker-config", str(config), "--scratch", str(env["scratch"]),
    )  # fmt: skip
    assert res.stdout, res.stderr
    report: dict[str, Any] = json.loads(res.stdout)
    return res.returncode, report


def _leftovers(run_id: str) -> list[str]:
    out = []
    for kind in (["ps", "-aq"], ["volume", "ls", "-q"]):
        res = subprocess.run(
            ["docker", "--host", SOCKET, *kind, "--filter", f"label=harness-compat.run={run_id}"],
            text=True,
            capture_output=True,
            check=True,
        )
        out += res.stdout.split()
    return out


def test_identical_toolkits_give_same_and_leave_nothing(tmp_path: Path, config: Path) -> None:
    env = _setup(tmp_path, "echo baseline-toolkit\n")
    rc, report = _compat_run(env, config)
    assert rc == 0, report
    proj = report["projects"][str(env["proj"])]
    assert proj["verdict"] == "same"
    assert report["kept"] == []
    assert not any(env["scratch"].glob(f"{report['run_id']}*/**/Makefile"))
    assert _leftovers(report["run_id"]) == []


def test_each_side_runs_against_its_own_toolkit(tmp_path: Path, config: Path) -> None:
    env = _setup(tmp_path, "echo candidate-toolkit\n")
    _, report = _compat_run(env, config)
    proj = report["projects"][str(env["proj"])]
    assert "baseline-toolkit" in proj["baseline"]["output"]
    assert "candidate-toolkit" in proj["candidate"]["output"]


def test_a_breaking_candidate_is_a_regression_and_copies_are_kept(
    tmp_path: Path, config: Path
) -> None:
    env = _setup(tmp_path, "echo '[FAIL] broken by candidate'; exit 1\n")
    rc, report = _compat_run(env, config)
    assert rc == 1
    assert report["regressions"] == [str(env["proj"])]
    assert report["kept"], "copies of a failed run must be kept for the report"
    assert all(Path(p).is_dir() for p in report["kept"])
    assert _leftovers(report["run_id"]) == []


def test_new_warnings_are_reported_without_failing(tmp_path: Path, config: Path) -> None:
    env = _setup(tmp_path, "echo baseline-toolkit; echo 'WARN: legacy seal missing'\n")
    rc, report = _compat_run(env, config)
    assert rc == 0
    proj = report["projects"][str(env["proj"])]
    assert proj["new_warnings"] == ["WARN: legacy seal missing"]


def test_original_project_and_toolkits_are_untouched(tmp_path: Path, config: Path) -> None:
    env = _setup(tmp_path, "echo '[FAIL] x'; exit 1\n")
    before = {k: _tree(env[k]) for k in ("A", "B", "proj")}
    _compat_run(env, config)
    assert {k: _tree(env[k]) for k in ("A", "B", "proj")} == before


def test_both_sides_see_the_same_state_even_if_the_original_changes_mid_run(
    tmp_path: Path, config: Path
) -> None:
    """Another session may edit the real project during a run (seen in C0.6): both sides
    must come from ONE point-in-time snapshot, so the edit cannot create a fake delta."""
    a = _toolkit(tmp_path / "A", "sleep 4; echo baseline-done\n")
    b = _toolkit(tmp_path / "B", "echo candidate-done\n")
    proj = _project(tmp_path, a)
    (proj / "state.txt").write_text("v1\n")
    (proj / "Makefile").write_text("check:\n\t@cat state.txt\n\t@bash harness/check.sh\n")
    _git(proj, "add", "-A")
    _git(proj, "commit", "-qm", "state")
    scratch = tmp_path / "scratch"
    proc = subprocess.Popen(
        [
            sys.executable, str(RUNNER), "run", "--project", str(proj),
            "--baseline", str(a), "--candidate", str(b), "--origin", str(a),
            "--image", IMAGE, "--docker-config", str(config), "--scratch", str(scratch),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )  # fmt: skip
    deadline = time.monotonic() + 60
    while not list(scratch.glob("*/*-0b")) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert list(scratch.glob("*/*-0b")), "baseline side never started"
    (proj / "state.txt").write_text("v2 edited by another session\n")
    stdout, stderr = proc.communicate(timeout=300)
    report: dict[str, Any] = json.loads(stdout)
    side = report["projects"][str(proj)]
    assert "v1" in side["baseline"]["output"]
    assert "v1" in side["candidate"]["output"], side["candidate"]["output"]
    assert "v2" not in side["candidate"]["output"]


def test_an_early_fail_line_is_never_lost_to_output_truncation(
    tmp_path: Path, config: Path
) -> None:
    """Only the stored output is truncated; WARN/FAIL lines come from the full output."""
    noisy = "echo '[FAIL] early failure'; head -c 60000 /dev/zero | tr '\\0' 'x'; echo\n"
    env = _setup(tmp_path, noisy)
    rc, report = _compat_run(env, config)
    proj = report["projects"][str(env["proj"])]
    assert proj["new_failures"] == ["[FAIL] early failure"]
    assert proj["verdict"] == "regression"
    assert rc == 1


def test_a_compat_failure_is_an_error_and_keeps_the_copies(tmp_path: Path, config: Path) -> None:
    env = _setup(tmp_path, "echo baseline-toolkit\n")
    (env["proj"] / "harness").unlink()  # relink fails on both sides: no make check runs
    rc, report = _compat_run(env, config)
    assert report["projects"][str(env["proj"])]["verdict"] == "error"
    assert report["errors"] == [str(env["proj"])]
    assert report["kept"]
    assert rc == 1


def test_run_never_touches_a_foreign_resource_carrying_the_session_label(
    tmp_path: Path, config: Path
) -> None:
    """A resource already carrying the session label makes the run refuse to start, and it
    is never removed."""
    env = _setup(tmp_path, "echo baseline-toolkit\n")
    session = "cfixed" + os.urandom(3).hex()
    foreign = f"foreign-{session}"
    subprocess.run(
        ["docker", "--host", SOCKET, "volume", "create",
         "--label", "harness-compat=1", "--label", f"harness-compat.run={session}", foreign],
        capture_output=True, check=True,
    )  # fmt: skip
    try:
        res = _compat(
            "run", "--project", str(env["proj"]), "--run-id", session,
            "--baseline", str(env["A"]), "--candidate", str(env["B"]), "--origin", str(env["A"]),
            "--image", IMAGE, "--docker-config", str(config), "--scratch", str(env["scratch"]),
        )  # fmt: skip
        # The id is in use (a resource carries its label): the run refuses to start.
        assert res.returncode != 0
        assert "in use" in res.stderr.lower()
        still = subprocess.run(
            ["docker", "--host", SOCKET, "volume", "inspect", foreign],
            capture_output=True, check=False,
        )  # fmt: skip
        assert still.returncode == 0, "a foreign volume was removed by the run's cleanup"
    finally:
        subprocess.run(
            ["docker", "--host", SOCKET, "volume", "rm", "-f", foreign],
            capture_output=True, check=False,
        )  # fmt: skip
