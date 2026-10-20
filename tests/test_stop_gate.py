"""stop_gate.sh: no infinite Stop loop, and no redundant `make check` runs."""

from __future__ import annotations

from conftest import Project

BLOCKED = 2

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


def _stop(project: Project, active: bool = False) -> int:
    payload = {"hook_event_name": "Stop", "stop_hook_active": active}
    return project.run("stop_gate.sh", payload).returncode


def test_red_check_blocks_the_stop(project: Project) -> None:
    _setup(project)
    project.write("FAIL", "")
    assert _stop(project) == BLOCKED


def test_does_not_block_again_when_stop_hook_already_active(project: Project) -> None:
    _setup(project)
    project.write("FAIL", "")
    res = project.run("stop_gate.sh", {"hook_event_name": "Stop", "stop_hook_active": True})
    assert res.returncode == 0
    assert "still red" in res.stderr


def test_green_check_is_cached_while_the_tree_is_unchanged(project: Project) -> None:
    _setup(project)
    assert _stop(project) == 0
    assert _stop(project) == 0
    assert _runs(project) == 1


def test_any_change_invalidates_the_cache(project: Project) -> None:
    _setup(project)
    assert _stop(project) == 0
    project.write("src/pkg/domain/user.py", "x = 1\n")
    assert _stop(project) == 0
    project.write("tests/acceptance/test_new.py", "untracked\n")
    assert _stop(project) == 0
    assert _runs(project) == 3


def test_red_result_is_never_cached(project: Project) -> None:
    _setup(project)
    project.write("FAIL", "")
    assert _stop(project) == BLOCKED
    assert _stop(project) == BLOCKED
    assert _runs(project) == 2
