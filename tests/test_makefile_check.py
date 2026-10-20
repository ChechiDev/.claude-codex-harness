"""Template Makefiles: `make check` reports every failure, not just the first."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import TOOLKIT

CHECK_SH = '#!/usr/bin/env bash\necho CHECK_SH_RAN\nexit "${CHECK_RC:-0}"\n'
# uv shim: logs each toolchain call; fails when UV_RC is set (python verify red).
UV_SHIM = '#!/usr/bin/env bash\necho "uv $*"\nexit "${UV_RC:-0}"\n'


def _project(tmp_path: Path, template: str, verify_red: bool) -> tuple[Path, dict[str, str]]:
    root = tmp_path / "proj"
    (root / "harness").mkdir(parents=True)
    shutil.copy(TOOLKIT / template / "Makefile", root / "Makefile")
    check = root / "harness" / "check.sh"
    check.write_text(CHECK_SH)
    check.chmod(0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text(UV_SHIM)
    uv.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    if verify_red and template == "templates":
        env["UV_RC"] = "1"
    if verify_red and template == "templates-c":
        (root / "src").mkdir()
        (root / "src" / "bad.c").write_text("int main(void) { return undefined; }\n")
    return root, env


def _check(root: Path, env: dict[str, str], check_rc: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "-s", "check"],
        cwd=root,
        env={**env, "CHECK_RC": str(check_rc)},
        text=True,
        capture_output=True,
        check=False,
    )


TEMPLATES = ["templates", "templates-c"]


@pytest.mark.parametrize("template", TEMPLATES)
def test_harness_invariants_still_run_when_verify_is_red(tmp_path: Path, template: str) -> None:
    root, env = _project(tmp_path, template, verify_red=True)
    res = _check(root, env, check_rc=1)
    assert res.returncode != 0
    assert "CHECK_SH_RAN" in res.stdout


@pytest.mark.parametrize("template", TEMPLATES)
@pytest.mark.parametrize(("verify_red", "check_rc"), [(True, 0), (False, 1)])
def test_check_is_red_if_either_half_fails(
    tmp_path: Path, template: str, verify_red: bool, check_rc: int
) -> None:
    root, env = _project(tmp_path, template, verify_red=verify_red)
    assert _check(root, env, check_rc=check_rc).returncode != 0


@pytest.mark.parametrize("template", TEMPLATES)
def test_check_is_green_when_both_halves_pass(tmp_path: Path, template: str) -> None:
    root, env = _project(tmp_path, template, verify_red=False)
    res = _check(root, env, check_rc=0)
    assert res.returncode == 0, res.stdout + res.stderr


def test_python_verify_runs_every_tool_even_after_a_failure(tmp_path: Path) -> None:
    root, env = _project(tmp_path, "templates", verify_red=True)
    out = _check(root, env, check_rc=0).stdout
    for tool in ("uv run ruff check", "uv run mypy", "uv run pytest"):
        assert tool in out, out
