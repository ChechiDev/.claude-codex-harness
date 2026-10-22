"""Agent/skill instructions agree on who branches, sets status and commits."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import TOOLKIT, Project, bash_payload

IMPLEMENT = TOOLKIT / "skills" / "implement" / "SKILL.md"
IMPLEMENTER = TOOLKIT / "agents" / "implementer.md"
LEADER = TOOLKIT / "agents" / "leader.md"


@pytest.mark.parametrize("doc", [IMPLEMENT, IMPLEMENTER], ids=lambda p: p.parent.name)
def test_implementer_never_branches_commits_or_closes(doc: Path) -> None:
    text = doc.read_text()
    for forbidden in ("git commit", "git checkout", "git switch", "COMMIT:"):
        assert forbidden not in text, forbidden


def test_implement_skill_is_language_neutral() -> None:
    text = IMPLEMENT.read_text()
    assert "uv run" not in text
    assert "make verify" in text
    assert "progress/impl_" in text


def test_leader_creates_the_task_branch() -> None:
    # One branch per task N.M (skills/github/branch); one commit per subtask N.M.K.
    text = LEADER.read_text()
    assert 'git switch -c "$(python3 harness/_harness.py branch <N.M>)" main' in text
    assert "default `feat/<N.M>-<slug>`" in text


@pytest.mark.parametrize("cmd", ["git switch -c feat/1.1-user main", "git switch feat/1.1-user"])
def test_guard_allows_the_leaders_branch_commands(project: Project, cmd: str) -> None:
    res = project.run("guard_bash.sh", bash_payload(cmd))
    assert res.returncode == 0, res.stderr


def test_leader_stops_after_planning_for_user_approval() -> None:
    text = LEADER.read_text()
    assert "Stop for the user's approval" in text
    assert "all `pending`" in text


def test_leader_files_rule_covers_c_headers() -> None:
    assert "under `src/` or `include/`" in LEADER.read_text()


def test_no_reference_to_a_missing_plan_skill() -> None:
    skills = TOOLKIT / "skills"
    assert not (skills / "plan").exists()
    for doc in skills.rglob("*.md"):
        if "synced" in doc.parts:
            continue
        assert "the `plan` skill" not in doc.read_text(), doc


# ── planning flow: discovery → roadmap gate → phase by phase → subtask cycle ──

REVIEWER = TOOLKIT / "codex" / "reviewer.md"
DOCUMENTER = TOOLKIT / "codex" / "documenter.md"
TEMPLATE_SETS = ["templates", "templates-c"]
SPEC_PATH = "docs/plan/phase-NN-<slug>/<N.M.K>-<slug>.md"


def test_leader_runs_discovery_one_question_at_a_time() -> None:
    text = LEADER.read_text()
    assert "## Discovery" in text
    assert "one question at a time" in text
    assert "## Project context" in text  # fills CLAUDE.md from the answers


def test_leader_plans_every_phase_then_one_phase_at_a_time() -> None:
    text = LEADER.read_text()
    for needle in (
        "./harness/review_with_codex.sh roadmap",
        "./harness/review_with_codex.sh phase <NN>",
        "Never break down phase N+1 before phase N is done",
        SPEC_PATH,
        "make status",
    ):
        assert needle in text, needle


def test_leader_stops_after_each_subtask() -> None:
    assert "stop and show `make status`" in LEADER.read_text()


def test_reviewer_has_roadmap_and_phase_modes() -> None:
    text = REVIEWER.read_text()
    for needle in (
        "## `roadmap` mode",
        "## `phase` mode",
        "**Verdict:** ROADMAP_APPROVED | CHANGES_REQUESTED",
        "**Verdict:** PHASE_APPROVED | CHANGES_REQUESTED",
        "progress/roadmap_review.md",
        "progress/phase_review_<NN>.md",
        SPEC_PATH,
    ):
        assert needle in text, needle


def test_documenter_closes_in_the_spec() -> None:
    text = DOCUMENTER.read_text()
    assert f"`status: done` in its spec `{SPEC_PATH}`" in text
    assert "docs/reference/<N.M.K>-<slug>.md" in text
    assert f"Plan: {SPEC_PATH}" in text


def test_documenter_rechecks_a_stale_blocker() -> None:
    # A blocked note from an earlier close attempt must not stop a relaunch on its own.
    text = DOCUMENTER.read_text()
    assert "is not a reason to stop" in text
    assert "blocked -> see progress/current.md" in text


@pytest.mark.parametrize("tpl", TEMPLATE_SETS)
def test_history_entry_never_asks_for_the_close_commit_hash(tpl: str) -> None:
    # The entry is part of the close commit, which cannot contain its own hash.
    text = (TOOLKIT / tpl / "progress" / "history.md").read_text()
    assert "<hash>" not in text
    assert "- **Commit:** <type>(<scope>): <what> [N.M.K]" in text
    assert "never a hash" in DOCUMENTER.read_text()


@pytest.mark.parametrize("doc", [IMPLEMENT, IMPLEMENTER], ids=lambda p: p.parent.name)
def test_implementer_reads_the_subtask_spec(doc: Path) -> None:
    assert SPEC_PATH in doc.read_text()


@pytest.mark.parametrize("tpl", TEMPLATE_SETS)
def test_plan_template_documents_the_new_layout(tpl: str) -> None:
    plan_dir = TOOLKIT / tpl / "docs" / "plan"
    assert sorted(p.name for p in plan_dir.iterdir()) == ["PLAN.md"]
    text = (plan_dir / "PLAN.md").read_text()
    for needle in ("<!-- plan:index:start -->", "<!-- plan:index:end -->", SPEC_PATH, "- goal:"):
        assert needle in text, needle


def _harness_docs() -> list[Path]:
    docs = [*(TOOLKIT / "agents").glob("*.md"), *(TOOLKIT / "codex").glob("*.md")]
    for tpl in TEMPLATE_SETS:
        docs += [p for p in (TOOLKIT / tpl).rglob("*.md")]
    docs += [IMPLEMENT, TOOLKIT / "skills/github/commit/SKILL.md", TOOLKIT / "README.md"]
    docs += sorted((TOOLKIT / "docs").glob("*.md"))
    return docs


@pytest.mark.parametrize("doc", _harness_docs(), ids=lambda p: str(p.relative_to(TOOLKIT)))
def test_no_leftovers_of_the_old_plan_format(doc: Path) -> None:
    text = doc.read_text()
    for old in ("### N.M.K", "roadmap.md", "phase-NN-<name>.md#NMK", "phase-01-example"):
        assert old not in text, old
