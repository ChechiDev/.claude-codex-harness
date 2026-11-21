"""I12b-key: the green full-check record must not outlive a change in the toolkit.

Since #55 the Stop only READS the record (gates and close write it) and warns when
it does not match. The project is installed from a throwaway COPY of the toolkit,
so changing "the toolkit" here never touches the real checkout.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import TOOLKIT
from i0_projects import Installed, install_project

pytestmark = pytest.mark.e2e  # real install.sh / make check (#68)

ALREADY_GREEN = "last green full check matches this exact state"
NO_GREEN = "no green full check for this state"
UNKNOWN = "unknown"
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
    fp = _fingerprint(project).stdout.strip()
    assert fp
    _cache(project).write_text(fp)  # the record gates/close write after a green check
    return toolkit, project


def test_the_project_runs_the_toolkit_copy(setup: tuple[Path, Installed]) -> None:
    toolkit, project = setup
    assert (project.root / "harness").resolve() == toolkit / "lib"


def test_an_unchanged_toolkit_keeps_the_cache(setup: tuple[Path, Installed]) -> None:
    _, project = setup
    res = _stop(project)
    assert res.returncode == 0
    assert ALREADY_GREEN in res.stdout


def test_a_toolkit_change_invalidates_the_record(setup: tuple[Path, Installed]) -> None:
    """The harness changed (check now red): the Stop warns, never runs it nor blocks."""
    toolkit, project = setup
    (toolkit / "lib/gate.sh").write_text(RED_GATE)
    res = _stop(project)
    assert res.returncode == 0, res.stdout + res.stderr
    assert ALREADY_GREEN not in res.stdout
    assert NO_GREEN in res.stdout + res.stderr
    assert "gate broken by a toolkit update" not in res.stdout + res.stderr


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


def test_python_bytecode_in_the_toolkit_is_part_of_the_key(
    setup: tuple[Path, Installed],
) -> None:
    """Python may run a .pyc from lib/__pycache__ instead of its source: it counts."""
    toolkit, project = setup
    before = _fingerprint(project).stdout
    cache_dir = toolkit / "lib/__pycache__"
    cache_dir.mkdir(exist_ok=True)
    (cache_dir / "extra.cpython-312.pyc").write_bytes(b"\0")
    assert _fingerprint(project).stdout != before


def test_a_stop_does_not_void_the_record_by_itself(
    setup: tuple[Path, Installed],
) -> None:
    """Running the Stop (and its bytecode) must not change the fingerprint it compares."""
    _, project = setup
    _stop(project)
    assert ALREADY_GREEN in _stop(project).stdout


def test_a_change_behind_a_directory_link_inside_lib_changes_it(
    setup: tuple[Path, Installed], tmp_path: Path
) -> None:
    toolkit, project = setup
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "helper.sh").write_text("echo one\n")
    (toolkit / "lib/linked").symlink_to(outside)
    before = _fingerprint(project).stdout
    (outside / "helper.sh").write_text("echo two\n")
    assert _fingerprint(project).stdout != before


def test_a_link_loop_inside_lib_still_gives_a_fingerprint(
    setup: tuple[Path, Installed],
) -> None:
    """A loop is hashed as a link (its target), not walked forever nor dropped."""
    toolkit, project = setup
    (toolkit / "lib/loop").symlink_to(toolkit / "lib")
    first = _fingerprint(project)
    assert first.returncode == 0 and first.stdout.strip()
    (toolkit / "lib/loop").unlink()
    (toolkit / "lib/loop").symlink_to(toolkit)
    assert _fingerprint(project).stdout != first.stdout


def test_an_unreadable_directory_in_lib_means_no_fingerprint(
    setup: tuple[Path, Installed],
) -> None:
    toolkit, project = setup
    hidden = toolkit / "lib/private"
    hidden.mkdir()
    (hidden / "x.sh").write_text("echo\n")
    hidden.chmod(0)
    try:
        res = _fingerprint(project)
    finally:
        hidden.chmod(0o755)
    assert res.returncode != 0 and not res.stdout.strip()


def test_a_broken_settings_link_means_unknown(setup: tuple[Path, Installed]) -> None:
    """The toolkit part cannot be computed: no fingerprint -> "unknown", never green."""
    _, project = setup
    link = project.root / ".claude/settings.json"
    link.unlink()
    link.symlink_to(project.root / "missing-settings.json")
    res = _fingerprint(project)
    assert res.returncode != 0 and not res.stdout.strip()
    stop = _stop(project)
    assert stop.returncode == 0, stop.stdout + stop.stderr
    assert ALREADY_GREEN not in stop.stdout
    assert UNKNOWN in stop.stdout + stop.stderr
