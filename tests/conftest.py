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
            "files": [DECLARED] if files is None else files,
        }
        data = {"features": [{"id": "1.1", "title": "users", "status": status, "subtasks": [sub]}]}
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
    proj.set_verdict("plan", "PLAN_APPROVED")
    proj.commit_all("init")
    yield proj


def write_payload(path: str | Path, tool: str = "Write") -> dict[str, Any]:
    key = "notebook_path" if tool == "NotebookEdit" else "file_path"
    return {"tool_name": tool, "tool_input": {key: str(path)}}


def bash_payload(command: str) -> dict[str, Any]:
    return {"tool_name": "Bash", "tool_input": {"command": command}}
