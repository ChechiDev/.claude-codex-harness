"""I0.3: the whole task cycle on an installed Python project, as it behaves today.

One subtask (1.1.1) in one task (1.1) in one phase (01): plan -> roadmap gate -> phase
gate -> plan gate (sealed oracle) -> implementation inside the declared files -> code
gate -> close (one commit) -> PR. Codex and gh are fakes (tests/i0_cycle.py).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from i0_cycle import PHASE_MD, PLAN_MD, SID, TASK, fake_tools, gate, git, hook, spec
from i0_projects import Installed, install_project

SRC = "src/demo/greet.py"
ACCEPTANCE = f"tests/acceptance/test_{SID}.py"
ACCEPTANCE_BODY = (
    '"""Acceptance test of 1.1.1 (written by Codex at the plan gate)."""\n\n'
    "from demo.greet import greet\n\n\ndef test_ac1_greets() -> None:\n"
    '    """AC1: greets by name."""\n'
    '    assert greet("Ana") == "Hello, Ana!"\n'
)
IMPLEMENTATION = (
    '"""Greeting."""\n\n\ndef greet(name: str) -> str:\n'
    '    """Return a greeting for name."""\n'
    '    return f"Hello, {name}!"\n'
)
SPEC = f"docs/plan/phase-01-greetings/{SID}-greet.md"


def _write(project: Installed, rel: str, text: str) -> None:
    path = project.root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _ok(res: object, what: str) -> None:
    rc = getattr(res, "returncode")  # noqa: B009 - CompletedProcess
    out = getattr(res, "stdout", "") + getattr(res, "stderr", "")  # noqa: B009
    assert rc == 0, f"{what} failed (rc={rc}):\n{out}"


@pytest.fixture
def cycle(tmp_path: Path) -> tuple[Installed, dict[str, str], Path]:
    project = install_project(tmp_path, "python")
    env = fake_tools(project, tmp_path, ACCEPTANCE, ACCEPTANCE_BODY, SPEC)
    git(project, "push", "-q", "origin", "main")
    return project, env, tmp_path


def _plan(project: Installed, status: str = "pending") -> None:
    _write(project, "docs/plan/PLAN.md", PLAN_MD)
    _write(project, "docs/plan/phase-01-greetings.md", PHASE_MD)
    _write(project, SPEC, spec(SRC, ACCEPTANCE, status))
    _ok(project.make("plan"), "make plan")


def _write_payload(project: Installed, rel: str) -> str:
    return json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(project.root / rel)}})


def test_task_cycle_end_to_end(cycle: tuple[Installed, dict[str, str], Path]) -> None:
    project, env, tmp = cycle

    # 1. Plan in docs/plan, task branch, roadmap and phase gates (both sealed).
    _plan(project)
    branch = project.run("python3", "harness/_harness.py", "branch", TASK).stdout.strip()
    git(project, "switch", "-qc", branch)
    _ok(gate(project, env, "roadmap"), "roadmap gate")
    _ok(gate(project, env, "phase", "01"), "phase gate")

    # 2. Leader starts the subtask; plan gate: Codex writes the oracle, it gets sealed.
    _plan(project, status="in_progress")
    _ok(gate(project, env, "plan", SID), "plan gate")
    assert (project.root / ACCEPTANCE).is_file()
    assert (project.root / f"progress/acceptance_{SID}.sha256").is_file()
    red = project.make("check")
    assert red.returncode != 0, "the new oracle must be red before the implementation"

    # 3. Implementation: the guard allows the declared file, blocks any other src file.
    assert hook(project, "guard_write.sh", _write_payload(project, SRC)).returncode == 0
    other = hook(project, "guard_write.sh", _write_payload(project, "src/demo/other.py"))
    assert other.returncode == 2
    _write(project, SRC, IMPLEMENTATION)
    _ok(project.make("check"), "make check after the implementation")

    # 4. Code gate (recorded in the review history), then the close: one commit.
    _ok(gate(project, env, "code", SID), "code gate")
    assert (project.root / f"progress/review_history/{SID}.md").is_file()
    before = git(project, "rev-list", "--count", "HEAD")
    closed = gate(project, env, "doc", SID)
    _ok(closed, "close (doc gate)")
    assert int(git(project, "rev-list", "--count", "HEAD")) == int(before) + 1
    message = git(project, "log", "-1", "--format=%B")
    assert message.startswith(f"feat(domain): greet by name [{SID}]")
    assert "co-authored-by" not in message.lower()
    assert git(project, "status", "--porcelain") == ""
    assert "- status: done" in (project.root / SPEC).read_text()

    # 5. The close finished the task: branch pushed, PR opened against main.
    remote = tmp / "origin.git"
    remote_head = git(project, "--git-dir", str(remote), "rev-parse", branch)
    assert remote_head == git(project, "rev-parse", "HEAD")
    create = next(
        ln for ln in (tmp / "gh.log").read_text().splitlines() if ln.startswith("pr create")
    )
    assert f"--head {branch}" in create and "--base main" in create
    assert "Greeting task." in (tmp / "gh.log.body").read_text()

    # 6. The project stays healthy and the gates ran in order.
    _ok(project.make("check"), "final make check")
    gates = (tmp / "codex.log").read_text().split()
    assert gates == ["roadmap", "roadmap", "phase", "01", "plan", SID, "code", SID, "doc", SID]
