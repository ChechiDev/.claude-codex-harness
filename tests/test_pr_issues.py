"""Issue pull request: title, body (Closes #n, issue docs) and when it is due."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from conftest import Plan


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, text=True, capture_output=True, check=True
    ).stdout


def _issue_branch(plan: Plan) -> Plan:
    """A repo with main and an issue branch carrying one close commit per subtask."""
    root = plan.root
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "dev@example.com")
    _git(root, "config", "user.name", "dev")
    plan.issue(14, "refactor").isub("ISS-14.1", "done").isub("ISS-14.2", "done").sync()
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "chore: plan")
    _git(root, "switch", "-qc", "refactor/ISS-14-issue-14")
    for sid in ("ISS-14.1", "ISS-14.2"):
        src = root / f"src/app/m{sid.replace('.', '')}.py"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text("x = 1\n")
        (root / f"progress/review_{sid}.md").write_text("**Verdict:** APPROVED\n")
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", f"refactor(app): sub {sid} [{sid}]")
    return plan


def test_title_follows_the_issue_type(plan: Plan) -> None:
    plan.issue(14, "refactor").isub("ISS-14.1").sync()
    assert plan.run_py("_pr.py", "title", "ISS-14").stdout.strip() == (
        "refactor: issue 14 [ISS-14]"
    )


def _retitle_issue(plan: Plan, n: int, title: str) -> None:
    """Give issue n a GitHub-style title and re-sync the plan."""
    header = plan.root / f"docs/plan/issues/ISS-{n}-i{n}.md"
    header.write_text(header.read_text().replace(f"# ISS-{n} Issue {n}", f"# ISS-{n} {title}"))
    res = plan.run_py("sync_plan.py")
    assert res.returncode == 0, res.stdout + res.stderr


@pytest.mark.parametrize(
    "title",
    [
        "refactor: Replace parse_config bit mask [2.3]",
        "feat: Replace parse_config bit mask [2.3]",  # the label wins over the title's type
        "fix(parsing): Replace parse_config bit mask [2.3]",
        "chore!: Replace parse_config bit mask [2.3]",
        "Refactor: Replace parse_config bit mask [2.3]",
    ],
    ids=["same-type", "other-type", "scoped", "breaking", "capitalised"],
)
def test_title_drops_a_type_prefix_already_in_the_issue_title(plan: Plan, title: str) -> None:
    plan.issue(14, "refactor").isub("ISS-14.1").sync()
    _retitle_issue(plan, 14, title)
    assert plan.run_py("_pr.py", "title", "ISS-14").stdout.strip() == (
        "refactor: replace parse_config bit mask [2.3] [ISS-14]"
    )


@pytest.mark.parametrize(
    "title", ["note: not a commit type", "Crash when the config has a colon: here"]
)
def test_title_keeps_text_that_is_not_a_type_prefix(plan: Plan, title: str) -> None:
    plan.issue(14, "fix").isub("ISS-14.1").sync()
    _retitle_issue(plan, 14, title)
    assert plan.run_py("_pr.py", "title", "ISS-14").stdout.strip() == (
        f"fix: {title.lower()} [ISS-14]"
    )


def test_pr_is_due_once_every_issue_subtask_is_done(plan: Plan) -> None:
    plan.issue(14).isub("ISS-14.1", "done").isub("ISS-14.2").sync()
    assert plan.run_py("_pr.py", "pr-due", "ISS-14.1").returncode == 1
    plan.isub("ISS-14.2", "done").sync()
    res = plan.run_py("_pr.py", "pr-due", "ISS-14.2")
    assert res.returncode == 0
    assert res.stdout.strip() == "ISS-14"
    assert plan.run_py("_pr.py", "last-of-task", "ISS-14.2").stdout.strip() == "ISS-14"


def test_body_closes_the_issue(plan: Plan) -> None:
    _issue_branch(plan)
    res = plan.run_py("_pr.py", "body", "ISS-14", "main")
    assert res.returncode == 0, res.stdout + res.stderr
    body = res.stdout
    assert "Closes #14" in body
    assert "Merges `refactor/ISS-14-issue-14` into `main`." in body


def test_body_lists_the_issue_subtasks_with_their_commits(plan: Plan) -> None:
    _issue_branch(plan)
    body = plan.run_py("_pr.py", "body", "ISS-14", "main").stdout
    for sid in ("ISS-14.1", "ISS-14.2"):
        sha = _git(plan.root, "log", "-1", "--format=%h", "-F", f"--grep=[{sid}]").strip()
        row = next(line for line in body.splitlines() if line.startswith(f"| {sid} "))
        assert sha in row


def test_body_documentation_points_to_the_issue(plan: Plan) -> None:
    _issue_branch(plan)
    body = plan.run_py("_pr.py", "body", "ISS-14", "main").stdout
    docs = body.split("## Documentation\n", 1)[1].split("\n## ", 1)[0]
    assert "https://github.com/acme/app/issues/14" in docs
    assert "`docs/plan/issues/ISS-14-i14.md`" in docs
    assert "`docs/plan/issues/ISS-14-i14/`" in docs


def test_body_without_narrative_falls_back_to_the_issue_goal(plan: Plan) -> None:
    _issue_branch(plan)
    body = plan.run_py("_pr.py", "body", "ISS-14", "main").stdout
    overview = body.split("## Overview\n", 1)[1].split("\n## ", 1)[0]
    assert overview.strip() == "g."


def test_documenter_role_covers_the_issue_pr() -> None:
    role = (Path(__file__).resolve().parents[1] / "codex/documenter.md").read_text()
    for needle in ("progress/pr_ISS-<n>.md", "## Issue body (imported from GitHub)", "Closes #<n>"):
        assert needle in role, needle


def test_pr_command_opens_the_issue_pr_and_cleans_the_claim(plan: Plan, tmp_path: Path) -> None:
    _issue_branch(plan)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    _git(plan.root, "remote", "add", "origin", str(remote))
    _git(plan.root, "push", "-q", "origin", "main")
    log = tmp_path / "gh.log"
    gh = tmp_path / "gh"
    gh.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$*" >> {log}\n'
        'if [ "$1 $2" = "pr view" ]; then exit 1; fi\nexit 0\n'
    )
    gh.chmod(0o755)
    toolkit = Path(__file__).resolve().parents[1]
    env = {
        "PATH": "/usr/bin:/bin",
        "HARNESS_GH": str(gh),
        "CLAUDE_PROJECT_DIR": str(plan.root),
        "HOME": str(tmp_path),
    }

    def harness(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(toolkit / "lib/review_with_codex.sh"), *args],
            cwd=plan.root,
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )

    # tmp_path is the repo here: keep the fakes and local files out of the PR tree.
    (plan.root / ".gitignore").write_text("/gh\n/gh.log\n/remote.git/\n")
    (plan.root / "Makefile").write_text("check:\n\t@echo ok\n")
    _git(plan.root, "add", "Makefile", ".gitignore")
    _git(plan.root, "commit", "-qm", "build: check")
    # Issue #55: with no green record of this state, `pr` runs the real, clean
    # `make check` itself (red-path blocking: tests/test_green_record_pr.py).
    res = harness("pr", "ISS-14")
    assert res.returncode == 0, res.stdout + res.stderr
    calls = log.read_text()
    assert "pr create --head refactor/ISS-14-issue-14 --base main" in calls
    assert "--title refactor: issue 14 [ISS-14]" in calls
    assert "claim cleaned up" in res.stdout + res.stderr
