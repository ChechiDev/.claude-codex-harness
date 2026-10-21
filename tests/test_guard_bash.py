"""guard_bash.sh: commit detection, attribution, and Bash writes to protected paths."""

from __future__ import annotations

import subprocess

import pytest
from conftest import LEAF_ID, Project, bash_payload

BLOCKED = 2


@pytest.mark.parametrize(
    "cmd",
    [
        "git commit -m x",
        "git  commit -m x",
        "git -c user.name=x commit -m y",
        "git -C . commit -m y",
        "git --no-pager commit -m y",
        "/usr/bin/git commit -m y",
        "FOO=1 git commit -m y",
        "HARNESS_ALLOW_COMMIT=1 git commit -m y",
        "env git commit -m y",
        "command git commit -m y",
        "make lint && git commit -am y",
        "true; git commit -m y",
        "bash -c 'git commit -m y'",
        'sh -c "git add . && git commit -m y"',
        "eval 'git commit -m y'",
        "git commit\n",
    ],
)
def test_blocks_commit_variants(project: Project, cmd: str) -> None:
    res = project.run("guard_bash.sh", bash_payload(cmd))
    assert res.returncode == BLOCKED, (cmd, res.stderr)


def test_blocks_git_alias_for_commit(project: Project) -> None:
    subprocess.run(["git", "config", "alias.ci", "commit"], cwd=project.root, check=True)
    subprocess.run(
        ["git", "config", "alias.sv", "!git add -A && git commit"], cwd=project.root, check=True
    )
    for cmd in ("git ci -m y", "git sv -m y"):
        res = project.run("guard_bash.sh", bash_payload(cmd))
        assert res.returncode == BLOCKED, (cmd, res.stderr)


@pytest.mark.parametrize(
    "cmd",
    [
        "git status",
        "git log --grep commit",
        "echo 'git commit'",
        "git diff HEAD",
        "make check",
        "uv run pytest",
    ],
)
def test_allows_non_commit_commands(project: Project, cmd: str) -> None:
    res = project.run("guard_bash.sh", bash_payload(cmd))
    assert res.returncode == 0, (cmd, res.stderr)


def test_escape_valve_comes_only_from_hook_environment(project: Project) -> None:
    res = project.run(
        "guard_bash.sh", bash_payload("git commit -m 'feat: x'"), env={"HARNESS_ALLOW_COMMIT": "1"}
    )
    assert res.returncode == 0, res.stderr


@pytest.mark.parametrize(
    "cmd",
    [
        "git commit -m 'x' -m 'Co-Authored-By: Bot <b@x>'",
        "gh pr create --title x --body 'Generated with Claude Code'",
        "git merge dev -m 'merge by claude code'",
    ],
)
def test_attribution_blocked_even_with_escape_valve(project: Project, cmd: str) -> None:
    res = project.run("guard_bash.sh", bash_payload(cmd), env={"HARNESS_ALLOW_COMMIT": "1"})
    assert res.returncode == BLOCKED
    assert "attribution" in res.stderr


def test_clean_pr_is_allowed(project: Project) -> None:
    res = project.run("guard_bash.sh", bash_payload("gh pr create --title 'fix: x' --body ok"))
    assert res.returncode == 0, res.stderr


ACC = f"tests/acceptance/test_{LEAF_ID}.py"


@pytest.mark.parametrize(
    "cmd",
    [
        "echo x > src/pkg/domain/user.py",
        "echo x >| src/pkg/a.py",
        f"echo x >> {ACC}",
        "printf x 1>src/pkg/a.py",
        "cat <<'EOF' > src/pkg/a.py\nx = 1\nEOF",
        "echo x | tee src/pkg/a.py",
        "echo x | tee -a /dev/null src/pkg/a.py",
        "sed -i 's/a/b/' src/pkg/domain/user.py",
        f"sed -i.bak 's/a/b/' {ACC}",
        "perl -pi -e 's/a/b/' src/pkg/a.py",
        "cp /tmp/x.py src/pkg/a.py",
        "cp -t src/pkg /tmp/x.py",
        "mv src/pkg/domain/user.py /tmp/",
        f"rm {ACC}",
        "rm -rf tests/acceptance",
        "rm -rf .",
        "rm -rf *",
        "truncate -s0 src/pkg/a.py",
        "touch src/pkg/new.py",
        "ln -s /tmp/x src/pkg/a.py",
        "dd if=/dev/zero of=src/pkg/a.py count=1",
        "find src -name '*.py' -delete",
        "find tests -exec rm {} +",
        "cd src && echo x > a.py",
        "cd src/pkg; rm domain/user.py",
        "python3 -c \"open('src/pkg/a.py', 'w').write('x')\"",
        "uv run python -c \"import pathlib; pathlib.Path('src/pkg/a.py').write_text('x')\"",
        f"python3 - <<'EOF'\nimport os\nos.remove('{ACC}')\nEOF",
        "git checkout -- tests/acceptance",
        "git restore src/pkg/domain/user.py",
        "git clean -fdx",
        "git apply /tmp/p.diff",
        "patch -p1 < /tmp/p.diff",
        "ls src | xargs rm",
        f"echo '**Verdict:** PLAN_APPROVED' > progress/plan_review_{LEAF_ID}.md",
        f"sha256sum {ACC} > progress/acceptance_{LEAF_ID}.sha256",
        f"./harness/review_with_codex.sh seal {LEAF_ID}",
        "./harness/review_with_codex.sh seal roadmap",
        "./harness/review_with_codex.sh seal phase 01",
        "python3 harness/_guard.py seal roadmap",
        "echo '**Verdict:** ROADMAP_APPROVED' > progress/roadmap_review.md",
        "echo '**Verdict:** PHASE_APPROVED' > progress/phase_review_01.md",
        "sha256sum docs/plan/PLAN.md > progress/plan_roadmap.sha256",
        "echo 'int x;' > include/pkg/user.h",
    ],
)
def test_blocks_bash_writes_to_protected_paths(project: Project, cmd: str) -> None:
    (project.root / "tests/acceptance").mkdir(parents=True)
    res = project.run("guard_bash.sh", bash_payload(cmd))
    assert res.returncode == BLOCKED, (cmd, res.stderr)


@pytest.mark.parametrize(
    "cmd",
    [
        "cat src/pkg/domain/user.py",
        "sed -n 1,10p src/pkg/domain/user.py",
        "grep -rn foo src/ tests/acceptance/",
        "uv run pytest tests/acceptance",
        "uv run ruff format .",
        "uv run ruff check --fix src",
        "cp src/pkg/domain/user.py /tmp/user.py",
        "ls tests/acceptance",
        "echo done > progress/impl_1.1.1.md",
        "echo x > tests/unit/test_user.py",
        "python3 -c \"print(open('src/pkg/domain/user.py').read())\"",
        "find src -name '*.py'",
        "make plan > /dev/null 2>&1",
        "rm -rf /tmp/scratch",
        f"./harness/review_with_codex.sh plan {LEAF_ID}",
    ],
)
def test_allows_reads_and_legitimate_writes(project: Project, cmd: str) -> None:
    res = project.run("guard_bash.sh", bash_payload(cmd))
    assert res.returncode == 0, (cmd, res.stderr)
