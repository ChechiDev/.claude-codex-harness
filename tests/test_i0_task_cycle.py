"""I0.3 + I0.4: the whole task cycle on installed projects, as it behaves today.

One subtask (1.1.1) in one task (1.1) in one phase (01): plan -> roadmap gate -> phase
gate -> plan gate (sealed oracle) -> implementation inside the declared files -> code
gate -> close (one commit) -> PR. Run on a Python project, a C project and a Python
project installed in backend/ of an existing repo. Codex and gh are fakes
(tests/i0_cycle.py).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from i0_cycle import PHASE_MD, PLAN_MD, SID, TASK, fake_tools, gate, git, hook, spec
from i0_projects import Installed, install_project

pytestmark = pytest.mark.e2e  # real install.sh / make check (#68)

SPEC = f"docs/plan/phase-01-greetings/{SID}-greet.md"

PY_ORACLE = (
    '"""Acceptance test of 1.1.1 (written by Codex at the plan gate)."""\n\n'
    "from demo.greet import greet\n\n\ndef test_ac1_greets() -> None:\n"
    '    """AC1: greets by name."""\n'
    '    assert greet("Ana") == "Hello, Ana!"\n'
)
PY_CODE = {
    "src/demo/greet.py": (
        '"""Greeting."""\n\n\ndef greet(name: str) -> str:\n'
        '    """Return a greeting for name."""\n'
        '    return f"Hello, {name}!"\n'
    )
}
C_ORACLE = (
    "/* Acceptance test of 1.1.1 (written by Codex at the plan gate). */\n"
    '#include <assert.h>\n#include <string.h>\n#include "demo/greet.h"\n\n'
    "int main(void)\n{\n\tchar out[32];\n\n"
    '\tgreet("Ana", out, sizeof(out));\n'
    '\tassert(strcmp(out, "Hello, Ana!") == 0);\n\treturn (0);\n}\n'
)
C_CODE = {
    "include/demo/greet.h": (
        "#ifndef DEMO_GREET_H\n# define DEMO_GREET_H\n\n# include <stddef.h>\n\n"
        "void\tgreet(const char *name, char *out, size_t size);\n\n#endif\n"
    ),
    "src/greet.c": (
        '#include <stdio.h>\n#include "demo/greet.h"\n\n'
        "void\tgreet(const char *name, char *out, size_t size)\n{\n"
        '\tsnprintf(out, size, "Hello, %s!", name);\n}\n'
    ),
}


@dataclass(frozen=True)
class Case:
    kind: str
    oracle_path: str
    oracle: str
    code: dict[str, str]
    undeclared: str


CASES = {
    "python": Case("python", f"tests/acceptance/test_{SID}.py", PY_ORACLE, PY_CODE,
                   "src/demo/other.py"),
    "c": Case("c", f"tests/acceptance/test_{SID}.c", C_ORACLE, C_CODE, "src/other.c"),
    "nested": Case("nested", f"tests/acceptance/test_{SID}.py", PY_ORACLE, PY_CODE,
                   "src/demo/other.py"),
}  # fmt: skip


def _write(project: Installed, rel: str, text: str) -> None:
    path = project.root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _ok(res: object, what: str) -> None:
    rc = getattr(res, "returncode")  # noqa: B009 - CompletedProcess
    out = getattr(res, "stdout", "") + getattr(res, "stderr", "")  # noqa: B009
    assert rc == 0, f"{what} failed (rc={rc}):\n{out}"


def _plan(project: Installed, case: Case, status: str = "pending") -> None:
    _write(project, "docs/plan/PLAN.md", PLAN_MD)
    _write(project, "docs/plan/phase-01-greetings.md", PHASE_MD)
    _write(project, SPEC, spec(list(case.code), case.oracle_path, status))
    _ok(project.make("plan"), "make plan")


def _write_payload(project: Installed, rel: str) -> str:
    return json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(project.root / rel)}})


@pytest.mark.parametrize("kind", list(CASES))
def test_task_cycle_end_to_end(tmp_path: Path, kind: str) -> None:
    case = CASES[kind]
    project = install_project(tmp_path, kind)
    env = fake_tools(project, tmp_path, case.oracle_path, case.oracle, SPEC)
    git(project, "push", "-q", "origin", "main")

    # 1. Plan in docs/plan, task branch, roadmap and phase gates (both sealed).
    _plan(project, case)
    branch = project.run("python3", "harness/_harness.py", "branch", TASK).stdout.strip()
    git(project, "switch", "-qc", branch)
    _ok(gate(project, env, "roadmap"), "roadmap gate")
    _ok(gate(project, env, "phase", "01"), "phase gate")

    # 2. Leader starts the subtask; plan gate: Codex writes the oracle, it gets sealed.
    _plan(project, case, status="in_progress")
    _ok(gate(project, env, "plan", SID), "plan gate")
    assert (project.root / case.oracle_path).is_file()
    assert (project.root / f"progress/acceptance_{SID}.sha256").is_file()
    red = project.make("check")
    assert red.returncode != 0, "the new oracle must be red before the implementation"

    # 3. Implementation: the guard allows the declared files, blocks any other src file.
    for rel in case.code:
        assert hook(project, "guard_write.sh", _write_payload(project, rel)).returncode == 0
    other = hook(project, "guard_write.sh", _write_payload(project, case.undeclared))
    assert other.returncode == 2
    for rel, text in case.code.items():
        _write(project, rel, text)
    _ok(project.make("check"), "make check after the implementation")

    # 4. Code gate (recorded in the review history), then the close: one commit.
    _ok(gate(project, env, "code", SID), "code gate")
    assert (project.root / f"progress/review_history/{SID}.md").is_file()
    before = git(project, "rev-list", "--count", "HEAD")
    _ok(gate(project, env, "doc", SID), "close (doc gate)")
    assert int(git(project, "rev-list", "--count", "HEAD")) == int(before) + 1
    message = git(project, "log", "-1", "--format=%B")
    assert message.startswith(f"feat(domain): greet by name [{SID}]")
    assert "co-authored-by" not in message.lower()
    assert git(project, "status", "--porcelain") == ""
    assert "- status: done" in (project.root / SPEC).read_text()
    committed = git(project, "show", "--name-only", "--format=", "HEAD").split()
    assert not [f for f in committed if f.endswith((".o", ".bin", ".pyc"))], committed

    # 5. The close finished the task: branch pushed, PR opened against main.
    remote = tmp_path / "origin.git"
    remote_head = git(project, "--git-dir", str(remote), "rev-parse", branch)
    assert remote_head == git(project, "rev-parse", "HEAD")
    create = next(
        ln for ln in (tmp_path / "gh.log").read_text().splitlines() if ln.startswith("pr create")
    )
    assert f"--head {branch}" in create and "--base main" in create
    assert "Greeting task." in (tmp_path / "gh.log.body").read_text()

    # 6. The project stays healthy and the gates ran in order.
    _ok(project.make("check"), "final make check")
    gates = (tmp_path / "codex.log").read_text().split()
    assert gates == ["roadmap", "roadmap", "phase", "01", "plan", SID, "code", SID, "doc", SID]
    if kind == "nested":
        assert not (project.root / ".git").exists(), "the cycle created a nested repo"
        assert git(project, "rev-parse", "--show-toplevel") == str(project.repo)
