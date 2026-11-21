"""Issue #75: scaffold_c.sh never creates a repo nested inside another one."""

from __future__ import annotations

import subprocess
from pathlib import Path

SCAFFOLD = Path(__file__).resolve().parent.parent / "lib" / "scaffold_c.sh"


def _run(dest: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCAFFOLD), str(dest), "demo"],
        capture_output=True,
        text=True,
        check=True,
    )


def test_subdir_of_repo_uses_parent(tmp_path: Path) -> None:
    parent = tmp_path / "mono"
    subprocess.run(["git", "init", "-q", "-b", "main", str(parent)], check=True)
    dest = parent / "backend"
    out = _run(dest)
    assert not (dest / ".git").exists()
    assert "git init" not in out.stdout
    assert f"kept (parent repo {parent.resolve()})" in out.stdout
    assert (dest / "src").is_dir()


def test_empty_dir_gets_git_init_main(tmp_path: Path) -> None:
    dest = tmp_path / "proj"
    out = _run(dest)
    assert (dest / ".git").is_dir()
    assert "git init" in out.stdout
    head = subprocess.run(
        ["git", "-C", str(dest), "symbolic-ref", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert head.stdout.strip() == "main"
