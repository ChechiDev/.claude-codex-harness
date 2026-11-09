"""I2a: each gate of review_with_codex.sh uses its own verdict file.

plan -> progress/plan_review_<id>.md, code -> progress/review_<id>.md, doc -> none.
Driven on an installed project with the fake reviewer CLI of tests/i0_cycle.py.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
from i0_cycle import PHASE_MD, PLAN_MD, SID, TASK, fake_tools, gate, git, spec
from i0_projects import Installed, install_project

SPEC = f"docs/plan/phase-01-greetings/{SID}-greet.md"
ORACLE_PATH = f"tests/acceptance/test_{SID}.py"
ORACLE = "def test_ac1_greets() -> None:\n    assert True\n"
PLAN_VERDICT = f"progress/plan_review_{SID}.md"
CODE_VERDICT = f"progress/review_{SID}.md"
SEAL = f"progress/acceptance_{SID}.sha256"
HISTORY = f"progress/review_history/{SID}.md"
ROUND = re.compile(r"^## Round \d+: (\w+ gate, \w+(?: \w+)?) \(", re.MULTILINE)


@dataclass(frozen=True)
class Cycle:
    project: Installed
    env: dict[str, str]

    def read(self, rel: str) -> str:
        return (self.project.root / rel).read_text()

    def run_gate(self, name: str, **env: str) -> subprocess.CompletedProcess[str]:
        res = gate(self.project, {**self.env, **env}, name, SID)
        assert res.returncode == 0, res.stdout + res.stderr
        return res

    def manual(self, name: str) -> str:
        return self.run_gate(name, HARNESS_CODEX_MODE="manual").stdout

    def rounds(self) -> list[str]:
        return ROUND.findall(self.read(HISTORY))

    def last_round_body(self) -> str:
        return self.read(HISTORY).rsplit("\n## Round ", 1)[1]


def _write_plan(project: Installed, status: str) -> None:
    for rel, text in (
        ("docs/plan/PLAN.md", PLAN_MD),
        ("docs/plan/phase-01-greetings.md", PHASE_MD),
        (SPEC, spec(["src/demo/greet.py"], ORACLE_PATH, status)),
    ):
        path = project.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    res = project.make("plan")
    assert res.returncode == 0, res.stdout + res.stderr


@pytest.fixture
def cycle(tmp_path: Path) -> Cycle:
    """A project whose subtask has passed the plan gate (PLAN_APPROVED on disk)."""
    project = install_project(tmp_path, "python")
    env = {
        **fake_tools(project, tmp_path, ORACLE_PATH, ORACLE, SPEC),
        "HARNESS_CHECK_CMD": "true",
    }
    _write_plan(project, "pending")
    branch = project.run("python3", "harness/_harness.py", "branch", TASK).stdout.strip()
    git(project, "switch", "-qc", branch)
    for args in (("roadmap",), ("phase", "01")):
        res = gate(project, env, *args)
        assert res.returncode == 0, res.stdout + res.stderr
    _write_plan(project, "in_progress")
    done = Cycle(project, env)
    done.run_gate("plan")
    return done


def test_code_gate_records_its_own_approval(cycle: Cycle) -> None:
    cycle.run_gate("code")
    assert cycle.rounds()[-1] == "code gate, APPROVED"
    body = cycle.last_round_body()
    assert "**Verdict:** APPROVED" in body
    assert "PLAN_APPROVED" not in body


def test_code_gate_records_a_rejection(cycle: Cycle) -> None:
    cycle.run_gate("code", I0_CODE_VERDICT="CHANGES_REQUESTED")
    assert cycle.rounds()[-1] == "code gate, CHANGES_REQUESTED"
    assert "**Verdict:** CHANGES_REQUESTED" in cycle.last_round_body()


def test_plan_gate_still_records_and_seals_its_verdict(cycle: Cycle) -> None:
    assert cycle.rounds() == ["plan gate, PLAN_APPROVED"]
    assert "**Verdict:** PLAN_APPROVED" in cycle.last_round_body()
    assert "PLAN_APPROVED" in cycle.read(PLAN_VERDICT)
    assert (cycle.project.root / SEAL).is_file()


def test_code_gate_never_seals(cycle: Cycle) -> None:
    progress = cycle.project.root / "progress"
    before = {p.name: p.read_bytes() for p in progress.glob("*.sha256")}
    assert SEAL.rsplit("/", 1)[1] in before
    (cycle.project.root / SEAL).unlink()
    before.pop(SEAL.rsplit("/", 1)[1])
    cycle.run_gate("code")
    assert {p.name: p.read_bytes() for p in progress.glob("*.sha256")} == before


def test_manual_code_names_its_own_verdict_file(cycle: Cycle) -> None:
    out = cycle.manual("code")
    assert f"--- verdict file: {CODE_VERDICT} ---" in out
    assert f"python3 harness/_pr.py record code {SID} {CODE_VERDICT}" in out
    assert PLAN_VERDICT not in out


def test_manual_plan_names_its_own_verdict_file(cycle: Cycle) -> None:
    out = cycle.manual("plan")
    assert f"--- verdict file: {PLAN_VERDICT} ---" in out
    assert f"python3 harness/_pr.py record plan {SID} {PLAN_VERDICT}" in out


def test_manual_doc_names_no_verdict_file(cycle: Cycle) -> None:
    cycle.run_gate("code")
    out = cycle.manual("doc")
    assert "--- verdict file" not in out
    assert PLAN_VERDICT not in out
    assert f"./harness/review_with_codex.sh close {SID}" in out
