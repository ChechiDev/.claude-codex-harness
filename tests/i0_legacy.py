"""I0.6 fixture: an installed project carrying legacy state, like codexion and guakamole.

Built once with the real flow (fakes for Codex and gh, tests/i0_cycle.py):
  phase 01 PHASE_APPROVED and sealed, roadmap ROADMAP_APPROVED and sealed;
  task 1.1: subtask 1.1.1 done through the whole cycle (sealed oracle, review
            APPROVED, reference doc, one close commit, PR opened);
  task 1.2: subtask 1.2.1 `baseline` (code that predates the harness, no oracle).
Each test gets its own copy (copy_legacy), so it may break the state freely.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from conftest import TOOLKIT
from i0_cycle import PHASE_MD, PLAN_MD, SID, TASK, fake_tools, gate, git, spec, stack_step
from i0_projects import Installed, install_project

SPEC = f"docs/plan/phase-01-greetings/{SID}-greet.md"
BASELINE_SPEC = "docs/plan/phase-01-greetings/1.2.1-hello.md"
ORACLE = f"tests/acceptance/test_{SID}.py"
ORACLE_BODY = (
    f'"""Acceptance test of {SID} (written by Codex at the plan gate)."""\n\n'
    "from demo.greet import greet\n\n\ndef test_ac1_greets() -> None:\n"
    '    """AC1: greets by name."""\n'
    '    assert greet("Ana") == "Hello, Ana!"\n'
)
CODE = {
    "src/demo/greet.py": (
        '"""Greeting."""\n\n\ndef greet(name: str) -> str:\n'
        '    """Return a greeting for name."""\n'
        '    return f"Hello, {name}!"\n'
    ),
}
BASELINE_CODE = {"src/demo/hello.py": '"""Pre-harness code."""\n\nHELLO = "hello"\n'}
PHASE_LEGACY = PHASE_MD + "\n## 1.2 Hello\n- layer: domain\n- goal: pre-harness hello.\n"
BASELINE = (
    "# 1.2.1 Hello\n- status: baseline\n- layer: domain\n- depends: none\n"
    "- files:\n  - src/demo/hello.py\n"
)


def _ok(res: subprocess.CompletedProcess[str], what: str) -> None:
    assert res.returncode == 0, f"{what} failed (rc={res.returncode}):\n{res.stdout}{res.stderr}"


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _plan(project: Installed, status: str) -> None:
    _write(project.root, "docs/plan/PLAN.md", PLAN_MD)
    _write(project.root, "docs/plan/phase-01-greetings.md", PHASE_LEGACY)
    _write(project.root, SPEC, spec(list(CODE), ORACLE, status))
    _write(project.root, BASELINE_SPEC, BASELINE)
    _ok(project.make("plan"), "make plan")


def build_legacy(tmp: Path, toolkit: Path = TOOLKIT) -> Installed:
    """Install a Python project and take it through one closed subtask + a baseline."""
    project = install_project(tmp, "python", toolkit=toolkit)
    env = fake_tools(project, tmp, ORACLE, ORACLE_BODY, SPEC)
    # The install leaves the project on its setup branch; the human merges it into main,
    # and the pre-harness code lives on main (issue #119).
    setup = git(project, "branch", "--show-current")
    git(project, "switch", "-q", "main")
    git(project, "merge", "-q", "--ff-only", setup)
    for rel, text in BASELINE_CODE.items():
        _write(project.root, rel, text)
    git(project, "add", "-A")
    git(project, "commit", "-qm", "pre-harness code")
    git(project, "push", "-q", "origin", "main")
    # issue #119: the pre-harness code is base (origin/main), not work of the task branch
    on_base = subprocess.run(
        ["git", "merge-base", "--is-ancestor", "HEAD", "origin/main"],
        cwd=project.root,
        env=project.env,
        check=False,
    )
    assert on_base.returncode == 0, "the pre-harness commit must be on origin/main"
    stack_step(project, env)  # issue #115: the stack comes before the plan
    _plan(project, "pending")
    branch = project.run("python3", "harness/_harness.py", "branch", TASK).stdout.strip()
    git(project, "switch", "-qc", branch)
    _ok(gate(project, env, "roadmap"), "roadmap gate")
    _ok(gate(project, env, "phase", "01"), "phase gate")
    _plan(project, "in_progress")
    _ok(gate(project, env, "plan", SID), "plan gate")
    for rel, text in CODE.items():
        _write(project.root, rel, text)
    _ok(gate(project, env, "code", SID), "code gate")
    _ok(gate(project, env, "doc", SID), "close (doc gate)")
    assert git(project, "status", "--porcelain") == "", "the legacy state must be committed"
    _ok(project.make("check"), "make check on the legacy project")
    return project


def copy_legacy(legacy: Installed, dest: Path) -> Installed:
    """A private copy of the legacy project (links keep pointing into the toolkit).

    .venv is left out: its editable install points at the original src/, so a copy
    that kept it would test the original code. uv rebuilds it on the first run."""
    shutil.copytree(legacy.root, dest, symlinks=True, ignore=shutil.ignore_patterns(".venv"))
    return Installed(kind=legacy.kind, root=dest, repo=dest, env=legacy.env)
