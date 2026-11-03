"""I0.2: the installed throwaway projects are a correct starting point for the cycles."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from conftest import TOOLKIT
from i0_projects import KINDS, LINKS, Installed, install_project


def _claude_top(home: Path) -> dict[str, str]:
    root = home / ".claude"
    if not root.exists():
        return {}
    return {
        p.name: (os.readlink(p) if p.is_symlink() else str(p.stat().st_mtime_ns))
        for p in root.iterdir()
        if p.name in ("skills", "CLAUDE.md", "settings.json")
    }


@pytest.fixture(scope="module", params=KINDS)
def installed(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> Installed:
    before = _claude_top(Path.home())
    project = install_project(tmp_path_factory.mktemp(request.param), request.param)
    assert _claude_top(Path.home()) == before, "install touched the real ~/.claude"
    return project


def test_harness_links_point_into_the_toolkit(installed: Installed) -> None:
    for rel, target in LINKS.items():
        link = installed.root / rel
        assert link.is_symlink(), rel
        assert Path(os.readlink(link)) == TOOLKIT / target, rel


def test_global_links_went_to_the_isolated_claude_home(installed: Installed) -> None:
    claude_home = Path(installed.env["CLAUDE_HOME"])
    assert (claude_home / "skills").is_symlink()
    assert Path(os.readlink(claude_home / "skills")) == TOOLKIT / "skills"


@pytest.mark.parametrize("rel", ["Makefile", "CLAUDE.md", "AGENTS.md", "docs/plan/PLAN.md"])
def test_templates_are_copied_not_linked(installed: Installed, rel: str) -> None:
    path = installed.root / rel
    assert path.is_file() and not path.is_symlink(), rel


def test_install_root_and_repo_match_the_kind(installed: Installed) -> None:
    assert (installed.repo / ".git").is_dir()
    if installed.kind == "nested":
        assert installed.root == installed.repo / "backend"
        assert not (installed.root / ".git").exists(), "nested install created a nested repo"
    else:
        assert installed.root == installed.repo


def test_make_check_is_green_right_after_install(installed: Installed) -> None:
    res = installed.make("check")
    assert res.returncode == 0, res.stdout + res.stderr


def test_nested_install_asks_to_commit_its_files_today(tmp_path: Path) -> None:
    """Characterisation: in an existing repo the installer makes no commit and the final
    check is red with an explicit hint; the fixture then commits, like the user would."""
    project = install_project(tmp_path, "nested")
    assert "commit them" in project.install_output
    assert project.make("check").returncode == 0
