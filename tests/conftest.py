"""Fixtures: a throwaway project with the harness installed (symlinked like install.sh)."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

TOOLKIT = Path(__file__).resolve().parents[1]
LEAF_ID = "1.1.1"
DECLARED = "src/pkg/domain/user.py"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@dataclass
class Project:
    root: Path
    env: dict[str, str] = field(default_factory=dict)

    def write(self, rel: str, content: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def set_leaf(self, status: str = "in_progress", files: list[str] | None = None) -> None:
        sub = {
            "id": LEAF_ID,
            "title": "user entity",
            "status": status,
            "depends": [],
            "files": [DECLARED] if files is None else files,
            "spec": f"docs/plan/phase-01-users/{LEAF_ID}-user-entity.md",
        }
        task = {"id": "1.1", "title": "users", "phase": "01", "status": status, "subtasks": [sub]}
        phase = {"id": "01", "title": "users", "status": status, "tasks": ["1.1"]}
        data = {"phases": [phase], "features": [task]}
        self.write("feature_list.json", json.dumps(data))

    def set_verdict(self, kind: str, verdict: str, extra: str = "") -> None:
        name = "plan_review" if kind == "plan" else "review"
        self.write(
            f"progress/{name}_{LEAF_ID}.md", f"# Review\n\n**Verdict:** {verdict}\n\n{extra}\n"
        )

    def commit_all(self, msg: str = "wip") -> None:
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-qm", msg)

    def run(
        self,
        script: str,
        payload: dict[str, Any] | None = None,
        *args: str,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        base = {k: v for k, v in os.environ.items() if not k.startswith("HARNESS_")}
        full_env = {**base, "CLAUDE_PROJECT_DIR": str(self.root), **self.env, **(env or {})}
        stdin = json.dumps(payload) if payload is not None else ""
        return subprocess.run(
            ["bash", str(self.root / "harness" / script), *args],
            input=stdin,
            text=True,
            capture_output=True,
            cwd=self.root,
            env=full_env,
            check=False,
        )


@pytest.fixture
def project(tmp_path: Path) -> Iterator[Project]:
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "dev@example.com")
    _git(root, "config", "user.name", "dev")
    (root / "harness").symlink_to(TOOLKIT / "lib")
    (root / ".claude").mkdir()
    (root / ".claude" / "codex").symlink_to(TOOLKIT / "codex")
    proj = Project(root)
    proj.write(".gitignore", "harness\n.claude/\n")
    proj.write(DECLARED, "")
    proj.set_leaf()
    # Roadmap and phase 01 already approved by Codex: the fixture starts mid-cycle.
    proj.write("progress/roadmap_review.md", "# Roadmap\n\n**Verdict:** ROADMAP_APPROVED\n")
    proj.write("progress/phase_review_01.md", "# Phase\n\n**Verdict:** PHASE_APPROVED\n")
    proj.set_verdict("plan", "PLAN_APPROVED")
    proj.commit_all("init")
    yield proj


def write_payload(path: str | Path, tool: str = "Write") -> dict[str, Any]:
    key = "notebook_path" if tool == "NotebookEdit" else "file_path"
    return {"tool_name": tool, "tool_input": {key: str(path)}}


def bash_payload(command: str) -> dict[str, Any]:
    return {"tool_name": "Bash", "tool_input": {"command": command}}


# ── docs/plan projects (phases + specs + Codex verdicts) ─────────────────────

PHASE = "# Phase {n} — {title}\n- goal: {title} goal.\n{tasks}"
TASK = "\n## {tid} Task {tid}\n{ticket}- layer: domain\n- goal: g.\n"
SPEC = (
    "# {sid} Sub {sid}\n- status: {status}\n- layer: domain\n- depends: {depends}\n"
    "- files:\n  - src/app/m{slug}.py\n"
    "- acceptance:\n  - AC1 | works | tests/acceptance/test_{sid}.py::test_ac1_works\n"
)
ISSUE = (
    "# {iid} Issue {n}\n- issue: https://github.com/acme/app/issues/{n}\n"
    "- type: {type}\n- origin: 1.1\n- priority: {priority}\n{extra}- goal: g.\n"
)


class Plan:
    """A throwaway project: docs/plan + Codex verdict files, synced with sync_plan."""

    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "docs" / "plan").mkdir(parents=True)
        (root / "docs" / "plan" / "PLAN.md").write_text("# Project plan\n\n## Vision\nv.\n")
        (root / "progress").mkdir()
        self.phases: dict[str, list[str]] = {}
        self.specs: dict[str, tuple[str, str]] = {}
        self.tickets: dict[str, str] = {}
        self.issues: dict[str, tuple[str, str, str]] = {}
        self.isubs: dict[str, tuple[str, str]] = {}

    def phase(self, n: str, *tasks: str) -> Plan:
        self.phases[n] = list(tasks)
        return self

    def sub(self, sid: str, status: str = "pending", depends: str = "none") -> Plan:
        self.specs[sid] = (status, depends)
        return self

    def issue(self, n: int, type_: str = "fix", priority: str = "next", extra: str = "") -> Plan:
        self.issues[f"ISS-{n}"] = (type_, priority, extra)
        return self

    def isub(self, sid: str, status: str = "pending", depends: str = "none") -> Plan:
        self.isubs[sid] = (status, depends)
        return self

    def approve_issue(self, n: int) -> Plan:
        return self.verdict(f"issue_review_ISS-{n}.md", "ISSUE_APPROVED")

    def ticket(self, tid: str, value: str) -> Plan:
        self.tickets[tid] = value
        return self

    def _ticket_line(self, tid: str) -> str:
        return f"- ticket: {self.tickets[tid]}\n" if tid in self.tickets else ""

    def config(self, text: str) -> Plan:
        (self.root / "harness.toml").write_text(text)
        return self

    def verdict(self, name: str, verdict: str) -> Plan:
        (self.root / "progress" / name).write_text(f"# Review\n\n**Verdict:** {verdict}\n")
        return self

    def approve_roadmap(self) -> Plan:
        return self.verdict("roadmap_review.md", "ROADMAP_APPROVED")

    def approve_phase(self, n: str) -> Plan:
        return self.verdict(f"phase_review_{n}.md", "PHASE_APPROVED")

    def sync(self) -> Plan:
        plan = self.root / "docs" / "plan"
        for n, tasks in self.phases.items():
            body = "".join(TASK.format(tid=t, ticket=self._ticket_line(t)) for t in tasks)
            (plan / f"phase-{n}-p{n}.md").write_text(PHASE.format(n=n, title=f"P{n}", tasks=body))
        for sid, (status, depends) in self.specs.items():
            n = f"{int(sid.split('.')[0]):02d}"
            folder = plan / f"phase-{n}-p{n}"
            folder.mkdir(exist_ok=True)
            text = SPEC.format(sid=sid, status=status, depends=depends, slug=sid.replace(".", ""))
            (folder / f"{sid}-s.md").write_text(text)
        self._sync_issues(plan)
        res = self.run_py("sync_plan.py")
        assert res.returncode == 0, res.stdout + res.stderr
        return self

    def _sync_issues(self, plan: Path) -> None:
        root = plan / "issues"
        for iid, (type_, priority, extra) in self.issues.items():
            root.mkdir(exist_ok=True)
            n = iid.split("-", 1)[1]
            header = ISSUE.format(iid=iid, n=n, type=type_, priority=priority, extra=extra)
            (root / f"{iid}-i{n}.md").write_text(header)
        for sid, (status, depends) in self.isubs.items():
            iid = sid.rsplit(".", 1)[0]
            folder = root / f"{iid}-i{iid.split('-', 1)[1]}"
            folder.mkdir(parents=True, exist_ok=True)
            text = SPEC.format(sid=sid, status=status, depends=depends, slug=sid.replace(".", ""))
            (folder / f"{sid}-s.md").write_text(text)

    def run_py(self, script: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(TOOLKIT / "lib" / script), *args],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=False,
        )

    def validate(self) -> subprocess.CompletedProcess[str]:
        return self.run_py("_harness.py", "validate")


@pytest.fixture
def plan(tmp_path: Path) -> Plan:
    return Plan(tmp_path)
