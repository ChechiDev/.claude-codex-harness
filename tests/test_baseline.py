"""`status: baseline`: work that existed before the harness, registered in the plan.

A baseline subtask counts as complete for phase progression and dependencies,
but only once Codex approved its phase; it needs no code review nor reference
doc. Its status line is part of the phase seal, so nothing can become (or stop
being) baseline after the approval.
"""

from __future__ import annotations

import json
import subprocess

import pytest
from conftest import TOOLKIT, Plan

SPEC = "docs/plan/phase-01-p01/1.1.2-s.md"


def _baseline_phase(plan: Plan, *, approved: bool = True) -> Plan:
    plan.phase("01", "1.1").sub("1.1.1", status="baseline").sub("1.1.2", status="baseline")
    plan.phase("02", "2.1").sub("2.1.1").approve_roadmap()
    if approved:
        plan.approve_phase("01")
    return plan.sync()


def _bash(plan: Plan, script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(TOOLKIT / "lib" / script)],
        cwd=plan.root,
        text=True,
        capture_output=True,
        check=False,
    )


def _features(plan: Plan) -> dict[str, object]:
    data: dict[str, object] = json.loads((plan.root / "feature_list.json").read_text())
    return data


# ── sync: the status is valid and derives like done ──────────────────────────


def test_baseline_is_a_valid_status(plan: Plan) -> None:
    _baseline_phase(plan)
    data = _features(plan)
    assert "baseline" in data["rules"]["valid_status"]  # type: ignore[index]
    task = data["features"][0]  # type: ignore[index]
    assert task["subtasks"][0]["status"] == "baseline"
    assert task["status"] == "done"
    assert data["phases"][0]["status"] == "done"  # type: ignore[index]


def test_index_marks_baseline_subtasks(plan: Plan) -> None:
    _baseline_phase(plan)
    index = (plan.root / "docs/plan/PLAN.md").read_text()
    assert "[x] [1.1.1 Sub 1.1.1](phase-01-p01/1.1.1-s.md) — baseline" in index
    assert "code review" not in index  # baseline was never code-reviewed
    assert "2 of 3 subtasks done (2 baseline)" in index
    assert "Phase 01 — P01 · 2/2 done" in index


# ── plan rules: baseline counts as done once its phase is approved ───────────


def test_baseline_phase_lets_the_next_phase_be_broken_down(plan: Plan) -> None:
    res = _baseline_phase(plan).validate()
    assert res.returncode == 0, res.stdout


def test_unapproved_baseline_does_not_count_as_done(plan: Plan) -> None:
    res = _baseline_phase(plan, approved=False).validate()
    assert res.returncode == 1
    assert "phase 02 is broken down but phase 01 is not done" in res.stdout


def test_baseline_satisfies_a_dependency(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1", status="baseline")
    plan.sub("1.1.2", status="in_progress", depends="1.1.1")
    plan.approve_roadmap().approve_phase("01").sync()
    res = plan.validate()
    assert res.returncode == 0, res.stdout


def test_baseline_needs_no_code_review_nor_reference_doc(plan: Plan) -> None:
    _baseline_phase(plan)
    assert plan.run_py("_harness.py", "done-ids").stdout == ""
    for script in ("gate.sh", "doc_gate.sh"):
        res = _bash(plan, script)
        assert res.returncode == 0, script + res.stdout


def test_status_report_tags_baseline(plan: Plan) -> None:
    _baseline_phase(plan)
    res = plan.run_py("plan_status.py")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "[BASE]" in res.stdout
    assert "2 baseline" in res.stdout


# ── seal: baseline is fixed by the phase approval ────────────────────────────


def _sealed(plan: Plan) -> Plan:
    plan.phase("01", "1.1").sub("1.1.1", status="baseline").sub("1.1.2")
    plan.approve_roadmap().approve_phase("01").sync()
    for args in (("seal", "roadmap"), ("seal", "phase", "01")):
        res = plan.run_py("_guard.py", *args)
        assert res.returncode == 0, res.stdout + res.stderr
    return plan


def _set_status(plan: Plan, rel: str, old: str, new: str) -> None:
    path = plan.root / rel
    path.write_text(path.read_text().replace(f"- status: {old}", f"- status: {new}"))
    assert plan.run_py("sync_plan.py").returncode == 0


@pytest.mark.e2e
def test_turning_a_subtask_into_baseline_after_approval_fails(plan: Plan) -> None:
    # Otherwise a pending subtask could skip its plan, code and doc gates.
    _sealed(plan)
    _set_status(plan, SPEC, "pending", "baseline")
    res = plan.run_py("_guard.py", "integrity")
    assert res.returncode == 1
    assert f"{SPEC} changed after phase 01 was approved" in res.stdout


def test_dropping_baseline_after_approval_fails(plan: Plan) -> None:
    _sealed(plan)
    _set_status(plan, "docs/plan/phase-01-p01/1.1.1-s.md", "baseline", "pending")
    assert plan.run_py("_guard.py", "integrity").returncode == 1


def test_other_status_changes_stay_outside_the_seal(plan: Plan) -> None:
    _sealed(plan)
    _set_status(plan, SPEC, "pending", "blocked")
    res = plan.run_py("_guard.py", "integrity")
    assert res.returncode == 0, res.stdout


# ── prompts and docs ─────────────────────────────────────────────────────────


def test_prompts_and_docs_cover_baseline() -> None:
    leader = (TOOLKIT / "agents" / "leader.md").read_text()
    reviewer = (TOOLKIT / "codex" / "reviewer.md").read_text()
    usage = (TOOLKIT / "docs" / "usage.md").read_text()
    assert "status: baseline" in leader
    assert "status: baseline" in reviewer
    assert "## Existing project: register what is already built" in usage


# ── git conventions: a task that is all baseline never gets a branch ─────────

FEATURE = '[git]\nbranch = "feature/{ticket}-{slug}"\n'


def test_baseline_task_needs_no_ticket(plan: Plan) -> None:
    # Work from before the harness often has no ticket; it never goes through the cycle.
    _baseline_phase(plan)
    plan.config(FEATURE)
    plan.ticket("2.1", "BACK-1").sync()
    res = plan.validate()
    assert res.returncode == 0, res.stdout


def test_task_with_pending_work_still_needs_a_ticket(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1", status="baseline").sub("1.1.2")
    plan.approve_roadmap().approve_phase("01").config(FEATURE).sync()
    res = plan.validate()
    assert res.returncode == 1
    assert "task 1.1 has no '- ticket:'" in res.stdout


def test_baseline_ticket_is_still_checked(plan: Plan) -> None:
    _baseline_phase(plan)
    plan.config(FEATURE).ticket("1.1", "BAD TICKET").ticket("2.1", "BACK-1").sync()
    assert plan.validate().returncode == 1
