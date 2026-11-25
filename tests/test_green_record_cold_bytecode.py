"""The green record survives a cold bytecode cache (PR #129, shard 6).

The fingerprint hashes the toolkit behind `harness/`, bytecode included (Python may
run a .pyc instead of its source). `run_check.sh --record` compares the fingerprint
before and after the check, so a helper of its own that compiles a toolkit module
inside that window (the first run after a toolkit update, or a fresh checkout) used
to change it: no green record, and `review_with_codex.sh pr` refused a green tree.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import TOOLKIT, Project


def _cold_toolkit(project: Project, tmp_path: Path) -> Path:
    """Point harness/ at a private copy of lib/ without any bytecode."""
    lib = tmp_path / "toolkit" / "lib"
    shutil.copytree(TOOLKIT / "lib", lib, ignore=shutil.ignore_patterns("__pycache__"))
    (project.root / "harness").unlink()
    (project.root / "harness").symlink_to(lib)
    return lib


def _out(project: Project, *args: str) -> str:
    res = subprocess.run(args, cwd=project.root, text=True, capture_output=True, check=True)
    return res.stdout.strip()


@pytest.mark.e2e
def test_a_cold_toolkit_still_gets_a_green_record(project: Project, tmp_path: Path) -> None:
    project.write("Makefile", "check:\n\t@echo ok\n")
    project.commit_all("build: check")
    _cold_toolkit(project, tmp_path)
    res = project.run("run_check.sh", None, "--decision", "--record")
    assert res.returncode == 0, res.stdout + res.stderr
    record = Path(
        _out(
            project, "git", "rev-parse", "--path-format=absolute", "--git-path", "harness-check.ok"
        )
    )
    assert record.is_file(), res.stdout + res.stderr
    fp = _out(project, "python3", "-E", "-s", "harness/_guard.py", "fingerprint")
    assert record.read_text() == fp
