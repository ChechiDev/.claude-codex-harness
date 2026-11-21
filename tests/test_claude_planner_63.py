"""Issue #63: leader + claude-planner subagent; codex/planner.md -> codex/codex-planner.md."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import TOOLKIT, Project, bash_payload, write_payload

BLOCKED = 2
AGENTS = TOOLKIT / "agents"
CODEX = TOOLKIT / "codex"
PLANNER_AGENT = AGENTS / "claude-planner.md"


def _frontmatter(path: Any) -> dict[str, str]:
    header = path.read_text().split("---")[1]
    out: dict[str, str] = {}
    for line in header.splitlines():
        key, sep, value = line.partition(":")
        if sep and not line.startswith(" "):
            out[key.strip()] = value.strip()
    return out


# ── claude-planner.md ────────────────────────────────────────────────────────


def test_claude_planner_frontmatter() -> None:
    meta = _frontmatter(PLANNER_AGENT)
    assert meta["name"] == "claude-planner"
    assert meta["model"] == "opus"
    assert meta["effort"] == "xhigh"
    tools = {t.strip() for t in meta["tools"].split(",")}
    assert tools == {"Read", "Glob", "Grep", "Write", "Edit", "Bash"}


def test_claude_planner_states_its_scope() -> None:
    text = PLANNER_AGENT.read_text()
    for needle in (
        "docs/plan/**",
        "## Project context",
        "make plan",
        "make check",
        "questions",
        "Micro-task granularity",
        "status: baseline",
        "ISS-<n>.<k>",
    ):
        assert needle in text, needle
    # Never runs gates, sets status, commits or talks to the user.
    lowered = text.lower()
    for forbidden in ("review_with_codex.sh", "commit", "user"):
        assert forbidden in lowered, forbidden
    assert "never run a gate" in lowered or "never run gates" in lowered


# ── leader.md: coordinator only ──────────────────────────────────────────────


def test_leader_delegates_planning() -> None:
    text = (AGENTS / "leader.md").read_text()
    assert 'subagent_type: "claude-planner"' in text
    assert _frontmatter(AGENTS / "leader.md")["model"] == "opus"
    assert "one question at a time" in text
    # Planning rules live in claude-planner.md, not duplicated here.
    assert "## Micro-task granularity" not in text
    assert "## Existing code: `status: baseline`" not in text
    assert "3. When you have enough, write:" not in text
    assert "Write **one spec per subtask**" not in text
    assert "/model" not in text


def test_leader_runs_every_gate() -> None:
    text = (AGENTS / "leader.md").read_text()
    for gate in ("roadmap", "phase <NN>", "task <N.M>", "task-close <N.M>",
                 "phase-close <NN>", "issue ISS-<n>", "plan <N.M.K>", "code <N.M.K>",
                 "doc <N.M.K>"):  # fmt: skip
        assert f"review_with_codex.sh {gate}" in text, gate


# ── codex-planner.md ─────────────────────────────────────────────────────────


def test_codex_planner_renamed() -> None:
    assert not (CODEX / "planner.md").exists()
    text = (CODEX / "codex-planner.md").read_text()
    assert "codex_model: gpt-6-astra" in text
    assert "codex_effort: high" in text
    assert "Codex role: codex-planner" in text


@pytest.mark.parametrize("mode", ["roadmap", "phase", "task", "task-close", "phase-close"])
def test_planning_gates_route_to_codex_planner(mode: str) -> None:
    script = (TOOLKIT / "lib/review_with_codex.sh").read_text()
    route = next(
        line
        for line in script.splitlines()
        if "ROLE=" in line and mode in line.strip().split(")", 1)[0].split("|")
    )
    assert 'ROLE="$CODEX_DIR/codex-planner.md"' in route


@pytest.mark.parametrize(
    "doc",
    ["codex/reviewer.md", "docs/configuration.md", "docs/design/task-flow.md",
     "agents/leader.md", "lib/WORKFLOW.md"],
)  # fmt: skip
def test_no_stale_planner_md_reference(doc: str) -> None:
    text = (TOOLKIT / doc).read_text()
    assert "`planner.md`" not in text, doc
    assert "codex/planner.md" not in text, doc


# ── WORKFLOW.md: fixed models ────────────────────────────────────────────────


def test_workflow_lists_six_actors_without_model_switch() -> None:
    text = (TOOLKIT / "lib/WORKFLOW.md").read_text()
    assert "/model" not in text
    for actor, model in (
        ("leader", "Opus 5.5 / high"),
        ("claude-planner", "Opus 5.5 / xhigh"),
        ("implementer", "sonnet / medium"),
        ("codex-planner", "gpt-6-astra / high"),
        ("reviewer", "gpt-6-sol / high"),
        ("documenter", "gpt-6-luna / low"),
    ):
        row = next((ln for ln in text.splitlines() if ln.startswith("|") and actor in ln
                    and model in ln), None)  # fmt: skip
        assert row, (actor, model)
    assert "claude-planner" in text.split("### Hard harness rules", 1)[1]


# ── Guard: claude-planner writes only docs/plan/** and CLAUDE.md ────────────


def _as_planner(payload: dict[str, Any]) -> dict[str, Any]:
    return {**payload, "agent_id": "a1", "agent_type": "claude-planner"}


@pytest.mark.parametrize(
    "path", ["docs/plan/PLAN.md", "docs/plan/phase-01-x/1.1.1-y.md", "CLAUDE.md"]
)
def test_guard_allows_planner_in_plan_and_claude_md(project: Project, path: str) -> None:
    res = project.run("guard_write.sh", _as_planner(write_payload(project.root / path)))
    assert res.returncode == 0, res.stderr


@pytest.mark.parametrize(
    "path",
    ["src/pkg/domain/user.py", "tests/unit/test_x.py", "progress/current.md",
     "docs/architecture.md", "docs/planx/a.md", "AGENTS.md"],
)  # fmt: skip
def test_guard_blocks_planner_elsewhere(project: Project, path: str) -> None:
    res = project.run("guard_write.sh", _as_planner(write_payload(project.root / path)))
    assert res.returncode == BLOCKED
    assert "claude-planner" in res.stderr


def test_guard_blocks_planner_outside_project(project: Project, tmp_path: Any) -> None:
    res = project.run("guard_write.sh", _as_planner(write_payload(tmp_path / "x.md")))
    assert res.returncode == BLOCKED


def test_guard_blocks_planner_traversal(project: Project) -> None:
    path = project.root / "docs/plan/../../src/pkg/x.py"
    res = project.run("guard_write.sh", _as_planner(write_payload(path)))
    assert res.returncode == BLOCKED


def test_guard_other_agents_unchanged(project: Project) -> None:
    payload = {**write_payload(project.root / "progress/current.md"), "agent_type": "implementer"}
    res = project.run("guard_write.sh", payload)
    assert res.returncode == 0, res.stderr


# ── Guard: claude-planner's Bash runs only `make plan` / `make check` ───────


@pytest.mark.e2e
@pytest.mark.parametrize(
    "command", ["make plan", "make check", "  make plan  ", "make plan && make check"]
)
def test_bash_guard_allows_planner_make_targets(project: Project, command: str) -> None:
    res = project.run("guard_bash.sh", _as_planner(bash_payload(command)))
    assert res.returncode == 0, res.stderr


@pytest.mark.e2e
@pytest.mark.parametrize(
    "command",
    ["echo x > notes.md", "make plan; rm -rf src", "make verify", "git commit -m x",
     "make plan && echo x > src/a.py", "./harness/review_with_codex.sh roadmap",
     "make plan $(touch x)", "make plan\ntouch x"],
)  # fmt: skip
def test_bash_guard_blocks_planner_other_commands(project: Project, command: str) -> None:
    res = project.run("guard_bash.sh", _as_planner(bash_payload(command)))
    assert res.returncode == BLOCKED
    assert "claude-planner" in res.stderr


def test_bash_guard_other_agents_unchanged(project: Project) -> None:
    payload = {**bash_payload("ls"), "agent_type": "implementer"}
    res = project.run("guard_bash.sh", payload)
    assert res.returncode == 0, res.stderr
