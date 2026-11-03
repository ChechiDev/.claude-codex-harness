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


@pytest.mark.pins("I12b-key")
def test_a_toolkit_change_is_hidden_by_the_cache_today(setup: tuple[Path, Installed]) -> None:
    """Known wrong: the harness changed (check now red) but the Stop is green from the
    cache, because the fingerprint only covers the project's own git state."""
    toolkit, project = setup
    (toolkit / "lib/gate.sh").write_text(RED_GATE)
    res = _stop(project)
    assert res.returncode == 0
    assert ALREADY_GREEN in res.stdout


def test_control_without_the_cache_the_changed_toolkit_is_red(
    setup: tuple[Path, Installed],
) -> None:
    """Control for the pin: the same toolkit change, no cache -> the Stop blocks."""
    toolkit, project = setup
    (toolkit / "lib/gate.sh").write_text(RED_GATE)
    _cache(project).unlink()
    res = _stop(project)
    assert res.returncode == 2, res.stdout + res.stderr
    assert "gate broken by a toolkit update" in res.stderr
