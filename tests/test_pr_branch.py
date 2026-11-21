"""PR branch guard (issue #80, I13) and base drift + "For the human" (issue #43).

`review_with_codex.sh pr <id>` only pushes the branch the task/issue lives on: never
the base, never another task's branch, never a branch carrying unpushed base commits.
The PR body reports how far behind origin/<base> the branch is, whether a merge would
conflict (computed with `git merge-tree`, the worktree untouched), and the paths a
human must look at.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from conftest import Plan, Project

GREEN = "check:\n\t@echo ok\n"


def _git(cwd: Path, *args: str) -> str:
    res = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return res.stdout.strip()


def _env(project: Project, tmp: Path, branch: str | None) -> dict[str, str]:
    """Fake gh logging its calls, a bare origin with main, HEAD on <branch>."""
    project.write("Makefile", GREEN)
    project.commit_all("build: check")
    bin_dir = tmp / "fakebin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(
        '#!/usr/bin/env bash\necho "$*" >> "$FAKE_LOG"\n'
        'if [ "$1 $2" = "pr view" ]; then exit 1; fi\n'
        'if [ "$1 $2" = "pr create" ]; then\n'
        '  while [ $# -gt 0 ]; do [ "$1" = --body-file ] && cp "$2" "$FAKE_LOG.body"; shift;'
        " done\nfi\nexit 0\n"
    )
    gh.chmod(0o755)
    remote = tmp / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    _git(project.root, "remote", "add", "origin", str(remote))
    _git(project.root, "push", "-q", "origin", "main")
    _git(project.root, "fetch", "-q", "origin")
    if branch:
        _git(project.root, "switch", "-qc", branch)
    return {"PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_LOG": str(tmp / "gh.log")}


def _pr(project: Project, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return project.run("review_with_codex.sh", None, "pr", "1.1", env=env)


def _remote_heads(tmp: Path) -> list[str]:
    out = _git(tmp / "remote.git", "for-each-ref", "--format=%(refname:short)", "refs/heads")
    return out.splitlines()


@pytest.mark.e2e
def test_on_main_the_pr_is_refused_and_nothing_pushed(project: Project, tmp_path: Path) -> None:
    env = _env(project, tmp_path, None)
    before = _git(tmp_path / "remote.git", "rev-parse", "main")
    project.write("src/x.py", "x = 1\n")
    project.commit_all("feat: local work on main")
    res = _pr(project, env)
    assert res.returncode == 1, res.stdout + res.stderr
    assert "feat/1.1-users" in res.stderr
    assert "main" in res.stderr
    assert _git(tmp_path / "remote.git", "rev-parse", "main") == before
    assert _remote_heads(tmp_path) == ["main"]
    assert not (tmp_path / "gh.log").exists()


@pytest.mark.e2e
def test_on_another_tasks_branch_the_pr_is_refused(project: Project, tmp_path: Path) -> None:
    project.write("harness.toml", '[git]\nbranch = "feat/{task}-{slug}"\n')
    env = _env(project, tmp_path, "feat/2.1-other")
    res = _pr(project, env)
    assert res.returncode == 1, res.stdout + res.stderr
    assert "feat/2.1-other" in res.stderr and "feat/1.1-users" in res.stderr
    assert _remote_heads(tmp_path) == ["main"]
    assert not (tmp_path / "gh.log").exists()


@pytest.mark.e2e
def test_without_git_section_another_branch_only_warns(project: Project, tmp_path: Path) -> None:
    env = _env(project, tmp_path, "feat/legacy-name")
    res = _pr(project, env)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "not enforced" in res.stderr
    assert "feat/legacy-name" in _remote_heads(tmp_path)


@pytest.mark.e2e
def test_unpushed_base_commits_on_the_branch_are_refused(project: Project, tmp_path: Path) -> None:
    env = _env(project, tmp_path, None)
    project.write("src/local.py", "y = 2\n")
    project.commit_all("chore: local-only commit on main")
    _git(project.root, "switch", "-qc", "feat/1.1-users")
    res = _pr(project, env)
    assert res.returncode == 1, res.stdout + res.stderr
    assert "origin/main" in res.stderr
    assert _remote_heads(tmp_path) == ["main"]
    # Still refused once the local main moved past the commit the branch carries.
    _git(project.root, "switch", "-q", "main")
    project.write("src/later.py", "z = 3\n")
    project.commit_all("chore: later local commit")
    _git(project.root, "switch", "-q", "feat/1.1-users")
    res = _pr(project, env)
    assert res.returncode == 1, res.stdout + res.stderr
    assert _remote_heads(tmp_path) == ["main"]


@pytest.mark.e2e
def test_on_the_expected_branch_the_pr_opens_with_drift_and_attention(
    project: Project, tmp_path: Path
) -> None:
    env = _env(project, tmp_path, "feat/1.1-users")
    project.write("pyproject.toml", '[project]\nname = "x"\n')
    project.commit_all("build: deps")
    res = _pr(project, env)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "feat/1.1-users" in _remote_heads(tmp_path)
    body = (tmp_path / "gh.log.body").read_text()
    assert "## For the human" in body
    assert "0 commits behind `origin/main`" in body
    assert "no conflicts" in body
    assert "`pyproject.toml`" in body


# ── unit: drift and attention in the body ────────────────────────────────────


def _repo(plan: Plan) -> Path:
    root = plan.root
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "dev@example.com")
    _git(root, "config", "user.name", "dev")
    plan.phase("01", "1.1").sub("1.1.1", "done").approve_roadmap().approve_phase("01").sync()
    (root / "a.txt").write_text("base\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "chore: plan")
    _git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(root, "switch", "-qc", "feat/1.1-task-1-1")
    return root


def _commit(root: Path, path: str, text: str, msg: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", msg)


def _body(plan: Plan) -> str:
    return plan.run_py("_pr.py", "body", "1.1", "main").stdout


def test_body_reports_behind_count_and_conflicts(plan: Plan) -> None:
    root = _repo(plan)
    _commit(root, "a.txt", "branch\n", "feat: branch side [1.1.1]")
    _git(root, "switch", "-q", "main")
    _commit(root, "a.txt", "main\n", "fix: main side")
    _commit(root, "b.txt", "b\n", "fix: another")
    _git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(root, "switch", "-q", "feat/1.1-task-1-1")
    head = _git(root, "rev-parse", "HEAD")
    status = _git(root, "status", "--porcelain")
    body = _body(plan)
    assert "2 commits behind `origin/main`" in body
    assert "conflicts" in body and "`a.txt`" in body
    assert _git(root, "rev-parse", "HEAD") == head  # information only
    assert _git(root, "status", "--porcelain") == status


@pytest.mark.parametrize(
    ("path", "label"),
    [
        ("uv.lock", "Dependencies"),
        ("migrations/0002_users.py", "Migrations"),
        (".github/workflows/ci.yml", "CI / infrastructure"),
        ("Dockerfile", "CI / infrastructure"),
        ("src/app/auth/tokens.py", "Auth / permissions"),
    ],
)
def test_body_flags_paths_for_the_human(plan: Plan, path: str, label: str) -> None:
    root = _repo(plan)
    _commit(root, path, "x\n", "feat: change [1.1.1]")
    body = _body(plan)
    section = body.split("## For the human", 1)[1]
    assert label in section and f"`{path}`" in section


def test_body_flags_the_source_of_a_rename(plan: Plan) -> None:
    root = _repo(plan)
    _commit(root, "uv.lock", "x\n", "chore: lock")
    _git(root, "update-ref", "refs/remotes/origin/main", "main")
    _git(root, "switch", "-q", "main")
    _git(root, "merge", "-q", "--ff-only", "feat/1.1-task-1-1")
    _git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(root, "switch", "-q", "feat/1.1-task-1-1")
    _git(root, "mv", "uv.lock", "notes.txt")
    _git(root, "commit", "-qm", "chore: rename [1.1.1]")
    section = _body(plan).split("## For the human", 1)[1]
    assert "`uv.lock`" in section


def test_body_says_nothing_flagged_and_keeps_the_documenter_note(plan: Plan) -> None:
    root = _repo(plan)
    _commit(root, "src/app/m.py", "x = 1\n", "feat: code [1.1.1]")
    (root / "progress").mkdir(exist_ok=True)
    (root / "progress/pr_1.1.md").write_text("## For the human\nCheck the retry policy.\n")
    section = _body(plan).split("## For the human", 1)[1]
    assert "No dependency, migration, CI or auth paths changed." in section
    assert "Check the retry policy." in section
