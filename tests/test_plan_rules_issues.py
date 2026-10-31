"""_harness.py: the issues lane — exempt from phase progression, own gate, own branch."""

from __future__ import annotations

from conftest import Plan


def _phase_in_progress(plan: Plan) -> Plan:
    """Phase 01 approved and being worked on; phase 02 only planned."""
    plan.phase("01", "1.1").sub("1.1.1", "done").sub("1.1.2").phase("02")
    return plan.approve_roadmap().approve_phase("01")


def test_issue_leaves_join_the_leaf_list(plan: Plan) -> None:
    _phase_in_progress(plan).issue(14).isub("ISS-14.1").isub("ISS-14.2").sync()
    assert plan.run_py("_harness.py", "spec", "ISS-14.1").stdout.strip() == (
        "docs/plan/issues/ISS-14-i14/ISS-14.1-s.md"
    )
    assert plan.run_py("_harness.py", "feature-name", "ISS-14").stdout.strip() == "Issue 14"
    assert plan.run_py("_harness.py", "feature-name", "ISS-14.2").stdout.strip() == ("Sub ISS-14.2")
    assert plan.run_py("_harness.py", "declared-files", "ISS-14.1").stdout.strip() == (
        "src/app/mISS-141.py"
    )


def test_an_issue_on_a_closed_phase_does_not_reopen_it(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1", "done").phase("02", "2.1").sub("2.1.1", "in_progress")
    plan.approve_roadmap().approve_phase("01").approve_phase("02")
    plan.issue(14).isub("ISS-14.1", "done", depends="1.1.1").approve_issue(14)
    res = plan.sync().validate()
    assert res.returncode == 0, res.stdout  # no "phase 02 is broken down but …"


def test_issue_subtask_needs_its_issue_approved(plan: Plan) -> None:
    _phase_in_progress(plan).issue(14).isub("ISS-14.1", "in_progress")
    res = plan.sync().validate()
    assert res.returncode == 1
    assert (
        "ISS-14.1 is in_progress but issue ISS-14 is not ISSUE_APPROVED "
        "(progress/issue_review_ISS-14.md)"
    ) in res.stdout
    assert plan.approve_issue(14).validate().returncode == 0


def test_issue_subtask_needs_its_dependencies_done(plan: Plan) -> None:
    _phase_in_progress(plan).issue(14).approve_issue(14)
    plan.isub("ISS-14.1").isub("ISS-14.2", "in_progress", depends="ISS-14.1")
    res = plan.sync().validate()
    assert res.returncode == 1
    assert "ISS-14.2 is in_progress but depends on ISS-14.1 (pending)" in res.stdout


def test_one_in_progress_across_both_lanes(plan: Plan) -> None:
    plan.phase("01", "1.1").sub("1.1.1", "in_progress").approve_roadmap().approve_phase("01")
    plan.issue(14).isub("ISS-14.1", "in_progress").approve_issue(14)
    res = plan.sync().validate()
    assert res.returncode == 1
    assert "2 leaves in in_progress (max 1): 1.1.1, ISS-14.1" in res.stdout


def test_issue_ready_needs_at_least_one_spec(plan: Plan) -> None:
    _phase_in_progress(plan).issue(14).sync()
    res = plan.run_py("_harness.py", "issue-ready", "ISS-14")
    assert res.returncode == 1
    assert "issue ISS-14 has no subtasks yet: break it down first" in res.stdout + res.stderr
    plan.isub("ISS-14.1").sync()
    assert plan.run_py("_harness.py", "issue-ready", "ISS-14").returncode == 0


def test_issue_ready_rejects_an_unknown_issue(plan: Plan) -> None:
    _phase_in_progress(plan).sync()
    res = plan.run_py("_harness.py", "issue-ready", "ISS-99")
    assert res.returncode == 1
    assert "issue ISS-99 does not exist in docs/plan/issues/" in res.stdout + res.stderr


def test_issue_ready_does_not_need_the_roadmap(plan: Plan) -> None:
    plan.issue(14).isub("ISS-14.1").sync()  # a project with only an issue planned
    assert plan.run_py("_harness.py", "issue-ready", "ISS-14").returncode == 0


def test_subtask_ready_for_an_issue_subtask(plan: Plan) -> None:
    _phase_in_progress(plan).issue(14).isub("ISS-14.1").sync()
    assert plan.run_py("_harness.py", "subtask-ready", "ISS-14.1").returncode == 1
    plan.approve_issue(14)
    assert plan.run_py("_harness.py", "subtask-ready", "ISS-14.1").returncode == 0


def test_issue_branch_is_type_slash_id_slug(plan: Plan) -> None:
    _phase_in_progress(plan).issue(14, "refactor").isub("ISS-14.1").sync()
    res = plan.run_py("_harness.py", "branch", "ISS-14")
    assert res.returncode == 0, res.stdout + res.stderr
    assert res.stdout.strip() == "refactor/ISS-14-issue-14"


def test_issue_branch_ignores_the_task_branch_template(plan: Plan) -> None:
    plan.config('[git]\nbranch = "feature/{ticket}-{slug}"\n').ticket("1.1", "T-1")
    _phase_in_progress(plan).issue(14, "fix").isub("ISS-14.1").sync()
    assert plan.run_py("_harness.py", "branch", "ISS-14").stdout.strip() == "fix/ISS-14-issue-14"
    assert plan.validate().returncode == 0  # issues need no ticket


def test_branch_of_a_leaf_resolves_its_task_or_issue(plan: Plan) -> None:
    _phase_in_progress(plan).issue(14, "docs").isub("ISS-14.1").sync()
    res = plan.run_py("_harness.py", "branch-of", "ISS-14.1")
    assert res.stdout.strip() == "docs/ISS-14-issue-14"
    res = plan.run_py("_harness.py", "branch-of", "1.1.2")
    assert res.stdout.strip() == "feat/1.1-task-1-1"


def test_issue_lookup_commands(plan: Plan) -> None:
    _phase_in_progress(plan).issue(14, "refactor", "now", "- interrupts: feat/1.1-x\n")
    plan.isub("ISS-14.1").sync()
    assert plan.run_py("_harness.py", "issue-of", "ISS-14.1").stdout.strip() == "ISS-14"
    assert plan.run_py("_harness.py", "issue-of", "1.1.1").returncode == 1
    res = plan.run_py("_harness.py", "issue-field", "ISS-14", "interrupts")
    assert res.stdout.strip() == "feat/1.1-x"
    res = plan.run_py("_harness.py", "issue-field", "ISS-14", "number")
    assert res.stdout.strip() == "14"
