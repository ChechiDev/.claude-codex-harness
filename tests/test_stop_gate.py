"""stop_gate.sh (#55): advisory only. It compares the tree with the last green full
check record and warns; it never blocks and never runs `make check`."""

from __future__ import annotations

import subprocess

from conftest import Project

NO_GREEN = "no green full check for this state"
MATCHES = "last green full check matches this exact state"

# Fake `make check`: counts its runs and fails when the FAIL marker file exists.
MAKEFILE = (
    "check:\n\t@echo run >> .check_runs\n\t@if [ -f FAIL ]; then echo 'check is red'; exit 1; fi\n"
)


def _setup(project: Project) -> None:
    project.write("Makefile", MAKEFILE)
    project.write(".gitignore", "harness\n.claude/\n.check_runs\n")
    project.commit_all("makefile")


def _runs(project: Project) -> int:
    path = project.root / ".check_runs"
    return len(path.read_text().splitlines()) if path.exists() else 0


def _record(project: Project) -> str:
    """Write the green record the way gates/close do (fingerprint of the tree)."""
    fp = subprocess.run(
        ["python3", "harness/_guard.py", "fingerprint"],
        cwd=project.root, text=True, capture_output=True, check=True,
    ).stdout.strip()  # fmt: skip
    assert fp
    (project.root / ".git" / "harness-check.ok").write_text(fp)
    return fp


def _stop(project: Project, active: bool = False) -> subprocess.CompletedProcess[str]:
    return project.run("stop_gate.sh", {"hook_event_name": "Stop", "stop_hook_active": active})


def test_without_a_record_it_warns_and_allows_the_stop(project: Project) -> None:
    _setup(project)
    res = _stop(project)
    assert res.returncode == 0
    assert NO_GREEN in res.stdout + res.stderr
    assert _runs(project) == 0


def test_a_red_tree_never_blocks_nor_runs_check(project: Project) -> None:
    _setup(project)
    project.write("FAIL", "")
    for active in (False, True):
        assert _stop(project, active).returncode == 0
    assert _runs(project) == 0


def test_a_matching_record_is_reported(project: Project) -> None:
    _setup(project)
    _record(project)
    res = _stop(project)
    assert res.returncode == 0
    assert MATCHES in res.stdout
    assert NO_GREEN not in res.stdout + res.stderr


def test_any_change_makes_the_record_stale(project: Project) -> None:
    _setup(project)
    _record(project)
    project.write("src/pkg/domain/user.py", "x = 1\n")
    res = _stop(project)
    assert res.returncode == 0
    assert NO_GREEN in res.stdout + res.stderr
    assert MATCHES not in res.stdout


def test_the_stop_never_writes_the_record(project: Project) -> None:
    _setup(project)
    record = project.root / ".git" / "harness-check.ok"
    _stop(project)
    assert not record.exists()
    record.write_text("stale")
    _stop(project)
    assert record.read_text() == "stale"
