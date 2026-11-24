"""I0 fixtures: throwaway projects installed with the real install.sh.

Three kinds, the shapes the real projects use:
  python  a fresh Python project (`--python demo`)
  c       a fresh C project (`--c demo`)
  nested  an existing repo with the harness installed in backend/ (like guakamole)

Isolation: CLAUDE_HOME points to a temp dir (link-global.sh never touches ~/.claude),
git config is isolated, HARNESS_* vars are dropped.
"""

from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from conftest import TOOLKIT

IDENTITY = {
    "GIT_AUTHOR_NAME": "dev",
    "GIT_AUTHOR_EMAIL": "dev@example.com",
    "GIT_COMMITTER_NAME": "dev",
    "GIT_COMMITTER_EMAIL": "dev@example.com",
}
KINDS = ("python", "c", "nested")
LINKS = {
    "harness": "lib",
    ".claude/agents": "agents",
    ".claude/codex": "codex",
    ".claude/skills": "skills",
    ".claude/settings.json": "settings.json",
}


@dataclass(frozen=True)
class Installed:
    kind: str
    root: Path  # install root (where Makefile and harness/ live)
    repo: Path  # git repository top
    env: dict[str, str]
    install_output: str = ""

    def run(self, *argv: str, timeout: int = 600) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            list(argv),
            cwd=self.root,
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )

    def make(self, target: str) -> subprocess.CompletedProcess[str]:
        return self.run("make", "-s", "--no-print-directory", target)


# PYTEST_*: the outer run's PYTEST_ADDOPTS (e.g. CI's -rs) must not reach the
# throwaway project's own pytest, or it changes what `make check` prints (#123).
LEAKY = ("GIT_", "HARNESS_", "UV_", "VIRTUAL_ENV", "CLAUDE_", "PYTEST_")


def uv_cache() -> str:
    """The user's uv cache, resolved once from the real environment."""
    out = subprocess.run(["uv", "cache", "dir"], text=True, capture_output=True, check=True)
    return out.stdout.strip()


def isolated_env(tmp: Path) -> dict[str, str]:
    """No network for uv (offline, from the user's cache), a throwaway HOME, no
    inherited git/harness/uv/venv/Claude variables."""
    cfg = tmp / "empty.gitconfig"
    cfg.touch()
    home = tmp / "home"
    home.mkdir()
    base = {k: v for k, v in os.environ.items() if not k.startswith(LEAKY)}
    return {
        **base,
        **IDENTITY,
        "HOME": str(home),
        "UV_OFFLINE": "1",
        "UV_CACHE_DIR": uv_cache(),
        "CLAUDE_HOME": str(tmp / "claude-home"),
        "GIT_CONFIG_GLOBAL": str(cfg),
        "GIT_CONFIG_NOSYSTEM": "1",
    }


def _sh(cwd: Path, env: dict[str, str], *argv: str) -> None:
    subprocess.run(list(argv), cwd=cwd, env=env, check=True, capture_output=True)


def _relax_coverage(root: Path) -> None:
    """The fixtures test the harness gates, not the Python profile: their tests rarely
    import src/, so the profile's 80% coverage threshold is off here (fail_under = 0;
    still measured). Likewise their modules never import the profile's pydantic, so
    deptry (strict once src/ has a module) ignores its DEP002 here, and only here.
    test_python_profile_v4_e2e.py restores both to test the profile."""
    pyproject = root / "pyproject.toml"
    text = pyproject.read_text()
    assert "fail_under = 80" in text, "the Python profile's coverage threshold moved"
    assert "[tool.deptry" not in text, "the Python profile now configures deptry"
    text = text.replace("fail_under = 80", "fail_under = 0", 1)
    pyproject.write_text(text + FIXTURE_DEPTRY)


# Appended last by _relax_coverage (a tool table: uv.lock is unaffected).
FIXTURE_DEPTRY = (
    "\n# Harness test fixture only: its modules never import the profile's pydantic.\n"
    "[tool.deptry.per_rule_ignores]\n"
    'DEP002 = ["pydantic", "pydantic-settings"]\n'
)


def _build_template(tmp: Path, kind: str, toolkit: Path) -> Installed:
    """Run the real install.sh once into `tmp` (the slow part: uv sync, git, hooks)."""
    env = isolated_env(tmp)
    repo = tmp / f"{kind}-proj"
    repo.mkdir()
    if kind == "nested":
        _sh(repo, env, "git", "init", "-q", "-b", "main")
        (repo / "README.md").write_text("monorepo\n")
        _sh(repo, env, "git", "add", "-A")
        _sh(repo, env, "git", "commit", "-qm", "monorepo root")
        root = repo / "backend"
        root.mkdir()
        args = ["--python", "demo"]
    else:
        root = repo
        args = ["--python", "demo"] if kind == "python" else ["--c", "demo"]
    res = subprocess.run(
        ["bash", str(toolkit / "install.sh"), str(root), *args],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=900,
    )
    if kind == "nested":
        # Today: inside an existing repo install.sh commits nothing, so its own files count
        # as changes and the final make check is red until the user commits them.
        if res.returncode != 0 and "commit them" not in res.stdout + res.stderr:
            raise AssertionError(f"install.sh failed for {kind}:\n{res.stdout}\n{res.stderr}")
        _relax_coverage(root)
        _sh(repo, env, "git", "add", "-A")
        _sh(repo, env, "git", "commit", "-qm", "install the harness in backend/")
    elif res.returncode != 0:
        raise AssertionError(f"install.sh failed for {kind}:\n{res.stdout}\n{res.stderr}")
    if kind == "python":  # part of install.sh's root commit: main = the setup branch
        _relax_coverage(root)
        _sh(repo, env, "git", "commit", "-q", "--amend", "--no-edit", "-a")
        _sh(repo, env, "git", "branch", "-f", "main", "HEAD")
    return Installed(kind=kind, root=root, repo=repo, env=env, install_output=res.stdout)


# One installed template per (kind) for the real toolkit, built once per process (each
# xdist worker builds its own). Tests get a private copy; the template is never handed out.
_TEMPLATES: dict[str, Installed] = {}


def _template(kind: str) -> Installed:
    if kind not in _TEMPLATES:
        base = Path(tempfile.mkdtemp(prefix=f"harness-tpl-{kind}-"))
        atexit.register(shutil.rmtree, base, ignore_errors=True)
        _TEMPLATES[kind] = _build_template(base, kind, TOOLKIT)
    return _TEMPLATES[kind]


def install_project(tmp: Path, kind: str, toolkit: Path = TOOLKIT) -> Installed:
    """Install the harness into a throwaway project of the given kind (from `toolkit`,
    the real worktree by default, or a throwaway copy of it).

    For the real toolkit the install runs once per process; every call gets its own copy
    of that template (own git repo, HOME and venv), so tests stay isolated. A custom
    `toolkit` always gets a real install."""
    if toolkit != TOOLKIT:
        return _build_template(tmp, kind, toolkit)
    tpl = _template(kind)
    env = isolated_env(tmp)
    repo = tmp / f"{kind}-proj"
    shutil.copytree(tpl.repo, repo, symlinks=True, ignore=shutil.ignore_patterns(".venv"))
    for key in ("HOME", "CLAUDE_HOME"):  # what install.sh left outside the repo
        src = Path(tpl.env[key])
        if src.is_dir():
            shutil.copytree(src, env[key], symlinks=True, dirs_exist_ok=True)
    root = repo / tpl.root.relative_to(tpl.repo)
    if (tpl.root / "pyproject.toml").exists():
        _sh(root, env, "uv", "sync", "--all-groups", "--quiet", "--offline")
    return Installed(kind=kind, root=root, repo=repo, env=env, install_output=tpl.install_output)
