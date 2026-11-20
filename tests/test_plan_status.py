"""plan_status.py (`make status`): planned / done / in progress / pending + next step."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import TOOLKIT, Plan


def _status(plan: Plan) -> str:
    res = plan.run_py("plan_status.py")
    assert res.returncode == 0, res.stdout + res.stderr
    return res.stdout


def test_empty_plan_points_at_discovery(plan: Plan) -> None:
    out = _status(plan.sync())
    assert "No phases planned yet" in out
    assert "Next: ask the leader to plan the project" in out


def test_roadmap_waiting_for_codex(plan: Plan) -> None:
    out = _status(plan.phase("01").phase("02").sync())
    assert "Roadmap    pending Codex review" in out
    assert "Phase 01 — P01 · not broken down yet" in out
    assert "Next: roadmap gate: ./harness/review_with_codex.sh roadmap" in out


def test_roadmap_changes_requested_is_visible(plan: Plan) -> None:
    plan.phase("01").verdict("roadmap_review.md", "CHANGES_REQUESTED")
    out = _status(plan.sync())
    assert "Roadmap    CHANGES_REQUESTED (progress/roadmap_review.md)" in out


def test_approved_roadmap_asks_for_the_first_breakdown(plan: Plan) -> None:
    out = _status(plan.phase("01").phase("02").approve_roadmap().sync())
    assert "Roadmap    ROADMAP_APPROVED" in out
    assert "Next: break down phase 01 into tasks" in out


def test_broken_down_phase_waits_for_its_gate(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1").approve_roadmap()
    out = _status(plan.sync())
    assert "Phase 01 — P01 · pending Codex review · 0/1 done" in out
    assert "Next: phase gate: ./harness/review_with_codex.sh phase 01" in out


def test_tree_shows_every_state_and_the_summary(plan: Plan) -> None:
    plan.phase("01", "1.1", "1.2").sub("1.1.1", "done").sub("1.1.2", "in_progress", "1.1.1")
    plan.sub("1.2.1", "blocked").sub("1.2.2").phase("02")
    plan.approve_roadmap().approve_phase("01").verdict("plan_review_1.1.2.md", "PLAN_APPROVED")
    out = _status(plan.sync())
    for line in (
        "Phase 01 — P01 · PHASE_APPROVED · 1/4 done",
        "  1.1 Task 1.1 · in progress",
        "    [DONE]  1.1.1 Sub 1.1.1",
        "    [WIP]   1.1.2 Sub 1.1.2 · plan: PLAN_APPROVED · code: —",
        "    [BLOCK] 1.2.1 Sub 1.2.1",
        "    [TODO]  1.2.2 Sub 1.2.2",
        "Phase 02 — P02 · not broken down yet",
        "1 done · 1 in progress · 1 pending · 1 blocked  (4 subtasks)",
        "Next: implement 1.1.2 (implementer), then code gate:"
        " ./harness/review_with_codex.sh code 1.1.2",
    ):
        assert line in out, line


@pytest.mark.parametrize(
    ("verdicts", "expected"),
    [
        ({}, "Next: plan gate: ./harness/review_with_codex.sh plan 1.1.1"),
        (
            {"plan_review_1.1.1.md": "CHANGES_REQUESTED"},
            "Next: fix the spec of 1.1.1 (progress/plan_review_1.1.1.md), then plan gate",
        ),
        (
            {"plan_review_1.1.1.md": "PLAN_APPROVED", "review_1.1.1.md": "CHANGES_REQUESTED"},
            "Next: fix 1.1.1 (progress/review_1.1.1.md), then code gate",
        ),
        (
            {"plan_review_1.1.1.md": "PLAN_APPROVED", "review_1.1.1.md": "APPROVED"},
            "Next: close: ./harness/review_with_codex.sh doc 1.1.1",
        ),
    ],
)
def test_next_step_follows_the_subtask_gates(
    plan: Plan, verdicts: dict[str, str], expected: str
) -> None:
    plan.phase("01", "1.1").sub("1.1.1", "in_progress").approve_roadmap().approve_phase("01")
    for name, verdict in verdicts.items():
        plan.verdict(name, verdict)
    assert expected in _status(plan.sync())


def test_next_pending_subtask_respects_dependencies(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1", "done").sub("1.1.2", depends="1.1.3").sub("1.1.3")
    out = _status(plan.approve_roadmap().approve_phase("01").sync())
    assert "Next: start subtask 1.1.3" in out


def test_finished_phase_points_at_the_next_breakdown(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1", "done").phase("02")
    out = _status(plan.approve_roadmap().approve_phase("01").sync())
    assert "Phase 01 — P01 · done · 1/1 done" in out
    assert "Next: break down phase 02 into tasks" in out


def test_everything_done(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1", "done")
    out = _status(plan.approve_roadmap().approve_phase("01").sync())
    assert "Next: all planned work is done" in out


@pytest.mark.parametrize("template", ["templates", "templates-c"])
def test_make_status_target(tmp_path: Path, template: str) -> None:
    plan = Plan(tmp_path).phase("01").sync()
    (tmp_path / "harness").symlink_to(TOOLKIT / "lib")
    shutil.copy(TOOLKIT / template / "Makefile", tmp_path / "Makefile")
    res = subprocess.run(
        ["make", "-s", "status"], cwd=plan.root, text=True, capture_output=True, check=False
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert "Roadmap" in res.stdout
