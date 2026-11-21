"""I1 (issue #18) block 1: required seals derived from state, WARN first (plan 0.5).

The I0 legacy fixture (tests/i0_legacy.py) is the base: roadmap and phase 01 approved
and sealed, done leaf 1.1.1 with a sealed oracle, baseline leaf 1.2.1 with no oracle.
No legacy inventory exists here, so nothing is exempt (exemption is block 2).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from conftest import ISSUE, SPEC
from i0_cycle import SID
from i0_legacy import ORACLE, build_legacy, copy_legacy
from i0_projects import Installed

pytestmark = pytest.mark.e2e  # real install.sh / make check (#68)

ACC_SEAL = f"progress/acceptance_{SID}.sha256"
ROADMAP_SEAL = "progress/plan_roadmap.sha256"
PHASE_SEAL = "progress/plan_phase_01.sha256"
DRAFT = "progress/issue_draft.md"
DRAFT_REVIEW = "progress/issue_draft_review.md"
DRAFT_SEAL = "progress/issue_draft.sha256"


@pytest.fixture(scope="module")
def legacy(tmp_path_factory: pytest.TempPathFactory) -> Installed:
    return build_legacy(tmp_path_factory.mktemp("legacy"))


@pytest.fixture
def proj(legacy: Installed, tmp_path: Path) -> Installed:
    return copy_legacy(legacy, tmp_path / "copy")


def _integrity(p: Installed) -> subprocess.CompletedProcess[str]:
    return p.run("python3", "harness/_guard.py", "integrity")


def _lines(res: subprocess.CompletedProcess[str], kind: str) -> list[str]:
    return [ln for ln in (res.stdout + res.stderr).splitlines() if kind in ln]


def _warned(res: subprocess.CompletedProcess[str], *needles: str) -> bool:
    return any(all(n in ln for n in needles) for ln in _lines(res, "WARN"))


def _failed(res: subprocess.CompletedProcess[str], *needles: str) -> bool:
    return any(all(n in ln for n in needles) for ln in _lines(res, "FAIL"))


def _write(p: Installed, rel: str, text: str) -> None:
    path = p.root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _git(p: Installed, *args: str) -> None:
    subprocess.run(["git", *args], cwd=p.repo, env=p.env, check=True, capture_output=True)


def _commit(p: Installed, msg: str) -> None:
    _git(p, "add", "-A")
    _git(p, "commit", "-qm", msg, "--no-verify")


def _sync(p: Installed) -> None:
    res = p.run("python3", "harness/sync_plan.py")
    assert res.returncode == 0, res.stdout + res.stderr


# ── A. required seals derived from state ─────────────────────────────────────


def test_fully_sealed_done_leaf_gives_no_seal_warn(proj: Installed) -> None:
    res = _integrity(proj)
    assert not _warned(res, SID), res.stdout


def test_done_leaf_without_its_acceptance_seal_warns(proj: Installed) -> None:
    (proj.root / ACC_SEAL).unlink()
    res = _integrity(proj)
    assert res.returncode == 0, res.stdout
    assert _warned(res, SID, "review_with_codex.sh seal"), res.stdout


def test_approved_roadmap_without_seal_still_warns(proj: Installed) -> None:
    (proj.root / ROADMAP_SEAL).unlink()
    res = _integrity(proj)
    assert res.returncode == 0
    assert _warned(res, "roadmap", "seal roadmap"), res.stdout


def test_approved_phase_without_seal_still_warns(proj: Installed) -> None:
    (proj.root / PHASE_SEAL).unlink()
    res = _integrity(proj)
    assert res.returncode == 0
    assert _warned(res, "phase 01", "seal phase 01"), res.stdout


def _approved_issue(p: Installed) -> None:
    issues = p.root / "docs/plan/issues"
    (issues / "ISS-1-i1").mkdir(parents=True)
    (issues / "ISS-1-i1.md").write_text(
        ISSUE.format(iid="ISS-1", n=1, type="fix", priority="next", extra="")
    )
    (issues / "ISS-1-i1" / "ISS-1.1-s.md").write_text(
        SPEC.format(sid="ISS-1.1", status="done", depends="none", slug="ISS11")
    )
    _write(p, "progress/issue_review_ISS-1.md", "# Review\n\n**Verdict:** ISSUE_APPROVED\n")
    _sync(p)


def test_approved_issue_without_seal_warns(proj: Installed) -> None:
    _approved_issue(proj)
    res = _integrity(proj)
    assert _warned(res, "ISS-1", "seal issue ISS-1"), res.stdout


def test_done_issue_leaf_with_tests_and_no_seal_warns(proj: Installed) -> None:
    _approved_issue(proj)
    _write(proj, "tests/acceptance/test_ISS-1.1.py", "def test_ac1_works() -> None: ...\n")
    res = _integrity(proj)
    assert res.returncode == 0, res.stdout
    assert _warned(res, "ISS-1.1", "review_with_codex.sh seal"), res.stdout


def _approved_draft(p: Installed) -> None:
    _write(p, DRAFT, "Title: draft\n\nBody.\n")
    _write(p, DRAFT_REVIEW, "# Review\n\n**Verdict:** DRAFT_APPROVED\n")


def test_approved_draft_without_seal_warns(proj: Installed) -> None:
    _approved_draft(proj)
    res = _integrity(proj)
    assert res.returncode == 0, res.stdout
    assert _warned(res, "issue draft", "seal draft"), res.stdout


# ── A. leaves without any oracle (Q1) ────────────────────────────────────────


def test_done_leaf_without_acceptance_tests_warns(proj: Installed) -> None:
    (proj.root / ORACLE).unlink()
    (proj.root / ACC_SEAL).unlink()
    res = _integrity(proj)
    assert res.returncode == 0, res.stdout
    assert _warned(res, SID, "no acceptance test"), res.stdout


def test_baseline_leaf_of_approved_phase_without_tests_warns(proj: Installed) -> None:
    res = _integrity(proj)
    assert res.returncode == 0, res.stdout
    assert _warned(res, "1.2.1", "no acceptance test"), res.stdout


def test_baseline_leaf_with_tests_and_no_seal_warns(proj: Installed) -> None:
    _write(proj, "tests/acceptance/test_1.2.1.py", "def test_ac1() -> None: ...\n")
    res = _integrity(proj)
    assert res.returncode == 0, res.stdout
    assert _warned(res, "1.2.1", "review_with_codex.sh seal 1.2.1"), res.stdout


def test_baseline_leaf_of_unapproved_phase_is_not_reported(proj: Installed) -> None:
    (proj.root / "progress/phase_review_01.md").unlink()
    (proj.root / PHASE_SEAL).unlink()
    res = _integrity(proj)
    assert not _warned(res, "1.2.1"), res.stdout


def test_done_leaf_with_empty_manifest_and_no_tests_warns(proj: Installed) -> None:
    (proj.root / ORACLE).unlink()
    (proj.root / ACC_SEAL).write_text("")
    res = _integrity(proj)
    assert res.returncode == 0, res.stdout
    assert _warned(res, SID, "no acceptance test"), res.stdout


def test_baseline_leaf_with_empty_manifest_and_no_tests_warns(proj: Installed) -> None:
    _write(proj, "progress/acceptance_1.2.1.sha256", "")
    res = _integrity(proj)
    assert res.returncode == 0, res.stdout
    assert _warned(res, "1.2.1", "no acceptance test"), res.stdout


# ── A. approved items whose plan file is gone ────────────────────────────────


PHASE_FILE = "docs/plan/phase-01-greetings.md"
ISSUE_FILE = "docs/plan/issues/ISS-1-i1.md"


@pytest.mark.parametrize("keep_seal", [False, True])
def test_approved_phase_without_plan_file_warns(proj: Installed, keep_seal: bool) -> None:
    (proj.root / PHASE_FILE).unlink()
    if not keep_seal:
        (proj.root / PHASE_SEAL).unlink()
    res = _integrity(proj)
    assert res.returncode == 1, res.stdout  # the roadmap seal already covers this file
    assert _failed(res, PHASE_FILE, "removed"), res.stdout
    assert _warned(res, "phase 01", "plan file"), res.stdout


def test_approved_issue_without_plan_file_warns(proj: Installed) -> None:
    _approved_issue(proj)
    (proj.root / ISSUE_FILE).unlink()
    res = _integrity(proj)
    assert res.returncode == 0, res.stdout
    assert _warned(res, "issue ISS-1", "plan file"), res.stdout


def test_unapproved_issue_without_plan_file_is_not_reported(proj: Installed) -> None:
    _approved_issue(proj)
    (proj.root / "progress/issue_review_ISS-1.md").unlink()
    (proj.root / ISSUE_FILE).unlink()
    assert not _warned(_integrity(proj), "plan file")


# ── B. manifest completeness ─────────────────────────────────────────────────


def test_unlisted_new_acceptance_test_warns(proj: Installed) -> None:
    _write(proj, f"tests/acceptance/test_{SID}.extra", "data\n")
    res = _integrity(proj)
    assert res.returncode == 0, res.stdout
    assert _warned(res, f"test_{SID}.extra", "not in its seal"), res.stdout


def test_emptied_manifest_warns(proj: Installed) -> None:
    (proj.root / ACC_SEAL).write_text("")
    res = _integrity(proj)
    assert res.returncode == 0, res.stdout
    assert _warned(res, ORACLE, "not in its seal"), res.stdout


def test_malformed_manifest_line_is_a_controlled_fail(proj: Installed) -> None:
    (proj.root / ACC_SEAL).write_text("garbage\n")
    res = _integrity(proj)
    assert res.returncode == 1
    assert "Traceback" not in res.stderr, res.stderr
    assert _failed(res, f"acceptance_{SID}.sha256", "line 1"), res.stdout


def test_changed_listed_test_still_fails(proj: Installed) -> None:
    (proj.root / ORACLE).write_text("changed\n")
    res = _integrity(proj)
    assert res.returncode == 1
    assert _failed(res, ORACLE), res.stdout


# ── C. committed deletions ───────────────────────────────────────────────────


def test_acceptance_seal_deleted_and_committed_warns(proj: Installed) -> None:
    (proj.root / ACC_SEAL).unlink()
    _commit(proj, "drop seal")
    res = _integrity(proj)
    assert res.returncode == 0, res.stdout
    assert _warned(res, SID, "review_with_codex.sh seal"), res.stdout


def test_phase_seal_deleted_and_committed_warns(proj: Installed) -> None:
    (proj.root / PHASE_SEAL).unlink()
    _commit(proj, "drop phase seal")
    res = _integrity(proj)
    assert _warned(res, "phase 01", "seal phase 01"), res.stdout


# ── draft content vs its existing seal ───────────────────────────────────────


def _sealed_draft(p: Installed) -> None:
    _approved_draft(p)
    res = p.run("bash", "harness/review_with_codex.sh", "seal", "draft")
    assert res.returncode == 0, res.stdout + res.stderr


def test_sealed_draft_unchanged_passes(proj: Installed) -> None:
    _sealed_draft(proj)
    res = _integrity(proj)
    assert not _failed(res, "issue draft"), res.stdout
    assert not _warned(res, "issue draft"), res.stdout


def test_sealed_draft_changed_fails(proj: Installed) -> None:
    _sealed_draft(proj)
    _write(proj, DRAFT, "Title: draft\n\nEdited body.\n")
    res = _integrity(proj)
    assert res.returncode == 1
    assert _failed(res, DRAFT), res.stdout


def test_sealed_draft_deleted_fails(proj: Installed) -> None:
    _sealed_draft(proj)
    (proj.root / DRAFT).unlink()
    res = _integrity(proj)
    assert res.returncode == 1
    assert _failed(res, DRAFT), res.stdout


# ── exit code unchanged on the untouched legacy project (0.5) ────────────────


def test_make_check_exit_code_unchanged_on_legacy(proj: Installed) -> None:
    res = proj.make("check")
    assert res.returncode == 0, res.stdout + res.stderr
