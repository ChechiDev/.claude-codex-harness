"""Issue #125: coupled re-gates while a subtask is in progress.

The user's decisions change docs/stack.md, a phase file and a task's specs at once,
while subtask 1.1.1 is in progress with a sealed, defective oracle. Before the fix no
gate order unlocked it: each counterfactual run assumed only its own target, the
in-progress oracle stayed red outside issue mode, and nothing could rewrite it.

The fix, approved as A1 + B1 (+config) + C1 + D4:
- A1: the counterfactual run of a gate also assumes every re-gate pending BELOW it
  (sealed before, now changed or not approved): stack > roadmap > phase > task.
- B1: the stack/roadmap/phase/task gates get the third run of issue #31, with T = the
  acceptance files of the in-progress subtask. B1-config: a changed test configuration
  file is allowed when it is in that subtask's `files:` (never a conftest.py).
- C1: the plan gate of a subtask with a sealed oracle is a re-plan: the prompt says so
  and gives a read-only copy of the sealed oracle; a re-seal that changes the oracle
  voids the subtask's earlier code approval.
- D4: the reviewer roles define all of it.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import TOOLKIT, Plan, bind_code_approval
from i0_projects import Installed, install_project
from test_regate_deadlock import (
    COUNTERFACTUAL,
    _check,
    _commit,
    _gate,
    _phase_project,
    _prompt,
    _seal,
    _sh,
    _verdict,
    _write,
)
from test_regate_inflight import THIRD

sys.path.insert(0, str(TOOLKIT / "lib"))
import _harness  # noqa: E402

SPEC_DIR = "docs/plan/phase-01-p01"


# ── A1. HARNESS_ASSUME_APPROVED takes a list ─────────────────────────────────


def test_assumed_reads_a_comma_separated_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_harness.ASSUME_VAR, "stack,phase:01,task:1.1")
    assert _harness.assumed("stack")
    assert _harness.assumed("phase", "01")
    assert _harness.assumed("task", "1.1")
    assert not _harness.assumed("roadmap")
    assert not _harness.assumed("phase", "02")
    assert not _harness.assumed("task", "1")


def test_assumed_never_matches_a_partial_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_harness.ASSUME_VAR, "phase:01x, stack")
    assert not _harness.assumed("phase", "01")
    assert not _harness.assumed("stack")


# ── A1. The cascade: pending re-gates below the gate's target ────────────────


def _guard(plan: Plan, *args: str) -> subprocess.CompletedProcess[str]:
    return plan.run_py("_guard.py", *args)


def _coupled_plan(plan: Plan) -> Plan:
    """Stack, roadmap, phase 01 and task 1.1 approved and sealed (task flow), 1.1.1
    in progress with a plan approval, 1.1.2 pending."""
    plan.approve_stack()
    plan.phase("01", "1.1").phase("02").approve_roadmap().sync()
    assert _guard(plan, "seal", "roadmap").returncode == 0
    plan.approve_phase("01")
    assert _guard(plan, "seal", "phase", "01").returncode == 0
    plan.sub("1.1.1", "in_progress").sub("1.1.2", "pending", "1.1.1").sync()
    plan.verdict("task_review_1.1.md", "TASK_APPROVED")
    res = _guard(plan, "seal", "task", "1.1")
    assert res.returncode == 0, res.stdout + res.stderr
    return plan


def _cascade(plan: Plan, target: str) -> str:
    res = plan.run_py("_expect_red.py", "cascade", target)
    assert res.returncode == 0, res.stdout + res.stderr
    return res.stdout.strip()


def _append(plan: Plan, rel: str, text: str) -> None:
    path = plan.root / rel
    path.write_text(path.read_text() + text)


def test_nothing_pending_means_an_empty_cascade(plan: Plan) -> None:
    _coupled_plan(plan)
    for target in ("stack", "roadmap", "phase:01", "task:1.1"):
        assert _cascade(plan, target) == "", target


def test_the_stack_gate_assumes_every_pending_regate_below_it(plan: Plan) -> None:
    _coupled_plan(plan)
    _append(plan, "docs/plan/phase-01-p01.md", "- note: changed.\n")
    _append(plan, f"{SPEC_DIR}/1.1.2-s.md", "- note: changed.\n")
    assert _cascade(plan, "stack") == "phase:01,task:1.1"
    assert _cascade(plan, "roadmap") == "phase:01,task:1.1"
    assert _cascade(plan, "phase:01") == "task:1.1"
    assert _cascade(plan, "task:1.1") == ""


def test_a_rejected_regate_with_an_intact_seal_is_pending(plan: Plan) -> None:
    _coupled_plan(plan)
    plan.verdict("phase_review_01.md", "CHANGES_REQUESTED")
    assert _cascade(plan, "stack") == "phase:01"


def test_a_gate_never_assumes_a_pending_regate_above_it(plan: Plan) -> None:
    _coupled_plan(plan)
    _append(plan, "docs/stack.md", "changed\n")
    plan.verdict("roadmap_review.md", "CHANGES_REQUESTED")
    _append(plan, f"{SPEC_DIR}/1.1.2-s.md", "- note: changed.\n")
    assert _cascade(plan, "phase:01") == "task:1.1"
    assert _cascade(plan, "task:1.1") == ""
    assert _cascade(plan, "roadmap") == "task:1.1"


def test_a_target_never_sealed_is_not_assumed(plan: Plan) -> None:
    """Only re-gates: a first approval never rides on another gate's run."""
    _coupled_plan(plan)
    plan.verdict("phase_review_02.md", "CHANGES_REQUESTED")
    plan.verdict("task_review_1.2.md", "CHANGES_REQUESTED")
    assert _cascade(plan, "stack") == ""


def test_a_pending_issue_hangs_below_the_stack_and_the_roadmap(plan: Plan) -> None:
    _coupled_plan(plan)
    plan.issue(7).isub("ISS-7.1").sync().approve_issue(7)
    assert _guard(plan, "seal", "issue", "ISS-7").returncode == 0
    plan.verdict("issue_review_ISS-7.md", "CHANGES_REQUESTED")
    assert _cascade(plan, "stack") == "issue:ISS-7"
    assert _cascade(plan, "roadmap") == "issue:ISS-7"
    assert _cascade(plan, "phase:01") == ""


def test_a_task_of_another_phase_is_not_below_the_phase(plan: Plan) -> None:
    _coupled_plan(plan)
    _append(plan, f"{SPEC_DIR}/1.1.2-s.md", "- note: changed.\n")
    assert _cascade(plan, "phase:02") == ""


def test_only_a_sealed_target_is_a_regate(plan: Plan) -> None:
    _coupled_plan(plan)
    for target in ("stack", "roadmap", "phase:01", "task:1.1"):
        assert plan.run_py("_expect_red.py", "sealed", target).returncode == 0, target
    for target in ("phase:02", "task:1.2", "issue:ISS-3"):
        assert plan.run_py("_expect_red.py", "sealed", target).returncode == 1, target
    assert plan.run_py("_expect_red.py", "sealed", "phase:1").returncode == 2


@pytest.mark.parametrize("target", ["", "phase", "phase:1", "task:x", "stack,roadmap", "plan"])
def test_a_malformed_target_is_refused(plan: Plan, target: str) -> None:
    _coupled_plan(plan)
    assert plan.run_py("_expect_red.py", "cascade", target).returncode == 2


# ── B1. T of the in-progress subtask ─────────────────────────────────────────


def test_t_is_the_open_leaf_acceptance_files(plan: Plan) -> None:
    _coupled_plan(plan)
    _write_oracle(plan.root, "1.1.1")
    plan.verdict("plan_review_1.1.1.md", "PLAN_APPROVED")
    res = plan.run_py("_expect_red.py", "leaf")
    assert res.returncode == 0, res.stdout + res.stderr
    assert res.stdout.split() == ["tests/acceptance/test_1.1.1.py"]


def test_a_code_approved_leaf_has_no_t(plan: Plan) -> None:
    _coupled_plan(plan)
    _write_oracle(plan.root, "1.1.1")
    plan.verdict("plan_review_1.1.1.md", "PLAN_APPROVED")
    plan.verdict("review_1.1.1.md", "APPROVED")
    res = plan.run_py("_expect_red.py", "leaf")
    assert res.returncode == 0 and res.stdout.strip() == ""


def _write_oracle(
    root: Path, sid: str, body: str = "def test_ac1_works() -> None:\n    pass\n"
) -> None:
    path = root / "tests" / "acceptance" / f"test_{sid}.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


# ── B1-config: a changed test configuration inside the leaf's files: ─────────


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def _config_plan(plan: Plan, files: str) -> Plan:
    _coupled_plan(plan)
    spec = plan.root / SPEC_DIR / "1.1.1-s.md"
    spec.write_text(spec.read_text().replace("  - src/app/m111.py\n", files))
    assert plan.run_py("sync_plan.py").returncode == 0
    plan.verdict("plan_review_1.1.1.md", "PLAN_APPROVED")
    (plan.root / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (plan.root / "tests").mkdir(exist_ok=True)
    (plan.root / "tests" / "conftest.py").write_text("import os\n")
    _git(plan.root, "init", "-q")
    _git(plan.root, "add", "-A")
    _git(plan.root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
    return plan


def _config_ok(plan: Plan) -> subprocess.CompletedProcess[str]:
    return plan.run_py("_expect_red.py", "config-ok")


def test_unchanged_configuration_is_ok(plan: Plan) -> None:
    _config_plan(plan, "  - pyproject.toml\n")
    res = _config_ok(plan)
    assert res.returncode == 0, res.stdout
    assert res.stdout.strip() == ""


def test_a_changed_pyproject_in_the_leaf_files_is_allowed_and_listed(plan: Plan) -> None:
    _config_plan(plan, "  - pyproject.toml\n  - uv.lock\n")
    _append(plan, "pyproject.toml", "# changed\n")
    res = _config_ok(plan)
    assert res.returncode == 0, res.stdout
    assert res.stdout.split() == ["pyproject.toml"]


def test_a_changed_pyproject_outside_the_leaf_files_is_refused(plan: Plan) -> None:
    _config_plan(plan, "  - src/app/m111.py\n")
    _append(plan, "pyproject.toml", "# changed\n")
    res = _config_ok(plan)
    assert res.returncode == 1
    assert "pyproject.toml" in res.stdout


def test_a_changed_conftest_is_refused_even_in_the_leaf_files(plan: Plan) -> None:
    _config_plan(plan, "  - tests/conftest.py\n")
    _append(plan, "tests/conftest.py", "import sys\n")
    res = _config_ok(plan)
    assert res.returncode == 1
    assert "tests/conftest.py" in res.stdout


def test_a_new_pytest_ini_outside_the_leaf_files_is_refused(plan: Plan) -> None:
    _config_plan(plan, "  - pyproject.toml\n")
    (plan.root / "pytest.ini").write_text("[pytest]\n")
    assert _config_ok(plan).returncode == 1


def test_an_assume_unchanged_pyproject_is_still_seen(plan: Plan) -> None:
    _config_plan(plan, "  - src/app/m111.py\n")
    _git(plan.root, "update-index", "--assume-unchanged", "pyproject.toml")
    _append(plan, "pyproject.toml", "# hidden\n")
    assert _config_ok(plan).returncode == 1


def test_a_rename_reports_its_source_path_too(plan: Plan) -> None:
    """git status -z prints a rename as `R  new\\0old\\0`: the old path counts."""
    _config_plan(plan, "  - tox.ini\n")
    (plan.root / "pytest.ini").write_text("[pytest]\n")
    _git(plan.root, "add", "pytest.ini")
    _git(plan.root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "ini")
    _git(plan.root, "mv", "pytest.ini", "tox.ini")
    res = _config_ok(plan)
    assert res.returncode == 1
    assert "pytest.ini" in res.stdout
    assert "tox.ini" not in res.stdout


# ── C1. The re-plan's verified copy of the sealed oracle ─────────────────────


def _replan_plan(plan: Plan) -> Plan:
    _config_plan(plan, "  - src/app/m111.py\n")
    _write_oracle(plan.root, "1.1.1", "def test_ac1_works() -> None:\n    assert 1\n")
    assert _guard(plan, "seal", "1.1.1").returncode == 0
    return plan


def _snapshot(plan: Plan) -> subprocess.CompletedProcess[str]:
    return plan.run_py("_oracle.py", "replan-snapshot", "1.1.1")


def test_the_snapshot_is_the_sealed_oracle(plan: Plan) -> None:
    _replan_plan(plan)
    res = _snapshot(plan)
    assert res.returncode == 0, res.stdout + res.stderr
    snap = Path(res.stdout.strip())
    assert (snap / "oracle/tests/acceptance/test_1.1.1.py").read_text().endswith("assert 1\n")
    assert (snap / "manifest.sha256").read_text() == (
        plan.root / "progress/acceptance_1.1.1.sha256"
    ).read_text()


def test_a_later_round_reuses_the_verified_snapshot(plan: Plan) -> None:
    """A rejected round may have rewritten the files: the copy of the seal stays."""
    _replan_plan(plan)
    first = _snapshot(plan).stdout.strip()
    _write_oracle(plan.root, "1.1.1", "def test_ac1_works() -> None:\n    assert 2\n")
    res = _snapshot(plan)
    assert res.returncode == 0 and res.stdout.strip() == first
    assert (
        (Path(first) / "oracle/tests/acceptance/test_1.1.1.py").read_text().endswith("assert 1\n")
    )


def test_no_verified_copy_refuses_the_replan(plan: Plan) -> None:
    _replan_plan(plan)
    _write_oracle(plan.root, "1.1.1", "def test_ac1_works() -> None:\n    pass\n")
    res = _snapshot(plan)
    assert res.returncode == 1
    assert "no verified copy" in res.stdout + res.stderr


def test_a_tampered_snapshot_is_not_trusted(plan: Plan) -> None:
    _replan_plan(plan)
    snap = Path(_snapshot(plan).stdout.strip())
    (snap / "oracle/tests/acceptance/test_1.1.1.py").write_text("weakened\n")
    _write_oracle(plan.root, "1.1.1", "def test_ac1_works() -> None:\n    pass\n")
    assert _snapshot(plan).returncode == 1


# ── D4. The roles and the workflow define it ─────────────────────────────────


def _flat_text(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


def test_reviewer_allows_the_regate_exception_in_task_mode_too() -> None:
    text = _flat_text(TOOLKIT / "codex" / "reviewer.md")
    assert "in `roadmap`, `phase`, `task` and `issue` modes" in text


def test_reviewer_defines_the_cascade_and_the_third_run_of_every_regate() -> None:
    text = _flat_text(TOOLKIT / "codex" / "reviewer.md")
    assert "pending re-gates below" in text
    assert "In-progress re-gate, in `roadmap`, `phase`, `task` and `issue` modes" in text
    assert "changed test configuration" in text


def test_reviewer_defines_the_replan() -> None:
    text = _flat_text(TOOLKIT / "codex" / "reviewer.md")
    assert "**Re-plan" in text
    assert "read-only copy of the sealed oracle" in text


def test_planner_stack_mode_takes_the_cascade_and_the_third_run() -> None:
    text = _flat_text(TOOLKIT / "codex" / "codex-planner.md")
    assert "pending re-gates below" in text
    assert "in-progress re-gate" in text


def test_workflow_documents_the_coupled_regate_order() -> None:
    text = _flat_text(TOOLKIT / "lib" / "WORKFLOW.md")
    assert "Coupled re-gates (issue #125)" in text
    assert "top down" in text


# ── e2e: the fly-in sequence in a throwaway project ──────────────────────────

STACK = """# Stack

## Project type
- type: cli
- language: Python 3.12
- why: a command-line tool.

## Architecture
- decision: none
- why: a single small module.

## Persistence
- decision: not applicable
- why: no state.

## Framework
- decision: not applicable
- why: plain argparse.

## Toolchain
- tests: pytest
- lint: ruff
- types: mypy
- build: uv
- why: the standard toolchain.

## Deploy
- decision: not applicable
- why: local only.

## Key dependencies
- pydantic: models.
"""
PHASE_FILE = "docs/plan/phase-01-setup.md"
PHASE_DIR = "docs/plan/phase-01-setup"
PHASE_MD = (
    "# Phase 01 — Setup\n- goal: set up.\n\n"
    "## 1.1 Toolchain\n- layer: infrastructure\n- goal: the toolchain.\n"
)
E2E_SPEC = (
    "# {sid} Sub {sid}\n- status: {status}\n- layer: infrastructure\n- depends: {dep}\n"
    "- files:\n{files}"
    "- acceptance:\n  - AC1 | {ac} | tests/acceptance/test_{sid}.py::test_ac1\n"
)
# The defective oracle: it splits at the FIRST mention of the heading, even mid-line.
BUGGY = '''import re
from pathlib import Path


def test_ac1() -> None:
    """AC1: deps match the stack."""
    stack = Path("docs/stack.md").read_text(encoding="utf-8")
    section = stack.split("## Key dependencies", 1)[1].split("\\n## ", 1)[0]
    assert set(re.findall(r"^- ([a-z0-9-]+):", section, re.MULTILINE)) == {"pydantic"}
'''
FIXED = '''import re
from pathlib import Path


def test_ac1() -> None:
    """AC1: deps match the stack."""
    stack = Path("docs/stack.md").read_text(encoding="utf-8")
    after = re.split(r"^## Key dependencies$", stack, maxsplit=1, flags=re.MULTILINE)[1]
    section = after.split("\\n## ", 1)[0]
    assert set(re.findall(r"^- ([a-z0-9-]+):", section, re.MULTILINE)) == {"pydantic"}
'''
ORACLE = "tests/acceptance/test_1.1.1.py"


def _spec_text(sid: str, status: str, dep: str, ac: str, files: str) -> str:
    slug = sid.replace(".", "")
    return E2E_SPEC.format(
        sid=sid, status=status, dep=dep, ac=ac, files=files or f"  - src/demo/m{slug}.py\n"
    )


def _approve(project: Installed, name: str, token: str, *seal: str) -> None:
    """What the human does in manual mode after the reviewer approves."""
    _verdict(project, name, token)
    _seal(project, *seal)


def _coupled(tmp: Path, files: str = "") -> Installed:
    """Everything approved and sealed, 1.1.1 in progress with the defective oracle
    (green: no mid-line mention yet), committed; then the user's decisions change the
    stack (a mid-line mention), the phase file and both specs."""
    project = install_project(tmp, "python")
    _write(project, "docs/stack.md", STACK)
    _approve(project, "stack_review.md", "STACK_APPROVED", "stack")
    _write(project, "docs/plan/PLAN.md", "# Project plan\n\n## Vision\nA demo.\n")
    _write(project, PHASE_FILE, PHASE_MD)
    _sh(project, "make", "-s", "plan")
    _approve(project, "roadmap_review.md", "ROADMAP_APPROVED", "roadmap")
    _approve(project, "phase_review_01.md", "PHASE_APPROVED", "phase", "01")
    _write(
        project,
        f"{PHASE_DIR}/1.1.1-a.md",
        _spec_text("1.1.1", "in_progress", "none", "deps", files),
    )
    _write(project, f"{PHASE_DIR}/1.1.2-b.md", _spec_text("1.1.2", "pending", "1.1.1", "make", ""))
    _sh(project, "make", "-s", "plan")
    _approve(project, "task_review_1.1.md", "TASK_APPROVED", "task", "1.1")
    _write(project, ORACLE, BUGGY)
    _approve(project, "plan_review_1.1.1.md", "PLAN_APPROVED", "1.1.1")
    _sh(project, "git", "checkout", "-qb", "feat/1.1")
    _commit(project, "approved and sealed")
    res = _check(project)
    assert res.returncode == 0, res.stdout + res.stderr
    stack = project.root / "docs/stack.md"
    stack.write_text(
        stack.read_text().replace(
            "- why: the standard toolchain.",
            "- why: the standard toolchain; the manifest matches `## Key dependencies`.",
        )
    )
    _write(project, PHASE_FILE, PHASE_MD.replace("the toolchain.", "the toolchain, docstrings."))
    _write(
        project,
        f"{PHASE_DIR}/1.1.1-a.md",
        _spec_text("1.1.1", "in_progress", "none", "deps, heading line", files),
    )
    _write(
        project,
        f"{PHASE_DIR}/1.1.2-b.md",
        _spec_text("1.1.2", "pending", "1.1.1", "make, docstrings", ""),
    )
    _sh(project, "make", "-s", "plan")
    return project


def _fails(log: str) -> list[str]:
    return [
        line
        for line in log.splitlines()
        if (line.startswith("[FAIL]") and "NOT healthy" not in line) or line.startswith("FAILED ")
    ]


def _runs(prompt: str) -> tuple[str, list[str], set[str], int, str]:
    """(assumed targets, counterfactual FAIL lines, T, third rc, third log)."""
    cf = COUNTERFACTUAL.search(prompt)
    assert cf is not None, prompt
    third = THIRD.search(prompt)
    assert third is not None, f"no third run in the prompt:\n{prompt}"
    t = set(third["t"].split(","))
    return (
        cf["target"].rstrip(":,"),
        _fails(Path(cf["log"]).read_text()),
        t,
        int(third["rc"]),
        Path(third["log"]).read_text(),
    )


@pytest.mark.e2e
def test_coupled_regates_unlock_top_down_and_the_replan_rewrites_the_oracle(
    tmp_path: Path,
) -> None:
    project = _coupled(tmp_path)

    target, cf_fails, t, rc3, log3 = _runs(_prompt(project, tmp_path, "stack"))
    assert target == "stack,phase:01,task:1.1"
    assert cf_fails == [f"FAILED {ORACLE}::test_ac1 - AssertionError"], cf_fails
    assert t == {ORACLE}
    assert rc3 == 0, log3
    _approve(project, "stack_review.md", "STACK_APPROVED", "stack")

    target, _, t, rc3, log3 = _runs(_prompt(project, tmp_path, "phase", "01"))
    assert target == "phase:01,task:1.1"
    assert t == {ORACLE}
    assert rc3 == 0, log3
    _approve(project, "phase_review_01.md", "PHASE_APPROVED", "phase", "01")

    target, _, t, rc3, log3 = _runs(_prompt(project, tmp_path, "task", "1.1"))
    assert target == "task:1.1"
    assert t == {ORACLE}
    assert rc3 == 0, log3
    _approve(project, "task_review_1.1.md", "TASK_APPROVED", "task", "1.1")

    prompt = _prompt(project, tmp_path, "plan", "1.1.1")
    assert "Re-plan (issue #125)" in prompt
    copy = re.search(r"read-only copy of the sealed oracle: (\S+?)\.?$", prompt, re.M)
    assert copy is not None, prompt
    assert (Path(copy[1]) / ORACLE).read_text() == BUGGY
    # The reviewer rewrites the oracle and approves; the human seals it and records
    # the superseded oracle (the command the manual-mode gate prints).
    _write(project, ORACLE, FIXED)
    _approve(project, "plan_review_1.1.1.md", "PLAN_APPROVED", "1.1.1")
    snap = str(Path(copy[1]).parent)
    assert f"python3 harness/_oracle.py replan-history 1.1.1 {snap}" in prompt
    _sh(project, "python3", "harness/_oracle.py", "replan-history", "1.1.1", snap)
    history = (project.root / "progress/review_history/1.1.1.md").read_text()
    assert "## Superseded oracle (re-plan, issue #125)" in history
    assert '-    section = stack.split("## Key dependencies", 1)' in history
    assert not Path(snap).exists()
    res = _check(project)
    assert res.returncode == 0, res.stdout + res.stderr


@pytest.mark.e2e
def test_a_lower_gate_never_assumes_a_pending_upper_one(tmp_path: Path) -> None:
    project = _coupled(tmp_path)
    target, cf_fails, _, rc3, log3 = _runs(_prompt(project, tmp_path, "phase", "01"))
    assert target == "phase:01,task:1.1"
    assert any("docs/stack.md changed since STACK_APPROVED" in line for line in cf_fails)
    assert rc3 != 0
    assert "docs/stack.md changed since STACK_APPROVED" in log3


@pytest.mark.e2e
def test_a_changed_pyproject_in_the_leaf_files_keeps_the_third_run(tmp_path: Path) -> None:
    project = _coupled(tmp_path, files="  - pyproject.toml\n")
    path = project.root / "pyproject.toml"
    path.write_text(path.read_text() + "\n# 1.1.1 in progress\n")
    prompt = _prompt(project, tmp_path, "stack")
    _, _, t, rc3, log3 = _runs(prompt)
    assert t == {ORACLE}
    assert rc3 == 0, log3
    assert re.search(r"changed test configuration.*?pyproject\.toml", " ".join(prompt.split()))


@pytest.mark.e2e
@pytest.mark.parametrize(
    ("files", "rel"),
    [("  - pyproject.toml\n", "tests/conftest.py"), ("", "pyproject.toml")],
    ids=["conftest-in-files", "pyproject-outside-files"],
)
def test_other_changed_configuration_gets_no_third_run(
    tmp_path: Path, files: str, rel: str
) -> None:
    project = _coupled(tmp_path, files=files)
    path = project.root / rel
    old = path.read_text() if path.is_file() else ""
    path.write_text(old + ("\nimport os\n" if rel.endswith(".py") else "\n# x\n"))
    prompt = _prompt(project, tmp_path, "stack")
    assert THIRD.search(prompt) is None, prompt


# ── C1: a re-seal that changes the oracle voids the earlier code approval ─────


@pytest.mark.e2e
def test_a_reseal_voids_the_code_approval(tmp_path: Path) -> None:
    """A re-seal is a re-plan: its spec may change even when its tests do not."""
    project = _phase_project(tmp_path)  # 1.1.2 in progress, its oracle sealed
    _verdict(project, "review_1.1.2.md", "APPROVED")
    bind_code_approval(project.root, "1.1.2")
    fresh = ["python3", "harness/_gate_run.py", "fresh", "1.1.2"]
    assert project.run(*fresh).returncode == 0
    _seal(project, "1.1.2")  # the same oracle
    res = project.run(*fresh)
    assert res.returncode == 1, res.stdout + res.stderr


@pytest.mark.e2e
def test_a_first_plan_gate_is_no_replan(tmp_path: Path) -> None:
    project = _phase_project(tmp_path)
    (project.root / "progress/acceptance_1.1.2.sha256").unlink()
    _commit(project, "no oracle seal yet")
    res = _gate(project, tmp_path, "plan", "1.1.2")
    assert "Re-plan" not in res.stdout + res.stderr
