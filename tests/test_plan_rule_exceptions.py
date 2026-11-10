"""Subtasks without a red state: the two exceptions are versioned and consistent.

Contract checks read the shared instructions; the lifecycle test drives a test-only
subtask with an empty `files:` on an installed project, with the fake tools of
tests/i0_cycle.py.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from conftest import TOOLKIT, write_payload
from i0_cycle import PHASE_MD, PLAN_MD, SID, TASK, fake_tools, gate, git, hook, spec
from i0_projects import Installed, install_project

STRUCTURAL = "Structural refactor (no behaviour change)"
TEST_ONLY = "Test-only verification (no production behaviour change)"
MARKERS = [STRUCTURAL, TEST_ONLY]
EMPTY_FILES = "empty `files:`"
NO_PRODUCTION = "changes no production file"
GATES = ["phase", "issue", "plan"]

REVIEWER = TOOLKIT / "codex" / "reviewer.md"
LEADER = TOOLKIT / "agents" / "leader.md"
WORKFLOW = TOOLKIT / "lib" / "WORKFLOW.md"
TEMPLATES = ["templates", "templates-c"]
PLAN_GATE_END = "Green is required at session start, the code gate, and close.\n"
TEMPLATE_NEXT_STEP = "2. Read `progress/current.md`"

SPEC = f"docs/plan/phase-01-greetings/{SID}-greet.md"
ORACLE_PATH = f"tests/acceptance/test_{SID}.py"
ORACLE = 'def test_ac1_greets() -> None:\n    """AC1: already holds."""\n    assert True\n'
SEAL = f"progress/acceptance_{SID}.sha256"
TEST_ONLY_BODY = f"\n## Description\n{TEST_ONLY}: pins behaviour that already exists.\n"


def _flat(text: str) -> str:
    return " ".join(text.split())


def _common_section() -> str:
    return REVIEWER.read_text().split("## `roadmap` mode", 1)[0]


def _gate_section(name: str) -> str:
    match = re.search(rf"^## `{name}` mode$(.*?)(?=^## `|\Z)", REVIEWER.read_text(), re.M | re.S)
    assert match is not None, name
    return match.group(1)


def _rules(text: str) -> list[str]:
    """Each bullet, numbered step or paragraph of a section, on one line."""
    return [_flat(part) for part in re.split(r"\n(?=\s*(?:- |\d+\. ))|\n\s*\n", text)]


def _rule_with(text: str, *needles: str) -> str:
    found = [rule for rule in _rules(text) if all(n in rule for n in needles)]
    assert found, f"no single rule mentions all of {needles}"
    return found[0]


# ── reviewer: common rule and gates ──────────────────────────────────────────


def test_common_scope_rule_defines_the_test_only_exception() -> None:
    rule = _rule_with(_common_section(), EMPTY_FILES, TEST_ONLY)
    assert NO_PRODUCTION in rule
    assert "1-3" in rule


@pytest.mark.parametrize("marker", MARKERS)
@pytest.mark.parametrize("name", GATES)
def test_gate_confirms_the_claim(name: str, marker: str) -> None:
    assert "confirm" in _rule_with(_gate_section(name), marker).lower()


@pytest.mark.parametrize("name", GATES)
def test_gate_confirms_the_regression_oracles(name: str) -> None:
    assert "regression" in _gate_section(name).lower()


@pytest.mark.parametrize("name", GATES)
def test_gate_limits_empty_files_to_a_test_only_claim(name: str) -> None:
    section = _gate_section(name)
    rule = _rule_with(section, EMPTY_FILES, TEST_ONLY)
    assert "non-baseline" in rule
    assert "only" in rule
    assert "1-3" in section


def test_plan_gate_no_longer_rejects_every_missing_files_list() -> None:
    assert "if it has no `files:`," not in _flat(_gate_section("plan"))


def test_baseline_rules_are_unchanged() -> None:
    assert "The 1-3 `files:` limit and the `tests/acceptance/` path do not apply to it." in _flat(
        _gate_section("phase")
    )


# ── reviewer: red state ──────────────────────────────────────────────────────


def test_red_state_exception_names_both_markers() -> None:
    rule = _rule_with(_gate_section("plan"), "already pass", STRUCTURAL, TEST_ONLY)
    assert "only" in rule


def test_red_state_exception_is_self_contained() -> None:
    assert "AGENTS.md exceptions" not in _flat(REVIEWER.read_text())


def test_failing_assertion_for_a_red_state_is_forbidden() -> None:
    text = _flat(_gate_section("plan")).lower()
    assert "never add a failing assertion" in text
    assert "red state" in text


# ── planning documents and templates ─────────────────────────────────────────


@pytest.mark.parametrize("doc", [LEADER, WORKFLOW], ids=lambda p: p.name)
def test_planning_documents_permit_the_same_exception(doc: Path) -> None:
    rule = _rule_with(doc.read_text(), EMPTY_FILES, TEST_ONLY)
    assert NO_PRODUCTION in rule


def _template_exceptions(template: str) -> str:
    text = (TOOLKIT / template / "AGENTS.md").read_text()
    assert PLAN_GATE_END in text and TEMPLATE_NEXT_STEP in text
    return text.split(PLAN_GATE_END, 1)[1].split(TEMPLATE_NEXT_STEP, 1)[0]


@pytest.mark.parametrize("marker", MARKERS)
@pytest.mark.parametrize("template", TEMPLATES)
def test_template_defines_the_exception_after_the_plan_gate(template: str, marker: str) -> None:
    assert marker in _flat(_template_exceptions(template))


def test_templates_define_the_exceptions_with_identical_text() -> None:
    first, second = (_template_exceptions(template) for template in TEMPLATES)
    assert first.strip()
    assert first == second


# ── no contradicting instruction left behind (review round 1) ────────────────

LIMIT_STATEMENTS = [
    ("agents/leader.md", "`files:` (1-3)"),
    ("templates/docs/plan/PLAN.md", "1-3 production files"),
    ("templates-c/docs/plan/PLAN.md", "1-3 production files"),
    ("templates/docs/conventions.md", "1-3 files declared"),
    ("docs/usage.md", "`files:` with 1-3 paths"),
]


@pytest.mark.parametrize(("rel", "limit"), LIMIT_STATEMENTS, ids=[r for r, _ in LIMIT_STATEMENTS])
def test_every_statement_of_the_limit_carries_the_exception(rel: str, limit: str) -> None:
    rule = _rule_with((TOOLKIT / rel).read_text(), limit)
    assert EMPTY_FILES in rule
    assert TEST_ONLY in rule
    assert NO_PRODUCTION in rule


def _line_with(rel: str, needle: str) -> str:
    """The single table row or diagram line holding `needle`."""
    found = [line for line in (TOOLKIT / rel).read_text().splitlines() if needle in line]
    assert found, f"{needle!r} not in {rel}"
    return found[0]


SHORT_LIMITS = [
    ("templates/CHECKPOINTS.md", "`files:` (1-3"),
    ("templates-c/CHECKPOINTS.md", "`files:` (1-3"),
    ("docs/architecture.md", "files (1-3"),
    ("docs/architecture.md", "`files:` (1-3 files"),
]


@pytest.mark.parametrize(("rel", "limit"), SHORT_LIMITS, ids=[f"{r}:{n}" for r, n in SHORT_LIMITS])
def test_short_statements_of_the_limit_mention_test_only(rel: str, limit: str) -> None:
    assert "test-only" in _line_with(rel, limit)


RED_STATEMENTS = [
    ("docs/usage.md", "one test per acceptance criterion, **red**"),
    ("docs/usage.md", "`make check` is **red** from here"),
    ("docs/usage.md", "`make check` red right after the plan gate"),
    ("lib/WORKFLOW.md", "red plan-gate oracles are expected"),
    ("templates-c/docs/verification.md", "**left red**"),
    ("templates/progress/current.md", "`tests/acceptance/test_<N.M.K>.py` (red"),
    ("templates-c/progress/current.md", "`tests/acceptance/test_<N.M.K>.py` (red"),
    ("README.md", "which stay red until the code exists"),
    ("README.md", "plan gate (writes acceptance tests, red"),
]
LINE_ONLY = ("|", "per subtask:", "- [ ]")


@pytest.mark.parametrize(
    ("rel", "red"), RED_STATEMENTS, ids=[f"{r}:{n}" for r, n in RED_STATEMENTS]
)
def test_red_state_statements_carry_the_marker_exception(rel: str, red: str) -> None:
    line = _line_with(rel, red)
    statement = (
        line
        if line.lstrip().startswith(LINE_ONLY)
        else _rule_with((TOOLKIT / rel).read_text(), red)
    )
    assert "marker" in statement


def test_leader_does_not_treat_a_confirmed_marker_as_an_error() -> None:
    rule = _rule_with(LEADER.read_text(), "PLAN_APPROVED` → continue")
    assert "confirmed marker" in rule


def test_code_gate_closes_every_production_directory_for_test_only() -> None:
    section = _flat(_gate_section("code"))
    assert "no production file may change" in section
    assert "no `src/` file may change" not in section


# ── lifecycle ────────────────────────────────────────────────────────────────


def _write_plan(project: Installed, status: str) -> None:
    for rel, text in (
        ("docs/plan/PLAN.md", PLAN_MD),
        ("docs/plan/phase-01-greetings.md", PHASE_MD),
        (SPEC, spec([], ORACLE_PATH, status) + TEST_ONLY_BODY),
    ):
        path = project.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    res = project.make("plan")
    assert res.returncode == 0, res.stdout + res.stderr


def _ok(res: subprocess.CompletedProcess[str], what: str) -> None:
    assert res.returncode == 0, f"{what}\n{res.stdout}\n{res.stderr}"


def test_test_only_subtask_with_empty_files_completes_its_cycle(tmp_path: Path) -> None:
    project = install_project(tmp_path, "python")
    env = fake_tools(project, tmp_path, ORACLE_PATH, ORACLE, SPEC)
    git(project, "push", "-q", "origin", "main")

    # Plan synchronization keeps the empty list; the gates accept the subtask.
    _write_plan(project, "pending")
    declared = project.run("python3", "harness/_harness.py", "declared-files", SID)
    assert (declared.returncode, declared.stdout) == (0, "")
    branch = project.run("python3", "harness/_harness.py", "branch", TASK).stdout.strip()
    git(project, "switch", "-qc", branch)
    _ok(gate(project, env, "roadmap"), "roadmap gate")
    _ok(gate(project, env, "phase", "01"), "phase gate")
    _write_plan(project, "in_progress")
    _ok(gate(project, env, "plan", SID), "plan gate")
    assert (project.root / SEAL).is_file()

    # Production stays closed: nothing is declared, so every src/ write is refused.
    payload = json.dumps(write_payload(project.root / "src/demo/greet.py"))
    blocked = hook(project, "guard_write.sh", payload)
    assert blocked.returncode == 2
    assert "declares no 'files:'" in blocked.stdout + blocked.stderr

    # The oracle already passes, with no production change; approval permits the close.
    _ok(project.make("check"), "make check with the oracle already green")
    _ok(gate(project, env, "code", SID), "code gate")
    before = int(git(project, "rev-list", "--count", "HEAD"))
    _ok(gate(project, env, "doc", SID), "close")
    assert int(git(project, "rev-list", "--count", "HEAD")) == before + 1
    assert "- status: done" in (project.root / SPEC).read_text()
    assert not git(project, "diff", "--name-only", "HEAD~1", "HEAD", "--", "src")
