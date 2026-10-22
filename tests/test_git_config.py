"""harness.toml [git]: per-project task-branch pattern, commit prefix and setup branch."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import TOOLKIT, Plan, Project, bash_payload

FEATURE = '[git]\nbranch = "feature/{ticket}-{slug}"\ncommit_prefix = "{ticket} "\n'


def _planned(plan: Plan, ticket: str | None = "GUAK-042") -> Plan:
    plan.phase("01", "1.1").sub("1.1.1", status="in_progress").approve_roadmap().approve_phase("01")
    if ticket is not None:
        plan.ticket("1.1", ticket)
    return plan


def _cmd(plan: Plan, *args: str) -> subprocess.CompletedProcess[str]:
    return plan.run_py("_harness.py", *args)


# ── plan: the ticket of a task ───────────────────────────────────────────────


def test_task_ticket_reaches_the_feature_list(plan: Plan) -> None:
    _planned(plan).sync()
    task = json.loads((plan.root / "feature_list.json").read_text())["features"][0]
    assert task["ticket"] == "GUAK-042"


def test_task_without_ticket_adds_no_key(plan: Plan) -> None:
    # Installed projects must not go stale: their feature_list.json has no "ticket".
    _planned(plan, ticket=None).sync()
    task = json.loads((plan.root / "feature_list.json").read_text())["features"][0]
    assert "ticket" not in task


# ── branch / commit-prefix commands ──────────────────────────────────────────


def test_default_branch_is_feat_task_slug(plan: Plan) -> None:
    _planned(plan, ticket=None).sync()
    res = _cmd(plan, "branch", "1.1")
    assert res.returncode == 0, res.stdout + res.stderr
    assert res.stdout.strip() == "feat/1.1-task-1-1"


def test_configured_branch_uses_the_ticket(plan: Plan) -> None:
    _planned(plan).config(FEATURE).sync()
    assert _cmd(plan, "branch", "1.1").stdout.strip() == "feature/GUAK-042-task-1-1"


def test_branch_needing_a_missing_ticket_fails(plan: Plan) -> None:
    _planned(plan, ticket=None).config(FEATURE).sync()
    res = _cmd(plan, "branch", "1.1")
    assert res.returncode == 1
    assert "ticket" in res.stdout + res.stderr


def test_branch_of_unknown_task_fails(plan: Plan) -> None:
    _planned(plan).sync()
    assert _cmd(plan, "branch", "9.9").returncode == 1


def test_commit_prefix_is_rendered_per_subtask(plan: Plan) -> None:
    _planned(plan).config(FEATURE).sync()
    res = _cmd(plan, "commit-prefix", "1.1.1")
    assert res.returncode == 0, res.stdout + res.stderr
    assert res.stdout == "GUAK-042 \n"


def test_default_commit_prefix_is_empty(plan: Plan) -> None:
    _planned(plan, ticket=None).sync()
    res = _cmd(plan, "commit-prefix", "1.1.1")
    assert res.returncode == 0, res.stdout + res.stderr
    assert res.stdout == "\n"


# ── validate: the plan must satisfy the pattern ──────────────────────────────


def test_validate_requires_tickets_when_the_pattern_uses_them(plan: Plan) -> None:
    _planned(plan, ticket=None).config(FEATURE).sync()
    res = plan.validate()
    assert res.returncode == 1
    assert "1.1" in res.stdout and "ticket" in res.stdout


def test_validate_passes_with_tickets(plan: Plan) -> None:
    _planned(plan).config(FEATURE).sync()
    res = plan.validate()
    assert res.returncode == 0, res.stdout


@pytest.mark.parametrize(
    "config",
    [
        '[git]\nbranch = "feature/{foo}"\n',  # unknown placeholder
        '[git]\nbranch = "feature/{ticket} {slug}"\n',  # not a valid ref
        "[git]\nbranch = 3\n",
        '[git]\ncommit_prefix = "{ticket}\\n"\n',  # would break the subject line
        "[git\n",
    ],
)
def test_validate_rejects_an_invalid_git_config(plan: Plan, config: str) -> None:
    _planned(plan).config(config).sync()
    assert plan.validate().returncode == 1


@pytest.mark.parametrize("ticket", ["GUAK 42", "../x", "a;b"])
def test_validate_rejects_a_ticket_unsafe_for_a_branch(plan: Plan, ticket: str) -> None:
    _planned(plan, ticket=ticket).config(FEATURE).sync()
    assert plan.validate().returncode == 1


# ── integrity: the in_progress subtask lives on its task branch ──────────────


def _integrity(project: Project) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOLKIT / "lib/_guard.py"), "integrity"],
        cwd=project.root,
        text=True,
        capture_output=True,
        check=False,
    )


def _git(project: Project, *args: str) -> None:
    subprocess.run(["git", *args], cwd=project.root, check=True, capture_output=True)


def _configure(project: Project, text: str = '[git]\nbranch = "feature/{task}-{slug}"\n') -> None:
    project.write("harness.toml", text)
    project.commit_all("config")


def test_wrong_branch_for_the_in_progress_subtask_fails(project: Project) -> None:
    _configure(project)
    res = _integrity(project)
    assert res.returncode == 1
    assert "feature/1.1-users" in res.stdout


def test_right_branch_for_the_in_progress_subtask_passes(project: Project) -> None:
    _configure(project)
    _git(project, "switch", "-q", "-c", "feature/1.1-users")
    res = _integrity(project)
    assert res.returncode == 0, res.stdout


def test_without_git_config_the_branch_is_not_enforced(project: Project) -> None:
    # Projects installed before [git] existed keep working on whatever branch they use.
    res = _integrity(project)
    assert res.returncode == 0, res.stdout


# ── setup-pending: the setup branch must be in main (squash merges count) ────


def _setup_pending(project: Project) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOLKIT / "lib/_guard.py"), "setup-pending"],
        cwd=project.root,
        text=True,
        capture_output=True,
        check=False,
    )


def _setup_branch(project: Project, name: str = "chore/project-setup") -> None:
    _git(project, "switch", "-q", "-c", name)
    project.write("docs/conventions.md", "ours\n")
    project.commit_all("setup")
    _git(project, "switch", "-q", "main")


def test_no_setup_branch_is_not_pending(project: Project) -> None:
    assert _setup_pending(project).returncode == 0


def test_unmerged_setup_branch_is_pending(project: Project) -> None:
    _setup_branch(project)
    res = _setup_pending(project)
    assert res.returncode == 1
    assert "chore/project-setup" in res.stdout


def test_merged_setup_branch_is_not_pending(project: Project) -> None:
    _setup_branch(project)
    _git(project, "merge", "-q", "--no-ff", "-m", "merge setup", "chore/project-setup")
    assert _setup_pending(project).returncode == 0


def test_squash_merged_setup_branch_is_not_pending(project: Project) -> None:
    _setup_branch(project)
    _git(project, "merge", "-q", "--squash", "chore/project-setup")
    _git(project, "commit", "-qm", "setup (squashed)")
    project.write("other.md", "main moved on\n")
    project.commit_all("unrelated work on main")
    res = _setup_pending(project)
    assert res.returncode == 0, res.stdout


def test_setup_branch_name_is_configurable(project: Project) -> None:
    _configure(project, '[git]\nsetup_branch = "chore/GUAK-1-harness-setup"\n')
    _setup_branch(project, "chore/GUAK-1-harness-setup")
    assert _setup_pending(project).returncode == 1


# ── the leader and the documenter use the commands, not hand-written names ───


def test_guard_allows_the_leaders_computed_branch_switch(project: Project) -> None:
    cmd = 'git switch -c "$(python3 harness/_harness.py branch 1.1)" main'
    res = project.run("guard_bash.sh", bash_payload(cmd))
    assert res.returncode == 0, res.stderr


def test_harness_toml_is_documented_for_git() -> None:
    text = (TOOLKIT / "docs" / "configuration.md").read_text()
    for needle in ("[git]", "branch =", "commit_prefix =", "setup_branch =", "- ticket:"):
        assert needle in text, needle


def test_prompts_use_the_harness_commands() -> None:
    leader = (TOOLKIT / "agents" / "leader.md").read_text()
    assert "harness/_harness.py branch <N.M>" in leader
    assert "harness/_guard.py setup-pending" in leader
    assert "git branch --no-merged main" not in leader
    assert (
        "harness/_harness.py commit-prefix <N.M.K>"
        in (TOOLKIT / "codex" / "documenter.md").read_text()
    )


def test_plan_template_documents_the_ticket() -> None:
    for tpl in ("templates", "templates-c"):
        assert "- ticket:" in (Path(TOOLKIT / tpl / "docs" / "plan" / "PLAN.md")).read_text(), tpl
