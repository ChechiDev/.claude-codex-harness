"""Issues lane: the approved issue breakdown is sealed; issue files are Codex-owned;
an in_progress issue subtask must be on its issue branch; close messages carry [ISS-n.k]."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from conftest import Plan, write_payload

ISSUE_DIR = "docs/plan/issues/ISS-14-i14"


def _guard(plan: Plan, *args: str) -> subprocess.CompletedProcess[str]:
    return plan.run_py("_guard.py", *args)


def _edit(plan: Plan, rel: str, old: str, new: str) -> None:
    path = plan.root / rel
    path.write_text(path.read_text().replace(old, new))


def _approved_issue(plan: Plan) -> Plan:
    plan.issue(14).isub("ISS-14.1").isub("ISS-14.2").sync().approve_issue(14)
    res = _guard(plan, "seal", "issue", "ISS-14")
    assert res.returncode == 0, res.stdout + res.stderr
    return plan


def test_issue_seal_needs_issue_approved(plan: Plan) -> None:
    plan.issue(14).isub("ISS-14.1").sync()
    res = _guard(plan, "seal", "issue", "ISS-14")
    assert res.returncode == 1
    assert "cannot seal issue ISS-14: it is not ISSUE_APPROVED" in res.stdout + res.stderr


def test_issue_seal_lists_header_specs_and_migration_snapshot(plan: Plan) -> None:
    _approved_issue(plan)
    manifest = plan.root / "progress/plan_issue_ISS-14.sha256"
    paths = [line.split(maxsplit=1)[1] for line in manifest.read_text().splitlines()]
    assert paths == [
        "docs/plan/issues/ISS-14-i14.md",
        f"{ISSUE_DIR}/ISS-14.1-s.md",
        f"{ISSUE_DIR}/ISS-14.2-s.md",
        "progress/issue_migrations_ISS-14.json",  # frozen even when empty
    ]


def test_sealed_issue_passes_integrity(plan: Plan) -> None:
    res = _guard(_approved_issue(plan), "integrity")
    assert res.returncode == 0, res.stdout


def test_status_moves_freely_after_issue_approval(plan: Plan) -> None:
    _approved_issue(plan)
    _edit(plan, f"{ISSUE_DIR}/ISS-14.2-s.md", "status: pending", "status: blocked")
    assert _guard(plan, "integrity").returncode == 0


def test_editing_an_issue_spec_after_approval_fails(plan: Plan) -> None:
    _approved_issue(plan)
    _edit(plan, f"{ISSUE_DIR}/ISS-14.2-s.md", "src/app/mISS-142.py", "src/app/other.py")
    res = _guard(plan, "integrity")
    assert res.returncode == 1
    assert (
        f"{ISSUE_DIR}/ISS-14.2-s.md changed after issue ISS-14 was approved "
        "(re-run its Codex gate: ./harness/review_with_codex.sh issue ISS-14)"
    ) in res.stdout


def test_adding_an_issue_spec_after_approval_fails(plan: Plan) -> None:
    _approved_issue(plan)
    plan.isub("ISS-14.3").sync()
    res = _guard(plan, "integrity")
    assert res.returncode == 1
    assert f"{ISSUE_DIR}/ISS-14.3-s.md added after issue ISS-14 was approved" in res.stdout


@pytest.mark.e2e
def test_issue_plan_gate_reseals_its_spec(plan: Plan) -> None:
    _approved_issue(plan)
    _edit(plan, f"{ISSUE_DIR}/ISS-14.1-s.md", "status: pending", "status: in_progress")
    _edit(plan, f"{ISSUE_DIR}/ISS-14.1-s.md", "src/app/mISS-141.py", "src/app/fixed.py")
    assert plan.run_py("sync_plan.py").returncode == 0
    plan.verdict("plan_review_ISS-14.1.md", "CHANGES_REQUESTED")
    res = _guard(plan, "integrity")
    assert res.returncode == 0, res.stdout  # only a warning while its plan gate is open
    assert "its subtask is still in its plan gate" in res.stdout
    plan.verdict("plan_review_ISS-14.1.md", "PLAN_APPROVED")
    acceptance = plan.root / "tests/acceptance/test_ISS-14.1.py"
    acceptance.parent.mkdir(parents=True)
    acceptance.write_text("def test_ac1_works(): ...\n")
    res = _guard(plan, "seal", "ISS-14.1")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "re-sealed its approved spec" in res.stdout
    res = _guard(plan, "integrity")
    assert res.returncode == 0, res.stdout
    assert "plan gate" not in res.stdout


def test_approved_but_unsealed_issue_warns(plan: Plan) -> None:
    plan.issue(14).isub("ISS-14.1").sync().approve_issue(14)
    res = _guard(plan, "integrity")
    assert res.returncode == 0
    assert (
        "issue ISS-14 is approved but not sealed (human: review_with_codex.sh seal issue ISS-14)"
    ) in res.stdout


@pytest.mark.parametrize(
    "rel",
    [
        "progress/issue_review_ISS-14.md",
        "progress/issue_draft_review.md",
        "progress/issue_migrations_ISS-14.json",
        "progress/oracle_migrations/ISS-14.1.json",
        "progress/plan_issue_ISS-14.sha256",
        "progress/issue_draft.sha256",
    ],
)
def test_issue_gate_files_are_codex_owned(plan: Plan, rel: str) -> None:
    res = subprocess.run(
        ["python3", str(Path(__file__).resolve().parents[1] / "lib/_guard.py"), "write"],
        cwd=plan.root,
        input=json.dumps(write_payload(plan.root / rel)),
        text=True,
        capture_output=True,
        check=False,
        env={"PATH": "/usr/bin:/bin", "CLAUDE_PROJECT_DIR": str(plan.root)},
    )
    assert res.returncode == 2, res.stderr
    assert "owned by Codex" in res.stderr


def test_the_issue_draft_is_the_leaders(plan: Plan) -> None:
    res = subprocess.run(
        ["python3", str(Path(__file__).resolve().parents[1] / "lib/_guard.py"), "write"],
        cwd=plan.root,
        input=json.dumps(write_payload(plan.root / "progress/issue_draft.md")),
        text=True,
        capture_output=True,
        check=False,
        env={"PATH": "/usr/bin:/bin", "CLAUDE_PROJECT_DIR": str(plan.root)},
    )
    assert res.returncode == 0, res.stderr


# ── issue branch ─────────────────────────────────────────────────────────────


def _git(plan: Plan, *args: str) -> None:
    subprocess.run(["git", *args], cwd=plan.root, check=True, capture_output=True)


def _repo_with_issue_in_progress(plan: Plan) -> Plan:
    plan.issue(14, "refactor").isub("ISS-14.1", "in_progress").sync().approve_issue(14)
    _git(plan, "init", "-q", "-b", "main")
    _git(
        plan,
        "-c",
        "user.email=t@t",
        "-c",
        "user.name=t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "init",
    )
    return plan


def test_issue_subtask_off_its_branch_fails_even_without_git_config(plan: Plan) -> None:
    res = _guard(_repo_with_issue_in_progress(plan), "integrity")
    assert res.returncode == 1
    assert (
        "subtask ISS-14.1 is in_progress on 'main', but its issue branch is "
        "'refactor/ISS-14-issue-14'"
    ) in res.stdout


def test_issue_subtask_on_its_branch_passes(plan: Plan) -> None:
    _repo_with_issue_in_progress(plan)
    _git(plan, "switch", "-q", "-c", "refactor/ISS-14-issue-14")
    res = _guard(plan, "integrity")
    assert "subtask ISS-14.1 is on its issue branch refactor/ISS-14-issue-14" in res.stdout


# ── close message ────────────────────────────────────────────────────────────


def test_close_message_for_an_issue_subtask(plan: Plan, tmp_path: Path) -> None:
    msg = tmp_path / "close.msg"
    msg.write_text("refactor(parsing): report invalid flags [ISS-14.1]\n\nBody.\n")
    assert _guard(plan, "commit-message", str(msg), "ISS-14.1").returncode == 0
    msg.write_text("refactor(parsing): report invalid flags [14.1]\n")
    assert _guard(plan, "commit-message", str(msg), "ISS-14.1").returncode == 1
