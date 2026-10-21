"""_harness.py: plan rules (roadmap → phase by phase → subtask) and gate preconditions."""

from __future__ import annotations

import pytest
from conftest import Plan


def test_roadmap_only_is_valid_before_approval(plan: Plan) -> None:
    res = plan.phase("01").phase("02").sync().validate()
    assert res.returncode == 0, res.stdout


def test_breaking_down_a_phase_needs_the_roadmap_approved(plan: Plan) -> None:
    res = plan.phase("01", "1.1").sub("1.1.1").sync().validate()
    assert res.returncode == 1
    assert "phase 01 is broken down but the roadmap is not ROADMAP_APPROVED" in res.stdout


def test_one_phase_at_a_time(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1", "in_progress").phase("02", "2.1").sub("2.1.1")
    res = plan.approve_roadmap().approve_phase("01").sync().validate()
    assert res.returncode == 1
    assert "phase 02 is broken down but phase 01 is not done (one phase at a time)" in res.stdout


def test_next_phase_can_be_broken_down_once_the_previous_is_done(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1", "done").phase("02", "2.1").sub("2.1.1")
    res = plan.approve_roadmap().approve_phase("01").sync().validate()
    assert res.returncode == 0, res.stdout


def test_skipping_an_unbroken_phase_is_rejected(plan: Plan) -> None:
    plan.phase("01").phase("02", "2.1").sub("2.1.1")
    res = plan.approve_roadmap().sync().validate()
    assert res.returncode == 1
    assert "phase 02 is broken down but phase 01 is not done" in res.stdout


@pytest.mark.parametrize("status", ["in_progress", "done"])
def test_started_subtask_needs_its_phase_approved(plan: Plan, status: str) -> None:
    res = plan.phase("01", "1.1").sub("1.1.1", status).approve_roadmap().sync().validate()
    assert res.returncode == 1
    assert f"1.1.1 is {status} but phase 01 is not PHASE_APPROVED" in res.stdout


def test_started_subtask_needs_its_dependencies_done(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1").sub("1.1.2", "in_progress", depends="1.1.1")
    res = plan.approve_roadmap().approve_phase("01").sync().validate()
    assert res.returncode == 1
    assert "1.1.2 is in_progress but depends on 1.1.1 (pending)" in res.stdout


def test_a_mentioned_verdict_is_not_a_verdict(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1")
    plan.verdict("roadmap_review.md", "CHANGES_REQUESTED\n\nnot ROADMAP_APPROVED yet")
    res = plan.sync().validate()
    assert res.returncode == 1


def test_happy_path_is_valid(plan: Plan) -> None:
    plan.phase("01", "1.1", "1.2").sub("1.1.1", "done").sub("1.1.2", "in_progress", "1.1.1")
    plan.sub("1.2.1").phase("02")
    res = plan.approve_roadmap().approve_phase("01").sync().validate()
    assert res.returncode == 0, res.stdout
    assert "3 work units" in res.stdout


# ── gate preconditions used by review_with_codex.sh ─────────────────────────


def test_phase_ready_needs_roadmap_and_a_breakdown(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1").sync()
    res = plan.run_py("_harness.py", "phase-ready", "01")
    assert res.returncode == 1
    assert "roadmap" in res.stdout + res.stderr
    plan.approve_roadmap()
    assert plan.run_py("_harness.py", "phase-ready", "01").returncode == 0


def test_phase_ready_rejects_a_phase_without_subtasks(plan: Plan) -> None:
    plan.phase("01").approve_roadmap().sync()
    res = plan.run_py("_harness.py", "phase-ready", "01")
    assert res.returncode == 1
    assert "no subtasks" in res.stdout + res.stderr


def test_subtask_ready_needs_its_phase_approved(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1").approve_roadmap().sync()
    assert plan.run_py("_harness.py", "subtask-ready", "1.1.1").returncode == 1
    plan.approve_phase("01")
    assert plan.run_py("_harness.py", "subtask-ready", "1.1.1").returncode == 0


def test_spec_and_phase_lookups(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1").sync()
    spec = plan.run_py("_harness.py", "spec", "1.1.1").stdout.strip()
    assert spec == "docs/plan/phase-01-p01/1.1.1-s.md"
    assert plan.run_py("_harness.py", "phase-of", "1.1.1").stdout.strip() == "01"
    assert plan.run_py("_harness.py", "feature-name", "01").stdout.strip() == "P01"
