"""guard_write.sh: Edit/Write/MultiEdit/NotebookEdit scope and ownership rules."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import DECLARED, LEAF_ID, TOOLKIT, Project, write_payload

BLOCKED = 2


def test_allows_declared_file_absolute(project: Project) -> None:
    res = project.run("guard_write.sh", write_payload(project.root / DECLARED))
    assert res.returncode == 0, res.stderr


@pytest.mark.parametrize("path", [DECLARED, f"./{DECLARED}", "src/pkg/./domain/user.py"])
def test_allows_declared_file_relative_spellings(project: Project, path: str) -> None:
    res = project.run("guard_write.sh", write_payload(path))
    assert res.returncode == 0, res.stderr


def test_blocks_undeclared_src_file(project: Project) -> None:
    res = project.run("guard_write.sh", write_payload(project.root / "src/pkg/other.py"))
    assert res.returncode == BLOCKED
    assert "NOT in the declared" in res.stderr


def test_blocks_traversal_out_of_declared_file(project: Project) -> None:
    path = project.root / "src/pkg/domain/../infra/repo.py"
    res = project.run("guard_write.sh", write_payload(path))
    assert res.returncode == BLOCKED


def test_blocks_src_write_through_symlink_alias(project: Project) -> None:
    (project.root / "alias").symlink_to(project.root / "src")
    res = project.run("guard_write.sh", write_payload(project.root / "alias/pkg/other.py"))
    assert res.returncode == BLOCKED


def test_blocks_acceptance_write_through_symlink_alias(project: Project) -> None:
    (project.root / "tests/acceptance").mkdir(parents=True)
    (project.root / "oracle").symlink_to(project.root / "tests/acceptance")
    res = project.run("guard_write.sh", write_payload(project.root / f"oracle/test_{LEAF_ID}.py"))
    assert res.returncode == BLOCKED


def test_ignores_paths_outside_project(project: Project, tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere" / "src" / "x.py"
    res = project.run("guard_write.sh", write_payload(outside))
    assert res.returncode == 0, res.stderr


def test_blocks_acceptance_tests(project: Project) -> None:
    res = project.run("guard_write.sh", write_payload(f"tests/acceptance/test_{LEAF_ID}.py"))
    assert res.returncode == BLOCKED
    assert "READ-ONLY" in res.stderr


@pytest.mark.parametrize(
    "rel",
    [
        f"progress/plan_review_{LEAF_ID}.md",
        f"progress/review_{LEAF_ID}.md",
        f"progress/acceptance_{LEAF_ID}.sha256",
    ],
)
def test_blocks_codex_owned_verdict_files(project: Project, rel: str) -> None:
    res = project.run("guard_write.sh", write_payload(rel))
    assert res.returncode == BLOCKED
    assert "Codex" in res.stderr


@pytest.mark.parametrize(
    "rel",
    [
        f"progress/impl_{LEAF_ID}.md",
        "progress/current.md",
        "docs/plan/phase-01-x.md",
        "tests/unit/test_user.py",
    ],
)
def test_allows_claude_owned_files(project: Project, rel: str) -> None:
    res = project.run("guard_write.sh", write_payload(rel))
    assert res.returncode == 0, res.stderr


def test_notebook_edit_is_guarded(project: Project) -> None:
    payload = write_payload(f"tests/acceptance/test_{LEAF_ID}.ipynb", tool="NotebookEdit")
    res = project.run("guard_write.sh", payload)
    assert res.returncode == BLOCKED


def test_changes_requested_mentioning_plan_approved_does_not_unlock_src(project: Project) -> None:
    project.set_verdict(
        "plan", "CHANGES_REQUESTED", extra="Fix the ports; then it can be PLAN_APPROVED."
    )
    res = project.run("guard_write.sh", write_payload(DECLARED))
    assert res.returncode == BLOCKED
    assert "no plan approved" in res.stderr


def test_settings_matcher_covers_every_file_writing_tool() -> None:
    settings = json.loads((TOOLKIT / "settings.json").read_text(encoding="utf-8"))
    matchers = [
        h["matcher"]
        for h in settings["hooks"]["PreToolUse"]
        if "guard_write" in h["hooks"][0]["command"]
    ]
    assert matchers
    tools = set(matchers[0].split("|"))
    assert {"Edit", "Write", "MultiEdit", "NotebookEdit"} <= tools
