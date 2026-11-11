"""I2 (C): verdict receipts are reported by `make check` (integrity), WARN only.

The I0 legacy fixture is built through the real gates, so on this branch its four
verdicts (roadmap, phase 01, plan and code of 1.1.1) already carry receipts. A verdict
from before I2 is simulated by deleting its receipt. Legacy exemption (plan 0.6) uses
the inventory flow of tests/test_m0b_inventory.py.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

import pytest
from conftest import DECLARED, LEAF_ID, TOOLKIT, Project, write_payload
from i0_cycle import SID
from i0_legacy import build_legacy, copy_legacy
from i0_projects import Installed
from test_m0b_inventory import INVENTORY, Repo, _fake_codex, _sealed

VERDICTS = {
    "progress/roadmap_review.md": "roadmap",
    "progress/phase_review_01.md": "01",
    f"progress/plan_review_{SID}.md": SID,
    f"progress/review_{SID}.md": SID,
}
CODE_VERDICT = f"progress/review_{SID}.md"
PHASE_VERDICT = "progress/phase_review_01.md"
MISSING = "verdict(s) without a receipt"
EXEMPT = "legacy item(s) exempt"


def receipt(verdict: str) -> str:
    return f"progress/verdict_{Path(verdict).name}.sha256"


@pytest.fixture(scope="module")
def legacy(tmp_path_factory: pytest.TempPathFactory) -> Installed:
    return build_legacy(tmp_path_factory.mktemp("legacy"))


@pytest.fixture
def repo(legacy: Installed, tmp_path: Path) -> Repo:
    copy = copy_legacy(legacy, tmp_path / "copy")
    return Repo(root=copy.root, repo=copy.repo, env=_fake_codex(tmp_path, copy.env))


def _run(repo: Repo, *argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv), cwd=repo.root, env=repo.env, text=True, capture_output=True, check=False
    )


def _integrity(repo: Repo) -> subprocess.CompletedProcess[str]:
    return _run(repo, "python3", "harness/_guard.py", "integrity")


def _make_check(repo: Repo) -> subprocess.CompletedProcess[str]:
    return _run(repo, "make", "-s", "--no-print-directory", "check")


def _warns(res: subprocess.CompletedProcess[str], *needles: str) -> list[str]:
    lines = (res.stdout + res.stderr).splitlines()
    return [ln for ln in lines if "WARN" in ln and all(n in ln for n in needles)]


def _receipt_warns(res: subprocess.CompletedProcess[str]) -> list[str]:
    return [ln for ln in _warns(res) if "receipt" in ln]


def _drop_receipts(repo: Repo, *verdicts: str) -> None:
    for verdict in verdicts:
        (repo.root / receipt(verdict)).unlink()


def _append(repo: Repo, rel: str, text: str = "\nEdited after the gate.\n") -> None:
    with (repo.root / rel).open("a") as handle:
        handle.write(text)


# ── table C ──────────────────────────────────────────────────────────────────


def test_the_legacy_fixture_carries_a_receipt_per_verdict(repo: Repo) -> None:
    for verdict in VERDICTS:
        assert (repo.root / receipt(verdict)).is_file(), verdict


def test_verdicts_with_matching_receipts_report_nothing(repo: Repo) -> None:
    res = _integrity(repo)
    assert res.returncode == 0, res.stdout + res.stderr
    assert _receipt_warns(res) == [], res.stdout


def test_a_changed_verdict_warns_naming_the_file(repo: Repo) -> None:
    _append(repo, CODE_VERDICT)
    res = _integrity(repo)
    assert res.returncode == 0, res.stdout + res.stderr
    hits = _warns(res, CODE_VERDICT, "receipt")
    assert len(hits) == 1, res.stdout
    assert "re-run" in hits[0]


def test_a_malformed_receipt_warns_like_a_mismatch(repo: Repo) -> None:
    (repo.root / receipt(CODE_VERDICT)).write_text("garbage\n")
    assert len(_warns(_integrity(repo), CODE_VERDICT, "receipt")) == 1


def test_verdicts_without_receipt_give_one_summary_line(repo: Repo) -> None:
    _drop_receipts(repo, CODE_VERDICT, PHASE_VERDICT)
    res = _integrity(repo)
    assert res.returncode == 0, res.stdout + res.stderr
    hits = _warns(res, MISSING)
    assert len(hits) == 1, res.stdout
    assert "2 " in hits[0]
    assert SID in hits[0] and "01" in hits[0]
    assert "re-run" in hits[0]


def test_the_summary_names_only_the_first_ids(repo: Repo) -> None:
    for n in range(2, 8):
        repo.write(f"progress/review_9.9.{n}.md", "# Review\n\n**Verdict:** APPROVED\n")
    res = _integrity(repo)
    hits = _warns(res, MISSING)
    assert len(hits) == 1, res.stdout
    assert "6 " in hits[0]
    assert "9.9.2" in hits[0] and "9.9.7" not in hits[0]


def test_a_receipt_without_its_verdict_warns(repo: Repo) -> None:
    stray = receipt("progress/review_9.9.9.md")
    repo.write(stray, f"{'0' * 64}  progress/review_9.9.9.md  code\n")
    res = _integrity(repo)
    assert res.returncode == 0, res.stdout + res.stderr
    assert len(_warns(res, stray)) == 1, res.stdout


def _sha(repo: Repo, rel: str) -> str:
    return hashlib.sha256((repo.root / rel).read_bytes()).hexdigest()


def test_a_receipt_naming_another_gate_warns(repo: Repo) -> None:
    line = f"{_sha(repo, CODE_VERDICT)}  {CODE_VERDICT}  plan\n"
    repo.write(receipt(CODE_VERDICT), line)
    assert len(_warns(_integrity(repo), CODE_VERDICT, "receipt")) == 1


@pytest.mark.parametrize("extra", ["lead", "trail"])
def test_a_receipt_with_extra_whitespace_warns(repo: Repo, extra: str) -> None:
    line = f"{_sha(repo, CODE_VERDICT)}  {CODE_VERDICT}  code"
    repo.write(receipt(CODE_VERDICT), f" {line}\n" if extra == "lead" else f"{line}  \n")
    assert len(_warns(_integrity(repo), CODE_VERDICT, "receipt")) == 1


@pytest.mark.parametrize("kind", ["directory", "broken-symlink"])
def test_an_unreadable_receipt_warns_and_keeps_the_exit_code(repo: Repo, kind: str) -> None:
    path = repo.root / receipt(CODE_VERDICT)
    path.unlink()
    if kind == "directory":
        path.mkdir()
    else:
        path.symlink_to(repo.root / "progress" / "nowhere")
    res = _make_check(repo)
    assert res.returncode == 0, res.stdout + res.stderr
    assert len(_warns(res, CODE_VERDICT, "receipt")) == 1, res.stdout


def test_a_receipt_gap_alone_asks_for_the_legacy_inventory(repo: Repo) -> None:
    repo.write("tests/acceptance/test_1.2.1.py", "def test_ac1() -> None: ...\n")
    sealed = _run(repo, "bash", "harness/review_with_codex.sh", "seal", "1.2.1")
    assert sealed.returncode == 0, sealed.stdout + sealed.stderr
    assert "legacy inventory not created" not in _integrity(repo).stdout
    _drop_receipts(repo, CODE_VERDICT)
    res = _integrity(repo)
    assert res.stdout.count("legacy inventory not created") == 1, res.stdout
    assert len(_warns(res, MISSING)) == 1, res.stdout


def test_review_history_and_drafts_are_not_verdicts(repo: Repo) -> None:
    assert list((repo.root / "progress/review_history").glob("*.md"))
    repo.write("progress/issue_draft.md", "---\ntitle: t\n---\nbody\n")
    res = _integrity(repo)
    assert _receipt_warns(res) == [], res.stdout


CASES = ["mismatch", "missing", "stray"]


@pytest.mark.parametrize("case", CASES)
def test_make_check_exit_code_is_unchanged_by_receipt_warns(repo: Repo, case: str) -> None:
    if case == "mismatch":
        _append(repo, CODE_VERDICT)
    elif case == "missing":
        _drop_receipts(repo, CODE_VERDICT)
    else:
        repo.write(receipt("progress/review_9.9.9.md"), "x\n")
    res = _make_check(repo)
    assert res.returncode == 0, res.stdout + res.stderr
    assert _receipt_warns(res), res.stdout


# ── legacy exemption (plan 0.6) ──────────────────────────────────────────────


def _legacy_without_receipts(repo: Repo) -> None:
    _drop_receipts(repo, *VERDICTS)
    repo.commit("verdicts from before I2")
    _sealed(repo)


def test_unchanged_legacy_verdicts_are_exempt_and_counted(repo: Repo) -> None:
    _legacy_without_receipts(repo)
    res = _integrity(repo)
    assert res.returncode == 0, res.stdout + res.stderr
    assert _warns(res, MISSING) == [], res.stdout
    assert any(EXEMPT in ln for ln in res.stdout.splitlines()), res.stdout


def test_a_changed_legacy_verdict_is_reported(repo: Repo) -> None:
    _legacy_without_receipts(repo)
    _append(repo, CODE_VERDICT)
    hits = _warns(_integrity(repo), MISSING)
    # The exemption is per item: a changed 1.1.1 reports both its verdicts (plan, code).
    assert len(hits) == 1 and SID in hits[0]
    assert "2 " in hits[0]
    assert "01" not in hits[0] and "roadmap" not in hits[0]


def test_a_tampered_inventory_exempts_no_verdict(repo: Repo) -> None:
    _legacy_without_receipts(repo)
    path = repo.root / INVENTORY
    data = json.loads(path.read_text())
    data["created_at"] = "tampered"
    path.write_text(json.dumps(data))
    hits = _warns(_integrity(repo), MISSING)
    assert len(hits) == 1 and "4 " in hits[0]


# ── the src/ unlock and seal do not change ───────────────────────────────────


def test_src_unlock_does_not_need_a_receipt(project: Project) -> None:
    assert not list((project.root / "progress").glob("verdict_*.sha256"))
    res = project.run("guard_write.sh", write_payload(project.root / DECLARED))
    assert res.returncode == 0, res.stdout + res.stderr


def test_seal_does_not_need_a_receipt(project: Project) -> None:
    project.write(f"tests/acceptance/test_{LEAF_ID}.py", "def test_ac1(): ...\n")
    res = project.run("review_with_codex.sh", None, "seal", LEAF_ID)
    assert res.returncode == 0, res.stdout + res.stderr


def test_seal_ignores_a_mismatched_receipt(project: Project) -> None:
    project.write(f"progress/verdict_plan_review_{LEAF_ID}.md.sha256", "x\n")
    project.write(f"tests/acceptance/test_{LEAF_ID}.py", "def test_ac1(): ...\n")
    res = project.run("review_with_codex.sh", None, "seal", LEAF_ID)
    assert res.returncode == 0, res.stdout + res.stderr


# ── manual mode: the human writes the receipt ────────────────────────────────


def _verdict_cli(project: Project, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python3", "harness/_verdict.py", *args],
        cwd=project.root,
        text=True,
        capture_output=True,
        check=False,
    )


def test_manual_mode_prints_the_receipt_step_before_record(project: Project) -> None:
    res = project.run(
        "review_with_codex.sh", None, "code", LEAF_ID, env={"HARNESS_CODEX_MODE": "manual"}
    )
    assert res.returncode == 0, res.stdout + res.stderr
    out = res.stdout
    step = f"python3 harness/_verdict.py receipt code {LEAF_ID}"
    assert step in out
    assert out.index(step) < out.index(f"python3 harness/_pr.py record code {LEAF_ID}")


def test_manual_mode_of_the_doc_gate_prints_no_receipt_step(project: Project) -> None:
    project.set_verdict("code", "APPROVED")
    res = project.run(
        "review_with_codex.sh", None, "doc", LEAF_ID, env={"HARNESS_CODEX_MODE": "manual"}
    )
    assert "_verdict.py receipt" not in res.stdout


@pytest.mark.parametrize(
    ("args", "verdict"),
    [
        (("code", LEAF_ID), f"progress/review_{LEAF_ID}.md"),
        (("plan", LEAF_ID), f"progress/plan_review_{LEAF_ID}.md"),
        (("phase", "01"), "progress/phase_review_01.md"),
        (("roadmap",), "progress/roadmap_review.md"),
    ],
)
def test_receipt_command_writes_the_receipt(
    project: Project, args: tuple[str, ...], verdict: str
) -> None:
    project.set_verdict("code", "APPROVED")
    res = _verdict_cli(project, "receipt", *args)
    assert res.returncode == 0, res.stdout + res.stderr
    digest = hashlib.sha256((project.root / verdict).read_bytes()).hexdigest()
    assert (project.root / receipt(verdict)).read_text() == f"{digest}  {verdict}  {args[0]}\n"


def test_receipt_command_refuses_another_gates_token(project: Project) -> None:
    project.set_verdict("code", "PLAN_APPROVED")
    res = _verdict_cli(project, "receipt", "code", LEAF_ID)
    assert res.returncode == 1
    assert not (project.root / receipt(f"progress/review_{LEAF_ID}.md")).exists()


def test_receipt_command_refuses_a_missing_verdict(project: Project) -> None:
    res = _verdict_cli(project, "receipt", "code", "9.9.9")
    assert res.returncode == 1


USAGE = TOOLKIT / "docs" / "usage.md"
KNOWN_LIMITS = [
    "SIGKILL",  # an orphaned reviewer can still write a verdict
    "byte-identical",  # a stale receipt still matches an identical verdict
    "unsealed",  # crash between commit and seal
    "linked worktree",  # slot outside the project root
    "does not prove who wrote",  # detection only
]


def _receipts_section() -> str:
    text = USAGE.read_text(encoding="utf-8")
    match = re.search(r"^## Verdict receipts\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert match, "docs/usage.md has no '## Verdict receipts' section"
    return match.group(1)


@pytest.mark.parametrize("limit", KNOWN_LIMITS)
def test_usage_states_known_limit(limit: str) -> None:
    assert limit in _receipts_section()


def test_usage_states_receipt_warns_and_remediation() -> None:
    section = _receipts_section()
    assert "progress/verdict_" in section
    assert "WARN" in section and "re-run" in section
