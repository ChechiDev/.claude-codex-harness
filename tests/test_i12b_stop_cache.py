"""I12b-key: the Stop cache must not outlive a change in the toolkit.

The project is installed from a throwaway COPY of the toolkit, so changing "the
toolkit" here never touches the real checkout. A toolkit change that turns
`make check` red is simulated by making the copy's gate.sh fail.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import TOOLKIT
from i0_projects import Installed, install_project

ALREADY_GREEN = "make check already green for this exact tree"
SKIP = shutil.ignore_patterns(".git", ".venv", "__pycache__", "*_cache", "scratchpad")
RED_GATE = '#!/usr/bin/env bash\necho "[FAIL]  gate broken by a toolkit update"\nexit 1\n'


def _toolkit_copy(tmp: Path) -> Path:
    copy = tmp / "toolkit"
    shutil.copytree(TOOLKIT, copy, symlinks=True, ignore=SKIP)
    return copy


def _stop(project: Installed) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "harness/stop_gate.sh"],
        input='{"stop_hook_active": false}',
        cwd=project.root,
        env={**project.env, "CLAUDE_PROJECT_DIR": str(project.root)},
        text=True,
        capture_output=True,
        check=False,
        timeout=900,
    )


def _cache(project: Installed) -> Path:
    rel = project.run("git", "rev-parse", "--git-path", "harness-check.ok").stdout.strip()
    return project.root / rel


@pytest.fixture
def setup(tmp_path: Path) -> tuple[Path, Installed]:
    toolkit = _toolkit_copy(tmp_path)
    project = install_project(tmp_path, "python", toolkit=toolkit)
    first = _stop(project)
    assert first.returncode == 0, first.stdout + first.stderr
    assert _cache(project).is_file(), "a green Stop writes its cache"
    return toolkit, project


def test_the_project_runs_the_toolkit_copy(setup: tuple[Path, Installed]) -> None:
    toolkit, project = setup
    assert (project.root / "harness").resolve() == toolkit / "lib"


def test_an_unchanged_toolkit_keeps_the_cache(setup: tuple[Path, Installed]) -> None:
    _, project = setup
    res = _stop(project)
    assert res.returncode == 0
    assert ALREADY_GREEN in res.stdout


def test_a_toolkit_change_invalidates_the_cache(setup: tuple[Path, Installed]) -> None:
    """The harness changed (check now red): the Stop runs make check again and blocks."""
    toolkit, project = setup
    (toolkit / "lib/gate.sh").write_text(RED_GATE)
    res = _stop(project)
    assert res.returncode == 2, res.stdout + res.stderr
    assert ALREADY_GREEN not in res.stdout
    assert "gate broken by a toolkit update" in res.stderr


def _fingerprint(project: Installed) -> subprocess.CompletedProcess[str]:
    return project.run("python3", "harness/_guard.py", "fingerprint")


def test_the_fingerprint_is_stable_while_nothing_changes(setup: tuple[Path, Installed]) -> None:
    _, project = setup
    first, second = _fingerprint(project), _fingerprint(project)
    assert first.returncode == 0 and first.stdout.strip()
    assert first.stdout == second.stdout
    assert _cache(project).read_text() == first.stdout.strip()


@pytest.mark.parametrize(
    "change",
    ["edit lib", "add to lib", "remove from lib", "edit settings"],
)
def test_any_toolkit_change_changes_the_fingerprint(
    setup: tuple[Path, Installed], change: str
) -> None:
    toolkit, project = setup
    before = _fingerprint(project).stdout
    if change == "edit lib":
        (toolkit / "lib/doc_gate.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
    elif change == "add to lib":
        (toolkit / "lib/new_helper.py").write_text("X = 1\n")
    elif change == "remove from lib":
        (toolkit / "lib/issue_template.md").unlink()
    else:
        (toolkit / "settings.json").write_text("{}\n")
    after = _fingerprint(project)
    assert after.returncode == 0
    assert after.stdout != before, change


def test_python_bytecode_in_the_toolkit_does_not_change_it(
    setup: tuple[Path, Installed],
) -> None:
    """Running the harness writes __pycache__ into lib/: that must not void the cache."""
    toolkit, project = setup
    before = _fingerprint(project).stdout
    cache_dir = toolkit / "lib/__pycache__"
    cache_dir.mkdir(exist_ok=True)
    (cache_dir / "extra.cpython-312.pyc").write_bytes(b"\0")
    assert _fingerprint(project).stdout == before


def test_a_broken_settings_link_means_no_cache(setup: tuple[Path, Installed]) -> None:
    """The toolkit part cannot be computed: no fingerprint, full check, no cache write."""
    _, project = setup
    link = project.root / ".claude/settings.json"
    link.unlink()
    link.symlink_to(project.root / "missing-settings.json")
    res = _fingerprint(project)
    assert res.returncode != 0 and not res.stdout.strip()
    _cache(project).unlink()
    stop = _stop(project)
    assert stop.returncode == 0, stop.stdout + stop.stderr
    assert ALREADY_GREEN not in stop.stdout
    assert not _cache(project).exists(), "no cache without a fingerprint"


def test_control_without_the_cache_the_changed_toolkit_is_red(
    setup: tuple[Path, Installed],
) -> None:
    """Control: the same toolkit change, no cache -> the Stop blocks."""
    toolkit, project = setup
    (toolkit / "lib/gate.sh").write_text(RED_GATE)
    _cache(project).unlink()
    res = _stop(project)
    assert res.returncode == 2, res.stdout + res.stderr
    assert "gate broken by a toolkit update" in res.stderr
