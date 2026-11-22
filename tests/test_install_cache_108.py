"""#108: install_project builds each kind once per process and copies it per test."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import i0_projects
import pytest
from i0_projects import Installed

pytestmark = pytest.mark.e2e


def _dir(tmp: Path, name: str) -> Path:
    (tmp / name).mkdir()
    return tmp / name


def test_second_install_reuses_the_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    real = i0_projects._build_template

    def counting(*args: Any, **kwargs: Any) -> Installed:
        calls.append("build")
        return real(*args, **kwargs)

    monkeypatch.setattr(i0_projects, "_build_template", counting)
    monkeypatch.setattr(i0_projects, "_TEMPLATES", {})
    first = i0_projects.install_project(_dir(tmp_path, "a"), "python")
    second = i0_projects.install_project(_dir(tmp_path, "b"), "python")
    assert calls == ["build"]
    assert first.root != second.root
    assert first.env["HOME"] != second.env["HOME"]


def test_copies_are_isolated_and_usable(tmp_path: Path) -> None:
    one = i0_projects.install_project(_dir(tmp_path, "a"), "python")
    two = i0_projects.install_project(_dir(tmp_path, "b"), "python")
    (one.root / "marker.txt").write_text("x")
    assert not (two.root / "marker.txt").exists()
    for proj in (one, two):
        assert str(tmp_path) in str(proj.root)
        res = proj.run("uv", "run", "--offline", "python", "-c", "import sys; print(sys.prefix)")
        assert res.returncode == 0, res.stderr
        assert str(proj.root) in res.stdout
    assert one.run("git", "status", "--porcelain").stdout.strip() == "?? marker.txt"
    assert two.run("git", "status", "--porcelain").stdout.strip() == ""
