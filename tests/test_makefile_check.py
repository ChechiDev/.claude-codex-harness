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
    # used by the Makefile recipes; harness.mk holds the shared check target
    for helper in ("status.sh", "_colors.sh", "harness.mk"):
        (root / "harness" / helper).symlink_to(TOOLKIT / "lib" / helper)
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


def test_c_failing_unit_test_is_named(tmp_path: Path) -> None:
    root, env = _project(tmp_path, "templates-c", verify_red=False)
    (root / "tests" / "unit").mkdir(parents=True)
    (root / "tests" / "unit" / "test_ok.c").write_text("int main(void) { return 0; }\n")
    (root / "tests" / "unit" / "test_bad.c").write_text("int main(void) { return 1; }\n")
    res = subprocess.run(
        ["make", "-s", "test"], cwd=root, env=env, text=True, capture_output=True, check=False
    )
    assert res.returncode != 0
    assert "[FAIL]  tests/unit/test_bad.c" in res.stdout
    assert "test_ok.c" not in res.stdout


def test_python_verify_runs_every_tool_even_after_a_failure(tmp_path: Path) -> None:
    root, env = _project(tmp_path, "templates", verify_red=True)
    out = _check(root, env, check_rc=0).stdout
    for tool in ("uv run ruff check", "uv run mypy", "uv run pytest"):
        assert tool in out, out


def test_python_test_without_tests_is_ok(tmp_path: Path) -> None:
    root, env = _project(tmp_path, "templates", verify_red=False)
    env["UV_RC"] = "5"  # pytest: no tests collected
    res = subprocess.run(
        ["make", "-s", "test"], cwd=root, env=env, text=True, capture_output=True, check=False
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert "[OK]    no tests yet" in res.stdout


def test_python_test_failure_is_still_red(tmp_path: Path) -> None:
    root, env = _project(tmp_path, "templates", verify_red=True)  # UV_RC=1
    res = subprocess.run(["make", "-s", "test"], cwd=root, env=env, capture_output=True)
    assert res.returncode != 0


def _c_env_check(tmp_path: Path, cc: str | None = None) -> subprocess.CompletedProcess[str]:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "harness").symlink_to(TOOLKIT / "lib")
    shutil.copy(TOOLKIT / "templates-c" / "Makefile", root / "Makefile")
    args = ["make", "-s", "env-check"] + ([f"CC={cc}"] if cc else [])
    return subprocess.run(args, cwd=root, text=True, capture_output=True, check=False)


@pytest.mark.skipif(not shutil.which("gcc"), reason="needs gcc")
def test_c_env_check_compiles_and_runs_the_probe_outside_the_project(tmp_path: Path) -> None:
    res = _c_env_check(tmp_path)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "C toolchain" in res.stdout
    assert "compiles and runs" in res.stdout
    project_files = sorted(p.name for p in (tmp_path / "proj").iterdir())
    assert project_files == ["Makefile", "harness"]


def test_c_env_check_fails_when_the_compiler_cannot_build(tmp_path: Path) -> None:
    broken = tmp_path / "brokencc"
    broken.write_text("#!/usr/bin/env bash\nexit 1\n")
    broken.chmod(0o755)
    res = _c_env_check(tmp_path, cc=str(broken))
    assert res.returncode != 0
    assert "[FAIL]" in res.stdout
