"""Trusted check launcher harness/run_check.sh (issue #64, WARN rollout).

Gates, close and the PR barrier decide only through the launcher. It validates the
effective Makefile, runs the check in a controlled environment and is the only
producer of the green record. In this rollout every finding is a [WARN] with a
remediation and never changes the exit code: the launcher returns the check's rc.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from conftest import LEAF_ID, TOOLKIT, Project

# A valid project Makefile: own targets, then the harness include last.
VALID = "verify:\n\t@echo verify-ok\nenv-check:\n\t@echo env-ok\ninclude harness/harness.mk\n"
# Green without harness.mk: the shape most gate tests use.
BARE_GREEN = "check:\n\t@echo ok\n"


def _makefile(project: Project, text: str, name: str = "Makefile") -> None:
    project.write(name, text)
    project.commit_all(f"{name}")


def _launch(
    project: Project, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return project.run("run_check.sh", None, *args, env=env)


def _warns(res: subprocess.CompletedProcess[str]) -> list[str]:
    return [ln for ln in (res.stdout + res.stderr).splitlines() if ln.startswith("[WARN]")]


def _record(project: Project) -> Path:
    return project.root / ".git" / "harness-check.ok"


# ── Makefile validation ──────────────────────────────────────────────────────


def test_valid_makefile_gives_no_warning(project: Project) -> None:
    _makefile(project, VALID)
    res = _launch(project, "--validate")
    assert res.returncode == 0, res.stdout + res.stderr
    assert _warns(res) == [], res.stdout + res.stderr


@pytest.mark.parametrize("template", ["templates", "templates-c"])
def test_template_makefiles_give_no_warning(project: Project, template: str) -> None:
    # C0 sameness: an untouched installed template is clean.
    _makefile(project, (TOOLKIT / template / "Makefile").read_text())
    res = _launch(project, "--validate")
    assert _warns(res) == [], res.stdout + res.stderr


@pytest.mark.parametrize("name", ["GNUmakefile", "makefile"])
def test_shadowing_makefile_warns(project: Project, name: str) -> None:
    _makefile(project, VALID)
    _makefile(project, VALID, name)
    res = _launch(project, "--validate")
    assert res.returncode == 0
    assert any(name in w for w in _warns(res)), res.stdout + res.stderr


def test_missing_include_warns(project: Project) -> None:
    _makefile(project, BARE_GREEN)
    res = _launch(project, "--validate")
    assert res.returncode == 0
    assert any("harness/harness.mk" in w for w in _warns(res)), res.stdout + res.stderr


def test_check_redefined_after_the_include_warns(project: Project) -> None:
    _makefile(project, VALID + "check:\n\t@echo hijacked\n")
    res = _launch(project, "--validate")
    assert any("check" in w and "redefin" in w for w in _warns(res)), res.stdout + res.stderr


def test_check_redefined_before_the_include_warns(project: Project) -> None:
    _makefile(project, "check:\n\t@echo early\n" + VALID)
    res = _launch(project, "--validate")
    assert any("check" in w for w in _warns(res)), res.stdout + res.stderr


def test_goal_conditional_redefinition_warns(project: Project) -> None:
    hidden = "ifeq ($(MAKECMDGOALS),check)\ncheck:\n\t@echo hijacked\nendif\n"
    _makefile(project, VALID + hidden)
    res = _launch(project, "--validate")
    assert any("check" in w for w in _warns(res)), res.stdout + res.stderr


def test_launcher_is_executable() -> None:
    assert os.access(TOOLKIT / "lib" / "run_check.sh", os.X_OK)


def test_fingerprint_ignores_an_inherited_pythonpath(project: Project, tmp_path: Path) -> None:
    _makefile(project, "check:\n\t@echo ok\n")
    hostile = tmp_path / "hostile"
    hostile.mkdir()
    (hostile / "subprocess.py").write_text("raise SystemExit(9)\n")
    _launch(project, "--decision", "--record", env={"PYTHONPATH": str(hostile)})
    assert _record(project).is_file()


def test_extra_prerequisite_on_check_warns(project: Project) -> None:
    _makefile(project, VALID + "check: evil\nevil:\n\t@echo evil\n")
    res = _launch(project, "--validate")
    assert any("prerequisite" in w for w in _warns(res)), res.stdout + res.stderr


def test_harness_target_defined_elsewhere_warns(project: Project) -> None:
    _makefile(project, VALID + "include extra.mk\n")
    project.write("extra.mk", "check:\n\t@echo other\n")
    res = _launch(project, "--validate")
    assert any("check" in w for w in _warns(res)), res.stdout + res.stderr


# ── command line and environment ─────────────────────────────────────────────


@pytest.mark.parametrize("arg", ["TESTS=none", "-f", "other.mk"])
def test_command_line_arguments_warn_and_are_ignored(project: Project, arg: str) -> None:
    _makefile(project, 'check:\n\t@test -z "$(TESTS)"\n')
    res = _launch(project, arg)
    assert res.returncode == 0, res.stdout + res.stderr
    assert any("command-line" in w for w in _warns(res)), res.stdout + res.stderr


def test_makeflags_assignment_warns_and_is_dropped(project: Project) -> None:
    _makefile(project, 'check:\n\t@test -z "$(TESTS)$$MAKEFLAGS_SEEN"\n')
    res = _launch(project, env={"MAKEFLAGS": "TESTS=none"})
    assert res.returncode == 0, res.stdout + res.stderr
    assert any("MAKEFLAGS" in w for w in _warns(res)), res.stdout + res.stderr


@pytest.mark.parametrize(
    "var", ["PYTHONPATH", "PYTHONSTARTUP", "PYTHONUSERBASE", "PYTHONHOME", "LD_PRELOAD"]
)
def test_startup_variables_warn_and_are_dropped(project: Project, var: str) -> None:
    _makefile(project, f'check:\n\t@test -z "$${var}"\n')
    res = _launch(project, env={var: "/tmp/hostile"})
    assert res.returncode == 0, res.stdout + res.stderr
    assert any(var in w for w in _warns(res)), res.stdout + res.stderr


def test_user_site_is_disabled(project: Project) -> None:
    _makefile(project, 'check:\n\t@test "$$PYTHONNOUSERSITE" = 1\n')
    res = _launch(project)
    assert res.returncode == 0, res.stdout + res.stderr


def test_lock_bypass_is_dropped(project: Project) -> None:
    _makefile(project, 'check:\n\t@test -z "$$HARNESS_CHECK_LOCK_HELD"\n')
    res = _launch(project, env={"HARNESS_CHECK_LOCK_HELD": "1"})
    assert res.returncode == 0, res.stdout + res.stderr
    assert any("HARNESS_CHECK_LOCK_HELD" in w for w in _warns(res)), res.stdout + res.stderr


@pytest.mark.parametrize("name", ["sitecustomize.py", "usercustomize.py", "src/evil.pth"])
def test_startup_hooks_in_the_tree_warn(project: Project, name: str) -> None:
    _makefile(project, BARE_GREEN)
    project.write(name, "import os\n")
    res = _launch(project)
    assert res.returncode == 0
    assert any(Path(name).name in w for w in _warns(res)), res.stdout + res.stderr


def test_site_packages_pth_is_not_a_warning(project: Project) -> None:
    _makefile(project, VALID)
    project.write(".venv/lib/python3.12/site-packages/_pkg.pth", "/x/src\n")
    res = _launch(project, "--validate")
    assert _warns(res) == [], res.stdout + res.stderr


def test_variables_outside_the_allowlist_are_named_not_valued(project: Project) -> None:
    _makefile(project, VALID)
    res = _launch(project, "--validate", env={"MY_SECRET_TOKEN": "s3cr3t-value"})
    out = res.stdout + res.stderr
    assert "MY_SECRET_TOKEN" in out
    assert "s3cr3t-value" not in out


# ── HARNESS_CHECK_CMD ────────────────────────────────────────────────────────


def test_check_cmd_override_warns_in_a_decision_run(project: Project) -> None:
    _makefile(project, BARE_GREEN)
    res = _launch(project, "--decision", env={"HARNESS_CHECK_CMD": "true"})
    assert res.returncode == 0
    assert any("HARNESS_CHECK_CMD" in w for w in _warns(res)), res.stdout + res.stderr


def test_check_cmd_override_is_quiet_outside_decision_runs(project: Project) -> None:
    _makefile(project, BARE_GREEN)
    res = _launch(project, env={"HARNESS_CHECK_CMD": "true"})
    assert res.returncode == 0
    assert not any("HARNESS_CHECK_CMD" in w for w in _warns(res)), res.stdout + res.stderr


# ── exit code and green record ───────────────────────────────────────────────


def test_warnings_never_change_the_exit_code(project: Project) -> None:
    _makefile(project, "check:\n\t@exit 3\n")
    _makefile(project, BARE_GREEN, "GNUmakefile")  # warns, and make reads it: green
    assert _launch(project).returncode == 0
    (project.root / "GNUmakefile").unlink()
    project.commit_all("rm")
    assert _launch(project).returncode == 2  # make: a failed recipe


def test_log_receives_output_and_warnings(project: Project, tmp_path: Path) -> None:
    _makefile(project, BARE_GREEN)
    log = tmp_path / "check.log"
    res = _launch(project, "--log", str(log))
    assert res.returncode == 0
    text = log.read_text()
    assert "ok" in text and "[WARN]" in text
    assert "[WARN]" in res.stderr  # also shown to the caller


def test_record_flag_writes_the_green_record(project: Project) -> None:
    _makefile(project, BARE_GREEN)
    assert _launch(project, "--decision", "--record").returncode == 0
    assert _record(project).is_file()


def test_record_flag_with_override_writes_no_record(project: Project) -> None:
    _makefile(project, BARE_GREEN)
    _launch(project, "--decision", "--record", env={"HARNESS_CHECK_CMD": "true"})
    assert not _record(project).exists()


def test_only_the_launcher_writes_the_green_record() -> None:
    lib = TOOLKIT / "lib"
    writers = [
        p.name
        for p in lib.iterdir()
        if p.is_file() and p.name != "run_check.sh" and '>"$GREEN_RECORD"' in p.read_text()
    ]
    assert writers == []
    assert '>"$GREEN_RECORD"' in (lib / "run_check.sh").read_text()


# ── wiring ───────────────────────────────────────────────────────────────────


def test_gate_runs_through_the_launcher(project: Project) -> None:
    _makefile(project, BARE_GREEN)  # no include: the launcher warns
    res = project.run(
        "review_with_codex.sh", None, "code", LEAF_ID, env={"HARNESS_CODEX_MODE": "manual"}
    )
    assert "harness/harness.mk" in res.stderr, res.stdout + res.stderr
    assert _record(project).is_file()


def test_direct_make_check_says_it_is_not_a_decision(project: Project) -> None:
    _makefile(project, VALID)
    env = {k: v for k, v in os.environ.items() if not k.startswith("HARNESS_")}
    res = subprocess.run(
        ["make", "check"], cwd=project.root, capture_output=True, text=True, env=env, check=False
    )
    assert "run_check.sh" in res.stderr, res.stdout + res.stderr
