"""review_with_codex.sh: the issue gate and the issue-draft review, and their seals."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest
from conftest import Project
from test_review_with_codex import _codex_writing, _manual, _manual_run

HEADER = (
    "# ISS-14 Replace the mask\n- issue: https://github.com/acme/app/issues/14\n"
    "- type: refactor\n- origin: 1.1\n- priority: next\n- goal: g.\n"
)
SPEC = (
    "# ISS-14.1 Flags\n- status: pending\n- layer: domain\n- depends: none\n"
    "- files:\n  - src/pkg/domain/flags.py\n"
    "- acceptance:\n  - AC1 | works | tests/acceptance/test_ISS-14.1.py::test_ac1\n"
)
DRAFT = (
    "---\ntitle: replace the mask\nlabels: [refactor]\npriority: next\n---\n"
    "## Problem\nThe mask is hard to read.\n"
)


def _with_issue(project: Project, spec: bool = True) -> Project:
    project.write("docs/plan/issues/ISS-14-replace-the-mask.md", HEADER)
    if spec:
        project.write("docs/plan/issues/ISS-14-replace-the-mask/ISS-14.1-flags.md", SPEC)
    res = subprocess.run(
        ["python3", "harness/sync_plan.py"],
        cwd=project.root,
        text=True,
        capture_output=True,
        check=False,
    )
    assert res.returncode == 0, res.stdout + res.stderr
    return project


@pytest.mark.e2e
def test_issue_gate_names_the_issue_and_its_verdict_file(project: Project) -> None:
    out = _manual(_with_issue(project), "issue", "ISS-14")
    assert "Phase: issue" in out
    assert "docs/plan/issues/ISS-14-replace-the-mask.md" in out
    assert "progress/issue_review_ISS-14.md" in out
    assert "seal issue ISS-14" in out
    assert "--sandbox workspace-write" in out


@pytest.mark.e2e
def test_issue_gate_accepts_the_bare_number(project: Project) -> None:
    out = _manual(_with_issue(project), "issue", "14")
    assert "progress/issue_review_ISS-14.md" in out


def test_issue_gate_refuses_an_issue_without_subtasks(project: Project) -> None:
    res = _manual_run(_with_issue(project, spec=False), "issue", "ISS-14")
    assert res.returncode != 0
    assert "has no subtasks yet" in res.stdout + res.stderr


@pytest.mark.e2e
def test_issue_gate_does_not_need_the_roadmap(project: Project) -> None:
    (project.root / "progress/roadmap_review.md").unlink()
    assert _manual_run(_with_issue(project), "issue", "ISS-14").returncode == 0


@pytest.mark.e2e
def test_approved_issue_gate_seals_the_issue(project: Project, tmp_path: Path) -> None:
    _with_issue(project)
    env = _codex_writing(tmp_path, "progress/issue_review_ISS-14.md", "ISSUE_APPROVED")
    res = project.run("review_with_codex.sh", None, "issue", "ISS-14", env=env)
    assert res.returncode == 0, res.stdout + res.stderr
    assert (project.root / "progress/plan_issue_ISS-14.sha256").is_file()
    assert (project.root / "progress/review_history/ISS-14.md").is_file()


@pytest.mark.e2e
def test_rejected_issue_gate_does_not_seal(project: Project, tmp_path: Path) -> None:
    _with_issue(project)
    env = _codex_writing(tmp_path, "progress/issue_review_ISS-14.md", "CHANGES_REQUESTED")
    project.run("review_with_codex.sh", None, "issue", "ISS-14", env=env)
    assert not (project.root / "progress/plan_issue_ISS-14.sha256").exists()


@pytest.mark.e2e
def test_plan_gate_of_an_issue_subtask_needs_the_issue_approved(project: Project) -> None:
    _with_issue(project)
    res = _manual_run(project, "plan", "ISS-14.1")
    assert res.returncode != 0
    assert "ISSUE_APPROVED" in res.stdout + res.stderr
    project.write("progress/issue_review_ISS-14.md", "# r\n\n**Verdict:** ISSUE_APPROVED\n")
    out = _manual(project, "plan", "ISS-14.1")
    assert "docs/plan/issues/ISS-14-replace-the-mask/ISS-14.1-flags.md" in out


# ── issue-draft review ───────────────────────────────────────────────────────


def test_draft_review_needs_a_draft(project: Project) -> None:
    res = _manual_run(project, "issue-draft")
    assert res.returncode != 0
    assert "no progress/issue_draft.md" in res.stdout + res.stderr


@pytest.mark.e2e
def test_draft_review_names_the_draft_and_its_verdict(project: Project) -> None:
    project.write("progress/issue_draft.md", DRAFT)
    out = _manual(project, "issue-draft", "")
    assert "Phase: issue-draft" in out
    assert "progress/issue_draft.md" in out
    assert "progress/issue_draft_review.md" in out
    assert "seal draft" in out


@pytest.mark.e2e
def test_approved_draft_is_sealed(project: Project, tmp_path: Path) -> None:
    project.write("progress/issue_draft.md", DRAFT)
    env = _codex_writing(tmp_path, "progress/issue_draft_review.md", "DRAFT_APPROVED")
    res = project.run("review_with_codex.sh", None, "issue-draft", env=env)
    assert res.returncode == 0, res.stdout + res.stderr
    manifest = (project.root / "progress/issue_draft.sha256").read_text()
    digest = hashlib.sha256(DRAFT.encode()).hexdigest()
    assert manifest == f"{digest}  progress/issue_draft.md\n"
    assert (project.root / "progress/review_history/issue-draft.md").is_file()


@pytest.mark.e2e
def test_rejected_draft_is_not_sealed(project: Project, tmp_path: Path) -> None:
    project.write("progress/issue_draft.md", DRAFT)
    env = _codex_writing(tmp_path, "progress/issue_draft_review.md", "CHANGES_REQUESTED")
    project.run("review_with_codex.sh", None, "issue-draft", env=env)
    assert not (project.root / "progress/issue_draft.sha256").exists()


def test_seal_draft_needs_draft_approved(project: Project) -> None:
    project.write("progress/issue_draft.md", DRAFT)
    res = project.run("review_with_codex.sh", None, "seal", "draft")
    assert res.returncode != 0
    assert "DRAFT_APPROVED" in res.stdout + res.stderr


def test_usage_lists_the_issue_gates(project: Project) -> None:
    res = project.run("review_with_codex.sh", None, "bogus")
    assert res.returncode == 2
    for gate in ("issue <ISS-n>", "issue-draft"):
        assert gate in res.stderr


def test_roles_describe_the_issue_gates() -> None:
    reviewer = (Path(__file__).resolve().parents[1] / "codex/reviewer.md").read_text()
    for needle in (
        "## `issue` mode",
        "ISSUE_APPROVED",
        "## `issue-draft` mode",
        "DRAFT_APPROVED",
        "too big for an issue",
        "migrates:",
    ):
        assert needle in reviewer, needle
