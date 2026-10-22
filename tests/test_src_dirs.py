"""harness.toml [scope] src_dirs: projects whose code does not live in src/."""

from __future__ import annotations

import subprocess
import sys

import pytest
from conftest import TOOLKIT, Project, bash_payload, write_payload

BLOCKED = 2
APP_FILE = "app/domain/user.py"


@pytest.fixture
def app_project(project: Project) -> Project:
    """A flat-layout project: the code lives in app/ and scripts/, declared in harness.toml."""
    project.write("harness.toml", '[scope]\nsrc_dirs = ["app", "scripts"]\n')
    project.write(APP_FILE, "")
    project.set_leaf(files=[APP_FILE])
    project.commit_all("flat layout")
    return project


def _integrity(project: Project) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOLKIT / "lib/_guard.py"), "integrity"],
        cwd=project.root,
        text=True,
        capture_output=True,
        check=False,
    )


def test_declared_file_in_a_configured_dir_is_allowed(app_project: Project) -> None:
    res = app_project.run("guard_write.sh", write_payload(APP_FILE))
    assert res.returncode == 0, res.stderr


@pytest.mark.parametrize("path", ["app/infra/repo.py", "scripts/seed.py"])
def test_undeclared_file_in_a_configured_dir_is_blocked(app_project: Project, path: str) -> None:
    res = app_project.run("guard_write.sh", write_payload(path))
    assert res.returncode == BLOCKED
    assert "NOT in the declared" in res.stderr


def test_configured_dirs_replace_the_src_default(app_project: Project) -> None:
    res = app_project.run("guard_write.sh", write_payload("src/notes.py"))
    assert res.returncode == 0, res.stderr


def test_configured_dirs_match_from_the_project_root(app_project: Project) -> None:
    # A tests/unit/app/ folder is test code, not the app package.
    res = app_project.run("guard_write.sh", write_payload("tests/unit/app/test_x.py"))
    assert res.returncode == 0, res.stderr


def test_configured_dir_write_without_plan_approval_is_blocked(app_project: Project) -> None:
    app_project.set_verdict("plan", "CHANGES_REQUESTED")
    res = app_project.run("guard_write.sh", write_payload(APP_FILE))
    assert res.returncode == BLOCKED


def test_shell_write_to_a_configured_dir_is_blocked(app_project: Project) -> None:
    res = app_project.run("guard_bash.sh", bash_payload("echo x > app/infra/repo.py"))
    assert res.returncode == BLOCKED


def test_interpreter_touching_a_configured_dir_is_blocked(app_project: Project) -> None:
    cmd = "python3 -c \"open('app/x.py', 'w').write('x')\""
    res = app_project.run("guard_bash.sh", bash_payload(cmd))
    assert res.returncode == BLOCKED


def test_deleting_a_configured_dir_is_blocked(app_project: Project) -> None:
    res = app_project.run("guard_bash.sh", bash_payload("rm -rf app"))
    assert res.returncode == BLOCKED


def test_integrity_flags_undeclared_changes_in_a_configured_dir(app_project: Project) -> None:
    app_project.write("scripts/seed.py", "x = 1\n")
    res = _integrity(app_project)
    assert res.returncode == 1
    assert "scripts/seed.py" in res.stdout


def test_integrity_accepts_declared_changes_in_a_configured_dir(app_project: Project) -> None:
    app_project.write(APP_FILE, "x = 1\n")
    res = _integrity(app_project)
    assert res.returncode == 0, res.stdout


@pytest.mark.parametrize("tool", ["write", "bash"])
def test_claude_cannot_edit_the_scope_config(app_project: Project, tool: str) -> None:
    # Dropping "app" from src_dirs would switch the protection off.
    if tool == "write":
        res = app_project.run("guard_write.sh", write_payload("harness.toml"))
    else:
        res = app_project.run("guard_bash.sh", bash_payload("echo '' > harness.toml"))
    assert res.returncode == BLOCKED


def test_invalid_config_fails_closed(app_project: Project) -> None:
    app_project.write("harness.toml", "[scope\nsrc_dirs = app\n")
    res = app_project.run("guard_write.sh", write_payload(APP_FILE))
    assert res.returncode == BLOCKED
    assert "harness.toml" in res.stderr
    assert _integrity(app_project).returncode == 1


def test_without_config_src_stays_the_default(project: Project) -> None:
    res = project.run("guard_write.sh", write_payload("src/pkg/other.py"))
    assert res.returncode == BLOCKED
