"""I8: commit / publication guard bypasses, the settings allow/deny list and the
outcome check (commits on the branch that the harness close did not make)."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import LEAF_ID, TOOLKIT, Project, bash_payload

BLOCKED = 2


def _guard(project: Project, cmd: str) -> subprocess.CompletedProcess[str]:
    return project.run("guard_bash.sh", bash_payload(cmd))


def _warning(res: subprocess.CompletedProcess[str]) -> str:
    """The WARN the hook hands to Claude and the user (exit 0, JSON on stdout)."""
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert out["systemMessage"] == ctx
    return str(ctx)


# ── (a) bypasses of the existing `git commit` block: BLOCK ──────────────────


@pytest.mark.parametrize(
    "cmd",
    [
        "timeout -k 5 10 git commit -m y",
        "timeout --foreground 10 git commit -m y",
        "timeout -s KILL 10 git commit -m y",
        "timeout -vk 1 2 git commit -m y",
        "timeout -vs KILL 2 git commit -m y",
        "sudo -Eu dev git commit -m y",
        "ionice -tc 3 git commit -m y",
        "flock -nw 5 /tmp/l git commit -m y",
        "timeout --signal=KILL 10s git commit -m y",
        "nice -n 5 git commit -m y",
        "nice --adjustment=5 git commit -m y",
        "nice -5 git commit -m y",
        "stdbuf -o0 git commit -m y",
        "stdbuf -o 0 git commit -m y",
        "sudo -u dev git commit -m y",
        "sudo -E git commit -m y",
        "time -p git commit -m y",
        "exec -a x git commit -m y",
        "command -p git commit -m y",
        "setsid git commit -m y",
        "ionice -c 3 git commit -m y",
        "flock /tmp/l git commit -m y",
        "flock -c 'git commit -m y' /tmp/l",
        "script -qc 'git commit -m y' /dev/null",
        "script -q -c 'git commit -m y' /dev/null",
        "bash -l -c 'git commit -m y'",
        "command git commit -v -m y",
        "flock 123 git commit -m y",
        "script -o 10 -c 'git commit -m y' /dev/null",
        "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=alias.ci GIT_CONFIG_VALUE_0=log "
        "GIT_CONFIG_PARAMETERS=\"'alias.ci'='commit'\" git ci -m y",
        "GIT_CONFIG_PARAMETERS=\"'alias.ci'='log'\" git -c alias.ci=commit ci -m y",
        "uv run git commit -m y",
        "echo y | xargs git commit -m",
        "find docs -maxdepth 0 -exec git commit -m y ';'",
        "git -c alias.ci=commit ci -m y",
        "git -c 'alias.ci=!git commit' ci -m y",
        "git --config-env alias.ci=V ci -m y",
        "git --config-env=alias.ci=V ci -m y",
        "git config alias.ci commit && git ci -m y",
        "git config --local alias.ci commit; git ci -m y",
        "git config set alias.ci commit && git ci -m y",
        "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=alias.ci GIT_CONFIG_VALUE_0=commit git ci -m y",
        "export GIT_CONFIG_PARAMETERS=\"'alias.ci=commit'\"; git ci -m y",
    ],
)
def test_blocks_commit_bypasses(project: Project, cmd: str) -> None:
    res = _guard(project, cmd)
    assert res.returncode == BLOCKED, (cmd, res.stdout, res.stderr)
    assert "Claude does not commit" in res.stderr


@pytest.mark.parametrize("cmd", ["echo commit -m y | xargs git", "find docs -exec git {} -m y ';'"])
def test_blocks_git_whose_subcommand_comes_from_input(project: Project, cmd: str) -> None:
    assert _guard(project, cmd).returncode == BLOCKED, cmd


def test_alias_is_read_from_the_config_git_would_use(project: Project, tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text("[alias]\n\tci = commit\n", encoding="utf-8")
    other = tmp_path / "other"
    subprocess.run(["git", "init", "-q", str(other)], check=True)
    subprocess.run(["git", "-C", str(other), "config", "alias.ci", "commit"], check=True)
    for cmd in (
        f"HOME={home} git ci -m y",
        f"git -C {other} ci -m y",
        f"git --git-dir={other}/.git ci -m y",
        f"cd {other} && git ci -m y",
        "cd $SOMEWHERE && git ci -m y",
    ):
        res = _guard(project, cmd)
        assert res.returncode == BLOCKED, (cmd, res.stderr)


def test_alias_lookup_fails_closed_when_a_wrapper_moves_the_command(
    project: Project, tmp_path: Path
) -> None:
    other = tmp_path / "other"
    subprocess.run(["git", "init", "-q", str(other)], check=True)
    subprocess.run(["git", "-C", str(other), "config", "alias.ci", "commit"], check=True)
    for cmd in (f"env -C {other} git ci -m y", f"sudo -D {other} git ci -m y"):
        assert _guard(project, cmd).returncode == BLOCKED, cmd
    assert _guard(project, f"env -C {other} git status").returncode == 0


def test_sourced_script_alias_write_reaches_later_git_calls(project: Project) -> None:
    project.write("tools/setup.sh", "git config alias.ci commit\n")
    for cmd in ("source tools/setup.sh; git ci -m y", "bash tools/setup.sh && git ci -m y"):
        assert _guard(project, cmd).returncode == BLOCKED, cmd


def test_alias_write_to_an_unrelated_config_file_is_ignored(project: Project) -> None:
    subprocess.run(["git", "config", "alias.ci", "log"], cwd=project.root, check=True)
    res = _guard(project, "git config -f /tmp/other-config alias.ci commit; git ci")
    assert res.returncode == 0, res.stderr
    res = _guard(project, "git config -f .git/config alias.ci commit; git ci")
    assert res.returncode == BLOCKED


def test_round4_bypasses_are_blocked(project: Project) -> None:
    script = project.write("tools/ship.sh", "#!/bin/sh\ngit commit -m y\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    for cmd in (
        "git -calias.ci=commit ci -m y",
        "find docs -exec ./tools/ship.sh ';'",
        "find docs -exec bash tools/ship.sh ';'",
        "echo x | xargs ./tools/ship.sh",
        "echo x | xargs sh tools/ship.sh",
        "git config -f /tmp/custom-config alias.ci commit; "
        "GIT_CONFIG_GLOBAL=/tmp/custom-config git ci -m y",
    ):
        res = _guard(project, cmd)
        assert res.returncode == BLOCKED, (cmd, res.stderr)


def test_shell_alias_runs_from_the_repository_top(project: Project) -> None:
    script = project.write("ship.sh", "#!/bin/sh\ngit commit -m y\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    subprocess.run(["git", "config", "alias.ship", "!./ship.sh"], cwd=project.root, check=True)
    for cmd in ("cd docs && git ship", "git -C docs ship"):
        (project.root / "docs").mkdir(exist_ok=True)
        res = _guard(project, cmd)
        assert res.returncode == BLOCKED, (cmd, res.stderr)


@pytest.mark.parametrize(
    ("cmd", "warns"),
    [("git push --dry-run --no-dry-run", True), ("git push --no-dry-run -n", False)],
)
def test_push_dry_run_is_the_last_option(project: Project, cmd: str, warns: bool) -> None:
    res = _guard(project, cmd)
    assert res.returncode == 0 and ("[WARN]" in res.stdout) is warns, res.stdout


def test_round6_bypasses_are_blocked(project: Project, tmp_path: Path) -> None:
    sub = project.root / "sub"
    sub.mkdir()
    script = project.write("sub/ship.sh", "#!/bin/sh\ngit commit -m y\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    for cmd in (
        "script -q -c'git commit -m y' /dev/null",
        "script -qc'git commit -m y' /dev/null",
        "flock -c'git commit -m y' /tmp/lock",
        "flock -nc'git commit -m y' /tmp/lock",
        f"GIT_DIR={project.root}/.git GIT_WORK_TREE={sub} git -c 'alias.s=!./ship.sh' s",
    ):
        res = _guard(project, cmd)
        assert res.returncode == BLOCKED, (cmd, res.stderr)


@pytest.mark.parametrize(
    "cmd",
    [
        "flock --timeout 5 /tmp/lock -c 'git commit -m y'",
        "flock --wait 5 /tmp/lock -c 'git commit -m y'",
        "flock --conflict-exit-code 3 /tmp/lock -c 'git commit -m y'",
        "flock --timeout=5 /tmp/lock -c 'git commit -m y'",
        "script --echo never -c 'git commit -m y' /dev/null",
        "script --log-out /tmp/o -q -c 'git commit -m y' /dev/null",
        "script --output-limit 10 -c 'git commit -m y' /dev/null",
    ],
)
def test_verification_long_option_values_do_not_hide_the_body(project: Project, cmd: str) -> None:
    res = _guard(project, cmd)
    assert res.returncode == BLOCKED, (cmd, res.stderr)


def test_project_binary_run_directly_is_allowed(project: Project) -> None:
    tool = project.root / "bin/tool"
    tool.parent.mkdir()
    tool.write_bytes(b"\x7fELF\x00\x00git commit\x00")
    tool.chmod(0o755)
    assert _guard(project, "./bin/tool --help").returncode == 0


@pytest.mark.parametrize("runner", ["bash {s}", "sh {s}", "bash -e {s}", "{s}", "source {s}"])
def test_blocks_commit_inside_a_project_script(project: Project, runner: str) -> None:
    script = project.write("tools/ship.sh", "#!/bin/sh\ngit add -A\ngit commit -m y\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    res = _guard(project, runner.format(s="./tools/ship.sh"))
    assert res.returncode == BLOCKED, (runner, res.stderr)


@pytest.mark.parametrize(
    "cmd",
    [
        f"./harness/review_with_codex.sh doc {LEAF_ID}",
        f"bash harness/review_with_codex.sh doc {LEAF_ID}",
        "./harness/check.sh",
    ],
)
def test_harness_scripts_are_not_read_as_project_scripts(project: Project, cmd: str) -> None:
    res = _guard(project, cmd)
    assert res.returncode == 0, (cmd, res.stderr)
    assert "[WARN]" not in res.stdout


def test_script_analysis_goes_on_past_a_blocked_write(project: Project) -> None:
    subprocess.run(["git", "config", "alias.ci", "commit"], cwd=project.root, check=True)
    project.write("tools/s.sh", f"echo x > tests/acceptance/test_{LEAF_ID}.py\ngit ci -m y\n")
    res = project.run(
        "guard_bash.sh", bash_payload("bash tools/s.sh"), env={"HARNESS_ALLOW_COMMIT": ""}
    )
    assert res.returncode == BLOCKED and "Claude does not commit" in res.stderr, res.stderr


@pytest.mark.parametrize("alias", ['commit "x', 'log "x'])
def test_alias_with_broken_quoting_runs_nothing(project: Project, alias: str) -> None:
    subprocess.run(["git", "config", "alias.zz", alias], cwd=project.root, check=True)
    git = subprocess.run(["git", "zz"], cwd=project.root, capture_output=True, text=True)
    assert git.returncode != 0 and "bad alias" in git.stderr  # git refuses it
    res = _guard(project, "git zz")
    assert res.returncode == 0, res.stderr


def test_script_without_commit_is_allowed(project: Project) -> None:
    script = project.write("tools/lint.sh", "#!/bin/sh\nruff check .\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    assert _guard(project, "bash tools/lint.sh").returncode == 0


@pytest.mark.parametrize(
    "cmd",
    [
        "git -c alias.st=commit status",  # git ignores an alias that shadows a builtin
        "git -c user.name=x log -1",
        "git config alias.lg 'log --oneline' && git lg",
        "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.pager GIT_CONFIG_VALUE_0=cat git log -1",
        "timeout 10 make check",
        "nice -n 5 uv run pytest",
        "command -v git",
        "flock 9",
    ],
)
def test_allows_non_commit_variants(project: Project, cmd: str) -> None:
    res = _guard(project, cmd)
    assert res.returncode == 0, (cmd, res.stderr)
    assert "[WARN]" not in res.stdout


# ── (b) commit-producing and publication operations: WARN (plan 0.5) ───────


@pytest.mark.parametrize(
    ("cmd", "what"),
    [
        ("git commit-tree HEAD^{tree} -p HEAD -m y", "commit-tree"),
        ("git update-ref refs/heads/main abc123", "update-ref"),
        ("git revert HEAD", "revert"),
        ("git cherry-pick abc123", "cherry-pick"),
        ("git pull", "pull"),
        ("git pull --rebase origin main", "pull"),
        ("git merge origin/main", "merge"),
        ("git rebase main", "rebase"),
        ("git rebase --continue", "rebase"),
        ("git push", "push"),
        ("git push -u origin feat/x", "push"),
        ("timeout 30 git push", "push"),
        ("bash -c 'git push --force'", "push"),
        ("gh pr create --title 'fix: x' --body ok", "gh pr create"),
        ("gh pr merge 12 --squash", "gh pr merge"),
        ("git -c alias.up=push up", "push"),
    ],
)
def test_warns_on_commit_producing_and_publication(project: Project, cmd: str, what: str) -> None:
    msg = _warning(_guard(project, cmd))
    assert "[WARN]" in msg and what in msg, (cmd, msg)


def test_warn_is_given_once_per_call(project: Project) -> None:
    msg = _warning(_guard(project, "git pull && git push && git push"))
    assert msg.count("[WARN]") == 2, msg  # pull and push, push once


@pytest.mark.parametrize(
    "cmd",
    [
        "git revert --abort",
        "git rebase --abort",
        "git merge --abort",
        "git cherry-pick --quit",
        "git revert -n HEAD",
        "git cherry-pick --no-commit abc123",
        "git push --dry-run",
        "git fetch origin",
        "git log --grep push",
        "gh pr view 12",
        "echo 'git push'",
    ],
)
def test_no_warn_for_operations_that_make_no_commit(project: Project, cmd: str) -> None:
    res = _guard(project, cmd)
    assert res.returncode == 0, (cmd, res.stderr)
    assert "[WARN]" not in res.stdout, (cmd, res.stdout)


def test_attribution_still_blocks_a_push_or_pr_merge(project: Project) -> None:
    res = _guard(project, "gh pr merge 3 --body 'Generated with Claude Code'")
    assert res.returncode == BLOCKED and "attribution" in res.stderr


# ── settings.json: allow list narrowed, deny list added ────────────────────


def _permissions() -> dict[str, list[str]]:
    data = json.loads((TOOLKIT / "settings.json").read_text(encoding="utf-8"))
    perms: dict[str, list[str]] = data["permissions"]
    return perms


def test_allow_list_names_concrete_targets_and_tools() -> None:
    allow = _permissions()["allow"]
    assert "Bash(make:*)" not in allow and "Bash(uv run:*)" not in allow
    for rule in ("Bash(make check)", "Bash(make test)", "Bash(uv run pytest:*)"):
        assert rule in allow, rule


def test_deny_list_covers_what_the_guard_blocks_without_a_valve() -> None:
    deny = _permissions()["deny"]
    for rule in (
        "Bash(git am:*)",
        "Bash(git apply:*)",
        "Bash(./harness/review_with_codex.sh seal:*)",
    ):
        assert rule in deny, rule
    # the escape-valve commit and the WARN-only operations stay out of the deny list
    assert not [r for r in deny if "commit" in r or "push" in r or "gh pr" in r], deny


# ── (d) outcome check in make check: commits the close did not make ────────


def _integrity(project: Project) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("HARNESS_")}
    return subprocess.run(
        [sys.executable, str(TOOLKIT / "lib/_guard.py"), "integrity"],
        cwd=project.root,
        text=True,
        capture_output=True,
        check=False,
        env={**env, "CLAUDE_PROJECT_DIR": str(project.root)},
    )


def _branch(project: Project) -> None:
    subprocess.run(["git", "switch", "-qc", "feat/1.1-users"], cwd=project.root, check=True)


def test_close_commit_passes_the_outcome_check(project: Project) -> None:
    _branch(project)
    project.set_leaf("done")
    project.commit_all(f"feat: user entity [{LEAF_ID}]")
    res = _integrity(project)
    assert "not made by the harness close" not in res.stdout, res.stdout


def test_stray_commit_on_the_branch_warns(project: Project) -> None:
    _branch(project)
    project.write("notes.txt", "x\n")
    project.commit_all("wip by hand")
    res = _integrity(project)
    assert res.returncode == 0, res.stdout  # WARN only (plan 0.5)
    line = next(ln for ln in res.stdout.splitlines() if "not made by the harness close" in ln)
    assert "WARN" in line and "wip by hand" in line


def test_forged_close_subject_without_the_done_transition_warns(project: Project) -> None:
    _branch(project)
    project.write("notes.txt", "x\n")
    project.commit_all(f"feat: forged [{LEAF_ID}]")  # leaf still in_progress in that commit
    res = _integrity(project)
    assert res.returncode == 0
    assert "forged" in res.stdout and "not made by the harness close" in res.stdout


def test_merge_of_the_base_is_not_reported(project: Project) -> None:
    _branch(project)
    subprocess.run(["git", "switch", "-q", "main"], cwd=project.root, check=True)
    project.write("other.txt", "x\n")
    project.commit_all("main moved")
    subprocess.run(["git", "switch", "-q", "feat/1.1-users"], cwd=project.root, check=True)
    subprocess.run(
        ["git", "merge", "-q", "--no-edit", "--no-ff", "main"],
        cwd=project.root,
        check=True,
        capture_output=True,
    )
    res = _integrity(project)
    assert "not made by the harness close" not in res.stdout, res.stdout


def _git_in(project: Project, *args: str) -> None:
    subprocess.run(["git", *args], cwd=project.root, check=True, capture_output=True)


def _with_origin(project: Project, tmp_path: Path) -> None:
    remote = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    _git_in(project, "remote", "add", "origin", str(remote))


def test_commit_already_on_the_remote_base_is_not_reported(
    project: Project, tmp_path: Path
) -> None:
    """Issue #119: pre-harness code pushed to origin/main is base, not branch work,
    even when the local main lags behind (the base is read from the remote first)."""
    _with_origin(project, tmp_path)
    project.write("legacy.txt", "x\n")
    project.commit_all("pre-harness code")
    _git_in(project, "push", "-q", "origin", "HEAD:main")
    _git_in(project, "reset", "-q", "--hard", "HEAD~1")  # local main behind origin/main
    _git_in(project, "switch", "-qc", "feat/1.1-users", "origin/main")
    res = _integrity(project)
    assert "not made by the harness close" not in res.stdout, res.stdout


def test_commit_pushed_off_the_base_still_warns(project: Project, tmp_path: Path) -> None:
    """Issue #119: being pushed is not enough; a commit outside the close that the
    base does not contain (here, on another remote branch) is still reported."""
    _with_origin(project, tmp_path)
    _git_in(project, "push", "-q", "origin", "main")
    _git_in(project, "switch", "-qc", "chore/project-setup")
    project.write("legacy.txt", "x\n")
    project.commit_all("pre-harness code")
    _git_in(project, "push", "-q", "origin", "chore/project-setup")
    _branch(project)
    res = _integrity(project)
    assert res.returncode == 0, res.stdout  # WARN only (plan 0.5)
    line = next(ln for ln in res.stdout.splitlines() if "not made by the harness close" in ln)
    assert "WARN" in line and "pre-harness code" in line


def test_outcome_check_covers_a_long_branch(project: Project) -> None:
    _branch(project)
    project.write("notes.txt", "x\n")
    project.commit_all("old stray")
    for n in range(210):
        subprocess.run(
            ["git", "commit", "--allow-empty", "-qm", f"filler {n}"], cwd=project.root, check=True
        )
    assert "'old stray'" in _integrity(project).stdout


def test_outcome_check_reads_subjects_with_unicode_separators(project: Project) -> None:
    _branch(project)
    project.write("notes.txt", "x\n")
    project.commit_all("odd\u2028subject")
    res = _integrity(project)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "not made by the harness close" in res.stdout


@pytest.mark.parametrize("sub", ["record 1.1.1 0 /tmp/log 1", "approve 1.1.1 1", "drop 1.1.1"])
def test_gate_run_records_are_sealers(project: Project, sub: str) -> None:
    for cmd in (f"python3 harness/_gate_run.py {sub}", f"uv run harness/_gate_run.py {sub}"):
        res = _guard(project, cmd)
        assert res.returncode == BLOCKED and "sealing" in res.stderr, (cmd, res.stderr)


def test_gate_run_fresh_stays_allowed(project: Project) -> None:
    assert _guard(project, "python3 harness/_gate_run.py fresh 1.1.1").returncode == 0
