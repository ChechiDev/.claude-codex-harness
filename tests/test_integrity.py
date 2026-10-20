"""check.sh backstop (_guard.py integrity): catches what the per-call hooks can miss."""

from __future__ import annotations

import subprocess
import sys

from conftest import DECLARED, LEAF_ID, TOOLKIT, Project

ACC = f"tests/acceptance/test_{LEAF_ID}.py"


def _integrity(project: Project) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOLKIT / "lib/_guard.py"), "integrity"],
        cwd=project.root,
        text=True,
        capture_output=True,
        check=False,
    )


def _seal(project: Project) -> None:
    res = project.run("review_with_codex.sh", None, "seal", LEAF_ID)
    assert res.returncode == 0, res.stderr


def test_clean_tree_passes(project: Project) -> None:
    res = _integrity(project)
    assert res.returncode == 0, res.stdout


def test_declared_src_change_passes(project: Project) -> None:
    project.write(DECLARED, "x = 1\n")
    assert _integrity(project).returncode == 0


def test_undeclared_src_change_fails(project: Project) -> None:
    project.write("src/pkg/infra/repo.py", "x = 1\n")
    res = _integrity(project)
    assert res.returncode == 1
    assert "src/pkg/infra/repo.py" in res.stdout


def test_src_change_without_in_progress_leaf_fails(project: Project) -> None:
    project.set_leaf(status="done")
    project.write(DECLARED, "x = 1\n")
    assert _integrity(project).returncode == 1


def test_sealed_acceptance_tests_unchanged_pass(project: Project) -> None:
    project.write(ACC, "def test_ac1(): assert False\n")
    _seal(project)
    assert _integrity(project).returncode == 0


def test_tampered_acceptance_tests_fail(project: Project) -> None:
    project.write(ACC, "def test_ac1(): assert False\n")
    _seal(project)
    project.write(ACC, "def test_ac1(): assert True\n")
    res = _integrity(project)
    assert res.returncode == 1
    assert ACC in res.stdout


def test_deleted_acceptance_tests_fail(project: Project) -> None:
    project.write(ACC, "def test_ac1(): assert False\n")
    _seal(project)
    (project.root / ACC).unlink()
    assert _integrity(project).returncode == 1


def test_approved_plan_without_seal_warns(project: Project) -> None:
    project.write(ACC, "def test_ac1(): assert False\n")
    res = _integrity(project)
    assert res.returncode == 0
    assert "[WARN]" in res.stdout


def test_no_git_history_skips_scope_check(project: Project) -> None:
    subprocess.run(["rm", "-rf", ".git"], cwd=project.root, check=True)
    project.write("src/pkg/other.py", "")
    res = _integrity(project)
    assert res.returncode == 0
    assert "[WARN]" in res.stdout


def test_gate_sh_ignores_approved_substrings(project: Project) -> None:
    project.set_leaf(status="done")
    project.set_verdict("code", "CHANGES_REQUESTED", extra="Not APPROVED yet.")
    res = project.run("gate.sh")
    assert res.returncode == 1


def test_harness_validate_counts_work_units(project: Project) -> None:
    res = subprocess.run(
        [sys.executable, str(TOOLKIT / "lib/_harness.py"), "validate"],
        cwd=project.root,
        text=True,
        capture_output=True,
        check=False,
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert "1 work units" in res.stdout
