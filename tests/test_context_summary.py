"""Issue #48: context, assumptions and a short summary in specs and reports."""

from __future__ import annotations

import re

import pytest
from conftest import TOOLKIT

LEADER = TOOLKIT / "agents" / "leader.md"
IMPLEMENTER = TOOLKIT / "agents" / "implementer.md"
PLANNER = TOOLKIT / "agents" / "claude-planner.md"
REVIEWER = TOOLKIT / "codex" / "reviewer.md"
DOCUMENTER = TOOLKIT / "codex" / "documenter.md"
PLAN_TEMPLATE = TOOLKIT / "templates" / "docs" / "plan" / "PLAN.md"

SPEC_SECTIONS = ("## Context", "## Out of scope", "## Assumptions")


@pytest.mark.parametrize("section", SPEC_SECTIONS)
def test_spec_template_has_section(section: str) -> None:
    assert section in PLAN_TEMPLATE.read_text()


@pytest.mark.parametrize("section", SPEC_SECTIONS)
def test_planner_writes_section(section: str) -> None:
    assert section in PLANNER.read_text()


def test_planner_context_uses_real_paths() -> None:
    assert "real paths" in PLANNER.read_text()


def test_behaviour_assumptions_need_user_approval() -> None:
    planner = PLANNER.read_text()
    leader = LEADER.read_text()
    assert "change behaviour" in planner
    assert "## Assumptions" in leader
    assert "user's approval" in leader


def test_implementer_report_has_summary() -> None:
    text = IMPLEMENTER.read_text()
    assert "## Summary" in text
    assert "3-5 bullets" in text


@pytest.mark.parametrize("mode", ["plan", "code"])
def test_reviewer_verdict_has_summary_and_mapping(mode: str) -> None:
    text = REVIEWER.read_text()
    heading = {"plan": "# Plan review — subtask", "code": "# Code review — subtask"}[mode]
    block = text.split(heading, 1)[1].split("```", 1)[0]
    assert "## Summary" in block
    assert "## Finding → fix" in block


def test_reviewer_keeps_verdict_tokens() -> None:
    text = REVIEWER.read_text()
    for token in ("PLAN_APPROVED", "APPROVED", "CHANGES_REQUESTED"):
        assert re.search(rf"\b{token}\b", text)


def test_documenter_keeps_anchors_and_pr_headings() -> None:
    text = DOCUMENTER.read_text()
    assert "3-5 bullets" in text
    for heading in ("## Overview", "## Decisions", "## Where to start reviewing"):
        assert heading in text
    assert text.count("<!-- section:") >= 5


def test_issue_gate_stop_quotes_assumptions() -> None:
    text = LEADER.read_text()
    block = text.split("`ISSUE_APPROVED` (sealed)", 1)[1].split("6. **Per subtask**", 1)[0]
    assert "## Assumptions" in block


def test_every_reviewer_mode_has_summary_rule() -> None:
    assert "Every verdict file, in every mode" in REVIEWER.read_text()
