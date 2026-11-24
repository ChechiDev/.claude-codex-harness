"""#79 (I15, py-init part): py-init is harness-neutral.

Contract tests on the skill text and the github assets, the helper scripts with
stubbed network tools (npx), and an e2e of both orders py-init <-> install.sh.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import TOOLKIT

SKILL_DIR = TOOLKIT / "skills" / "py-init"
SKILL = SKILL_DIR / "SKILL.md"
GH_ASSETS = TOOLKIT / "skills" / "github" / "assets"
DETECT = SKILL_DIR / "scripts" / "detect_harness.sh"
ARCHIFY = SKILL_DIR / "scripts" / "install_archify.sh"

IDENTITY = {
    "GIT_AUTHOR_NAME": "dev",
    "GIT_AUTHOR_EMAIL": "dev@example.com",
    "GIT_COMMITTER_NAME": "dev",
    "GIT_COMMITTER_EMAIL": "dev@example.com",
}


def _text() -> str:
    return SKILL.read_text(encoding="utf-8")


def _env(tmp_path: Path, extra_path: Path | None = None) -> dict[str, str]:
    empty_cfg = tmp_path / "empty.gitconfig"
    empty_cfg.touch()
    base = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "HARNESS_"))}
    path = f"{extra_path}:{base['PATH']}" if extra_path else base["PATH"]
    return {
        **base,
        **IDENTITY,
        "PATH": path,
        "CLAUDE_HOME": str(tmp_path / "claude-home"),
        "GIT_CONFIG_GLOBAL": str(empty_cfg),
        "GIT_CONFIG_NOSYSTEM": "1",
    }


def _run(
    args: list[str], env: dict[str, str], cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True, check=False)


# ── Contract: the skill text ──────────────────────────────────────────────────


def test_skill_installs_no_role_files() -> None:
    text = _text()
    assert "github/assets/AGENTS.md" not in text
    assert "ln -s AGENTS.md CLAUDE.md" not in text
    assert "CLAUDE.md` is a symlink" not in text


def test_inverted_roles_asset_is_gone() -> None:
    assert not (GH_ASSETS / "AGENTS.md").exists()


def test_no_asset_or_skill_inverts_the_roles() -> None:
    # Claude implements; only Codex commits the close. Nothing here says otherwise.
    for path in [SKILL, *GH_ASSETS.rglob("*")]:
        if path.is_file():
            body = path.read_text(encoding="utf-8")
            assert "implements code, commits" not in body, path
            assert "orchestrator and reviewer" not in body, path


def test_skill_never_writes_claude_skills() -> None:
    text = _text()
    assert "npx skills add" not in text  # only through install_archify.sh
    assert "test -f .claude/skills/archify" not in text
    assert "auto-creates `.claude/skills/archify" not in text
    assert "scripts/install_archify.sh" in text


def test_skill_checks_fail_for_real() -> None:
    assert "|| echo" not in _text()


def test_skill_guided_flow_order() -> None:
    text = _text()
    assert "scripts/detect_harness.sh" in text
    # install.sh is offered without --python, so py-init's pyproject.toml stays.
    assert "install.sh <project-dir>" in text
    assert "without `--python`" in text
    i_scaffold = text.index("### Phase 1")
    i_install = text.index("### Phase 2")
    i_ci = text.index("### Phase 3")
    assert i_scaffold < i_install < i_ci
    assert "chore/project-setup" in text[i_ci:]
    assert "never commits" in text


def test_skill_tells_how_to_invoke_archify() -> None:
    assert "Read `.agents/skills/archify/SKILL.md`" in _text()


# ── Contract: CI aligned with make check ──────────────────────────────────────


def test_ci_has_no_placeholders_and_matches_make_check() -> None:
    # The jobs call the Makefile targets make check runs: no command re-typed.
    ci = (GH_ASSETS / "ci.yml").read_text(encoding="utf-8")
    assert "<project-packages>" not in ci
    for target in ("make lint", "make type", "make deps", "make test"):
        assert f"run: {target}" in ci, target
    assert "uv run" not in ci


@pytest.mark.parametrize("name", ["ci.yml", "e2e.yml"])
def test_empty_suites_do_not_fail(name: str) -> None:
    # Exit 5 (no tests yet) is green inside the Makefile targets; no raw pytest here.
    body = (GH_ASSETS / name).read_text(encoding="utf-8")
    assert "uv run pytest" not in body, name
    makefile = (TOOLKIT / "templates" / "Makefile").read_text(encoding="utf-8")
    assert makefile.count("if [ $$rc -eq 5 ]") == 3  # test, test-integration, test-e2e


def test_ci_coverage_threshold_is_the_pyproject_one() -> None:
    # make check and CI enforce the same threshold: [tool.coverage.report] only.
    assert "--cov" not in (GH_ASSETS / "ci.yml").read_text(encoding="utf-8")


def test_skill_scaffold_steps_tolerate_existing_project() -> None:
    text = _text()
    assert "mkdir -p <project-name>" in text
    assert "git rev-parse --git-dir" in text
    assert "test -f pyproject.toml ||" in text
    assert "git log --oneline main" in text  # install.sh outcome is checked


# ── detect_harness.sh ─────────────────────────────────────────────────────────


def test_detect_missing_dir_is_fresh(tmp_path: Path) -> None:
    res = _run(["bash", str(DETECT), str(tmp_path / "new")], _env(tmp_path))
    assert res.returncode == 0, res.stderr
    assert res.stdout == "harness=absent\nhistory=no\n"
    assert not (tmp_path / "new").exists()


def test_detect_fresh_dir(tmp_path: Path) -> None:
    dest = tmp_path / "p"
    dest.mkdir()
    res = _run(["bash", str(DETECT), str(dest)], _env(tmp_path))
    assert res.returncode == 0, res.stderr
    assert "harness=absent" in res.stdout
    assert "history=no" in res.stdout


def test_detect_harness_and_history(tmp_path: Path) -> None:
    dest = tmp_path / "p"
    (dest / ".claude").mkdir(parents=True)
    (dest / ".claude" / "skills").symlink_to(TOOLKIT / "skills")
    env = _env(tmp_path)
    _run(["git", "init", "-q", "-b", "main", str(dest)], env)
    _run(["git", "commit", "-q", "--allow-empty", "-m", "x"], env, cwd=dest)
    res = _run(["bash", str(DETECT), str(dest)], env)
    assert "harness=installed" in res.stdout
    assert "history=yes" in res.stdout


# ── install_archify.sh (npx stubbed) ──────────────────────────────────────────


def _npx_stub(tmp_path: Path, write_skill: bool = True) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "npx.log"
    skill = (
        'mkdir -p .agents/skills/archify && echo "# archify" > .agents/skills/archify/SKILL.md\n'
        if write_skill
        else ""
    )
    npx = bin_dir / "npx"
    npx.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$PWD $*" >> "{log}"\n'
        f"{skill}"
        "mkdir -p .claude/skills && ln -sfn ../../.agents/skills/archify .claude/skills/archify\n"
    )
    npx.chmod(0o755)
    return bin_dir


def test_archify_writes_only_agents_skills(tmp_path: Path) -> None:
    dest = tmp_path / "p"
    shared = tmp_path / "shared-skills"
    shared.mkdir()
    (dest / ".claude").mkdir(parents=True)
    (dest / ".claude" / "skills").symlink_to(shared)  # harness link to the toolkit
    env = _env(tmp_path, _npx_stub(tmp_path))
    res = _run(["bash", str(ARCHIFY), str(dest)], env)
    assert res.returncode == 0, res.stdout + res.stderr
    assert (dest / ".agents" / "skills" / "archify" / "SKILL.md").is_file()
    assert list(shared.iterdir()) == []  # the shared toolkit was not touched
    assert "tt-a1i/archify" in (tmp_path / "npx.log").read_text()
    assert str(dest) not in (tmp_path / "npx.log").read_text()  # ran elsewhere


def test_archify_fresh_project_gets_no_claude_dir(tmp_path: Path) -> None:
    dest = tmp_path / "p"
    dest.mkdir()
    res = _run(["bash", str(ARCHIFY), str(dest)], _env(tmp_path, _npx_stub(tmp_path)))
    assert res.returncode == 0, res.stderr
    assert not (dest / ".claude").exists()


def test_archify_fails_when_skill_missing(tmp_path: Path) -> None:
    dest = tmp_path / "p"
    dest.mkdir()
    env = _env(tmp_path, _npx_stub(tmp_path, write_skill=False))
    res = _run(["bash", str(ARCHIFY), str(dest)], env)
    assert res.returncode != 0
    assert not (dest / ".agents").exists()


def test_archify_keeps_existing_install(tmp_path: Path) -> None:
    dest = tmp_path / "p"
    target = dest / ".agents" / "skills" / "archify"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("pinned\n")
    res = _run(["bash", str(ARCHIFY), str(dest)], _env(tmp_path, _npx_stub(tmp_path)))
    assert res.returncode != 0
    assert (target / "SKILL.md").read_text() == "pinned\n"


# ── e2e: both orders (real install.sh, real make check) ──────────────────────


def _py_init_scaffold(dest: Path, pkg: str) -> None:
    """File-producing steps of py-init Phase 1 (no network: uv init, graphify, archify)."""
    dest.mkdir(parents=True, exist_ok=True)
    tpl = (TOOLKIT / "skills" / "py-style" / "assets" / "pyproject.toml").read_text()
    (dest / "pyproject.toml").write_text(
        tpl.replace('name = "your-project"', f'name = "{pkg.replace("_", "-")}"')
        .replace('known-first-party = ["your_package"]', f'known-first-party = ["{pkg}"]')
        .replace("# Add packages = ", f'packages = ["{pkg}"]\n# was: ')
    )
    src = dest / "src" / pkg
    src.mkdir(parents=True)
    (src / "__init__.py").write_text('__version__: str = "0.1.0"\n')
    (src / "py.typed").touch()
    (dest / ".python-version").write_text("3.12\n")
    for sub in ("", "unit", "integration", "e2e"):
        d = dest / "tests" / sub
        d.mkdir(parents=True, exist_ok=True)
        (d / "__init__.py").touch()
        (d / "conftest.py").touch()
    (dest / "tests" / "unit" / "test_smoke.py").write_text(
        f"import pytest\n\nimport {pkg}\n\n\n@pytest.mark.unit\n"
        "def test_package_is_importable() -> None:\n"
        '    """Verify the package exposes __version__."""\n'
        f"    assert isinstance({pkg}.__version__, str)\n"
    )
    for name in (".gitignore", ".editorconfig", "README.md"):
        shutil.copy(SKILL_DIR / "assets" / name, dest / name)
    shutil.copy(
        TOOLKIT / "skills" / "py-pytest" / "assets" / "root_conftest.py", dest / "conftest.py"
    )
    shutil.copy(
        TOOLKIT / "skills" / "github" / "assets" / ".pre-commit-config.yaml",
        dest / ".pre-commit-config.yaml",
    )


@pytest.mark.e2e
def test_py_init_then_install(tmp_path: Path) -> None:
    dest = tmp_path / "demo"
    env = _env(tmp_path)
    _py_init_scaffold(dest, "demo")
    assert "harness=absent" in _run(["bash", str(DETECT), str(dest)], env).stdout

    res = _run(["bash", str(TOOLKIT / "install.sh"), str(dest)], env)
    assert res.returncode == 0, res.stdout + res.stderr

    lines = [
        ln for ln in (dest / ".gitignore").read_text().splitlines() if ln and not ln.startswith("#")
    ]
    assert len(lines) == len(set(lines)), "duplicated .gitignore entries"
    assert "harness/harness.mk" in (dest / "Makefile").read_text()
    assert "Clean-code caps" in (dest / "pyproject.toml").read_text()
    assert 'packages = ["demo"]' in (dest / "pyproject.toml").read_text()
    for role in ("CLAUDE.md", "AGENTS.md"):
        assert not (dest / role).is_symlink()
        assert (dest / role).read_text() == (TOOLKIT / "templates" / role).read_text()
    log = _run(["git", "log", "--format=%s", "main"], env, cwd=dest).stdout.split("\n")
    assert [ln for ln in log if ln] == ["chore: install harness"]  # one initial commit
    assert _run(["git", "status", "--porcelain"], env, cwd=dest).stdout == ""
    assert "harness=installed" in _run(["bash", str(DETECT), str(dest)], env).stdout
    check = _run(["make", "-s", "check"], env, cwd=dest)
    assert check.returncode == 0, check.stdout + check.stderr


@pytest.mark.e2e
def test_install_then_py_init_detects_harness(tmp_path: Path) -> None:
    dest = tmp_path / "demo"
    dest.mkdir()
    env = _env(tmp_path)
    res = _run(["bash", str(TOOLKIT / "install.sh"), str(dest)], env)
    assert res.returncode == 0, res.stdout + res.stderr
    out = _run(["bash", str(DETECT), str(dest)], env).stdout
    assert "harness=installed" in out
    assert "history=yes" in out
    # py-init then skips role files, commits and branches: the archify step still
    # never writes through the shared .claude/skills link.
    before = sorted(p.name for p in (TOOLKIT / "skills").iterdir())
    arch = _run(["bash", str(ARCHIFY), str(dest)], _env(tmp_path, _npx_stub(tmp_path)))
    assert arch.returncode == 0, arch.stderr
    assert sorted(p.name for p in (TOOLKIT / "skills").iterdir()) == before
    assert (dest / ".claude" / "skills").is_symlink()
