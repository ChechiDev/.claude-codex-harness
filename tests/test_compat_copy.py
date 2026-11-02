"""C0.1: isolated copies of real projects, re-linked to the toolkit mount point.

The runner lives in compat/ (toolkit-only, never linked into projects). These tests
use a fake toolkit and throwaway repos: nothing outside tmp_path is touched.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import TOOLKIT

RUNNER = TOOLKIT / "compat" / "_compat.py"
MOUNT = "/toolkit"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RUNNER), *args], text=True, capture_output=True, check=False
    )


def _fake_toolkit(root: Path) -> Path:
    for rel in ("lib", "agents", "codex", "skills/implement", "packs/web/react", "catalog/ecc/x"):
        (root / rel).mkdir(parents=True)
    (root / "settings.json").write_text("{}")
    return root


def _repo(tmp_path: Path, toolkit: Path, *, install: str = "", per_skill: bool = False) -> Path:
    """A git repo with the harness installed at <repo>/<install> like install.sh does."""
    repo = tmp_path / "repo"
    root = repo / install if install else repo
    (root / ".claude").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "dev@example.com")
    _git(repo, "config", "user.name", "dev")
    (root / "harness").symlink_to(toolkit / "lib")
    for name in ("agents", "codex"):
        (root / ".claude" / name).symlink_to(toolkit / name)
    (root / ".claude" / "settings.json").symlink_to(toolkit / "settings.json")
    if per_skill:
        (root / ".claude" / "skills").mkdir()
        (root / ".claude" / "skills" / "react").symlink_to(toolkit / "packs" / "web" / "react")
    else:
        (root / ".claude" / "skills").symlink_to(toolkit / "skills")
    (root / "src.py").write_text("x = 1\n")
    (repo / ".gitignore").write_text("harness\n.claude/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    return root


def _state(root: Path) -> dict[str, str]:
    """Every path under root: link target for symlinks, content hash for files."""
    out: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                out[rel] = "link:" + os.readlink(path)
            elif path.is_file():
                out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


@pytest.fixture
def toolkit(tmp_path: Path) -> Path:
    return _fake_toolkit(tmp_path / "toolkit")


def _copy(tmp_path: Path, root: Path) -> Path:
    res = _run("copy", str(root), str(tmp_path / "scratch"))
    assert res.returncode == 0, res.stderr
    return Path(res.stdout.strip())


# ── copy ─────────────────────────────────────────────────────────────────────


def test_copy_takes_the_whole_git_repo_and_returns_the_install_root(
    tmp_path: Path, toolkit: Path
) -> None:
    root = _repo(tmp_path, toolkit, install="backend")
    res = _run("copy", str(root), str(tmp_path / "scratch"))
    assert res.returncode == 0, res.stderr
    copy_root = Path(res.stdout.strip())
    assert copy_root == tmp_path / "scratch" / "repo" / "backend"
    assert (copy_root.parent / ".git").is_dir()
    assert (copy_root / "src.py").read_text() == "x = 1\n"
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=copy_root, text=True, capture_output=True, check=True
    )
    assert "init" in log.stdout


def test_copy_preserves_links_as_links(tmp_path: Path, toolkit: Path) -> None:
    root = _repo(tmp_path, toolkit)
    copy_root = _copy(tmp_path, root)
    assert (copy_root / "harness").is_symlink()
    assert os.readlink(copy_root / "harness") == str(toolkit / "lib")


def test_copy_is_independent_from_the_original(tmp_path: Path, toolkit: Path) -> None:
    root = _repo(tmp_path, toolkit)
    copy_root = _copy(tmp_path, root)
    (copy_root / "src.py").write_text("changed\n")
    assert (root / "src.py").read_text() == "x = 1\n"


def test_copy_fails_outside_a_git_repo(tmp_path: Path) -> None:
    loose = tmp_path / "loose"
    loose.mkdir()
    res = _run("copy", str(loose), str(tmp_path / "scratch"))
    assert res.returncode != 0
    assert "git" in res.stderr.lower()
    assert not (tmp_path / "scratch").exists()


def test_copy_refuses_an_existing_destination(tmp_path: Path, toolkit: Path) -> None:
    root = _repo(tmp_path, toolkit)
    (tmp_path / "scratch" / "repo").mkdir(parents=True)
    res = _run("copy", str(root), str(tmp_path / "scratch"))
    assert res.returncode != 0
    assert "exists" in res.stderr.lower()


# ── relink ───────────────────────────────────────────────────────────────────


def test_relink_directory_shape_points_every_link_to_the_mount(
    tmp_path: Path, toolkit: Path
) -> None:
    copy_root = _copy(tmp_path, _repo(tmp_path, toolkit))
    res = _run("relink", str(copy_root), "--toolkit", str(toolkit), "--mount", MOUNT)
    assert res.returncode == 0, res.stderr
    expected = {
        "harness": f"{MOUNT}/lib",
        ".claude/agents": f"{MOUNT}/agents",
        ".claude/codex": f"{MOUNT}/codex",
        ".claude/settings.json": f"{MOUNT}/settings.json",
        ".claude/skills": f"{MOUNT}/skills",
    }
    for rel, target in expected.items():
        assert os.readlink(copy_root / rel) == target, rel


def test_relink_per_skill_shape_keeps_the_directory_and_relinks_each_skill(
    tmp_path: Path, toolkit: Path
) -> None:
    copy_root = _copy(tmp_path, _repo(tmp_path, toolkit, per_skill=True))
    res = _run("relink", str(copy_root), "--toolkit", str(toolkit), "--mount", MOUNT)
    assert res.returncode == 0, res.stderr
    skills = copy_root / ".claude" / "skills"
    assert skills.is_dir() and not skills.is_symlink()
    assert os.readlink(skills / "react") == f"{MOUNT}/packs/web/react"


def test_relink_rejects_a_link_outside_the_toolkit_and_changes_nothing(
    tmp_path: Path, toolkit: Path
) -> None:
    copy_root = _copy(tmp_path, _repo(tmp_path, toolkit, per_skill=True))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (copy_root / ".claude" / "skills" / "rogue").symlink_to(elsewhere)
    before = _state(copy_root)
    res = _run("relink", str(copy_root), "--toolkit", str(toolkit), "--mount", MOUNT)
    assert res.returncode != 0
    assert "rogue" in res.stderr
    assert _state(copy_root) == before


def test_relink_rejects_a_skill_linked_to_the_catalog(tmp_path: Path, toolkit: Path) -> None:
    copy_root = _copy(tmp_path, _repo(tmp_path, toolkit, per_skill=True))
    (copy_root / ".claude" / "skills" / "x").symlink_to(toolkit / "catalog" / "ecc" / "x")
    before = _state(copy_root)
    res = _run("relink", str(copy_root), "--toolkit", str(toolkit), "--mount", MOUNT)
    assert res.returncode != 0
    assert "catalog" in res.stderr
    assert _state(copy_root) == before


def test_relink_fails_when_an_expected_link_is_missing(tmp_path: Path, toolkit: Path) -> None:
    copy_root = _copy(tmp_path, _repo(tmp_path, toolkit))
    (copy_root / "harness").unlink()
    res = _run("relink", str(copy_root), "--toolkit", str(toolkit), "--mount", MOUNT)
    assert res.returncode != 0
    assert "harness" in res.stderr


# ── originals and snapshot ───────────────────────────────────────────────────


def test_copy_and_relink_leave_the_original_byte_and_link_identical(
    tmp_path: Path, toolkit: Path
) -> None:
    root = _repo(tmp_path, toolkit, install="backend")
    repo = root.parent
    before = _state(repo)
    copy_root = _copy(tmp_path, root)
    res = _run("relink", str(copy_root), "--toolkit", str(toolkit), "--mount", MOUNT)
    assert res.returncode == 0, res.stderr
    assert _state(repo) == before


def test_snapshot_lists_every_link_with_its_target(tmp_path: Path, toolkit: Path) -> None:
    root = _repo(tmp_path, toolkit, per_skill=True)
    res = _run("snapshot", str(root))
    assert res.returncode == 0, res.stderr
    snap = json.loads(res.stdout)
    assert snap["harness"] == str(toolkit / "lib")
    assert snap[".claude/skills/react"] == str(toolkit / "packs" / "web" / "react")
    assert ".git" not in "".join(snap)
