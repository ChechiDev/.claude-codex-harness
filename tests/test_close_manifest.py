"""The close commit stages only the subtask manifest (issue #40).

review_with_codex.sh close runs _close_scope.py before committing: a foreign path in
the tree or in the index blocks the close with its explanation, and the commit holds
exactly the allowed paths (renames and deletions included). The manifest is never
widened automatically.
"""

from __future__ import annotations

import subprocess

import pytest
from conftest import DECLARED, LEAF_ID, Project, bind_code_approval

SUBJECT = f"feat(domain): add user entity [{LEAF_ID}]"
UNIT = "tests/unit/test_user.py"


def _git(project: Project, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=project.root, text=True, capture_output=True, check=True
    ).stdout


def _close(project: Project) -> subprocess.CompletedProcess[str]:
    bind_code_approval(project.root, LEAF_ID)
    project.write(f"progress/close_{LEAF_ID}.msg", SUBJECT + "\n")
    return project.run(
        "review_with_codex.sh", None, "close", LEAF_ID, env={"HARNESS_CHECK_CMD": "true"}
    )


def _committed(project: Project) -> set[str]:
    out = _git(project, "show", "--name-status", "--format=", "-M", "HEAD")
    return {line.strip() for line in out.splitlines() if line.strip()}


@pytest.fixture
def ready(project: Project) -> Project:
    project.write(UNIT, "def test_u(): pass\n")
    project.write("src/pkg/domain/old.py", "x = 1\n")
    project.set_leaf(files=[DECLARED, "src/pkg/domain/old.py", "src/pkg/domain/new.py"])
    project.commit_all("base")
    project.set_verdict("code", "APPROVED")
    project.write(DECLARED, "class User: ...\n")
    return project


def test_close_commits_only_the_manifest(ready: Project) -> None:
    res = _close(ready)
    assert res.returncode == 0, res.stdout + res.stderr
    names = {line.split("\t")[-1] for line in _committed(ready)}
    assert DECLARED in names
    assert _git(ready, "status", "--porcelain") == ""


def test_untracked_foreign_file_blocks_the_close(ready: Project) -> None:
    ready.write("scratch/notes.txt", "personal\n")
    head = _git(ready, "rev-parse", "HEAD")
    res = _close(ready)
    assert res.returncode != 0
    assert "scratch/notes.txt" in res.stderr
    assert "outside the close manifest" in res.stderr
    assert _git(ready, "rev-parse", "HEAD") == head
    # never widened, never staged, message kept for the retry
    assert "scratch/notes.txt" not in _git(ready, "diff", "--cached", "--name-only")
    assert (ready.root / f"progress/close_{LEAF_ID}.msg").is_file()


def test_staged_foreign_file_blocks_the_close(ready: Project) -> None:
    ready.write("other.py", "y = 2\n")
    _git(ready, "add", "other.py")
    head = _git(ready, "rev-parse", "HEAD")
    res = _close(ready)
    assert res.returncode != 0
    assert "already staged" in res.stderr
    assert "git restore --staged" in res.stderr
    assert _git(ready, "rev-parse", "HEAD") == head


def test_rename_and_deletion_are_committed(ready: Project) -> None:
    _git(ready, "mv", "src/pkg/domain/old.py", "src/pkg/domain/new.py")
    (ready.root / UNIT).unlink()
    res = _close(ready)
    assert res.returncode == 0, res.stdout + res.stderr
    entries = _committed(ready)
    assert any(e.startswith("R") and e.endswith("src/pkg/domain/new.py") for e in entries)
    assert f"D\t{UNIT}" in entries
    assert _git(ready, "status", "--porcelain") == ""


def test_unstaged_rename_into_a_foreign_path_blocks(ready: Project) -> None:
    (ready.root / "src/pkg/domain/old.py").rename(ready.root / "elsewhere.py")
    res = _close(ready)
    assert res.returncode != 0
    assert "elsewhere.py" in res.stderr


def test_failed_commit_keeps_the_message(ready: Project) -> None:
    hook = ready.root / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    res = _close(ready)
    assert res.returncode != 0
    assert (ready.root / f"progress/close_{LEAF_ID}.msg").read_text() == SUBJECT + "\n"
