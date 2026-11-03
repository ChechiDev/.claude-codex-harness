"""I0 fixtures: throwaway projects installed with the real install.sh.

Three kinds, the shapes the real projects use:
  python  a fresh Python project (`--python demo`)
  c       a fresh C project (`--c demo`)
  nested  an existing repo with the harness installed in backend/ (like guakamole)

Isolation: CLAUDE_HOME points to a temp dir (link-global.sh never touches ~/.claude),
git config is isolated, HARNESS_* vars are dropped.
"""

from __future__ import annotations

import os
import subprocess
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


LEAKY = ("GIT_", "HARNESS_", "UV_", "VIRTUAL_ENV", "CLAUDE_")


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


def install_project(tmp: Path, kind: str) -> Installed:
    """Install the harness into a throwaway project of the given kind."""
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
        ["bash", str(TOOLKIT / "install.sh"), str(root), *args],
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
        _sh(repo, env, "git", "add", "-A")
        _sh(repo, env, "git", "commit", "-qm", "install the harness in backend/")
    elif res.returncode != 0:
        raise AssertionError(f"install.sh failed for {kind}:\n{res.stdout}\n{res.stderr}")
    return Installed(kind=kind, root=root, repo=repo, env=env, install_output=res.stdout)
