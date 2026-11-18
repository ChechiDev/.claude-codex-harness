"""Close-commit manifest: only the subtask's own paths may enter its close commit."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import _close_scope as cs
import pytest

TOOLKIT = Path(__file__).resolve().parents[1]
SPEC = "docs/plan/phase-01-p01/1.1.1-user.md"
ISS_SPEC = "docs/plan/issues/ISS-7-bug/ISS-7.2-fix.md"


def _git(root: Path, *args: str) -> str:
    res = subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)
    return res.stdout


def _write(root: Path, rel: str, content: str = "x\n") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _leaf(  # noqa: PLR0913
    sid: str = "1.1.1",
    files: list[str] | None = None,
    spec: str = SPEC,
    migrates: list[str] | None = None,
    *,
    phase: str = "01",
    issue: str = "",
) -> dict[str, Any]:
    return {
        "id": sid,
        "phase": phase,
        "issue": issue,
        "files": ["src/pkg/user.py"] if files is None else files,
        "spec": spec,
        "migrates": migrates or [],
    }


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    for rel in (
        "README.md",
        "src/pkg/other.py",
        "src/pkg/user.py",
        "tests/acceptance/test_1.1.10.py",
    ):
        _write(tmp_path, rel)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def _full_close(root: Path) -> None:
    for rel in (
        "src/pkg/user.py",
        "tests/unit/test_user.py",
        "tests/acceptance/test_1.1.1.py",
        "progress/acceptance_1.1.1.sha256",
        "progress/review_1.1.1.md",
        "progress/plan_review_1.1.1.md",
        "progress/impl_1.1.1.md",
        "progress/close_1.1.1.msg",
        "progress/review_history/1.1.1.md",
        "progress/current.md",
        "progress/history.md",
        "progress/pr_1.1.md",
        "docs/reference/1.1.1-user.md",
        "docs/plan/PLAN.md",
        SPEC,
        "feature_list.json",
    ):
        _write(root, rel, "changed\n")


def test_close_inside_manifest_is_ok(repo: Path) -> None:
    _full_close(repo)
    report = cs.check(repo, "1.1.1", _leaf())
    assert report.ok, report.explain()
    assert report.disallowed == ()
    assert "src/pkg/user.py" in report.allowed


def test_undeclared_production_file_blocks_with_reason(repo: Path) -> None:
    _write(repo, "src/pkg/other.py", "changed\n")
    report = cs.check(repo, "1.1.1", _leaf())
    assert not report.ok
    assert report.disallowed == ("src/pkg/other.py",)
    assert any("src/pkg/other.py" in line for line in report.explain())


@pytest.mark.parametrize(
    "rel",
    [
        "tests/acceptance/test_1.1.10.py",
        "tests/acceptance/test_1.1.2.py",
        "progress/review_1.1.10.md",
        "progress/acceptance_1.1.2.sha256",
        "progress/review_history/1.1.10.md",
        "docs/reference/1.1.10-user.md",
        "docs/plan/phase-01-p01/1.1.2-other.md",
        "progress/pr_1.2.md",
        ".env",
    ],
)
def test_other_ids_and_prefix_ids_are_foreign(repo: Path, rel: str) -> None:
    _write(repo, rel, "changed\n")
    assert cs.check(repo, "1.1.1", _leaf()).disallowed == (rel,)


def test_foreign_staged_content_is_reported(repo: Path) -> None:
    _write(repo, "src/pkg/user.py", "changed\n")
    _write(repo, "notes.txt")
    _git(repo, "add", "notes.txt", "src/pkg/user.py")
    report = cs.check(repo, "1.1.1", _leaf())
    assert report.staged_foreign == ("notes.txt",)
    assert not report.ok
    assert any("staged" in line for line in report.explain())


def test_deletions(repo: Path) -> None:
    (repo / "src/pkg/user.py").unlink()
    assert cs.check(repo, "1.1.1", _leaf()).ok
    (repo / "src/pkg/other.py").unlink()
    assert cs.check(repo, "1.1.1", _leaf()).disallowed == ("src/pkg/other.py",)


def test_staged_deletion_of_foreign_file(repo: Path) -> None:
    _git(repo, "rm", "-q", "README.md")
    report = cs.check(repo, "1.1.1", _leaf())
    assert report.staged_foreign == ("README.md",)


def test_rename_needs_both_sides(repo: Path) -> None:
    _git(repo, "mv", "src/pkg/user.py", "src/pkg/moved.py")
    report = cs.check(repo, "1.1.1", _leaf())
    assert report.disallowed == ("src/pkg/moved.py",)
    leaf = _leaf(files=["src/pkg/user.py", "src/pkg/moved.py"])
    assert cs.check(repo, "1.1.1", leaf).ok


def test_rename_from_foreign_source_blocks(repo: Path) -> None:
    _git(repo, "mv", "src/pkg/other.py", "src/pkg/user2.py")
    leaf = _leaf(files=["src/pkg/user.py", "src/pkg/user2.py"])
    report = cs.check(repo, "1.1.1", leaf)
    assert report.disallowed == ("src/pkg/other.py",)
    assert report.staged_foreign == ("src/pkg/other.py",)


@pytest.mark.parametrize(
    "name", ["src/pkg/my file.py", 'src/pkg/q"uote.py', "src/pkg/ñandú.py", "src/pkg/new\nline.py"]
)
def test_special_names(repo: Path, name: str) -> None:
    _write(repo, name)
    assert cs.check(repo, "1.1.1", _leaf()).disallowed == (name,)
    assert cs.check(repo, "1.1.1", _leaf(files=["src/pkg/user.py", name])).ok


def test_issue_leaf(repo: Path) -> None:
    for rel in (
        "src/pkg/user.py",
        "tests/acceptance/test_ISS-7.2.py",
        "progress/review_ISS-7.2.md",
        "progress/pr_ISS-7.md",
        "docs/reference/ISS-7.2-fix.md",
        "docs/plan/issues/ISS-7-bug.md",
        ISS_SPEC,
    ):
        _write(repo, rel, "changed\n")
    report = cs.check(repo, "ISS-7.2", _leaf("ISS-7.2", spec=ISS_SPEC))
    assert report.ok, report.explain()
    _write(repo, "docs/plan/issues/ISS-70-other.md")
    _write(repo, "progress/pr_ISS-70.md")
    assert cs.check(repo, "ISS-7.2", _leaf("ISS-7.2", spec=ISS_SPEC)).disallowed == (
        "docs/plan/issues/ISS-70-other.md",
        "progress/pr_ISS-70.md",
    )


def test_migrated_oracle_is_allowed(repo: Path) -> None:
    _write(repo, "tests/acceptance/test_1.1.10.py", "changed\n")
    _write(repo, "progress/acceptance_1.1.10.sha256")
    assert cs.check(repo, "1.1.1", _leaf(migrates=["1.1.10"])).ok


def test_declared_paths_are_normalized_and_cannot_escape(repo: Path) -> None:
    _write(repo, "src/pkg/user.py", "changed\n")
    assert cs.check(repo, "1.1.1", _leaf(files=["./src/pkg/user.py"])).ok
    _write(repo, "README.md", "changed\n")
    leaf = _leaf(files=["src/pkg/../../README.md"])
    assert cs.check(repo, "1.1.1", leaf).disallowed == ("README.md", "src/pkg/user.py")


def test_invalid_id_is_rejected(repo: Path) -> None:
    with pytest.raises(ValueError):
        cs.check(repo, "1.1.*", _leaf())


def test_parse_porcelain_rename_and_spaces() -> None:
    raw = b"R  new name.py\x00old name.py\x00 D gone.py\x00?? a b\x00"
    changes = cs.parse_porcelain(raw)
    assert changes == [
        cs.Change("new name.py", "old name.py", "R", " "),
        cs.Change("gone.py", None, " ", "D"),
        cs.Change("a b", None, "?", "?"),
    ]


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOLKIT / "lib/_close_scope.py"), *args],
        cwd=root,
        capture_output=True,
        text=True,
    )


def test_cli(repo: Path) -> None:
    features = {
        "features": [
            {"id": "1.1", "phase": "01", "subtasks": [{**_leaf(), "status": "in_progress"}]}
        ]
    }
    (repo / "feature_list.json").write_text(json.dumps(features), encoding="utf-8")
    _git(repo, "add", "feature_list.json")
    _git(repo, "commit", "-q", "-m", "plan")
    _write(repo, "src/pkg/user.py", "changed\n")
    assert _cli(repo, "check", "1.1.1").returncode == 0
    _write(repo, "src/pkg/other.py", "changed\n")
    res = _cli(repo, "check", "1.1.1")
    assert res.returncode == 1
    assert "src/pkg/other.py" in res.stderr
    assert _cli(repo, "check", "9.9.9").returncode == 2


def test_hidden_submodule_gitlink_is_still_reported(repo: Path) -> None:
    _git(repo, "config", "diff.ignoreSubmodules", "all")
    sha = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{sha},vendor/sub")
    _git(repo, "commit", "-q", "-m", "sub")
    _write(repo, "README.md", "next\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "next")
    new = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "update-index", "--cacheinfo", f"160000,{new},vendor/sub")
    report = cs.check(repo, "1.1.1", _leaf())
    assert report.staged_foreign == ("vendor/sub",)


def test_copy_source_is_not_part_of_the_change() -> None:
    copy = cs.Change("src/pkg/user.py", "README.md", "C", " ")
    report = cs.evaluate("1.1.1", _leaf(), [copy])
    assert report.ok, report.explain()


def test_documenter_concept_docs_are_allowed(repo: Path) -> None:
    _write(repo, "docs/architecture.md")
    _write(repo, "docs/conventions.md")
    assert cs.check(repo, "1.1.1", _leaf()).ok


def test_cli_files_prints_the_allowed_paths_nul_separated(repo: Path) -> None:
    features = {
        "features": [
            {"id": "1.1", "phase": "01", "subtasks": [{**_leaf(), "status": "in_progress"}]}
        ]
    }
    (repo / "feature_list.json").write_text(json.dumps(features), encoding="utf-8")
    _git(repo, "add", "feature_list.json")
    _git(repo, "commit", "-q", "-m", "plan")
    _write(repo, "src/pkg/user.py", "changed\n")
    _write(repo, "tests/unit/test a.py", "changed\n")
    res = _cli(repo, "files", "1.1.1")
    assert res.returncode == 0, res.stderr
    assert res.stdout.split("\0") == ["src/pkg/user.py", "tests/unit/test a.py", ""]
    _write(repo, "src/pkg/other.py", "changed\n")
    res = _cli(repo, "files", "1.1.1")
    assert res.returncode == 1
    assert res.stdout == ""
    assert "src/pkg/other.py" in res.stderr


# ── harness bookkeeping of the subtask's chain (issue #40) ────────────────────


def test_gate_seals_and_verdict_receipts_of_the_chain(repo: Path) -> None:
    for rel in (
        "progress/verdict_review_1.1.1.md.sha256",
        "progress/verdict_plan_review_1.1.1.md.sha256",
        "progress/verdict_phase_review_01.md.sha256",
        "progress/verdict_roadmap_review.md.sha256",
        "progress/plan_roadmap.sha256",
        "progress/plan_phase_01.sha256",
    ):
        _write(repo, rel)
    assert cs.check(repo, "1.1.1", _leaf()).ok
    for rel in (
        "progress/verdict_review_1.1.2.md.sha256",
        "progress/verdict_phase_review_02.md.sha256",
        "progress/plan_phase_02.sha256",
        "progress/plan_issue_ISS-7.sha256",
    ):
        _write(repo, rel)
    assert len(cs.check(repo, "1.1.1", _leaf()).disallowed) == 4


def test_issue_chain_bookkeeping(repo: Path) -> None:
    for rel in (
        "progress/verdict_issue_review_ISS-7.md.sha256",
        "progress/issue_review_ISS-7.md",
        "progress/plan_issue_ISS-7.sha256",
        "progress/issue_migrations_ISS-7.json",
        "progress/review_history/ISS-7.md",
    ):
        _write(repo, rel)
    leaf = _leaf("ISS-7.2", spec=ISS_SPEC, phase="", issue="ISS-7")
    assert cs.check(repo, "ISS-7.2", leaf).ok
    _write(repo, "progress/issue_review_ISS-8.md")
    assert cs.check(repo, "ISS-7.2", leaf).disallowed == ("progress/issue_review_ISS-8.md",)


def test_first_close_carries_the_roadmap_and_phase_approval(repo: Path) -> None:
    for rel in (
        "progress/roadmap_review.md",
        "progress/phase_review_01.md",
        "progress/review_history/roadmap.md",
        "progress/review_history/phase-01.md",
        "docs/plan/phase-01-p01.md",
    ):
        _write(repo, rel)
    assert cs.check(repo, "1.1.1", _leaf()).ok
    _write(repo, "docs/plan/phase-02-next.md")
    _write(repo, "progress/phase_review_02.md")
    assert cs.check(repo, "1.1.1", _leaf()).disallowed == (
        "docs/plan/phase-02-next.md",
        "progress/phase_review_02.md",
    )


def test_legacy_leaf_without_spec_resolves_it_by_convention(repo: Path) -> None:
    _write(repo, "docs/plan/phase-01-p01.md")
    _write(repo, SPEC)
    _write(repo, "docs/plan/phase-01-p01/1.1.2-other.md")
    report = cs.check(repo, "1.1.1", _leaf(spec=""))
    assert SPEC in report.allowed
    assert report.disallowed == ("docs/plan/phase-01-p01/1.1.2-other.md",)


def test_project_in_a_subdirectory(repo: Path) -> None:
    sub = repo / "backend"
    _write(sub, "src/pkg/user.py")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "sub")
    _write(sub, "src/pkg/user.py", "changed\n")
    _write(sub, "progress/review_1.1.1.md")
    report = cs.check(sub, "1.1.1", _leaf())
    assert report.ok, report.explain()
    assert report.allowed == ("progress/review_1.1.1.md", "src/pkg/user.py")
    _write(repo, "src/pkg/user.py", "outside the project\n")
    report = cs.check(sub, "1.1.1", _leaf())
    assert report.disallowed == (":/src/pkg/user.py",)


@pytest.mark.parametrize(
    "rel",
    [
        "progress/notes.md",
        "progress/verdict_notes.md.sha256",
        "progress/review_history/other.md",
        "progress/plan_other.sha256",
        "docs/plan/notes.md",
    ],
)
def test_foreign_file_in_progress_still_blocks(repo: Path, rel: str) -> None:
    _write(repo, "docs/plan/phase-01-p01.md")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "phase")
    _write(repo, rel)
    assert cs.check(repo, "1.1.1", _leaf()).disallowed == (rel,)


def test_first_close_carries_the_specs_of_the_sealed_phase(repo: Path) -> None:
    sibling = "docs/plan/phase-01-p01/1.2.1-hello.md"
    _write(repo, "docs/plan/phase-01-p01.md")
    _write(repo, sibling)
    assert cs.check(repo, "1.1.1", _leaf()).disallowed == (sibling,)
    _write(repo, "progress/plan_phase_01.sha256", f"{'0' * 64}  {sibling}\n")
    assert cs.check(repo, "1.1.1", _leaf()).ok
    _write(repo, "progress/plan_phase_01.sha256", f"{'0' * 64}  ../escape.md\n")
    assert cs.check(repo, "1.1.1", _leaf()).disallowed == (sibling,)


@pytest.mark.parametrize("listed", ["scratch/notes.txt", "src/pkg/other.py", "docs/plan/x.md"])
def test_a_tampered_seal_cannot_widen_the_manifest(repo: Path, listed: str) -> None:
    _write(repo, "docs/plan/phase-01-p01.md")
    _write(repo, listed, "changed\n")
    _write(repo, "progress/plan_phase_01.sha256", f"{'0' * 64}  {listed}\n")
    assert cs.check(repo, "1.1.1", _leaf()).disallowed == (listed,)
