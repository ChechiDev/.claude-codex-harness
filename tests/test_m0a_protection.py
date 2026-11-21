"""M0a (#14), block 3: backups stay out of git, the record is committed, the
migration level only WARNs, Claude cannot write .harness/, and the docs say how.

User decision (option A): existing projects ignore .harness/migrations/ through
.git/info/exclude (local, not a project file); fresh installs through .gitignore."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
from conftest import TOOLKIT, Project, bash_payload, write_payload

BLOCKED = 2
LINKS = {
    ".claude/agents": "agents",
    ".claude/codex": "codex",
    ".claude/skills": "skills",
    ".claude/settings.json": "settings.json",
    "harness": "lib",
}
GIT_ENV = {
    "GIT_AUTHOR_NAME": "dev",
    "GIT_AUTHOR_EMAIL": "dev@example.com",
    "GIT_COMMITTER_NAME": "dev",
    "GIT_COMMITTER_EMAIL": "dev@example.com",
}


def _env(tmp_path: Path) -> dict[str, str]:
    empty = tmp_path / "empty.gitconfig"
    empty.touch()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "HARNESS_"))}
    return {
        **env,
        **GIT_ENV,
        "CLAUDE_HOME": str(tmp_path / "claude-home"),
        "GIT_CONFIG_GLOBAL": str(empty),
        "GIT_CONFIG_NOSYSTEM": "1",
    }


def _run(cmd: list[str], cwd: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
        env=_env(tmp_path),
        stdin=subprocess.DEVNULL,
    )


def _install(root: Path, tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return _run(["bash", str(TOOLKIT / "install.sh"), str(root), *args], tmp_path, tmp_path)


def _files_and_links(root: Path, tmp_path: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for name in ("Makefile", "CLAUDE.md", "AGENTS.md"):
        (root / name).write_text((TOOLKIT / "templates" / name).read_text())
    for rel, target in LINKS.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).symlink_to(TOOLKIT / target)
    (tmp_path / "claude-home").mkdir(exist_ok=True)


def _existing_repo(tmp_path: Path) -> Path:
    """A pre-M0a project in its own git repo, with history and the links."""
    root = tmp_path / "proj"
    root.mkdir()
    for name in ("Makefile", "CLAUDE.md", "AGENTS.md"):
        (root / name).write_text((TOOLKIT / "templates" / name).read_text())
    (root / ".gitignore").write_text(".env\n")
    for rel, target in LINKS.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).symlink_to(TOOLKIT / target)
    (tmp_path / "claude-home").mkdir(exist_ok=True)
    _run(["git", "init", "-q", "-b", "main"], root, tmp_path)
    _run(["git", "add", "Makefile", "CLAUDE.md", "AGENTS.md", ".gitignore"], root, tmp_path)
    _run(["git", "commit", "-qm", "base"], root, tmp_path)
    return root


def _ignored(root: Path, rel: str, tmp_path: Path) -> bool:
    return _run(["git", "check-ignore", "-q", rel], root, tmp_path).returncode == 0


def _upgrade() -> ModuleType:
    sys.path.insert(0, str(TOOLKIT / "lib"))
    spec = importlib.util.spec_from_file_location("_upgrade_m0a_b3", TOOLKIT / "lib/_upgrade.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── backups out of git, record in git ────────────────────────────────────────


@pytest.mark.e2e
def test_a_fresh_install_ignores_backups_and_commits_the_record(tmp_path: Path) -> None:
    dest = tmp_path / "fresh"
    res = _install(dest, tmp_path, "--lang", "python")
    assert (dest / ".git").is_dir(), res.stdout + res.stderr
    assert ".harness/migrations/" in (dest / ".gitignore").read_text().splitlines()
    tracked = _run(["git", "ls-files"], dest, tmp_path).stdout.split()
    assert ".harness/templates.json" in tracked
    assert _ignored(dest, ".harness/migrations/x/manifest.json", tmp_path)


@pytest.mark.e2e
def test_an_existing_project_ignores_backups_through_info_exclude(tmp_path: Path) -> None:
    root = _existing_repo(tmp_path)
    exclude = root / ".git/info/exclude"
    before = exclude.read_text() if exclude.exists() else ""
    gitignore = (root / ".gitignore").read_text()

    dry = _install(root, tmp_path, "--migrate")
    assert dry.returncode == 0, dry.stdout + dry.stderr
    assert ".git/info/exclude" in dry.stdout and ".harness/migrations/" in dry.stdout
    assert (exclude.read_text() if exclude.exists() else "") == before  # dry-run

    res = _install(root, tmp_path, "--migrate", "--apply", "--yes")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "/.harness/migrations/" in exclude.read_text().splitlines()
    assert (root / ".gitignore").read_text() == gitignore  # project file untouched
    assert _ignored(root, ".harness/migrations/x/manifest.json", tmp_path)
    assert not _ignored(root, ".harness/templates.json", tmp_path)


@pytest.mark.e2e
def test_the_exclude_entry_is_added_once(tmp_path: Path) -> None:
    root = _existing_repo(tmp_path)
    assert _install(root, tmp_path, "--migrate", "--apply", "--yes").returncode == 0
    (root / ".harness/templates.json").unlink()  # force a second migration
    assert _install(root, tmp_path, "--migrate", "--apply", "--yes").returncode == 0
    lines = (root / ".git/info/exclude").read_text().splitlines()
    assert lines.count("/.harness/migrations/") == 1


@pytest.mark.e2e
def test_a_monorepo_subdir_gets_an_anchored_entry(tmp_path: Path) -> None:
    mono = tmp_path / "mono"
    mono.mkdir()
    _run(["git", "init", "-q", "-b", "main"], mono, tmp_path)
    (mono / "README").write_text("x\n")
    _run(["git", "add", "README"], mono, tmp_path)
    _run(["git", "commit", "-qm", "base"], mono, tmp_path)
    root = mono / "sub"
    _files_and_links(root, tmp_path)
    res = _install(root, tmp_path, "--migrate", "--apply", "--yes")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "/sub/.harness/migrations/" in (mono / ".git/info/exclude").read_text().splitlines()
    assert _ignored(mono, "sub/.harness/migrations/x/manifest.json", tmp_path)
    assert not _ignored(mono, ".harness/migrations/x/manifest.json", tmp_path)  # anchored
    assert not (root / ".git").exists()


@pytest.mark.e2e
def test_a_worktree_project_uses_the_shared_exclude(tmp_path: Path) -> None:
    main = _existing_repo(tmp_path)
    wt = tmp_path / "wt"
    _run(["git", "worktree", "add", "-q", str(wt)], main, tmp_path)
    assert (wt / ".git").is_file()
    for rel, target in LINKS.items():
        (wt / rel).parent.mkdir(parents=True, exist_ok=True)
        (wt / rel).symlink_to(TOOLKIT / target)
    res = _install(wt, tmp_path, "--migrate", "--apply", "--yes")
    assert res.returncode == 0, res.stdout + res.stderr
    assert (wt / ".git").is_file()  # still a worktree link file, never rewritten
    assert _ignored(wt, ".harness/migrations/x/manifest.json", tmp_path)
    git_path = _run(["git", "rev-parse", "--git-path", "info/exclude"], wt, tmp_path).stdout.strip()
    exclude = Path(git_path) if Path(git_path).is_absolute() else wt / git_path
    assert "/.harness/migrations/" in exclude.read_text().splitlines()


@pytest.mark.e2e
def test_a_project_without_git_writes_no_ignore_and_says_what_to_do(tmp_path: Path) -> None:
    root = tmp_path / "nogit"
    _files_and_links(root, tmp_path)
    res = _install(root, tmp_path, "--migrate", "--apply", "--yes")
    out = res.stdout + res.stderr
    assert res.returncode == 0, out
    assert "not a git repo" in out and ".git/info/exclude" in out
    assert not (root / ".git").exists() and not (root / ".gitignore").exists()


@pytest.mark.e2e
def test_git_add_stages_the_record_and_never_a_backup(tmp_path: Path) -> None:
    root = _existing_repo(tmp_path)
    assert _install(root, tmp_path, "--migrate", "--apply", "--yes").returncode == 0
    _run(["git", "add", "-A"], root, tmp_path)
    staged = _run(["git", "diff", "--cached", "--name-only"], root, tmp_path).stdout.split()
    assert ".harness/templates.json" in staged
    assert not [p for p in staged if p.startswith(".harness/migrations/")]


@pytest.mark.e2e
def test_rollback_keeps_the_exclusion(tmp_path: Path) -> None:
    root = _existing_repo(tmp_path)
    assert _install(root, tmp_path, "--migrate", "--apply", "--yes").returncode == 0
    mig = sorted((root / ".harness/migrations").iterdir())[0].name
    res = _install(root, tmp_path, "--rollback", mig, "--apply", "--yes")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "/.harness/migrations/" in (root / ".git/info/exclude").read_text().splitlines()


# ── the record is validated when read ────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        "[]",
        '{"migration_level": 0, "set": "templates"}',
        '{"migration_level": 0, "set": "nope", "templates": {}}',
        '{"migration_level": "0", "set": "templates", "templates": {}}',
        '{"migration_level": 0, "set": "templates", "templates": {"Makefile": 3}}',
        '{"migration_level": 0, "set": "templates", "templates": '
        '{"Makefile": {"id": "makefile-guard", "mode": "fragments", "state": "weird"}}}',
        '{"migration_level": 0, "set": "templates", "templates": '
        '{"Makefile": {"id": "other-id", "mode": "fragments", "state": "absent"}}}',
        '{"migration_level": 0, "set": "templates", "templates": '
        '{"Makefile": {"id": "makefile-guard", "mode": "whole", "state": "absent"}}}',
        '{"migration_level": 0, "set": "templates", "templates": {"Makefile": {"id": '
        '"makefile-guard", "mode": "fragments", "state": "installed", "version": "9", '
        '"sha256": "x"}}}',
        '{"migration_level": 0, "set": "templates", "templates": {"Makefile": {"id": '
        '"makefile-guard", "mode": "fragments", "state": "installed", "version": "2", '
        '"sha256": "deadbeef"}}}',
    ],
)
def test_a_corrupt_record_stops_cleanly(tmp_path: Path, content: str) -> None:
    root = _existing_repo(tmp_path)
    (root / ".harness").mkdir()
    (root / ".harness/templates.json").write_text(content)
    before = {p: (root / p).read_text() for p in ("Makefile", "CLAUDE.md", "AGENTS.md")}
    res = _install(root, tmp_path, "--migrate", "--apply", "--yes")
    out = res.stdout + res.stderr
    assert res.returncode == 1, out
    assert "templates.json" in out and "Traceback" not in out
    assert {p: (root / p).read_text() for p in before} == before
    assert not (root / ".harness/migrations").exists()


# ── migration level: WARN only ───────────────────────────────────────────────


def _registered(tmp_path: Path) -> Path:
    root = _existing_repo(tmp_path)
    assert _install(root, tmp_path, "--migrate", "--apply", "--yes").returncode == 0
    return root


@pytest.mark.e2e
def test_level_at_the_required_one_is_quiet(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _registered(tmp_path)
    assert _upgrade().check_level(root) == 0
    assert "WARN" not in capsys.readouterr().out


@pytest.mark.e2e
def test_level_below_the_required_one_warns_and_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _registered(tmp_path)
    assert json.loads((root / ".harness/templates.json").read_text())["migration_level"] == 0
    up = _upgrade()
    monkeypatch.setattr(up, "REQUIRED_MIGRATION_LEVEL", 1)
    assert up.check_level(root) == 0
    out = capsys.readouterr().out
    assert "WARN" in out and "--migrate" in out


def test_no_record_warns_and_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _existing_repo(tmp_path)
    assert _upgrade().check_level(root) == 0
    out = capsys.readouterr().out
    assert "WARN" in out and "--migrate" in out


@pytest.mark.e2e
def test_make_check_reports_the_level(tmp_path: Path) -> None:
    root = _existing_repo(tmp_path)  # no record yet
    env = {**_env(tmp_path), "CLAUDE_PROJECT_DIR": str(root)}
    res = subprocess.run(
        ["bash", str(root / "harness/check.sh")],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    out = res.stdout + res.stderr
    assert "Migration level" in out
    assert "WARN" in out and "--migrate" in out


# ── Claude cannot write .harness/ ────────────────────────────────────────────


@pytest.mark.parametrize(
    "rel", [".harness/templates.json", ".harness/migrations/20261104T000000Z-x/manifest.json"]
)
def test_claude_cannot_write_the_record_or_backups(project: Project, rel: str) -> None:
    res = project.run("guard_write.sh", write_payload(rel))
    assert res.returncode == BLOCKED, res.stderr


@pytest.mark.parametrize(
    "cmd",
    [
        "echo x > .harness/templates.json",
        "printf '{}' >> .harness/templates.json",
        "rm -rf .harness",
        "rm .harness/migrations/a/manifest.json",
        "rm -rf .harness/migrations",
        "python3 -c \"open('.harness/templates.json', 'w').write('{}')\"",
        "ls .harness/migrations | xargs rm -rf",
    ],
)
def test_claude_cannot_change_harness_state_through_bash(project: Project, cmd: str) -> None:
    (project.root / ".harness/migrations/a").mkdir(parents=True)
    res = project.run("guard_bash.sh", bash_payload(cmd))
    assert res.returncode == BLOCKED, (cmd, res.stderr)


def test_reading_the_record_stays_allowed(project: Project) -> None:
    res = project.run("guard_bash.sh", bash_payload("cat .harness/templates.json"))
    assert res.returncode == 0, res.stderr


# ── docs ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "needle",
    [
        "--migrate --apply",
        "--rollback",
        ".harness/templates.json",
        ".harness/migrations/",
        ".git/info/exclude",
        "human-only",
        "migration level",
    ],
)
def test_usage_documents_the_migration(needle: str) -> None:
    assert needle in (TOOLKIT / "docs/usage.md").read_text()
