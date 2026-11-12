"""Shared setup for the I3a tests: a project with the full install.sh link set."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from conftest import TOOLKIT, Project

BLOCKED = 2
HARNESS_MSG = "harness-owned"
# Link path -> toolkit target, as install.sh creates them (lib/_upgrade.py LINKS).
LINKS = {
    ".claude/agents": "agents",
    ".claude/codex": "codex",
    ".claude/skills": "skills",
    ".claude/settings.json": "settings.json",
    "harness": "lib",
}


def install_links(root: Path, toolkit: Path = TOOLKIT) -> None:
    """Create every missing harness link, pointing into `toolkit`."""
    (root / ".claude").mkdir(exist_ok=True)
    for rel, target in LINKS.items():
        path = root / rel
        if not path.is_symlink():
            path.symlink_to(toolkit / target)


def claude_home(tmp: Path, toolkit: Path = TOOLKIT) -> Path:
    """A throwaway $CLAUDE_HOME with the machine-level links of link-global.sh."""
    home = tmp / "claude_home"
    home.mkdir()
    (home / "skills").symlink_to(toolkit / "skills")
    (home / "CLAUDE.md").symlink_to(toolkit / "global" / "CLAUDE.md")
    return home


def installed(project: Project, tmp: Path) -> Project:
    install_links(project.root)
    project.env["CLAUDE_HOME"] = str(claude_home(tmp))
    return project


def copy_toolkit(dest: Path) -> Path:
    """A minimal toolkit copy (the running guard derives TOOLKIT from its own path)."""
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("lib", "agents", "codex", "skills", "global", "templates"):
        shutil.copytree(TOOLKIT / name, dest / name, symlinks=True)
    shutil.copy2(TOOLKIT / "settings.json", dest / "settings.json")
    return dest


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()
