"""I3a (A/B): harness-owned paths — toolkit links, settings and the files behind them."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from conftest import DECLARED, Project, bash_payload, write_payload
from i3a_helpers import (
    BLOCKED,
    HARNESS_MSG,
    copy_toolkit,
    git,
    install_links,
    installed,
)


def _blocked_as_harness(res: subprocess.CompletedProcess[str], what: str) -> None:
    assert res.returncode == BLOCKED, (what, res.stderr)
    assert HARNESS_MSG in res.stderr, (what, res.stderr)


def _write(project: Project, path: str | Path) -> subprocess.CompletedProcess[str]:
    return project.run("guard_write.sh", write_payload(path))


def _bash(project: Project, cmd: str) -> subprocess.CompletedProcess[str]:
    return project.run("guard_bash.sh", bash_payload(cmd))


# ── Acceptance 1: every "allowed" row of the issue's Context table ──────────


@pytest.mark.parametrize(
    "rel", [".claude/settings.json", "harness/_guard.py", ".claude/settings.local.json"]
)
def test_context_rows_blocked_edit_write(project: Project, tmp_path: Path, rel: str) -> None:
    installed(project, tmp_path)
    _blocked_as_harness(_write(project, rel), rel)


@pytest.mark.parametrize(
    "cmd",
    [
        "echo x > .claude/settings.local.json",
        "echo x >> .claude/settings.json",
        "sed -i 's/a/b/' harness/_guard.py",
        "rm .claude/skills",
        "ln -sfn /tmp .claude/skills",
        "mv harness h2",
    ],
)
def test_context_rows_blocked_bash(project: Project, tmp_path: Path, cmd: str) -> None:
    installed(project, tmp_path)
    _blocked_as_harness(_bash(project, cmd), cmd)


# ── Acceptance 2: other spellings, aliases, per-skill shape ─────────────────


@pytest.mark.e2e
def test_other_spellings_blocked(project: Project, tmp_path: Path) -> None:
    installed(project, tmp_path)
    for spelling in (
        str(project.root / "harness/_guard.py"),
        "./harness/_guard.py",
        "src/../harness/_guard.py",
        "./.claude/settings.json",
    ):
        _blocked_as_harness(_write(project, spelling), spelling)
        _blocked_as_harness(_bash(project, f"echo x > {spelling}"), spelling)


def test_new_link_into_toolkit_blocked(project: Project, tmp_path: Path) -> None:
    installed(project, tmp_path)
    (project.root / "tools").symlink_to(project.root / "harness")
    _blocked_as_harness(_write(project, "tools/_guard.py"), "tools/_guard.py")
    _blocked_as_harness(_bash(project, "echo x > tools/_guard.py"), "bash tools/_guard.py")


def test_alias_to_settings_local_blocked(project: Project, tmp_path: Path) -> None:
    installed(project, tmp_path)
    (project.root / "cfg.json").symlink_to(project.root / ".claude/settings.local.json")
    _blocked_as_harness(_write(project, "cfg.json"), "cfg.json")
    _blocked_as_harness(_bash(project, "echo x > cfg.json"), "bash cfg.json")


@pytest.mark.parametrize(
    "cmd",
    [
        "unlink .claude/settings.json",
        "cp /etc/hostname .claude/settings.local.json",
        "install -m 644 /etc/hostname .claude/settings.local.json",
        "mkdir .claude/settings.local.json",
        "rmdir .claude/skills",
        "python3 -c \"open('harness/_guard.py','w')\"",
        "tee .claude/settings.local.json < /dev/null",
    ],
)
def test_link_ops_cover_all_verbs(project: Project, tmp_path: Path, cmd: str) -> None:
    installed(project, tmp_path)
    _blocked_as_harness(_bash(project, cmd), cmd)


@pytest.mark.e2e
def test_per_skill_shape(project: Project, tmp_path: Path) -> None:
    installed(project, tmp_path)
    (project.root / ".claude/skills").unlink()
    (project.root / ".claude/skills/docs").mkdir(parents=True)
    for rel in (".claude/skills/docs/SKILL.md", ".claude/skills/new/SKILL.md"):
        res = _write(project, rel)
        assert "Traceback" not in res.stderr, res.stderr
        _blocked_as_harness(res, rel)
    _blocked_as_harness(_bash(project, "rm -r .claude/skills"), "rm -r .claude/skills")


# ── Acceptance 3: machine-level links in $CLAUDE_HOME ───────────────────────


@pytest.mark.e2e
@pytest.mark.parametrize("name", ["skills", "CLAUDE.md"])
def test_global_links_blocked(project: Project, tmp_path: Path, name: str) -> None:
    installed(project, tmp_path)
    link = f"{project.env['CLAUDE_HOME']}/{name}"
    inner = f"{link}/docs/SKILL.md" if name == "skills" else link
    _blocked_as_harness(_write(project, inner), inner)
    for cmd in (f"echo x > {inner}", f"rm {link}", f"ln -sfn /tmp {link}", f"mv {link} /tmp/x"):
        _blocked_as_harness(_bash(project, cmd), cmd)


# ── Acceptance 4: toolkit boundary ──────────────────────────────────────────


def _run_hook(
    hook: Path, root: Path, payload: dict[str, object], env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    import json

    base = {k: v for k, v in os.environ.items() if not k.startswith("HARNESS_")}
    return subprocess.run(
        ["bash", str(hook)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=root,
        env={**base, "CLAUDE_PROJECT_DIR": str(root), **env},
        check=False,
    )


@pytest.mark.e2e
def test_toolkit_worktree_detected(project: Project, tmp_path: Path) -> None:
    """The guard running from a toolkit git worktree protects that worktree's files."""
    main = tmp_path / "tk-main"
    main.mkdir()
    git(main, "init", "-q", "-b", "main")
    git(main, "config", "user.email", "dev@example.com")
    git(main, "config", "user.name", "dev")
    copy_toolkit(main)
    git(main, "add", "-A")
    git(main, "commit", "-qm", "tk")
    wt = tmp_path / "tk-wt"
    git(main, "worktree", "add", "-q", str(wt))
    for rel in ("harness", ".claude/codex"):
        (project.root / rel).unlink()
    install_links(project.root, wt)
    res = _run_hook(wt / "lib/guard_write.sh", project.root, write_payload("harness/_guard.py"), {})
    _blocked_as_harness(res, "worktree toolkit")


def test_project_inside_toolkit_writes_own_files(tmp_path: Path) -> None:
    tk = copy_toolkit(tmp_path / "tk")
    root = tk / "proj"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    install_links(root, tk)
    proj = Project(root)
    proj.write(".gitignore", "harness\n.claude/\n")
    proj.write(DECLARED, "")
    proj.set_leaf()
    proj.write("progress/plan_review_1.1.1.md", "# Review\n\n**Verdict:** PLAN_APPROVED\n")
    own = _run_hook(tk / "lib/guard_write.sh", root, write_payload(DECLARED), {})
    assert own.returncode == 0, own.stderr
    harness = _run_hook(tk / "lib/guard_write.sh", root, write_payload("harness/_guard.py"), {})
    _blocked_as_harness(harness, "lexical rule inside toolkit")


# ── Acceptance 5: reads/execution allowed, destructive ops on .claude blocked ─


@pytest.mark.parametrize(
    "cmd",
    [
        "cat harness/_guard.py",
        "ls -l .claude",
        "readlink .claude/skills",
        "./harness/review_with_codex.sh status",
        "mkdir -p .claude",
        "echo x > .claude/commands/x.md",
    ],
)
def test_reads_and_exec_allowed(project: Project, tmp_path: Path, cmd: str) -> None:
    installed(project, tmp_path)
    res = _bash(project, cmd)
    assert res.returncode == 0, (cmd, res.stderr)


def test_writing_claude_commands_allowed(project: Project, tmp_path: Path) -> None:
    installed(project, tmp_path)
    res = _write(project, ".claude/commands/x.md")
    assert res.returncode == 0, res.stderr


@pytest.mark.parametrize("cmd", ["rm -r .claude", "mv .claude x", "rm -rf {home}"])
def test_destructive_on_claude_dir_blocked(project: Project, tmp_path: Path, cmd: str) -> None:
    installed(project, tmp_path)
    cmd = cmd.format(home=project.env["CLAUDE_HOME"])
    _blocked_as_harness(_bash(project, cmd), cmd)


# ── Review r1: directory destinations and interpreter false positives ───────


@pytest.mark.e2e
@pytest.mark.parametrize(
    "cmd",
    [
        "cp /etc/hostname /tmp/settings.json .claude/",
        "cp /tmp/settings.local.json .claude/",
        "cp /tmp/settings.json .claude",
        "cp -t .claude /tmp/settings.json",
        "cp --target-directory=.claude /tmp/settings.local.json",
        "install -m 644 /tmp/settings.json .claude/",
        "ln -sf /tmp/skills .claude/",
        "cp /tmp/CLAUDE.md {home}/",
    ],
)
def test_directory_destination_blocked(project: Project, tmp_path: Path, cmd: str) -> None:
    installed(project, tmp_path)
    cmd = cmd.format(home=project.env["CLAUDE_HOME"])
    _blocked_as_harness(_bash(project, cmd), cmd)


@pytest.mark.parametrize(
    "cmd",
    ["cp /tmp/x.md .claude/", "cp /tmp/x.md .claude/commands/"],
)
def test_directory_destination_other_names_allowed(
    project: Project, tmp_path: Path, cmd: str
) -> None:
    installed(project, tmp_path)
    (project.root / ".claude/commands").mkdir()
    res = _bash(project, cmd)
    assert res.returncode == 0, (cmd, res.stderr)


def test_interpreter_write_under_nested_harness_dir_allowed(
    project: Project, tmp_path: Path
) -> None:
    installed(project, tmp_path)
    res = _bash(project, "python3 -c \"open('docs/harness/output.txt','w')\"")
    assert res.returncode == 0, res.stderr


def test_interpreter_write_absolute_harness_path_blocked(project: Project, tmp_path: Path) -> None:
    installed(project, tmp_path)
    cmd = f"python3 -c \"open('{project.root}/.claude/settings.local.json','w')\""
    _blocked_as_harness(_bash(project, cmd), cmd)


# ── Review r2: rsync, directory-contents copies, `..` spellings ─────────────


@pytest.mark.e2e
@pytest.mark.parametrize(
    "cmd",
    [
        "rsync -t /tmp/settings.local.json .claude/",
        "rsync -a /tmp/seed/ .claude/",
        "rsync -a /tmp/seed .claude",
        "cp -R /tmp/seed/. .claude/",
        "cp -R /tmp/seed/. {home}/",
        "rsync -a /tmp/seed/ .",
        "python3 -c \"open('docs/../harness/_guard.py','w')\"",
    ],
)
def test_review_r2_routes_blocked(project: Project, tmp_path: Path, cmd: str) -> None:
    installed(project, tmp_path)
    cmd = cmd.format(home=project.env["CLAUDE_HOME"])
    _blocked_as_harness(_bash(project, cmd), cmd)


@pytest.mark.e2e
@pytest.mark.parametrize(
    "cmd",
    [
        "rsync -a /tmp/seed/ docs/",
        "cp -R /tmp/seed/. build/",
        "cp -t docs /tmp/x.md",
        "cp /tmp/x.md .claude/",
    ],
)
def test_review_r2_other_destinations_allowed(project: Project, tmp_path: Path, cmd: str) -> None:
    installed(project, tmp_path)
    for rel in ("docs", "build"):
        (project.root / rel).mkdir(exist_ok=True)
    res = _bash(project, cmd)
    assert res.returncode == 0, (cmd, res.stderr)


# ── Review r3: a directory alias as the destination of a contents copy ──────


@pytest.mark.parametrize("target", ["claude", "home"])
@pytest.mark.parametrize("cmd", ["rsync -a /tmp/seed/ alias/", "cp -R /tmp/seed/. alias/"])
def test_contents_copy_through_dir_alias_blocked(
    project: Project, tmp_path: Path, target: str, cmd: str
) -> None:
    installed(project, tmp_path)
    dest = project.root / ".claude" if target == "claude" else Path(project.env["CLAUDE_HOME"])
    (project.root / "alias").symlink_to(dest)
    _blocked_as_harness(_bash(project, cmd), cmd)
