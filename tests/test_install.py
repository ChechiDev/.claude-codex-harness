"""install.sh: locale safety, an empty plan, and a ready-to-work git base."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import TOOLKIT

IDENTITY = {
    "GIT_AUTHOR_NAME": "dev",
    "GIT_AUTHOR_EMAIL": "dev@example.com",
    "GIT_COMMITTER_NAME": "dev",
    "GIT_COMMITTER_EMAIL": "dev@example.com",
}
LANG_ARGS = {"python": ["--lang", "python"], "c": ["--c", "demo"]}


def _env(tmp_path: Path, identity: bool = True) -> dict[str, str]:
    # Isolated from the machine's ~/.claude and ~/.gitconfig.
    empty_cfg = tmp_path / "empty.gitconfig"
    empty_cfg.touch()
    base = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "HARNESS_"))}
    env = {
        **base,
        "CLAUDE_HOME": str(tmp_path / "claude-home"),
        "GIT_CONFIG_GLOBAL": str(empty_cfg),
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    return {**env, **IDENTITY} if identity else env


def _install(dest: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(TOOLKIT / "install.sh"), str(dest), *args],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _git(dest: Path, env: dict[str, str], *args: str) -> str:
    res = subprocess.run(
        ["git", *args], cwd=dest, env=env, text=True, capture_output=True, check=True
    )
    return res.stdout.strip()


def test_children_keep_the_callers_locale(tmp_path: Path) -> None:
    # A python3 shim first in PATH records the LANG each child process sees.
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    record = tmp_path / "lang.txt"
    real_python = shutil.which("python3")
    assert real_python
    shim = shim_dir / "python3"
    shim.write_text(f'#!/usr/bin/env bash\necho "$LANG" >> "{record}"\nexec "{real_python}" "$@"\n')
    shim.chmod(0o755)

    env = {**_env(tmp_path), "PATH": f"{shim_dir}:{os.environ['PATH']}", "LANG": "C.UTF-8"}
    res = _install(tmp_path / "proj", env, "--lang", "python")

    assert res.returncode == 0, res.stderr
    seen = set(record.read_text().split())
    assert seen == {"C.UTF-8"}
    assert res.stdout.startswith("Installing harness (python) into ")


@pytest.mark.parametrize("lang", sorted(LANG_ARGS))
def test_fresh_install_has_an_empty_plan(tmp_path: Path, lang: str) -> None:
    dest = tmp_path / "proj"
    res = _install(dest, _env(tmp_path), *LANG_ARGS[lang])
    assert res.returncode == 0, res.stderr
    assert list((dest / "docs" / "plan").glob("phase-*.md")) == []
    assert json.loads((dest / "feature_list.json").read_text())["features"] == []


@pytest.mark.parametrize("lang", sorted(LANG_ARGS))
def test_fresh_repo_gets_root_commit_and_setup_branch(tmp_path: Path, lang: str) -> None:
    dest = tmp_path / "proj"
    env = _env(tmp_path)
    res = _install(dest, env, *LANG_ARGS[lang])
    assert res.returncode == 0, res.stderr

    assert _git(dest, env, "rev-list", "--count", "main") == "1"
    message = _git(dest, env, "log", "-1", "--format=%B", "main")
    assert message == "chore: install harness"
    assert _git(dest, env, "branch", "--show-current") == "chore/project-setup"
    assert _git(dest, env, "status", "--porcelain") == ""


def test_existing_history_is_left_untouched(tmp_path: Path) -> None:
    dest = tmp_path / "proj"
    dest.mkdir()
    env = _env(tmp_path)
    _git(dest, env, "init", "-q", "-b", "main")
    (dest / "README.md").write_text("mine\n")
    _git(dest, env, "add", "-A")
    _git(dest, env, "commit", "-qm", "my history")
    head = _git(dest, env, "rev-parse", "HEAD")

    res = _install(dest, env, "--c", "demo")

    assert res.returncode == 0, res.stderr
    assert _git(dest, env, "rev-parse", "HEAD") == head
    assert _git(dest, env, "branch", "--show-current") == "main"


def test_missing_git_identity_skips_the_commit_without_failing(tmp_path: Path) -> None:
    dest = tmp_path / "proj"
    env = _env(tmp_path, identity=False)
    res = _install(dest, env, "--c", "demo")

    assert res.returncode == 0, res.stderr
    assert "git identity" in res.stdout
    log = subprocess.run(["git", "log"], cwd=dest, env=env, capture_output=True, check=False)
    assert log.returncode != 0  # still no commits


@pytest.mark.parametrize("lang", sorted(LANG_ARGS))
def test_root_commit_never_contains_secrets(tmp_path: Path, lang: str) -> None:
    dest = tmp_path / "proj"
    dest.mkdir()
    (dest / ".env").write_text("TOKEN=secret\n")
    env = _env(tmp_path)

    res = _install(dest, env, *LANG_ARGS[lang])

    assert res.returncode == 0, res.stderr
    tracked = _git(dest, env, "ls-files").splitlines()
    assert ".env" not in tracked
    assert ".gitignore" in tracked


def test_c_gitignore_covers_build_artifacts(tmp_path: Path) -> None:
    dest = tmp_path / "proj"
    assert _install(dest, _env(tmp_path), "--c", "demo").returncode == 0
    ignored = (dest / ".gitignore").read_text().splitlines()
    assert {".env", "*.o", "*.bin"} <= set(ignored)


def test_gitignore_is_appended_not_duplicated(tmp_path: Path) -> None:
    dest = tmp_path / "proj"
    dest.mkdir()
    (dest / ".gitignore").write_text("mine/\n.env\n")
    env = _env(tmp_path)
    assert _install(dest, env, "--c", "demo").returncode == 0
    assert _install(dest, env, "--c", "demo").returncode == 0
    lines = (dest / ".gitignore").read_text().splitlines()
    assert lines[0] == "mine/"
    assert lines.count(".env") == 1
    assert lines.count("*.o") == 1


def test_output_is_one_line_per_step(tmp_path: Path) -> None:
    dest = tmp_path / "proj"
    env = _env(tmp_path)
    # Machine already linked (the usual case): link-global then has nothing to report.
    subprocess.run(["bash", str(TOOLKIT / "link-global.sh")], env=env, capture_output=True)
    res = _install(dest, env, "--c", "demo")
    assert res.returncode == 0, res.stderr
    lines = res.stdout.splitlines()
    assert lines[0] == f"Installing harness (c) into {dest}"
    assert [line.split()[0] for line in lines[1:]] == [
        "[symlink]",
        "[copy]",
        "[c]",
        "[gen]",
        "[git]",
        "[OK]",
    ]
    assert lines[-1] == "[OK]      harness ready · make check green"


def test_reinstall_lists_kept_files_in_one_skip_line(tmp_path: Path) -> None:
    dest = tmp_path / "proj"
    env = _env(tmp_path)
    assert _install(dest, env, "--c", "demo").returncode == 0
    res = _install(dest, env, "--c", "demo")
    assert res.returncode == 0, res.stderr
    skips = [line for line in res.stdout.splitlines() if line.startswith("[SKIP]")]
    assert any("CLAUDE.md" in line and "Makefile" in line for line in skips)
    assert "existing history kept" in res.stdout


@pytest.mark.skipif(not shutil.which("gcc"), reason="needs gcc")
def test_c_project_is_green_right_after_install(tmp_path: Path) -> None:
    dest = tmp_path / "proj"
    env = _env(tmp_path)
    assert _install(dest, env, "--c", "demo").returncode == 0
    res = subprocess.run(
        ["make", "check"], cwd=dest, env=env, text=True, capture_output=True, check=False
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert "WARN" not in res.stdout
    # Quiet output: no sub-make directory noise, no per-test cc/run lines.
    assert "make[" not in res.stdout + res.stderr
    assert "  cc  " not in res.stdout
    assert "  run " not in res.stdout


@pytest.mark.parametrize("lang", sorted(LANG_ARGS))
def test_no_harness_tests_are_left_in_the_project(tmp_path: Path, lang: str) -> None:
    # The toolchain probe lives in the toolkit; tests/ holds only real project tests.
    dest = tmp_path / "proj"
    assert _install(dest, _env(tmp_path), *LANG_ARGS[lang]).returncode == 0
    assert [p for p in (dest / "tests").rglob("*") if p.is_file()] == []
    assert not (dest / "include" / "demo" / "version.h").exists()


@pytest.mark.skipif(not shutil.which("gcc"), reason="needs gcc")
def test_red_make_check_fails_the_install_with_its_output(tmp_path: Path) -> None:
    dest = tmp_path / "proj"
    (dest / "src").mkdir(parents=True)
    (dest / "src" / "bad.c").write_text("int main(void) { return undefined; }\n")
    res = _install(dest, _env(tmp_path), "--c", "demo")
    assert res.returncode != 0
    assert "undefined" in res.stdout + res.stderr  # make check output is shown
    assert res.stdout.splitlines()[-1].startswith("[FAIL]")
    assert "make check" in res.stdout.splitlines()[-1]


def _uv_shim(tmp_path: Path) -> tuple[dict[str, str], Path]:
    log = tmp_path / "uv.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text(f'#!/usr/bin/env bash\necho "$*" >> "{log}"\necho "uv 0.0.0"\n')
    uv.chmod(0o755)
    return {**_env(tmp_path), "PATH": f"{bin_dir}:{os.environ['PATH']}"}, log


def test_python_install_syncs_then_runs_make_check(tmp_path: Path) -> None:
    env, log = _uv_shim(tmp_path)
    res = _install(tmp_path / "proj", env, "--python", "demo")
    assert res.returncode == 0, res.stdout + res.stderr
    calls = log.read_text().splitlines()
    assert calls[0] == "sync --all-groups --quiet"
    assert any(call.startswith("run pytest") for call in calls[1:])
    rows = [line.split()[0] for line in res.stdout.splitlines()]
    assert "[uv]" in rows
    assert res.stdout.splitlines()[-1] == "[OK]      harness ready · make check green"


def test_python_without_pyproject_skips_the_check(tmp_path: Path) -> None:
    env, log = _uv_shim(tmp_path)
    res = _install(tmp_path / "proj", env, "--lang", "python")
    assert res.returncode == 0, res.stdout + res.stderr
    assert not log.exists()
    last = res.stdout.splitlines()[-1]
    assert last.startswith("[SKIP]")
    assert "pyproject.toml" in last


def test_python_scaffold_on_an_empty_dir_is_buildable(tmp_path: Path) -> None:
    # The canonical pyproject declares readme = "README.md"; without it uv cannot build.
    env, _ = _uv_shim(tmp_path)
    dest = tmp_path / "proj"
    assert _install(dest, env, "--python", "demo").returncode == 0
    assert (dest / "README.md").read_text().startswith("# demo")


def test_python_scaffold_keeps_an_existing_readme(tmp_path: Path) -> None:
    env, _ = _uv_shim(tmp_path)
    dest = tmp_path / "proj"
    dest.mkdir()
    (dest / "README.md").write_text("mine\n")
    assert _install(dest, env, "--python", "demo").returncode == 0
    assert (dest / "README.md").read_text() == "mine\n"


def test_pyproject_backup_is_not_committed(tmp_path: Path) -> None:
    env, _ = _uv_shim(tmp_path)
    dest = tmp_path / "proj"
    dest.mkdir()
    (dest / "pyproject.toml").write_text('[project]\nname = "from-uv-init"\n')
    assert _install(dest, env, "--python", "demo").returncode == 0
    assert list(dest.glob("pyproject.toml.bak.*"))  # the backup is kept on disk…
    assert not [f for f in _git(dest, env, "ls-files").splitlines() if ".bak." in f]  # …not in git


# ── Installing into an existing repo (monorepo subdir, shared history) ───────

SYMLINKS = [".claude/agents", ".claude/codex", ".claude/skills", ".claude/settings.json", "harness"]


def _monorepo(tmp_path: Path, env: dict[str, str], commit: bool = True) -> Path:
    root = tmp_path / "mono"
    (root / "backend" / "app").mkdir(parents=True)
    (root / "backend" / "app" / "main.py").write_text("app = 1\n")
    (root / "README.md").write_text("mono\n")
    _git(root, env, "init", "-q", "-b", "main")
    if commit:
        _git(root, env, "add", "-A")
        _git(root, env, "commit", "-qm", "my history")
    return root


def test_monorepo_subdir_install_never_nests_a_repo(tmp_path: Path) -> None:
    env = _env(tmp_path)
    root = _monorepo(tmp_path, env)
    head = _git(root, env, "rev-parse", "HEAD")

    res = _install(root / "backend", env, "--lang", "python")

    assert res.returncode == 0, res.stdout + res.stderr
    assert not (root / "backend" / ".git").exists()
    assert _git(root, env, "rev-parse", "HEAD") == head
    assert _git(root, env, "branch", "--show-current") == "main"
    assert "existing history kept" in res.stdout


def test_existing_repo_install_only_adds_files(tmp_path: Path) -> None:
    env = _env(tmp_path)
    root = _monorepo(tmp_path, env)

    assert _install(root / "backend", env, "--lang", "python").returncode == 0

    assert _git(root, env, "diff", "--stat") == ""  # no tracked file changed
    assert (root / "backend" / "app" / "main.py").read_text() == "app = 1\n"


def test_existing_repo_keeps_the_harness_symlinks_out_of_git_locally(tmp_path: Path) -> None:
    # The symlinks point into this machine's toolkit: committed, they break for teammates.
    env = _env(tmp_path)
    root = _monorepo(tmp_path, env)

    assert _install(root / "backend", env, "--lang", "python").returncode == 0

    untracked = _git(root, env, "status", "--porcelain", "-uall")
    for link in SYMLINKS:
        assert f"backend/{link}" not in untracked, link
    assert "backend/CLAUDE.md" in untracked  # the plan side is versioned
    assert not (root / ".gitignore").exists()  # shared ignore files untouched
    exclude = Path(_git(root, env, "rev-parse", "--git-path", "info/exclude"))
    excluded = (root / exclude).read_text().splitlines()
    assert "/backend/harness" in excluded


def test_exclude_entries_are_not_duplicated(tmp_path: Path) -> None:
    env = _env(tmp_path)
    root = _monorepo(tmp_path, env)
    assert _install(root / "backend", env, "--lang", "python").returncode == 0
    assert _install(root / "backend", env, "--lang", "python").returncode == 0
    exclude = root / _git(root, env, "rev-parse", "--git-path", "info/exclude")
    assert exclude.read_text().splitlines().count("/backend/harness") == 1


def test_monorepo_subdir_without_commits_makes_no_commit(tmp_path: Path) -> None:
    env = _env(tmp_path)
    root = _monorepo(tmp_path, env, commit=False)

    res = _install(root / "backend", env, "--lang", "python")

    assert res.returncode == 0, res.stdout + res.stderr
    assert not (root / "backend" / ".git").exists()
    log = subprocess.run(["git", "log"], cwd=root, env=env, capture_output=True, check=False)
    assert log.returncode != 0  # still no commits
    assert "inside an existing repo" in res.stdout


def test_src_dirs_option_writes_the_scope_config(tmp_path: Path) -> None:
    env = _env(tmp_path)
    root = _monorepo(tmp_path, env)
    dest = root / "backend"

    res = _install(dest, env, "--lang", "python", "--src-dirs", "app,scripts")

    assert res.returncode == 0, res.stdout + res.stderr
    assert (dest / "harness.toml").read_text() == '[scope]\nsrc_dirs = ["app", "scripts"]\n'


def test_src_dirs_never_overwrites_an_existing_config(tmp_path: Path) -> None:
    env = _env(tmp_path)
    root = _monorepo(tmp_path, env)
    dest = root / "backend"
    (dest / "harness.toml").write_text('[scope]\nsrc_dirs = ["app"]\n')

    res = _install(dest, env, "--lang", "python", "--src-dirs", "lib")

    assert res.returncode == 0, res.stdout + res.stderr
    assert (dest / "harness.toml").read_text() == '[scope]\nsrc_dirs = ["app"]\n'
    assert "harness.toml" in res.stdout  # reported as kept


@pytest.mark.parametrize("bad", ["", "../app", "/etc", 'a"b'])
def test_src_dirs_rejects_unsafe_values(tmp_path: Path, bad: str) -> None:
    env = _env(tmp_path)
    root = _monorepo(tmp_path, env)
    res = _install(root / "backend", env, "--lang", "python", "--src-dirs", bad)
    assert res.returncode == 2
    assert not (root / "backend" / "harness.toml").exists()
