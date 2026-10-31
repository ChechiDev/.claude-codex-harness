"""Every role, skill, template and toolkit doc knows the issues lane (ISS-<n>.<k>)."""

from __future__ import annotations

import pytest
from conftest import TOOLKIT

# file -> phrases it must contain
DOCS: dict[str, tuple[str, ...]] = {
    "agents/leader.md": (
        "## Issues lane",
        "make issue N=",
        "make issue-start N=",
        "review_with_codex.sh issue ISS-<n>",
        "harness/issue.sh resume",
        "make issue-new",
    ),
    "agents/implementer.md": ("ISS-<n>.<k>",),
    "skills/implement/SKILL.md": ("ISS-<n>.<k>",),
    "skills/github/branch/SKILL.md": ("<type>/ISS-<n>-<slug>",),
    "skills/github/commit/SKILL.md": ("[ISS-<n>.<k>]", "docs/plan/issues/"),
    "templates/CHECKPOINTS.md": ("ISS-<n>.<k>",),
    "templates-c/CHECKPOINTS.md": ("ISS-<n>.<k>",),
    "templates/docs/verification.md": ("ISS-<n>.<k>",),
    "templates-c/docs/verification.md": ("ISS-<n>.<k>",),
    "templates/docs/reference/README.md": ("ISS-<n>.<k>",),
    "templates-c/docs/reference/README.md": ("ISS-<n>.<k>",),
    "templates/progress/history.md": ("ISS-<n>.<k>",),
    "templates/progress/current.md": ("ISS-<n>.<k>",),
    "templates-c/progress/history.md": ("ISS-<n>.<k>",),
    "templates-c/progress/current.md": ("ISS-<n>.<k>",),
    "templates/docs/plan/PLAN.md": ("docs/plan/issues/",),
    "templates-c/docs/plan/PLAN.md": ("docs/plan/issues/",),
    "docs/architecture.md": (
        "## Issues lane",
        "docs/plan/issues/",
        "progress/issue_review_ISS-<n>.md",
        "progress/plan_issue_ISS-<n>.sha256",
        "harness.mk",
        "WORKFLOW.md",
    ),
    "docs/usage.md": ("make issue N=", "make issue-start N=", "install.sh <project> --migrate"),
    "docs/configuration.md": ("<type>/ISS-<n>-<slug>", "priority:now", "status:in-progress"),
    "README.md": ("issues lane", "--migrate", "harness.mk", "WORKFLOW.md"),
}


@pytest.mark.parametrize(("rel", "needles"), sorted(DOCS.items()), ids=lambda v: str(v))
def test_doc_covers_the_issues_lane(rel: str, needles: tuple[str, ...]) -> None:
    text = (TOOLKIT / rel).read_text()
    missing = [n for n in needles if n not in text]
    assert not missing, f"{rel} lacks {missing}"
