"""plan_status.py (`make status`): the issues block, the GitHub queue and Next for issues."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from conftest import TOOLKIT, Plan

FAKE_GH = Path(__file__).resolve().parent / "fake_gh.py"


def _status(plan: Plan, gh: dict[str, Any] | None = None) -> str:
    env = {**os.environ, "HARNESS_GH": str(plan.root / "no-such-gh")}
    if gh is not None:
        state = plan.root / "gh.json"
        state.write_text(json.dumps(gh))
        FAKE_GH.chmod(0o755)
        env |= {"HARNESS_GH": str(FAKE_GH), "FAKE_GH_STATE": str(state)}
    res = subprocess.run(
        ["python3", str(TOOLKIT / "lib/plan_status.py")],
        cwd=plan.root,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    assert res.returncode == 0, res.stdout + res.stderr
    return res.stdout


def _phase_done(plan: Plan) -> Plan:
    return plan.phase("01", "1.1").sub("1.1.1", "done").approve_roadmap().approve_phase("01")


QUEUE = {
    "me": "alice",
    "issues": {
        "14": {"title": "Replace the mask", "labels": ["refactor", "priority:next"]},
        "20": {"title": "Fix the parser", "labels": ["bug", "priority:now"]},
        "21": {"title": "Taken", "labels": ["bug", "status:in-progress"], "assignees": ["bob"]},
        "22": {"title": "Not queued", "labels": ["bug"]},
        "23": {"title": "Later", "labels": ["enhancement", "priority:backlog"]},
    },
}


def _lane_on(plan: Plan) -> Plan:
    """The project uses the issues lane: docs/plan/issues/ exists (shipped README)."""
    folder = plan.root / "docs/plan/issues"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "README.md").write_text("# Issues lane\n")
    return plan


def test_without_the_issues_lane_status_never_calls_github(plan: Plan) -> None:
    _phase_done(plan).sync()
    out = _status(plan, QUEUE)  # gh would answer, but must not be asked
    assert "Issues" not in out
    assert "queue" not in out
    calls = json.loads((plan.root / "gh.json").read_text()).get("calls", [])
    assert calls == []


def test_the_shipped_issues_readme_turns_the_lane_on(plan: Plan) -> None:
    out = _status(_lane_on(_phase_done(plan)).sync(), QUEUE)
    assert "Issue queue (GitHub)" in out


def test_the_issues_block_lists_planned_issues_apart(plan: Plan) -> None:
    _phase_done(plan).issue(14, "refactor").isub("ISS-14.1", "done").isub("ISS-14.2")
    out = _status(plan.approve_issue(14).sync())
    assert "Issues" in out
    assert "ISS-14 Issue 14 · ISSUE_APPROVED · 1/2 done · refactor · priority next" in out
    assert "ISS-14.1 Sub ISS-14.1" in out
    assert "1 issue · 1/2 issue subtasks done" in out
    assert "1 done · 0 in progress · 0 pending · 0 blocked  (1 subtasks)" in out  # phases only


def test_an_issue_being_planned(plan: Plan) -> None:
    out = _status(_phase_done(plan).issue(14).sync())
    assert "ISS-14 Issue 14 · planning · fix · priority next" in out
    assert "Next: break down issue ISS-14 into subtask specs" in out


def test_next_follows_the_issue_gate(plan: Plan) -> None:
    _phase_done(plan).issue(14).isub("ISS-14.1").sync()
    assert "Next: issue gate: ./harness/review_with_codex.sh issue ISS-14" in _status(plan)
    plan.verdict("issue_review_ISS-14.md", "CHANGES_REQUESTED")
    assert "Next: fix the breakdown of issue ISS-14" in _status(plan)
    plan.approve_issue(14)
    assert "Next: start subtask ISS-14.1 (leader: in_progress + make plan)" in _status(plan)


def test_next_follows_an_in_progress_issue_subtask(plan: Plan) -> None:
    _phase_done(plan).issue(14).isub("ISS-14.1", "in_progress").approve_issue(14).sync()
    out = _status(plan)
    assert "Next: plan gate: ./harness/review_with_codex.sh plan ISS-14.1" in out


def test_a_done_issue_waits_for_its_pr_merge(plan: Plan) -> None:
    _phase_done(plan).issue(14).isub("ISS-14.1", "done").approve_issue(14).sync()
    out = _status(plan)
    assert "Next: issue ISS-14 is done: merge its PR (Closes #14)" in out


def test_a_done_now_issue_points_at_resume(plan: Plan) -> None:
    _phase_done(plan).issue(14, "fix", "now", "- interrupts: feat/1.1-task-1-1\n")
    plan.isub("ISS-14.1", "done").approve_issue(14).sync()
    out = _status(plan)
    assert (
        "Next: issue ISS-14 is done: merge its PR (Closes #14), then resume task "
        "feat/1.1-task-1-1: harness/issue.sh resume"
    ) in out


def test_the_queue_comes_from_github(plan: Plan) -> None:
    out = _status(_lane_on(_phase_done(plan)).sync(), QUEUE)
    assert "Issue queue (GitHub)" in out
    lines = [line.strip() for line in out.splitlines()]
    assert "#20 Fix the parser · now" in lines
    assert "#14 Replace the mask · next" in lines
    assert "#23 Later · backlog" in lines
    assert not any("#21" in line or "#22" in line for line in lines)
    assert out.index("#20") < out.index("#14") < out.index("#23")  # now, next, backlog


def test_a_now_issue_in_the_queue_is_the_next_step(plan: Plan) -> None:
    out = _status(_lane_on(_phase_done(plan)).sync(), QUEUE)
    assert "Next: start issue #20 (now): make issue-start N=20" in out


def test_without_gh_the_queue_is_unavailable_and_the_rest_prints(plan: Plan) -> None:
    _phase_done(plan).issue(14).isub("ISS-14.1").sync()
    out = _status(plan, None)
    assert "Issue queue (GitHub) unavailable" in out
    assert "ISS-14 Issue 14" in out
    assert "Next:" in out


def test_without_the_lane_the_output_is_byte_for_byte_the_previous_one(plan: Plan) -> None:
    _phase_done(plan).phase("02", "2.1").sub("2.1.1", "in_progress").sync()
    old = subprocess.run(
        ["git", "show", "54ca858:lib/plan_status.py"],
        cwd=TOOLKIT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    script = plan.root / "old_plan_status.py"
    script.write_text(old)
    env = {**os.environ, "PYTHONPATH": str(TOOLKIT / "lib")}
    before = subprocess.run(
        ["python3", str(script)],
        cwd=plan.root,
        text=True,
        capture_output=True,
        env=env,
        check=True,
    ).stdout
    script.unlink()
    assert _status(plan) == before
